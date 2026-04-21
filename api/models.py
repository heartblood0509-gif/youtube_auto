"""Pydantic 요청/응답 스키마"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
import datetime


class StylePreset(str, Enum):
    REALISTIC = "realistic"
    ANIME = "anime"
    ILLUSTRATION = "illustration"


class TTSEngine(str, Enum):
    TYPECAST = "typecast"


class MotionType(str, Enum):
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    PAN_UP = "pan_up"
    PAN_DOWN = "pan_down"


class VideoMode(str, Enum):
    KENBURNS = "kenburns"
    HAILUO = "hailuo"
    HAILUO23 = "hailuo23"
    WAN = "wan"
    KLING = "kling"
    VEO = "veo"
    VEO_LITE = "veo_lite"


class JobStatus(str, Enum):
    PENDING = "pending"
    GENERATING_IMAGES = "generating_images"
    PREVIEW_READY = "preview_ready"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    GENERATING_CLIPS = "generating_clips"
    CLIPS_READY = "clips_ready"
    GENERATING_TTS = "generating_tts"
    ASSEMBLING_VIDEO = "assembling_video"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Step 2: 제목 생성 ──

class TitleRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=200)
    category: str = "general"
    pain_point: Optional[str] = None
    ingredient: Optional[str] = None
    content_type: Optional[str] = None  # "info" | "promo" | "promo_comment" (cosmetics 전용)
    keyword: Optional[str] = None  # "info" 전용 — 주제를 좁히는 서브키워드


class TitleOption(BaseModel):
    title: str
    hook: str


class TitleResponse(BaseModel):
    titles: list[TitleOption]


# ── Step 3: 나레이션 생성 ──

class NarrationRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=200)
    selected_title: str = Field(..., min_length=1, max_length=30)
    num_lines: int = Field(default=6, ge=5, le=8)
    category: str = "general"
    pain_point: Optional[str] = None
    ingredient: Optional[str] = None
    content_type: Optional[str] = None  # "info" | "promo" | "promo_comment" (cosmetics 전용)
    keyword: Optional[str] = None  # "info" 전용 — 주제를 좁히는 서브키워드


class NarrationLine(BaseModel):
    text: str
    role: str


class NarrationResponse(BaseModel):
    lines: list[NarrationLine]


# ── Step 4: 이미지 프롬프트 생성 ──

class ImagePromptRequest(BaseModel):
    narration_lines: list[str]
    style: StylePreset = StylePreset.REALISTIC
    category: str = "general"
    topic: str = ""
    content_type: Optional[str] = None  # "info" | "promo" | "promo_comment" (cosmetics 전용)


class ScriptLine(BaseModel):
    text: str
    image_prompt: str
    motion: MotionType


class ImagePromptResponse(BaseModel):
    lines: list[ScriptLine]


class JobCreateRequest(BaseModel):
    topic: str
    style: StylePreset
    video_mode: VideoMode = VideoMode.KENBURNS
    tts_engine: TTSEngine = TTSEngine.TYPECAST
    tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    voice_id: Optional[str] = None
    emotion: Optional[str] = None
    title: str
    title_line1: Optional[str] = None
    title_line2: Optional[str] = None
    lines: list[ScriptLine]
    bgm_volume: float = Field(default=0.12, ge=0.0, le=0.5)
    bgm_filename: Optional[str] = None
    bgm_start_sec: float = Field(default=0.0, ge=0.0)
    product_image_id: Optional[str] = None
    # 음성 단계에서 사전 생성된 TTS 세션 ID (있으면 영상 조립 시 재사용)
    tts_session_id: Optional[str] = None


class TtsPreviewBuildRequest(BaseModel):
    """음성 설정 단계에서 TTS를 미리 생성해 세션에 저장하는 요청."""
    sentences: list[str]
    voice_id: str
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    emotion: Optional[str] = None
    # 아래는 커밋 5의 6초 초과 분리에서 사용 (현재는 무시)
    content_type: Optional[str] = None
    topic: Optional[str] = None
    style: Optional[str] = None


# ── 제품 이미지 ──

class UserProductResponse(BaseModel):
    id: str
    name: str
    filename: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True


# ── 응답 ──

class ScriptResponse(BaseModel):
    title: str
    lines: list[ScriptLine]


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: float
    current_step: str
    created_at: str
    completed_at: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    files_expired: bool = False
    days_remaining: Optional[int] = None
    topic: Optional[str] = None
    owner_nickname: Optional[str] = None
    owner_email: Optional[str] = None


class PreviewResponse(BaseModel):
    title: str
    lines: list[ScriptLine]
    image_urls: list[str]


class ClipPreviewResponse(BaseModel):
    title: str
    lines: list[ScriptLine]
    clip_urls: list[str]
    image_urls: list[str]


# ── 인증 ──

class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    nickname: Optional[str]
    role: str
    provider: str
    approved: bool = False
    has_gemini_key: bool = False
    has_typecast_key: bool = False
    has_fal_key: bool = False


# ── API 키 설정 ──

class ApiKeysUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    typecast_api_key: Optional[str] = None
    fal_key: Optional[str] = None


class ApiKeysResponse(BaseModel):
    gemini: Optional[str] = None
    typecast: Optional[str] = None
    fal: Optional[str] = None
