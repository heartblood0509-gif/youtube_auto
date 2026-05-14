"""이미지/영상/BGM 파일 서빙 API"""

import json
import os
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException, Path, Depends, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session
from config import settings
from core.video_assembler import get_duration
from core.r2_storage import (
    is_r2_enabled, r2_file_exists, stream_from_r2,
    generate_presigned_url, upload_file as r2_upload, require_r2_for_generation,
)
from core.time_utils import utc_now_naive
from api.deps import get_approved_user, get_user_job
from db.database import get_db
from db.models import Job, User, UserBgm

router = APIRouter(prefix="/api/jobs", tags=["assets"])
bgm_router = APIRouter(prefix="/api/assets", tags=["bgm"])


def _mark_expired_if_old(db: Session, job_id: str):
    """파일이 없고 30일 지난 작업이면 만료 표시"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job and job.completed_at and not job.files_expired_at:
        age = utc_now_naive() - job.completed_at
        if age.days >= 30:
            job.files_expired_at = utc_now_naive()
            job.video_path = None
            db.commit()


# ── 이미지/영상 서빙 ──


@router.get("/{job_id}/images/{idx}")
async def get_image(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    idx: int = Path(..., ge=0, le=100),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """생성된 이미지 파일 서빙"""
    get_user_job(db, job_id, _user)
    path = os.path.join(settings.STORAGE_DIR, job_id, "images", f"img_{idx:02d}.png")

    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")

    r2_key = f"jobs/{job_id}/images/img_{idx:02d}.png"
    if is_r2_enabled() and r2_file_exists(r2_key):
        return StreamingResponse(stream_from_r2(r2_key), media_type="image/png")

    _mark_expired_if_old(db, job_id)
    raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다")


@router.get("/{job_id}/video")
async def get_video(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """최종 영상 파일 서빙"""
    get_user_job(db, job_id, _user)
    path = os.path.join(settings.STORAGE_DIR, job_id, "output", "shorts_final.mp4")

    if os.path.exists(path):
        return FileResponse(path, media_type="video/mp4", filename=f"shorts_{job_id}.mp4")

    r2_key = f"jobs/{job_id}/output/shorts_final.mp4"
    if is_r2_enabled() and r2_file_exists(r2_key):
        url = generate_presigned_url(r2_key)
        return RedirectResponse(url)

    _mark_expired_if_old(db, job_id)
    raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다")


# ── BGM 관련 엔드포인트 ──


def _probe_audio(filepath: str) -> float:
    """ffprobe로 오디오 파일 검증 + duration 반환. 실패 시 예외."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, encoding="utf-8",
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        raise ValueError("유효한 오디오 파일이 아닙니다")


@bgm_router.get("/bgm", response_model=list)
async def list_bgm(db: Session = Depends(get_db), _user: User = Depends(get_approved_user)):
    """BGM 파일 목록 반환 (R2 활성: DB 조회, 비활성: 로컬 스캔)"""
    if is_r2_enabled():
        # DB에서 사용자의 BGM 목록 조회
        bgms = db.query(UserBgm).filter(UserBgm.user_id == _user.id).order_by(UserBgm.created_at.desc()).all()
        return [
            {
                "id": b.id,
                "filename": b.filename,
                "duration": round(b.duration, 1),
                "url": f"/api/assets/bgm/{b.id}",
            }
            for b in bgms
        ]
    else:
        # 로컬 개발: bgm/ 폴더 스캔
        bgm_dir = settings.BGM_DIR
        if not os.path.isdir(bgm_dir):
            return []
        files = []
        for fname in sorted(os.listdir(bgm_dir)):
            if fname.lower().endswith((".mp3", ".wav", ".ogg")):
                fpath = os.path.join(bgm_dir, fname)
                try:
                    duration = get_duration(fpath)
                except Exception:
                    duration = 0
                files.append({
                    "filename": fname,
                    "duration": round(duration, 1),
                    "url": f"/api/assets/bgm/{fname}",
                })
        return files


@bgm_router.post("/bgm")
async def upload_bgm(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """BGM 업로드 → ffprobe 검증 → R2 저장 → DB 기록"""
    try:
        require_r2_for_generation()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    # 확장자 체크
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".mp3", ".wav", ".ogg"):
        raise HTTPException(status_code=400, detail="MP3, WAV, OGG 파일만 업로드 가능합니다")

    # 개수 제한 (최대 3개)
    if is_r2_enabled():
        count = db.query(UserBgm).filter(UserBgm.user_id == _user.id).count()
    else:
        bgm_dir = settings.BGM_DIR
        count = len([f for f in os.listdir(bgm_dir) if f.lower().endswith(('.mp3', '.wav', '.ogg'))]) if os.path.exists(bgm_dir) else 0
    if count >= 3:
        raise HTTPException(status_code=400, detail="BGM은 최대 3개까지 업로드 가능합니다")

    # 크기 체크
    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기는 20MB 이하만 가능합니다")

    # 중복 체크
    existing = db.query(UserBgm).filter(
        UserBgm.user_id == _user.id,
        UserBgm.filename == file.filename,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="같은 이름의 BGM이 이미 있습니다")

    # 임시 파일에 저장 + ffprobe 검증
    tmp_dir = os.path.join(settings.STORAGE_DIR, "tmp_bgm")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, file.filename)

    try:
        with open(tmp_path, "wb") as f:
            f.write(contents)

        # ffprobe로 실제 오디오 검증 + duration 추출
        try:
            duration = _probe_audio(tmp_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="유효한 오디오 파일이 아닙니다")

        # R2 업로드
        r2_key = f"bgm/{_user.id}/{file.filename}"
        if is_r2_enabled():
            ok = await r2_upload(tmp_path, r2_key)
            if not ok:
                raise HTTPException(status_code=500, detail="파일 업로드에 실패했습니다")

        # DB 저장
        bgm = UserBgm(
            user_id=_user.id,
            filename=file.filename,
            duration=duration,
            r2_key=r2_key,
        )
        db.add(bgm)
        db.commit()
        db.refresh(bgm)

        return {"id": bgm.id, "filename": bgm.filename, "duration": round(duration, 1)}

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@bgm_router.delete("/bgm/{bgm_id}")
async def delete_bgm(
    bgm_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """BGM 삭제 (본인 것만)"""
    bgm = db.query(UserBgm).filter(UserBgm.id == bgm_id).first()
    if not bgm:
        raise HTTPException(status_code=404, detail="BGM을 찾을 수 없습니다")
    if bgm.user_id != _user.id and _user.role != "admin":
        raise HTTPException(status_code=404, detail="BGM을 찾을 수 없습니다")

    # R2 삭제
    if is_r2_enabled():
        from core.r2_storage import get_r2_client
        try:
            get_r2_client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=bgm.r2_key)
        except Exception:
            pass

    db.delete(bgm)
    db.commit()
    return {"message": "BGM이 삭제되었습니다"}


@bgm_router.get("/bgm/{bgm_id_or_filename:path}")
async def get_bgm_file(
    bgm_id_or_filename: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """BGM 파일 서빙 (R2 활성: 스트리밍, 비활성: 로컬)"""
    if is_r2_enabled():
        # DB에서 ID로 조회
        bgm = db.query(UserBgm).filter(UserBgm.id == bgm_id_or_filename).first()
        if not bgm:
            # filename으로 시도
            bgm = db.query(UserBgm).filter(
                UserBgm.user_id == _user.id,
                UserBgm.filename == bgm_id_or_filename,
            ).first()
        if not bgm:
            raise HTTPException(status_code=404, detail="BGM 파일을 찾을 수 없습니다")
        if bgm.user_id != _user.id and _user.role != "admin":
            raise HTTPException(status_code=404, detail="BGM 파일을 찾을 수 없습니다")

        if r2_file_exists(bgm.r2_key):
            # presigned URL로 리다이렉트 (Range Request 지원)
            url = generate_presigned_url(bgm.r2_key)
            if url:
                return RedirectResponse(url)
            return StreamingResponse(stream_from_r2(bgm.r2_key), media_type="audio/mpeg")
        raise HTTPException(status_code=404, detail="BGM 파일을 찾을 수 없습니다")
    else:
        # 로컬 개발 모드
        fpath = os.path.join(settings.BGM_DIR, bgm_id_or_filename)
        abs_path = os.path.abspath(fpath)
        abs_bgm_dir = os.path.abspath(settings.BGM_DIR)
        if not abs_path.startswith(abs_bgm_dir):
            raise HTTPException(status_code=403, detail="접근 불가")
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="BGM 파일을 찾을 수 없습니다")
        return FileResponse(abs_path, media_type="audio/mpeg")
