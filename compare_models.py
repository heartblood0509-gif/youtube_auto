"""6개 i2v 모델 비교 스크립트 - 같은 이미지 6장으로 모델별 클립 생성"""

import asyncio
import io
import os
import shutil
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.fal_video import (
    upload_image_to_fal,
    submit_task,
    poll_task,
    download_video,
    MODELS,
)

# ── 설정 ──
TTS_JOB_ID = "7971c5ccb99d"
IMAGES = [
    "storage/34c72d258ef6/images/img_00.png",  # 1번
    "storage/34c72d258ef6/images/img_01.png",  # 2번
    "storage/b03ef3155b36/images/img_02.png",  # 3번
    "storage/9fd711602643/images/img_03.png",  # 4번
    "storage/34c72d258ef6/images/img_04.png",  # 5번
    "storage/34c72d258ef6/images/img_03.png",  # 6번
]
MODELS_TO_TEST = ["hailuo", "hailuo23", "wan", "kling", "kling26", "veo"]
OUTPUT_DIR = "storage/model_comparison"


async def generate_for_model(model_key: str, image_urls: list[str]) -> dict:
    """단일 모델로 6개 클립 생성 (6개 동시 제출 → 폴링 → 다운로드)"""
    label = MODELS[model_key]["label"]
    clips_dir = os.path.join(OUTPUT_DIR, model_key, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    start = time.time()

    # 1) 태스크 제출 (6개)
    print(f"  [{model_key}] 태스크 제출 중...")
    submissions = []
    for i, url in enumerate(image_urls):
        try:
            info = await submit_task(model_key, url)
            submissions.append((i, info))
            print(f"    clip {i}: 제출 OK")
        except Exception as e:
            print(f"    clip {i}: 제출 실패 - {e}")
            submissions.append((i, None))

    # 2) 폴링 + 다운로드 (6개 동시)
    print(f"  [{model_key}] 영상 생성 대기 중...")

    async def _poll_download(idx, task_info):
        if task_info is None:
            return None
        out = os.path.join(clips_dir, f"clip_raw_{idx:02d}.mp4")
        try:
            video_url = await poll_task(
                task_info["status_url"], task_info["response_url"],
                timeout=600, interval=8,
            )
            await download_video(video_url, out)
            print(f"    clip {idx}: 다운로드 완료")
            return out
        except Exception as e:
            print(f"    clip {idx}: 실패 - {e}")
            return None

    clip_results = await asyncio.gather(
        *[_poll_download(i, info) for i, info in submissions]
    )

    elapsed = time.time() - start
    success = sum(1 for r in clip_results if r is not None)
    print(f"  [{model_key}] {label}: {success}/6 성공 ({elapsed:.0f}초)")
    return {"model": model_key, "label": label, "success": success, "elapsed": elapsed}


async def main():
    print("=" * 55)
    print("  6개 i2v 모델 비교 (같은 이미지 6장)")
    print("=" * 55)

    # 1) 이미지 복사
    img_dir = os.path.join(OUTPUT_DIR, "images")
    os.makedirs(img_dir, exist_ok=True)
    for i, src in enumerate(IMAGES):
        shutil.copy2(src, os.path.join(img_dir, f"img_{i:02d}.png"))
    print(f"[OK] 이미지 6장 복사 완료")

    # 2) TTS 복사 (나중에 영상 조립용)
    tts_src = os.path.join("storage", TTS_JOB_ID, "tts")
    tts_dst = os.path.join(OUTPUT_DIR, "tts")
    if os.path.exists(tts_src) and not os.path.exists(tts_dst):
        shutil.copytree(tts_src, tts_dst)
        print(f"[OK] TTS 파일 복사 완료 ({TTS_JOB_ID})")

    # 3) 이미지 fal CDN 업로드 (1회만)
    print(f"[...] 이미지 fal CDN 업로드 중...")
    image_urls = await asyncio.gather(*[upload_image_to_fal(img) for img in IMAGES])
    print(f"[OK] 이미지 6장 업로드 완료\n")

    # 4) 모델별 순차 처리
    all_results = []
    for idx, model_key in enumerate(MODELS_TO_TEST, 1):
        label = MODELS[model_key]["label"]
        print(f"\n{'─' * 55}")
        print(f"  [{idx}/6] {model_key} - {label}")
        print(f"{'─' * 55}")
        result = await generate_for_model(model_key, image_urls)
        all_results.append(result)

    # 5) 최종 요약
    print(f"\n{'=' * 55}")
    print(f"  최종 결과 요약")
    print(f"{'=' * 55}")
    for r in all_results:
        print(f"  {r['model']:10} | {r['label']:25} | {r['success']}/6 | {r['elapsed']:.0f}초")
    print(f"{'=' * 55}")
    print(f"  클립 저장 위치: {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    asyncio.run(main())
