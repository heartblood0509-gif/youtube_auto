"""TTS 음성 미리듣기 엔드포인트"""

import asyncio
import json
import os
import re
import shutil
import uuid

from fastapi import APIRouter, Query, HTTPException, Depends
from fastapi.responses import FileResponse

import requests as http_requests

from sqlalchemy.orm import Session
from config import settings
from core.tts_engines import generate_tts_typecast
from core.line_splitter import (
    detect_overlong_lines,
    split_long_line_with_gemini,
    split_by_punctuation,
)
from core.audio_splitter import (
    calculate_split_point,
    cut_wav_at,
    get_wav_duration,
)
from api.deps import get_approved_user, resolve_user_api_keys
from api.models import TtsPreviewBuildRequest
from db.database import get_db
from db.models import User

router = APIRouter(prefix="/api/tts", tags=["tts"])

TTS_SESSIONS_DIR = os.path.join(settings.STORAGE_DIR, "tts_sessions")
LINE_DURATION_THRESHOLD = 6.0  # veo 3.1 lite 클립당 6초 고정 제약

PREVIEW_DIR = os.path.join(settings.STORAGE_DIR, "tts_preview")
SAMPLE_TEXT = "안녕하세요, 반갑습니다."

_SAFE_FILENAME = re.compile(r"^[\w\-]+$")


def _cache_path(user_id: str, engine: str, voice_id: str, speed: float, emotion: str) -> str:
    safe_id = voice_id.replace("-", "_")
    return os.path.join(
        PREVIEW_DIR,
        f"{user_id}_{engine}_{safe_id}_s{speed}_{emotion}.mp3"
    )


EMOTION_LABELS = {
    "normal": "보통", "happy": "기쁨", "sad": "슬픔", "angry": "화남",
    "whisper": "속삭임", "toneup": "밝게", "tonedown": "차분하게",
    "tonemid": "중간톤", "regret": "후회", "urgent": "급박한",
    "scream": "외침", "shout": "소리침", "trustful": "신뢰감",
    "soft": "부드럽게", "cold": "차갑게", "sarcasm": "비꼼",
    "inspire": "영감", "cute": "귀엽게", "cheer": "응원", "casual": "캐주얼",
}


@router.get("/emotions")
async def get_voice_emotions(voice_id: str = Query(..., min_length=1), db: Session = Depends(get_db), _user: User = Depends(get_approved_user)):
    """Typecast 성우의 지원 감정 목록 반환"""
    keys = resolve_user_api_keys(db, _user.id)
    tc_key = keys["typecast"]
    if not tc_key:
        raise HTTPException(500, "Typecast API 키가 설정되지 않았습니다. 설정 페이지에서 키를 입력하세요.")

    resp = http_requests.get(
        f"https://api.typecast.ai/v1/voices/{voice_id}",
        headers={"X-API-KEY": tc_key},
    )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "성우 정보 조회 실패")

    # 보통 → 긍정 → 부정 → 특수 순서
    EMOTION_ORDER = [
        "normal",
        "happy", "cheer", "toneup", "inspire", "cute", "casual", "trustful", "soft",
        "sad", "angry", "cold", "sarcasm", "regret", "tonedown", "tonemid",
        "whisper", "urgent", "scream", "shout",
    ]

    raw_emotions = []
    entries = resp.json()
    # ssfm-v30 우선, 없으면 ssfm-v21 사용
    for entry in entries:
        if entry.get("model") == "ssfm-v30":
            raw_emotions = entry.get("emotions", [])
            break
    if not raw_emotions:
        for entry in entries:
            if entry.get("model") == "ssfm-v21":
                raw_emotions = entry.get("emotions", [])
                break

    order_map = {e: i for i, e in enumerate(EMOTION_ORDER)}
    sorted_emotions = sorted(raw_emotions, key=lambda e: order_map.get(e, 99))

    return [
        {"value": e, "label": EMOTION_LABELS.get(e, e)}
        for e in sorted_emotions
    ]


@router.get("/preview")
async def tts_preview(
    engine: str = Query(..., pattern="^typecast$"),
    voice_id: str = Query(..., min_length=1, max_length=100),
    speed: float = Query(default=1.0, ge=0.5, le=2.0),
    emotion: str = Query(default="normal", max_length=20),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """선택한 엔진+음성+속도+감정으로 샘플 오디오 생성/반환"""
    keys = resolve_user_api_keys(db, _user.id)
    if not keys["typecast"]:
        raise HTTPException(400, "Typecast API 키가 설정되지 않았습니다. 설정 페이지에서 사용자 본인의 키를 입력하세요.")

    if not _SAFE_FILENAME.match(voice_id.replace("-", "_").replace(".", "_")):
        raise HTTPException(400, "잘못된 voice_id 형식입니다")

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    cached = _cache_path(_user.id, engine, voice_id, speed, emotion)

    if os.path.exists(cached):
        media_type = "audio/mpeg" if cached.endswith(".mp3") else "audio/wav"
        return FileResponse(cached, media_type=media_type)

    tmp_dir = os.path.join(PREVIEW_DIR, f"tmp_{engine}_{voice_id.replace('-', '_')}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        sentences = [SAMPLE_TEXT]

        emo = emotion if emotion != "normal" else None
        await generate_tts_typecast(
            tmp_dir, sentences,
            voice_id=voice_id, speed=speed, emotion=emo,
            api_key=keys["typecast"],
        )
        wav_path = os.path.join(tmp_dir, "sent_00.wav")
        if os.path.exists(wav_path):
            os.replace(wav_path, cached)
        else:
            raise HTTPException(500, "Typecast 오디오 생성 실패")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"미리듣기 생성 실패: {e}")
    finally:
        import shutil
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not os.path.exists(cached):
        raise HTTPException(500, "오디오 파일 생성 실패")

    media_type = "audio/mpeg" if cached.endswith(".mp3") else "audio/wav"
    return FileResponse(cached, media_type=media_type)


# ──────────────────────────────────────────────────────────────
# /api/tts/preview-build — 음성 설정 단계에서 실제 TTS 생성
# ──────────────────────────────────────────────────────────────
# 목적: "나레이션 음성 만들기" 버튼 클릭 시 호출돼 각 줄의 TTS를 미리 생성.
# 결과 파일은 storage/tts_sessions/{session_id}/ 에 sent_XX.wav 로 저장되며,
# 이후 Job 생성 시 job_dir/tts/ 로 이동돼 영상 조립에서 재사용된다(재생성 스킵).
# 커밋 5에서 promo_comment 한정 6초 초과 자동 분리가 이 엔드포인트에 통합될 예정.

@router.post("/preview-build")
async def preview_build(
    req: TtsPreviewBuildRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    if not req.sentences:
        raise HTTPException(400, "sentences가 비어있습니다")

    keys = resolve_user_api_keys(db, _user.id)
    if not keys["typecast"]:
        raise HTTPException(400, "Typecast API 키가 설정되지 않았습니다. 설정 페이지에서 사용자 본인의 키를 입력하세요.")
    session_id = uuid.uuid4().hex[:12]
    session_dir = os.path.join(TTS_SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    emotion = req.emotion if req.emotion and req.emotion != "normal" else None

    try:
        raw_timings = await generate_tts_typecast(
            session_dir,
            req.sentences,
            voice_id=req.voice_id,
            speed=req.speed,
            emotion=emotion,
            api_key=keys["typecast"],
        )
    except Exception as e:
        import shutil, traceback
        # 서버 stdout에 전체 트레이스백 기록 (향후 디버깅용)
        print(f"[preview-build] TTS 생성 실패 session={session_id} err={e}")
        traceback.print_exc()
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(500, f"TTS 생성 실패: {e}")

    durations = [t["duration"] for t in raw_timings]
    expanded_sentences = list(req.sentences)
    split_from_map: dict[int, int] = {}  # 새 인덱스 → 원본 인덱스 (분리 추적용)

    # promo_comment 전용: 6초 초과 줄 자동 분리 + wav 파일 재배치
    if req.content_type == "promo_comment":
        overlong = detect_overlong_lines(durations, LINE_DURATION_THRESHOLD)
        if overlong:
            try:
                expanded_sentences, durations, split_from_map = await _split_overlong_lines(
                    session_dir=session_dir,
                    sentences=req.sentences,
                    durations=durations,
                    overlong_indices=overlong,
                    topic=req.topic or "",
                    style=req.style or "realistic",
                    gemini_api_key=keys["gemini"],
                )
            except Exception as e:
                import traceback
                print(f"[preview-build] 분리 실패 session={session_id} err={e}")
                traceback.print_exc()
                # 분리 실패는 TTS 생성 자체의 실패가 아니므로 세션은 유지
                # (원본 5줄로라도 진행 — 6초 초과 줄은 영상에서 잘릴 뿐)

    # timings_raw.json 재생성 (기존 파일 덮어쓰기) — 조립기가 이 파일을 읽음
    raw_timings_out = [
        {"text": s, "duration": d}
        for s, d in zip(expanded_sentences, durations)
    ]
    with open(os.path.join(session_dir, "timings_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw_timings_out, f, ensure_ascii=False, indent=2)

    # 세션 메타 저장 (Job 생성 시 사용, 24h GC 대상)
    metadata = {
        "voice_id": req.voice_id,
        "speed": req.speed,
        "emotion": req.emotion,
        "original_sentences": list(req.sentences),
        "expanded_sentences": expanded_sentences,
        "durations": durations,
        "split_from_map": split_from_map,  # 빈 dict면 분리 없음
        "content_type": req.content_type,
    }
    with open(os.path.join(session_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return {
        "session_id": session_id,
        "lines_count": len(expanded_sentences),
        "durations": durations,
        "split_count": len(split_from_map),
        "expanded_sentences": expanded_sentences,
    }


async def _split_overlong_lines(
    session_dir: str,
    sentences: list[str],
    durations: list[float],
    overlong_indices: list[int],
    topic: str,
    style: str,
    gemini_api_key: str | None,
) -> tuple[list[str], list[float], dict[int, int]]:
    """6초 초과 줄을 Gemini(1순위) 또는 구두점(폴백)으로 2분할하고
    세션 디렉토리의 sent_XX.wav 파일들을 재배치.

    반환: (확장된 sentences, 확장된 durations, {새 인덱스: 원본 인덱스} 매핑)
    """
    # 1) 각 초과 줄의 분리 텍스트 계산
    split_texts: dict[int, list[str]] = {}
    for idx in overlong_indices:
        parts = await split_long_line_with_gemini(
            sentences[idx], topic, style, api_key=gemini_api_key
        )
        if parts is None:
            parts = split_by_punctuation(sentences[idx])
        split_texts[idx] = parts

    # 2) 임시 디렉토리에 재번호 매긴 wav 생성 후 세션 디렉토리로 이동
    temp_dir = os.path.join(session_dir, "_split_temp")
    os.makedirs(temp_dir, exist_ok=True)

    expanded_sentences: list[str] = []
    expanded_durations: list[float] = []
    split_from_map: dict[int, int] = {}

    new_idx = 0
    for orig_idx, sent in enumerate(sentences):
        src_wav = os.path.join(session_dir, f"sent_{orig_idx:02d}.wav")
        if orig_idx in split_texts:
            parts = split_texts[orig_idx]
            # 분할 시각 계산 (음절 비율 + 침묵 감지 보정)
            cut_sec = await asyncio.to_thread(
                calculate_split_point, src_wav, parts[0], parts[1]
            )
            out_a = os.path.join(temp_dir, f"sent_{new_idx:02d}.wav")
            out_b = os.path.join(temp_dir, f"sent_{new_idx + 1:02d}.wav")
            await asyncio.to_thread(cut_wav_at, src_wav, cut_sec, out_a, out_b)
            dur_a = await asyncio.to_thread(get_wav_duration, out_a)
            dur_b = await asyncio.to_thread(get_wav_duration, out_b)

            expanded_sentences.extend(parts)
            expanded_durations.extend([round(dur_a, 2), round(dur_b, 2)])
            split_from_map[new_idx] = orig_idx
            split_from_map[new_idx + 1] = orig_idx
            new_idx += 2
        else:
            out = os.path.join(temp_dir, f"sent_{new_idx:02d}.wav")
            shutil.copy(src_wav, out)
            expanded_sentences.append(sent)
            expanded_durations.append(durations[orig_idx])
            new_idx += 1

    # 3) 원본 sent_XX.wav 삭제 후 temp 파일들을 세션 디렉토리로 이동
    for orig_idx in range(len(sentences)):
        orig = os.path.join(session_dir, f"sent_{orig_idx:02d}.wav")
        if os.path.exists(orig):
            os.remove(orig)
    for i in range(new_idx):
        src = os.path.join(temp_dir, f"sent_{i:02d}.wav")
        dst = os.path.join(session_dir, f"sent_{i:02d}.wav")
        shutil.move(src, dst)
    shutil.rmtree(temp_dir, ignore_errors=True)

    return expanded_sentences, expanded_durations, split_from_map
