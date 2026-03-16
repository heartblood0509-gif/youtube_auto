"""Pydantic 요청/응답 스키마"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class StylePreset(str, Enum):
    REALISTIC = "realistic"
    ANIME = "anime"
    THREE_D = "3d"
    ILLUSTRATION = "illustration"
    CINEMATIC = "cinematic"


class TTSEngine(str, Enum):
    EDGE = "edge"
    TYPECAST = "typecast"
    QWEN = "qwen"


class MotionType(str, Enum):
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    PAN_UP = "pan_up"
    PAN_DOWN = "pan_down"


class JobStatus(str, Enum):
    PENDING = "pending"
    GENERATING_IMAGES = "generating_images"
    PREVIEW_READY = "preview_ready"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    GENERATING_TTS = "generating_tts"
    ASSEMBLING_VIDEO = "assembling_video"
    COMPLETED = "completed"
    FAILED = "failed"


# ── 요청 ──

class ScriptRequest(BaseModel):
    topic: str = Field(..., min_length=2, max_length=200)
    style: StylePreset = StylePreset.CINEMATIC
    num_lines: int = Field(default=6, ge=5, le=8)


class ScriptLine(BaseModel):
    text: str
    image_prompt: str
    motion: MotionType


class JobCreateRequest(BaseModel):
    topic: str
    style: StylePreset
    tts_engine: TTSEngine = TTSEngine.EDGE
    tts_speed: float = Field(default=1.1, ge=0.8, le=1.5)
    title: str
    lines: list[ScriptLine]
    bgm_volume: float = Field(default=0.12, ge=0.0, le=0.5)


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


class PreviewResponse(BaseModel):
    title: str
    lines: list[ScriptLine]
    image_urls: list[str]
