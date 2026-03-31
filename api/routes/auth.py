"""인증 API - 회원가입, 로그인, OAuth, 비밀번호 재설정, 아이디 찾기"""

from fastapi import APIRouter, HTTPException, Depends, Response, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import User
from api.models import (
    RegisterRequest, LoginRequest, UserResponse,
    PasswordResetRequest, PasswordResetConfirm,
    FindEmailRequest, FindEmailResponse,
    ApiKeysUpdateRequest, ApiKeysResponse,
)
from api.deps import get_current_user
from core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
    encrypt_api_key, decrypt_api_key,
)
from config import settings
import jwt
import uuid
import datetime
import time

router = APIRouter(prefix="/api/auth", tags=["auth"])

# OAuth CSRF state 저장소 (메모리, {state: expire_timestamp})
_oauth_states: dict[str, float] = {}

def _create_oauth_state() -> str:
    """state 생성 + 저장 (5분 유효)"""
    # 만료된 state 정리
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if v < now]
    for k in expired:
        del _oauth_states[k]
    state = uuid.uuid4().hex
    _oauth_states[state] = now + 300  # 5분
    return state

def _verify_oauth_state(state: str) -> bool:
    """state 검증 + 소비 (1회용)"""
    expire = _oauth_states.pop(state, None)
    if expire is None:
        return False
    return time.time() < expire


def _set_auth_cookies(response: Response, user: User):
    """JWT 쿠키 설정"""
    token_data = {"sub": user.id, "email": user.email, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    is_https = settings.BASE_URL.startswith("https")
    response.set_cookie(
        key="access_token", value=access_token,
        httponly=True, samesite="lax", secure=is_https,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token,
        httponly=True, samesite="lax", secure=is_https,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )
    return access_token


def _user_response(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "role": user.role,
        "provider": user.provider,
        "has_gemini_key": bool(user.gemini_api_key_enc),
        "has_typecast_key": bool(user.typecast_api_key_enc),
        "has_fal_key": bool(user.fal_key_enc),
    }


# ── 공개 설정 ──

@router.get("/settings")
async def get_auth_settings():
    """프론트엔드에서 초대 코드 필요 여부 확인용"""
    return {"invite_code_required": bool(settings.INVITE_CODE)}


# ── 이메일 회원가입/로그인 ──

@router.post("/register")
async def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    # 초대 코드 검증 (설정된 경우)
    if settings.INVITE_CODE and req.invite_code != settings.INVITE_CODE:
        raise HTTPException(status_code=403, detail="초대 코드가 올바르지 않습니다")

    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 이메일입니다")

    user = User(
        id=uuid.uuid4().hex,
        email=req.email,
        nickname=req.nickname,
        hashed_password=hash_password(req.password),
        provider="email",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _set_auth_cookies(response, user)
    return {"message": "회원가입 완료", "user": _user_response(user)}


@router.post("/login")
async def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다")

    if not user.hashed_password:
        raise HTTPException(
            status_code=401,
            detail="이메일 또는 비밀번호가 올바르지 않습니다"
        )

    if not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다")

    _set_auth_cookies(response, user)
    return {"message": "로그인 성공", "user": _user_response(user)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"message": "로그아웃 완료"}


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    return {"user": _user_response(user)}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="리프레시 토큰이 없습니다")

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="리프레시 토큰이 만료되었습니다")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")

    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=401, detail="유효하지 않은 사용자입니다")

    _set_auth_cookies(response, user)
    return {"message": "토큰 갱신 완료", "user": _user_response(user)}


# ── OAuth: Google ──

@router.get("/google/login")
async def google_login():
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth가 설정되지 않았습니다")

    from core.oauth import get_google_auth_url
    state = _create_oauth_state()
    url = get_google_auth_url(state)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    if not _verify_oauth_state(state):
        return RedirectResponse("/static/login.html?error=invalid_state")

    from core.oauth import exchange_google_code

    try:
        user_info = await exchange_google_code(code)
    except Exception as e:
        return RedirectResponse(f"/static/login.html?error=google_failed")

    email = user_info.get("email")
    if not email:
        return RedirectResponse(f"/static/login.html?error=no_email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            id=uuid.uuid4().hex,
            email=email,
            nickname=user_info.get("name", email.split("@")[0]),
            provider="google",
            provider_id=user_info.get("sub"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    response = RedirectResponse("/")
    _set_auth_cookies(response, user)
    return response


# ── OAuth: Kakao ──

@router.get("/kakao/login")
async def kakao_login():
    if not settings.KAKAO_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Kakao OAuth가 설정되지 않았습니다")

    from core.oauth import get_kakao_auth_url
    state = _create_oauth_state()
    url = get_kakao_auth_url(state)
    return RedirectResponse(url)


@router.get("/kakao/callback")
async def kakao_callback(code: str, state: str = "", db: Session = Depends(get_db)):
    if not _verify_oauth_state(state):
        return RedirectResponse("/static/login.html?error=invalid_state")

    from core.oauth import exchange_kakao_code

    try:
        user_info = await exchange_kakao_code(code)
    except Exception as e:
        return RedirectResponse(f"/static/login.html?error=kakao_failed")

    email = user_info.get("email")
    if not email:
        return RedirectResponse(f"/static/login.html?error=no_email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            id=uuid.uuid4().hex,
            email=email,
            nickname=user_info.get("nickname", email.split("@")[0]),
            provider="kakao",
            provider_id=str(user_info.get("id")),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    response = RedirectResponse("/")
    _set_auth_cookies(response, user)
    return response


# ── 비밀번호 재설정 ──

@router.post("/password-reset/request")
async def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    # 보안: 계정 존재 여부와 관계없이 동일한 응답
    if not user:
        return {"message": "등록된 이메일이라면 재설정 링크가 발송됩니다"}

    if not user.hashed_password:
        return {"message": "소셜 로그인 계정은 비밀번호 재설정이 필요하지 않습니다"}

    token = uuid.uuid4().hex
    user.reset_token = token
    user.reset_token_expires = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    db.commit()

    # 이메일 발송
    if settings.SMTP_USER and settings.SMTP_PASSWORD:
        from core.email_utils import send_reset_email
        reset_link = f"{settings.BASE_URL}/static/reset-password.html?token={token}"
        try:
            send_reset_email(user.email, reset_link)
        except Exception:
            raise HTTPException(status_code=500, detail="이메일 발송에 실패했습니다. 관리자에게 문의하세요.")
    else:
        raise HTTPException(status_code=500, detail="이메일 설정이 완료되지 않았습니다. 관리자에게 문의하세요.")

    return {"message": "등록된 이메일이라면 재설정 링크가 발송됩니다"}


@router.post("/password-reset/confirm")
async def confirm_password_reset(req: PasswordResetConfirm, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == req.token).first()
    if not user:
        raise HTTPException(status_code=400, detail="유효하지 않은 재설정 링크입니다")

    if user.reset_token_expires and user.reset_token_expires < datetime.datetime.utcnow():
        raise HTTPException(status_code=400, detail="재설정 링크가 만료되었습니다. 다시 요청해주세요.")

    user.hashed_password = hash_password(req.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()

    return {"message": "비밀번호가 성공적으로 변경되었습니다"}


# ── 아이디(이메일) 찾기 ──

@router.post("/find-email")
async def find_email(req: FindEmailRequest, db: Session = Depends(get_db)):
    # SQL 와일드카드 문자 이스케이프
    safe_nickname = req.nickname.replace("%", "").replace("_", "")
    if len(safe_nickname) < 1:
        return FindEmailResponse(masked_emails=[], message="검색어를 입력해주세요")

    users = db.query(User).filter(User.nickname.ilike(f"%{safe_nickname}%")).all()

    if not users:
        return FindEmailResponse(
            masked_emails=[],
            message="일치하는 계정을 찾을 수 없습니다"
        )

    def mask_email(email: str) -> str:
        local, domain = email.split("@")
        if len(local) <= 2:
            masked = local[0] + "***"
        else:
            masked = local[:2] + "***"
        return f"{masked}@{domain}"

    masked = [mask_email(u.email) for u in users]
    return FindEmailResponse(
        masked_emails=masked,
        message=f"{len(masked)}개의 계정을 찾았습니다"
    )


# ── API 키 관리 ──

def _mask_key(key: str) -> str:
    """API 키 마스킹: 앞4자 + *** + 뒤3자"""
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "***" + key[-3:]


@router.get("/api-keys")
async def get_api_keys(user: User = Depends(get_current_user)):
    """현재 사용자의 API 키 상태 조회 (마스킹)"""
    result = {"gemini": None, "typecast": None, "fal": None}
    if user.gemini_api_key_enc:
        result["gemini"] = _mask_key(decrypt_api_key(user.gemini_api_key_enc))
    if user.typecast_api_key_enc:
        result["typecast"] = _mask_key(decrypt_api_key(user.typecast_api_key_enc))
    if user.fal_key_enc:
        result["fal"] = _mask_key(decrypt_api_key(user.fal_key_enc))
    return result


@router.put("/api-keys")
async def update_api_keys(
    req: ApiKeysUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """API 키 저장 (검증 후 암호화)"""
    import httpx

    # Gemini 키 검증 + 저장
    if req.gemini_api_key is not None:
        if req.gemini_api_key == "":
            user.gemini_api_key_enc = None
        else:
            try:
                from google import genai
                client = genai.Client(api_key=req.gemini_api_key)
                client.models.list(config={"page_size": 1})
            except Exception:
                raise HTTPException(status_code=400, detail="Gemini API 키가 유효하지 않습니다")
            user.gemini_api_key_enc = encrypt_api_key(req.gemini_api_key)

    # Typecast 키 검증 + 저장
    if req.typecast_api_key is not None:
        if req.typecast_api_key == "":
            user.typecast_api_key_enc = None
        else:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://api.typecast.ai/v1/voices",
                        headers={"X-API-KEY": req.typecast_api_key},
                    )
                    if resp.status_code == 401:
                        raise ValueError("Unauthorized")
            except Exception:
                raise HTTPException(status_code=400, detail="Typecast API 키가 유효하지 않습니다")
            user.typecast_api_key_enc = encrypt_api_key(req.typecast_api_key)

    # FAL 키 검증 + 저장
    if req.fal_key is not None:
        if req.fal_key == "":
            user.fal_key_enc = None
        else:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://queue.fal.run/fal-ai/fast-sdxl",
                        headers={"Authorization": f"Key {req.fal_key}"},
                    )
                    if resp.status_code == 401:
                        raise ValueError("Unauthorized")
            except Exception:
                raise HTTPException(status_code=400, detail="FAL API 키가 유효하지 않습니다")
            user.fal_key_enc = encrypt_api_key(req.fal_key)

    db.commit()
    return {"message": "API 키가 저장되었습니다"}
