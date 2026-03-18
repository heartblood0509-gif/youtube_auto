"""백그라운드 워커 - 이미지 생성 및 영상 조립"""

import json
import os
import glob

from db.database import SessionLocal
from db.models import Job
from jobs_queue.job_manager import update_job_progress, mark_job_failed, set_video_path
from config import settings


async def generate_images_for_job(job_id: str):
    """이미지 생성 백그라운드 태스크"""
    from core.gemini_client import generate_all_images

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        update_job_progress(job_id, "generating_images", 0.0, "이미지 생성 준비 중...")

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)

        try:
            await generate_all_images(
                job_id=job_id,
                lines=lines,
                style=job.style,
                storage_dir=job_dir,
                progress_callback=update_job_progress,
            )

            update_job_progress(job_id, "preview_ready", 0.4, "이미지 생성 완료 - 미리보기 확인")

        except Exception as e:
            mark_job_failed(job_id, f"이미지 생성 실패: {str(e)}")
    finally:
        db.close()


async def render_video_for_job(job_id: str):
    """영상 조립 백그라운드 태스크"""
    from core.video_assembler import assemble_shorts

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        update_job_progress(job_id, "generating_tts", 0.4, "TTS 나레이션 생성 중...")

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)

        # 이미지 파일 목록
        images = sorted(glob.glob(os.path.join(job_dir, "images", "img_*.png")))

        # BGM 파일 결정: 사용자 선택 > 첫 번째 파일 폴백
        bgm_path = None
        if job.bgm_filename:
            selected_path = os.path.join(settings.BGM_DIR, job.bgm_filename)
            if os.path.exists(selected_path):
                bgm_path = selected_path
        if not bgm_path:
            bgm_files = glob.glob(os.path.join(settings.BGM_DIR, "*.mp3"))
            if bgm_files:
                bgm_path = bgm_files[0]

        config = {
            "job_dir": job_dir,
            "images": images,
            "lines": lines,
            "title": job.title,
            "tts_engine": job.tts_engine,
            "tts_speed": job.tts_speed,
            "voice_id": job.voice_id,
            "emotion": job.emotion,
            "bgm_path": bgm_path,
            "bgm_volume": job.bgm_volume,
            "bgm_start_sec": job.bgm_start_sec or 0.0,
            "font_title": settings.FONT_TITLE,
            "font_sub": settings.FONT_SUB,
        }

        try:
            video_path = await assemble_shorts(
                job_id=job_id,
                config=config,
                progress_callback=update_job_progress,
            )
            set_video_path(job_id, video_path)

        except Exception as e:
            mark_job_failed(job_id, f"영상 조립 실패: {str(e)}")
    finally:
        db.close()


async def regenerate_image_for_job(job_id: str, line_index: int, korean_request: str = None):
    """단일 이미지 재생성 — 한글 요청어를 Nano Banana 2용 프롬프트로 변환"""
    from core.gemini_client import generate_image, korean_to_nb2_prompt

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        lines = json.loads(job.script_json)
        line = lines[line_index]
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        output_path = os.path.join(job_dir, "images", f"img_{line_index:02d}.png")

        try:
            # 한글 요청어 → Nano Banana 2용 영어 프롬프트 변환
            prompt = await korean_to_nb2_prompt(
                korean_request=korean_request or line["text"],
                narration_text=line["text"],
            )

            await generate_image(
                prompt=prompt,
                style=job.style,
                output_path=output_path,
            )
        except Exception as e:
            mark_job_failed(job_id, f"이미지 재생성 실패: {str(e)}")
    finally:
        db.close()
