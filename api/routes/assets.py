"""이미지/영상 파일 서빙 API"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from config import settings
import os

router = APIRouter(prefix="/api/jobs", tags=["assets"])


@router.get("/{job_id}/images/{idx}")
async def get_image(job_id: str, idx: int):
    """생성된 이미지 파일 서빙"""
    path = os.path.join(settings.STORAGE_DIR, job_id, "images", f"img_{idx:02d}.png")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다")
    return FileResponse(path, media_type="image/png")


@router.get("/{job_id}/video")
async def get_video(job_id: str):
    """최종 영상 파일 서빙"""
    path = os.path.join(settings.STORAGE_DIR, job_id, "output", "shorts_final.mp4")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"shorts_{job_id}.mp4",
    )
