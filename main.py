"""YouTube Shorts 자동 제작 웹앱 - FastAPI 진입점"""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from contextlib import asynccontextmanager
from db.database import init_db
from api.routes import generate, jobs, preview, assets, tts_preview, auth, admin
from api.routes.assets import bgm_router
from config import settings
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JWT_SECRET 필수 체크
    if not settings.JWT_SECRET or settings.JWT_SECRET == "your-secret-key-change-this":
        raise RuntimeError(
            "\n\n  [오류] JWT_SECRET이 설정되지 않았습니다!\n"
            "  .env 파일에서 JWT_SECRET 값을 안전한 랜덤 문자열로 변경하세요.\n"
            "  생성 방법: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        )

    # 시작 시 초기화
    init_db()
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)
    os.makedirs(settings.BGM_DIR, exist_ok=True)
    print(f"\n  AI 쇼츠 자동 제작 웹앱 시작!")
    print(f"  http://localhost:8000\n")
    yield


app = FastAPI(title="AI 쇼츠 자동 제작", lifespan=lifespan)

STATIC_DIR = os.path.join(settings.BASE_DIR, "static")

# 정적 파일 서빙
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# API 라우트 등록
app.include_router(auth.router)
app.include_router(generate.router)
app.include_router(jobs.router)
app.include_router(preview.router)
app.include_router(assets.router)
app.include_router(bgm_router)
app.include_router(tts_preview.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    return RedirectResponse("/static/login.html")


if __name__ == "__main__":
    import uvicorn

    is_dev = not settings.DATABASE_URL  # PostgreSQL 설정 시 프로덕션 모드
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=is_dev)
