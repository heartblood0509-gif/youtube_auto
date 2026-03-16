"""이미지 기반 YouTube Shorts 영상 조립 파이프라인"""

import subprocess
import json
import os

from core.audio_utils import (
    run,
    speed_up_sentences,
    build_aligned_narration,
)
from core.subtitle_utils import split_subtitle_natural, split_title
from core.tts_engines import generate_tts_edge, generate_tts_qwen, generate_tts_typecast
from core.image_pipeline import apply_ken_burns
from config import settings


def get_duration(filepath):
    """ffprobe로 미디어 길이 조회"""
    probe = subprocess.run(
        f'ffprobe -v quiet -print_format json -show_format "{filepath}"',
        shell=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def calculate_dynamic_clips_image(sentence_durations, buffer=0.5):
    """이미지 기반 클립 길이 계산 (소스 영상 제약 없음)"""
    clip_durations = []
    clip_starts = []
    t = 0.0
    for dur in sentence_durations:
        actual_dur = dur + buffer
        clip_durations.append(round(actual_dur, 2))
        clip_starts.append(round(t, 2))
        t += actual_dur
    return clip_durations, clip_starts, round(t, 2)


def assemble_shorts(job_id: str, config: dict, progress_callback=None):
    """
    메인 파이프라인: 이미지 + TTS → 최종 9:16 쇼츠 영상.

    config 키:
        job_dir, images, lines, title,
        tts_engine, tts_speed, bgm_path, bgm_volume,
        font_title, font_sub
    """
    job_dir = config["job_dir"]
    temp_dir = os.path.join(job_dir, "temp")
    tts_dir = os.path.join(job_dir, "tts")
    output_dir = os.path.join(job_dir, "output")

    sentences = [line["text"] for line in config["lines"]]
    images = config["images"]
    motions = [line["motion"] for line in config["lines"]]

    # ── Step 1: TTS 생성 ──
    _update(progress_callback, job_id, "generating_tts", 0.4, "TTS 나레이션 생성 중...")

    engine = config.get("tts_engine", "edge")
    tts_speed = config.get("tts_speed", 1.1)

    if engine == "edge":
        narration_path, timings = generate_tts_edge(tts_dir, sentences)
        clip_durations = [t["duration"] + 0.5 for t in timings]
        clip_starts = []
        t_acc = 0.0
        for d in clip_durations:
            clip_starts.append(round(t_acc, 2))
            t_acc += d
        total_dur = round(t_acc, 2)

    elif engine == "typecast":
        generate_tts_typecast(tts_dir, sentences)
        sentence_durations = speed_up_sentences(tts_dir, sentences, tts_speed)
        clip_durations, clip_starts, total_dur = calculate_dynamic_clips_image(
            sentence_durations
        )
        narration_path, timings = build_aligned_narration(
            tts_dir, sentences, clip_starts, total_dur
        )

    elif engine == "qwen":
        generate_tts_qwen(tts_dir, sentences)
        sentence_durations = speed_up_sentences(tts_dir, sentences, tts_speed)
        clip_durations, clip_starts, total_dur = calculate_dynamic_clips_image(
            sentence_durations
        )
        narration_path, timings = build_aligned_narration(
            tts_dir, sentences, clip_starts, total_dur
        )
    else:
        raise ValueError(f"알 수 없는 TTS 엔진: {engine}")

    # ── Step 2: Ken Burns 모션 적용 ──
    _update(
        progress_callback, job_id, "assembling_video", 0.55, "Ken Burns 모션 적용 중..."
    )

    clip_files = []
    for i, (img_path, motion, dur) in enumerate(zip(images, motions, clip_durations)):
        clip_path = os.path.join(temp_dir, f"clip_{i:02d}.mp4")
        apply_ken_burns(
            image_path=img_path,
            output_path=clip_path,
            motion_type=motion,
            duration=dur,
            width=settings.TARGET_WIDTH,
            height=settings.TARGET_HEIGHT,
            fps=settings.FPS,
        )
        clip_files.append(clip_path)
        _update(
            progress_callback,
            job_id,
            "assembling_video",
            0.55 + (i + 1) / len(images) * 0.15,
            f"Ken Burns 적용 ({i + 1}/{len(images)})",
        )

    # ── Step 3: 클립 연결 ──
    _update(progress_callback, job_id, "assembling_video", 0.72, "클립 연결 중...")

    concat_list = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list, "w") as f:
        for clip in clip_files:
            f.write(f"file '{clip}'\n")

    concat_out = os.path.join(temp_dir, "concat_raw.mp4")
    run(
        f'ffmpeg -y -f concat -safe 0 -i "{concat_list}" '
        f'-c:v libx264 -preset fast -crf 18 "{concat_out}"'
    )

    # ── Step 4: 오디오 믹싱 ──
    _update(progress_callback, job_id, "assembling_video", 0.80, "오디오 믹싱 중...")

    audio_out = os.path.join(temp_dir, "mixed_audio.mp4")
    vid_duration = get_duration(concat_out)

    bgm_path = config.get("bgm_path")
    bgm_vol = config.get("bgm_volume", 0.12)
    has_bgm = bgm_path and os.path.exists(bgm_path)

    if has_bgm:
        run(
            f'ffmpeg -y -i "{concat_out}" -i "{narration_path}" -i "{bgm_path}" '
            f'-filter_complex "'
            f"[1:a]volume=1.0[narr];"
            f"[2:a]atrim=0:{vid_duration},volume={bgm_vol}[bgm];"
            f'[narr][bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]'
            f'" '
            f'-map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k '
            f'-shortest "{audio_out}"'
        )
    else:
        run(
            f'ffmpeg -y -i "{concat_out}" -i "{narration_path}" '
            f"-map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k "
            f'-shortest "{audio_out}"'
        )

    # ── Step 5: 자막 + 타이틀 오버레이 ──
    _update(progress_callback, job_id, "assembling_video", 0.90, "자막/타이틀 합성 중...")

    subtitles = split_subtitle_natural(timings)
    font_title = config.get("font_title", settings.FONT_TITLE)
    font_sub = config.get("font_sub", settings.FONT_SUB)
    title_text = config.get("title", "")
    title_color = "#00CED1"
    sq = settings.TARGET_WIDTH  # 1080
    h = settings.TARGET_HEIGHT  # 1920
    sq_y = (h - sq) // 2  # 420

    sub_y = sq_y + sq - 130
    sub_filters = []
    for start, end, text in subtitles:
        escaped = text.replace("'", "'\\''").replace(",", "\\,").replace(":", "\\:")
        sub_filters.append(
            f"drawtext=fontfile='{font_sub}':text='{escaped}':"
            f"fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={sub_y}:"
            f"enable='between(t,{start},{end})'"
        )

    title_filters = []
    if title_text and font_title:
        title_lines = split_title(title_text, max_chars=8)
        title_fontsize = 73
        title_line_gap = 85
        for j, line in enumerate(title_lines):
            escaped = line.replace("'", "'\\''").replace(",", "\\,").replace(":", "\\:")
            if len(title_lines) == 1:
                ty = sq_y - title_fontsize - 30
            else:
                base_y = sq_y - (len(title_lines) * title_line_gap) - 10
                ty = base_y + (j * title_line_gap)
            title_filters.append(
                f"drawtext=fontfile='{font_title}':text='{escaped}':"
                f"fontsize={title_fontsize}:fontcolor={title_color}:borderw=3:bordercolor=black:"
                f"x=(w-text_w)/2:y={ty}:"
                f"enable='between(t,0,15)':"
                f"alpha='if(lt(t,0.3),t/0.3,1)'"
            )

    all_filters = title_filters + sub_filters
    output_path = os.path.join(output_dir, "shorts_final.mp4")

    if all_filters:
        filter_str = ",".join(all_filters)
        run(
            f'ffmpeg -y -i "{audio_out}" '
            f'-vf "{filter_str}" '
            f'-c:v libx264 -preset fast -crf 18 -c:a copy "{output_path}"'
        )
    else:
        run(f'ffmpeg -y -i "{audio_out}" -c copy "{output_path}"')

    # ── 완료 ──
    _update(progress_callback, job_id, "completed", 1.0, "완료!")
    return output_path


def _update(callback, job_id, status, progress, step):
    if callback:
        callback(job_id=job_id, status=status, progress=progress, step=step)
