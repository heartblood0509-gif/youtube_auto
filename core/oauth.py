"""Google / Kakao OAuth 헬퍼"""

import httpx
from urllib.parse import urlencode
from config import settings


# ── Google OAuth ──

def get_google_auth_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_google_code(code: str) -> dict:
    """인가 코드 → 사용자 정보 (email, name, sub)"""
    async with httpx.AsyncClient() as client:
        # 코드를 토큰으로 교환
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        # 사용자 정보 조회
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        return userinfo_resp.json()


# ── Kakao OAuth ──

def get_kakao_auth_url(state: str) -> str:
    params = {
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": settings.KAKAO_REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    return f"https://kauth.kakao.com/oauth/authorize?{urlencode(params)}"


async def exchange_kakao_code(code: str) -> dict:
    """인가 코드 → 사용자 정보 (email, nickname, id)"""
    async with httpx.AsyncClient() as client:
        # 코드를 토큰으로 교환
        token_resp = await client.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.KAKAO_CLIENT_ID,
                "client_secret": settings.KAKAO_CLIENT_SECRET,
                "redirect_uri": settings.KAKAO_REDIRECT_URI,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        # 사용자 정보 조회
        user_resp = await client.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        user_resp.raise_for_status()
        data = user_resp.json()

        kakao_account = data.get("kakao_account", {})
        properties = data.get("properties", {})

        return {
            "id": data.get("id"),
            "email": kakao_account.get("email"),
            "nickname": properties.get("nickname"),
        }
