"""작업 상태 관리"""

from db.database import SessionLocal
from db.models import Job
import datetime


def update_job_progress(job_id: str, status: str, progress: float, step: str):
    """작업 상태를 DB에 업데이트"""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = status
            job.progress = progress
            job.current_step = step
            if status == "completed":
                job.completed_at = datetime.datetime.utcnow()
            db.commit()
    finally:
        db.close()


def mark_job_failed(job_id: str, error_message: str):
    """작업 실패 처리"""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = error_message
            job.current_step = "에러 발생"
            db.commit()
    finally:
        db.close()


def set_video_path(job_id: str, video_path: str):
    """완성 영상 경로 저장"""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.video_path = video_path
            db.commit()
    finally:
        db.close()
