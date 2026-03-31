# AI 쇼츠 자동 제작 - Docker 이미지
#
# 사용법 (로컬):
#   docker build -t ai-shorts .
#   docker run -p 8000:8000 --env-file .env ai-shorts
#
# Railway 배포: railway.toml로 자동 빌드

FROM python:3.11-slim

# 시스템 패키지: FFmpeg + 한국어 폰트 + soundfile 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 비root 사용자 생성
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Python 의존성 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 + BGM 복사
COPY . .

# 디렉토리 생성 + 권한 설정
RUN mkdir -p /app/storage /app/bgm && chown -R appuser:appuser /app

# 비root 사용자로 전환
USER appuser

EXPOSE 8000

CMD ["python", "main.py"]
