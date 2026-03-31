"""6개 모델별 최종 영상 조립 - TTS 재사용, 모델별 순차 처리"""

import asyncio
import glob
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.audio_utils import run, build_aligned_narration
from core.subtitle_utils import split_subtitle_natural, split_title
from core.image_pipeline import process_ai_clip
from core.video_assembler import get_duration, calculate_dynamic_clips_image
from config import settings

# ── 설정 ──
BASE_DIR = "storage/model_comparison"
TTS_DIR = os.path.join(BASE_DIR, "tts")
MODELS_TO_TEST = ["hailuo", "hailuo23", "wan", "kling", "kling26", "veo"]
TITLE = "빨간 홍조? 이거 모르면 손해"
LINES = [
    {"text": "건조한 피부 때문에 울긋불긋한 얼굴 고민이죠?"},
    {"text": "아무리 발라도 빨간 얼굴은 그대로잖아요."},
    {"text": "이건 피부 장벽이 무너졌다는 신호거든요."},
    {"text": "세라마이솜 5만 ppm이 장벽을 꽉 채워준대요."},
    {"text": "고농축 성분이라 진정 효과가 진짜 확실해요."},
    {"text": "홍조 탈출 비법 궁금하면 바로 댓글 확인!"},
]
BGM_PATH = os.path.join(settings.BGM_DIR, "Good Starts - Jingle Punks.mp3")
BGM_VOLUME = 0.12
BGM_START_SEC = 1.7
SENTENCES = [l["text"] for l in LINES]


def _escape_filter(text):
    return text.replace("'", "'\\''").replace(",", "\\,").replace(":", "\\:")


def _escape_fontpath(path):
    return path.replace(":", "\\:")


def build_subtitle_filter(timings):
    """자막 + 타이틀 필터 문자열 생성"""
    font_title = settings.FONT_TITLE
    font_sub = settings.FONT_SUB
    sq = settings.TARGET_WIDTH
    h = settings.TARGET_HEIGHT
    sq_y = (h - sq) // 2
    sub_y = sq_y + sq - 200

    subtitles = split_subtitle_natural(timings)

    sub_filters = []
    for start, end, text in subtitles:
        escaped = _escape_filter(text)
        sub_filters.append(
            f"drawtext=fontfile='{_escape_fontpath(font_sub)}':text='{escaped}':"
            f"fontsize=55:fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={sub_y}:"
            f"enable='between(t,{start},{end})'"
        )

    title_filters = []
    title_lines = split_title(TITLE, max_chars=8)
    title_fontsize = 120
    title_line_gap = 114
    title_colors = ["white", "#E8D44D"]
    fp = _escape_fontpath(font_title)

    for j, line in enumerate(title_lines):
        escaped = _escape_filter(line)
        if len(title_lines) == 1:
            ty = sq_y - title_fontsize - 30
        else:
            base_y = sq_y - (len(title_lines) * title_line_gap) - 10
            ty = base_y + (j * title_line_gap)
        color = title_colors[min(j, len(title_colors) - 1)]
        # 그림자
        title_filters.append(
            f"drawtext=fontfile='{fp}':text='{escaped}':"
            f"fontsize={title_fontsize}:fontcolor=black@0.5:"
            f"x=(w-text_w)/2+6:y={ty}+6"
        )
        # 본문
        title_filters.append(
            f"drawtext=fontfile='{fp}':text='{escaped}':"
            f"fontsize={title_fontsize}:fontcolor={color}:"
            f"borderw=4:bordercolor=black@0.8:"
            f"x=(w-text_w)/2:y={ty}"
        )

    return ",".join(title_filters + sub_filters)


async def assemble_one_model(model_key, clip_durations, narration_path, timings):
    """단일 모델 최종 영상 조립"""
    model_dir = os.path.join(BASE_DIR, model_key)
    clips_dir = os.path.join(model_dir, "clips")
    temp_dir = os.path.join(model_dir, "temp")
    output_dir = os.path.join(model_dir, "output")
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    ai_clips = sorted(glob.glob(os.path.join(clips_dir, "clip_raw_*.mp4")))
    if len(ai_clips) != 6:
        raise RuntimeError(f"클립 {len(ai_clips)}개 (6개 필요)")

    # Step 1: AI 클립 trim + 줌인
    print(f"  [1/5] AI 클립 trim + 줌인...")
    clip_files = []
    for i, (raw, dur) in enumerate(zip(ai_clips, clip_durations)):
        out = os.path.join(temp_dir, f"clip_{i:02d}.mp4")
        await asyncio.to_thread(
            process_ai_clip,
            clip_path=raw,
            output_path=out,
            duration=dur,
            width=settings.TARGET_WIDTH,
            height=settings.TARGET_HEIGHT,
            fps=settings.FPS,
        )
        clip_files.append(out)

    # Step 2: 클립 연결
    print(f"  [2/5] 클립 연결...")
    concat_list = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list, "w") as f:
        for c in clip_files:
            f.write(f"file '{os.path.abspath(c)}'\n")
    concat_out = os.path.join(temp_dir, "concat_raw.mp4")
    await asyncio.to_thread(
        run,
        f'ffmpeg -y -f concat -safe 0 -i "{concat_list}" '
        f'-c:v libx264 -preset fast -crf 18 "{concat_out}"',
    )

    # Step 3: 오디오 믹싱 (나레이션 + BGM)
    print(f"  [3/5] 오디오 믹싱...")
    audio_out = os.path.join(temp_dir, "mixed_audio.mp4")
    vid_dur = await asyncio.to_thread(get_duration, concat_out)
    await asyncio.to_thread(
        run,
        f'ffmpeg -y -i "{concat_out}" -i "{narration_path}" -i "{BGM_PATH}" '
        f'-filter_complex "'
        f"[1:a]volume=1.0[narr];"
        f"[2:a]atrim={BGM_START_SEC}:{BGM_START_SEC + vid_dur},"
        f"asetpts=PTS-STARTPTS,volume={BGM_VOLUME}[bgm];"
        f'[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]'
        f'" '
        f'-map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k '
        f'-shortest "{audio_out}"',
    )

    # Step 4: 라우드니스 노멀라이즈
    print(f"  [4/5] 오디오 노멀라이즈...")
    norm_out = os.path.join(temp_dir, "normalized.mp4")
    await asyncio.to_thread(
        run,
        f'ffmpeg -y -i "{audio_out}" '
        f"-af loudnorm=I=-14:LRA=11:TP=-1.0 "
        f'-c:v copy -c:a aac -b:a 192k "{norm_out}"',
    )

    # Step 5: 자막 + 타이틀
    print(f"  [5/5] 자막/타이틀 합성...")
    filter_str = build_subtitle_filter(timings)
    filter_script = os.path.join(temp_dir, "subtitle_filter.txt")
    with open(filter_script, "w", encoding="utf-8") as f:
        f.write(filter_str)

    output_path = os.path.join(output_dir, "shorts_final.mp4")
    await asyncio.to_thread(
        run,
        f'ffmpeg -y -i "{norm_out}" '
        f'-filter_script:v "{filter_script}" '
        f'-c:v libx264 -preset fast -crf 18 -c:a copy "{output_path}"',
    )

    return output_path


async def main():
    print("=" * 55)
    print("  6개 모델 최종 영상 조립")
    print("=" * 55)

    # TTS 타이밍 로드 + 나레이션 빌드 (1회)
    timings_raw = json.loads(
        open(os.path.join(TTS_DIR, "timings_raw.json"), encoding="utf-8").read()
    )
    sentence_durations = [t["duration"] for t in timings_raw]
    clip_durations, clip_starts, total_dur = calculate_dynamic_clips_image(
        sentence_durations
    )

    narration_path, timings = await asyncio.to_thread(
        build_aligned_narration, TTS_DIR, SENTENCES, clip_starts, total_dur
    )

    print(f"[OK] TTS 준비 완료")
    print(f"  클립 길이: {clip_durations}")
    print(f"  총 길이: {total_dur}초\n")

    # 모델별 순차 조립
    results = []
    for idx, model_key in enumerate(MODELS_TO_TEST, 1):
        print(f"{'─' * 55}")
        print(f"  [{idx}/6] {model_key}")
        print(f"{'─' * 55}")
        start = time.time()
        try:
            out = await assemble_one_model(
                model_key, clip_durations, narration_path, timings
            )
            elapsed = time.time() - start
            print(f"  >> 완료! ({elapsed:.0f}초)\n")
            results.append((model_key, True, elapsed))
        except Exception as e:
            elapsed = time.time() - start
            print(f"  >> 실패 ({elapsed:.0f}초): {e}\n")
            results.append((model_key, False, elapsed))

    # 최종 요약
    print(f"\n{'=' * 55}")
    print(f"  최종 결과")
    print(f"{'=' * 55}")
    for model, ok, sec in results:
        status = "OK" if ok else "FAIL"
        path = f"storage/model_comparison/{model}/output/shorts_final.mp4"
        print(f"  {model:10} | {status:4} | {sec:.0f}초 | {path}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    asyncio.run(main())
