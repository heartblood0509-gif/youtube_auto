"""SQLAlchemy ORM 모델"""

from sqlalchemy import Column, String, Float, Text, DateTime, Index, Boolean
from sqlalchemy.orm import declarative_base
import uuid
import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    email = Column(String, unique=True, nullable=False, index=True)
    nickname = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)
    role = Column(String, default="user")
    provider = Column(String, default="email")
    provider_id = Column(String, nullable=True)
    approved = Column(Boolean, default=False)
    reset_token = Column(String, nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)
    gemini_api_key_enc = Column(String, nullable=True)
    typecast_api_key_enc = Column(String, nullable=True)
    fal_key_enc = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class UserBgm(Base):
    __tablename__ = "user_bgms"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String, nullable=False, index=True)
    filename = Column(String, nullable=False)
    duration = Column(Float, default=0.0)
    r2_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class UserProduct(Base):
    __tablename__ = "user_products"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    r2_key = Column(String, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    user_id = Column(String, nullable=True, index=True)
    status = Column(String, default="pending")
    progress = Column(Float, default=0.0)
    current_step = Column(String, default="")

    # 입력 파라미터
    topic = Column(Text, default="")
    style = Column(String, default="realistic")
    video_mode = Column(String, default="kenburns")
    tts_engine = Column(String, default="typecast")
    tts_speed = Column(Float, default=1.1)
    voice_id = Column(String, nullable=True)
    emotion = Column(String, nullable=True)
    title = Column(Text, default="")
    title_line1 = Column(String, nullable=True)
    title_line2 = Column(String, nullable=True)
    script_json = Column(Text, default="[]")
    product_image_id = Column(String, nullable=True)
    bgm_volume = Column(Float, default=0.12)
    bgm_filename = Column(String, nullable=True)
    bgm_start_sec = Column(Float, default=0.0)

    # 음성 단계에서 사전 생성한 TTS 세션 ID (있으면 영상 조립 시 재사용)
    tts_session_id = Column(String, nullable=True)

    # 출력
    video_path = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    # R2 동기화
    r2_synced = Column(String, default="none")
    files_expired_at = Column(DateTime, nullable=True)

    # 시간
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
