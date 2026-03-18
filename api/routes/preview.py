"""미리보기 API"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Path
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from api.models import PreviewResponse, ScriptLine
from db.database import get_db
from db.models import Job
from config import settings
import json
import os

router = APIRouter(prefix="/api/jobs", tags=["preview"])


@router.get("/{job_id}/preview", response_model=PreviewResponse)
async def get_preview(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
):
    """생성된 이미지 + 대본 미리보기"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    if job.status not in ("preview_ready", "awaiting_confirmation"):
        raise HTTPException(status_code=400, detail=f"미리보기 불가 (상태: {job.status})")

    lines = [ScriptLine(**l) for l in json.loads(job.script_json)]
    image_urls = [f"/api/jobs/{job_id}/images/{i}" for i in range(len(lines))]

    return PreviewResponse(title=job.title, lines=lines, image_urls=image_urls)


@router.post("/{job_id}/confirm")
async def confirm_and_render(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """미리보기 확인 → TTS + 영상 조립 시작"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    if job.status not in ("preview_ready", "awaiting_confirmation"):
        raise HTTPException(status_code=400, detail=f"확정 불가 (상태: {job.status})")

    job.status = "awaiting_confirmation"
    job.current_step = "영상 제작 준비 중..."
    db.commit()

    background_tasks.add_task(_render_video_task, job_id)
    return {"message": "영상 제작을 시작합니다", "job_id": job_id}


class RegenerateRequest(BaseModel):
    korean_request: Optional[str] = None


@router.post("/{job_id}/regenerate-image/{line_index}")
async def regenerate_image(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    line_index: int = 0,
    body: RegenerateRequest = RegenerateRequest(),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """특정 이미지 재생성 (한글 요청어 → Imagen 프롬프트 변환)"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

    lines = json.loads(job.script_json)
    if line_index < 0 or line_index >= len(lines):
        raise HTTPException(status_code=400, detail="잘못된 이미지 인덱스")

    background_tasks.add_task(_regenerate_single_image, job_id, line_index, body.korean_request)
    return {"message": f"이미지 {line_index + 1} 재생성 시작"}


async def _render_video_task(job_id: str):
    """백그라운드: TTS + 영상 조립"""
    from jobs_queue.worker import render_video_for_job

    await render_video_for_job(job_id)


async def _regenerate_single_image(job_id: str, line_index: int, korean_request: str = None):
    """백그라운드: 단일 이미지 재생성"""
    from jobs_queue.worker import regenerate_image_for_job

    await regenerate_image_for_job(job_id, line_index, korean_request)
