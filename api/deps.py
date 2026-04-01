"""FastAPI 인증 의존성"""

from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import User, Job
from core.security import decode_token, decrypt_api_key
from config import settings
import jwt


def get_user_job(db: Session, job_id: str, user: User) -> Job:
    """현재 사용자의 Job 조회. 없거나 다른 사용자면 404."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    if job.user_id is not None and job.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    return job


def get_user_job_by_uid(db: Session, job_id: str, user_id: str, role: str = "user") -> Job | None:
    """user_id 문자열 기반 Job 조회 (SSE 등 Depends 미사용 컨텍스트)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return None
    if job.user_id is not None and job.user_id != user_id and role != "admin":
        return None
    return job


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰이 만료되었습니다")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="유효하지 않은 사용자입니다")
    return user


def get_approved_user(user: User = Depends(get_current_user)) -> User:
    """승인된 사용자만 접근 가능. 미승인 시 403."""
    if not user.approved:
        raise HTTPException(
            status_code=403,
            detail="승인 대기 중입니다. 관리자 승인 후 이용 가능합니다."
        )
    return user


def get_current_admin(user: User = Depends(get_approved_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return user


def resolve_user_api_keys(db: Session, user_id: str | None) -> dict:
    """사용자 키 복호화 반환. 미설정 키만 서버 기본 키로 폴백."""
    keys = {"gemini": None, "typecast": None, "fal": None}
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            if user.gemini_api_key_enc:
                keys["gemini"] = decrypt_api_key(user.gemini_api_key_enc)
            if user.typecast_api_key_enc:
                keys["typecast"] = decrypt_api_key(user.typecast_api_key_enc)
            if user.fal_key_enc:
                keys["fal"] = decrypt_api_key(user.fal_key_enc)
    # 사용자 키 없는 경우만 서버 기본 키 폴백
    if not keys["gemini"]:
        keys["gemini"] = settings.GEMINI_API_KEY or None
    if not keys["typecast"]:
        keys["typecast"] = settings.TYPECAST_API_KEY or None
    if not keys["fal"]:
        keys["fal"] = settings.FAL_KEY or None
    return keys
