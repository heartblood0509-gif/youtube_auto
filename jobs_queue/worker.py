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

        job.status = "generating_images"
        job.current_step = "이미지 생성 준비 중..."
        db.commit()

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

            job = db.query(Job).filter(Job.id == job_id).first()
            job.status = "preview_ready"
            job.progress = 0.4
            job.current_step = "이미지 생성 완료 - 미리보기 확인"
            db.commit()

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

        job.status = "generating_tts"
        job.current_step = "TTS 나레이션 생성 중..."
        db.commit()

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)

        # 이미지 파일 목록
        images = sorted(glob.glob(os.path.join(job_dir, "images", "img_*.png")))

        # BGM 자동 탐색
        bgm_path = None
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
            "bgm_path": bgm_path,
            "bgm_volume": job.bgm_volume,
            "font_title": settings.FONT_TITLE,
            "font_sub": settings.FONT_SUB,
        }

        try:
            video_path = assemble_shorts(
                job_id=job_id,
                config=config,
                progress_callback=update_job_progress,
            )
            set_video_path(job_id, video_path)

        except Exception as e:
            mark_job_failed(job_id, f"영상 조립 실패: {str(e)}")
    finally:
        db.close()


async def regenerate_image_for_job(job_id: str, line_index: int):
    """단일 이미지 재생성"""
    from core.gemini_client import generate_image

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
            await generate_image(
                prompt=line["image_prompt"],
                style=job.style,
                output_path=output_path,
            )
        except Exception as e:
            mark_job_failed(job_id, f"이미지 재생성 실패: {str(e)}")
    finally:
        db.close()
