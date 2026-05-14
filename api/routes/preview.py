"""미리보기 API — 이미지 미리보기 + AI 클립 미리보기"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Path, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Literal
from sqlalchemy.orm import Session
from api.models import (
    PreviewResponse,
    ClipPreviewResponse,
    ScriptLine,
    SplitLineRequest,
    SplitLineResponse,
    EditLineRequest,
    MergeLineRequest,
    DeleteLineRequest,
)
from api.deps import get_approved_user, get_user_job
from db.database import get_db
from db.models import Job, User
from jobs_queue.task_queue import active_task_exists, enqueue_task, get_active_task, task_payload
from core.r2_storage import require_r2_for_generation, is_r2_enabled, r2_file_exists
from config import settings
from PIL import Image, ImageOps
from core.user_assets_visual import (
    clear_line_asset_progress,
    clear_line_visual_fields,
    ensure_line_ids,
    invalidate_visual_plan,
    mark_line_asset_ready,
    new_line_id,
    parse_visual_plan,
    set_line_asset_progress,
    style_suffix,
    visual_plan_script_hash,
)
import asyncio
import json
import os
import io
import logging
import subprocess

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["preview"])


def _require_generation_storage():
    try:
        require_r2_for_generation()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── 카드 B: 진행 중인 AI 이미지 생성 추적 (분할 시 경쟁 가드용) ──
_AI_IN_FLIGHT: dict[str, set[int]] = {}
_AI_IN_FLIGHT_LOCK = asyncio.Lock()


async def _mark_ai_started(job_id: str, line_index: int) -> None:
    async with _AI_IN_FLIGHT_LOCK:
        _AI_IN_FLIGHT.setdefault(job_id, set()).add(line_index)


async def _mark_ai_finished(job_id: str, line_index: int) -> None:
    async with _AI_IN_FLIGHT_LOCK:
        s = _AI_IN_FLIGHT.get(job_id)
        if s:
            s.discard(line_index)
            if not s:
                _AI_IN_FLIGHT.pop(job_id, None)


def _ai_in_flight_count(job_id: str) -> int:
    return len(_AI_IN_FLIGHT.get(job_id, set()))


def _ffprobe_duration(path: str) -> float:
    """영상 길이(초). 실패 시 0.0."""
    try:
        cmd = (
            f'ffprobe -v error -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 "{path}"'
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _job_asset_exists(job_id: str, relative_path: str) -> bool:
    local_path = os.path.join(settings.STORAGE_DIR, job_id, relative_path)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return True
    if not is_r2_enabled():
        return False
    r2_key = f"jobs/{job_id}/{relative_path.replace(os.sep, '/')}"
    return r2_file_exists(r2_key)


def _set_line_source(job: Job, line_index: int, source: Literal["ai", "image", "clip"], *, status: str = "ready", fail_reason: Optional[str] = None) -> None:
    """줄별 자산 출처와 상태를 Job에 기록. 호출 측에서 db.commit() 필요."""
    sources = json.loads(job.line_sources_json or "[]")
    lines = json.loads(job.script_json or "[]")
    ensure_line_ids(lines)
    n = len(lines)
    # 길이 보정
    if len(sources) < n:
        sources = sources + ["ai"] * (n - len(sources))
    elif len(sources) > n:
        sources = sources[:n]
    if 0 <= line_index < n:
        sources[line_index] = source
        if status == "ready":
            mark_line_asset_ready(lines[line_index])
        else:
            lines[line_index]["status"] = status
            lines[line_index]["fail_reason"] = fail_reason
            clear_line_asset_progress(lines[line_index])
    job.line_sources_json = json.dumps(sources, ensure_ascii=False)
    job.script_json = json.dumps(lines, ensure_ascii=False)
    invalidate_visual_plan(job)


def _is_ai_owned_asset(job_dir: str, line_index: int, source: str) -> bool:
    """True when the current asset should be invalidated by AI prompt/text changes."""
    if source == "ai":
        return True
    if source == "clip":
        # AI image->video conversion keeps the source image; direct user video upload removes it.
        return os.path.exists(os.path.join(job_dir, "images", f"img_{line_index:02d}.png"))
    return False


async def _delete_line_assets_r2(job_id: str, line_index: int) -> None:
    from core.r2_storage import delete_object as r2_delete, is_r2_enabled

    if not is_r2_enabled():
        return
    await r2_delete(f"jobs/{job_id}/images/img_{line_index:02d}.png")
    await r2_delete(f"jobs/{job_id}/clips/clip_raw_{line_index:02d}.mp4")


async def _discard_line_assets(job_id: str, job_dir: str, line_index: int) -> None:
    _delete_line_assets(job_dir, line_index)
    await _delete_line_assets_r2(job_id, line_index)


def _pending_ai_line(text: str) -> dict:
    return {
        "line_id": new_line_id(),
        "text": text,
        "image_prompt": "",
        "motion": "zoom_in",
        "status": "pending",
        "fail_reason": None,
    }


# ─────────────────────────────────────
# 카드 B: 자산 시프트 보조 (병합·삭제에서 공유)
# ─────────────────────────────────────


def _shift_assets_down_in_place(job_dir: str, n_old: int, removed_index: int) -> list[tuple[str, str]]:
    """removed_index 이후 자산 파일들을 -1 인덱스로 시프트. 성공한 rename 쌍을 반환 (롤백용)."""
    renames: list[tuple[str, str]] = []
    for i in range(removed_index + 1, n_old):
        for sub, fname_old, fname_new in (
            ("images", f"img_{i:02d}.png", f"img_{i - 1:02d}.png"),
            ("clips", f"clip_raw_{i:02d}.mp4", f"clip_raw_{i - 1:02d}.mp4"),
        ):
            src = os.path.join(job_dir, sub, fname_old)
            if not os.path.exists(src):
                continue
            dst = os.path.join(job_dir, sub, fname_new)
            os.rename(src, dst)
            renames.append((src, dst))
    return renames


def _delete_line_assets(job_dir: str, line_index: int) -> None:
    """제거되는 줄의 자산 파일을 best-effort 삭제."""
    for sub, fname in (
        ("images", f"img_{line_index:02d}.png"),
        ("clips", f"clip_raw_{line_index:02d}.mp4"),
    ):
        p = os.path.join(job_dir, sub, fname)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


async def _r2_shift_down(job_id: str, job_dir: str, n_old: int, removed_index: int) -> None:
    """R2 활성 시: removed_index 객체 삭제 + 이후 인덱스 -1 시프트 (best-effort)."""
    from core.r2_storage import copy_object as r2_copy, delete_object as r2_delete, is_r2_enabled
    if not is_r2_enabled():
        return
    # 제거되는 줄의 R2 객체 삭제
    for sub, fname in (
        ("images", f"img_{removed_index:02d}.png"),
        ("clips", f"clip_raw_{removed_index:02d}.mp4"),
    ):
        try:
            await r2_delete(f"jobs/{job_id}/{sub}/{fname}")
        except Exception:
            pass
    # 시프트: 로컬에서 이미 -1로 이동된 항목만 R2도 시프트
    for i in range(removed_index + 1, n_old):
        for sub, fname_old, fname_new in (
            ("images", f"img_{i:02d}.png", f"img_{i - 1:02d}.png"),
            ("clips", f"clip_raw_{i:02d}.mp4", f"clip_raw_{i - 1:02d}.mp4"),
        ):
            if not os.path.exists(os.path.join(job_dir, sub, fname_new)):
                continue
            src_key = f"jobs/{job_id}/{sub}/{fname_old}"
            dst_key = f"jobs/{job_id}/{sub}/{fname_new}"
            try:
                ok = await r2_copy(src_key, dst_key)
                if ok:
                    await r2_delete(src_key)
            except Exception as e:
                logger.warning("[shift-down] R2 시프트 실패 job=%s key=%s: %s", job_id, src_key, e)


# ─────────────────────────────────────
# 카드 B: 카드 안에서 Enter로 줄 분할 + 텍스트 sync
# ─────────────────────────────────────


@router.post("/{job_id}/split-line", response_model=SplitLineResponse)
async def split_line(
    body: SplitLineRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 전용: line_index 위치를 before/after 두 줄로 분리. 이후 인덱스 자산 파일은 +1 시프트."""
    job = get_user_job(db, job_id, _user)
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")
    if job.status != "preview_ready":
        raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")
    if _ai_in_flight_count(job_id) > 0 or active_task_exists(db, job_id):
        raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

    lines = json.loads(job.script_json or "[]")
    sources = json.loads(job.line_sources_json or "[]")
    ensure_line_ids(lines)
    n = len(lines)
    if len(sources) != n:
        raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")
    if not (0 <= body.line_index < n):
        raise HTTPException(status_code=400, detail="잘못된 줄 인덱스")

    L = body.line_index
    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    cur = lines[L]
    source = sources[L]
    ai_owned = _is_ai_owned_asset(job_dir, L, source)
    first = {**cur, "text": body.before}
    if ai_owned:
        clear_line_visual_fields(first, status="pending")
        first_source = "ai"
    else:
        first_source = source
    second = _pending_ai_line(body.after)
    new_lines = lines[:L] + [first, second] + lines[L + 1:]
    new_sources = sources[:L] + [first_source, "ai"] + sources[L + 1:]

    # 자산 파일 시프트 — 역순으로 (높은 인덱스부터)
    renames: list[tuple[str, str]] = []
    try:
        for i in range(n - 1, L, -1):
            for sub, fname_old, fname_new in (
                ("images", f"img_{i:02d}.png", f"img_{i + 1:02d}.png"),
                ("clips", f"clip_raw_{i:02d}.mp4", f"clip_raw_{i + 1:02d}.mp4"),
            ):
                src = os.path.join(job_dir, sub, fname_old)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(job_dir, sub, fname_new)
                os.rename(src, dst)
                renames.append((src, dst))
    except OSError as e:
        for src, dst in reversed(renames):
            try:
                os.rename(dst, src)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"자산 파일 시프트 실패: {e}")

    if ai_owned:
        await _discard_line_assets(job_id, job_dir, L)

    # R2 시프트 (활성화된 경우, best-effort)
    from core.r2_storage import copy_object as r2_copy, delete_object as r2_delete, is_r2_enabled
    if is_r2_enabled():
        try:
            for i in range(n - 1, L, -1):
                for sub, fname_old, fname_new in (
                    ("images", f"img_{i:02d}.png", f"img_{i + 1:02d}.png"),
                    ("clips", f"clip_raw_{i:02d}.mp4", f"clip_raw_{i + 1:02d}.mp4"),
                ):
                    # 로컬에서 시프트된 항목만 R2도 시프트
                    if not os.path.exists(os.path.join(job_dir, sub, fname_new)):
                        continue
                    src_key = f"jobs/{job_id}/{sub}/{fname_old}"
                    dst_key = f"jobs/{job_id}/{sub}/{fname_new}"
                    ok = await r2_copy(src_key, dst_key)
                    if ok:
                        await r2_delete(src_key)
        except Exception as e:
            logger.warning("[split-line] R2 시프트 일부 실패 job=%s: %s", job_id, e)

    job.script_json = json.dumps(new_lines, ensure_ascii=False)
    job.line_sources_json = json.dumps(new_sources, ensure_ascii=False)
    invalidate_visual_plan(job)
    db.commit()

    return SplitLineResponse(
        lines=[ScriptLine(**l) for l in new_lines],
        sources=new_sources,
    )


@router.post("/{job_id}/edit-line")
async def edit_line(
    body: EditLineRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 전용: 줄 텍스트 편집을 서버 script_json에 sync. 빈 문자열 허용."""
    job = get_user_job(db, job_id, _user)
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")
    if job.status != "preview_ready":
        raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")

    lines = json.loads(job.script_json or "[]")
    ids_changed = ensure_line_ids(lines)
    if not (0 <= body.line_index < len(lines)):
        raise HTTPException(status_code=400, detail="잘못된 줄 인덱스")
    old_text = lines[body.line_index].get("text") or ""
    if old_text == body.text:
        if ids_changed:
            invalidate_visual_plan(job)
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()
        return {"ok": True}

    lines[body.line_index]["text"] = body.text
    sources = json.loads(job.line_sources_json or "[]")
    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    source = sources[body.line_index] if body.line_index < len(sources) else "ai"
    if _is_ai_owned_asset(job_dir, body.line_index, source):
        await _discard_line_assets(job_id, job_dir, body.line_index)
        clear_line_visual_fields(lines[body.line_index], status="pending")
        if body.line_index < len(sources):
            sources[body.line_index] = "ai"
        job.line_sources_json = json.dumps(sources, ensure_ascii=False)
    invalidate_visual_plan(job)
    job.script_json = json.dumps(lines, ensure_ascii=False)
    db.commit()
    return {"ok": True}


@router.post("/{job_id}/merge-line", response_model=SplitLineResponse)
async def merge_line(
    body: MergeLineRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 전용: line_index 카드를 line_index-1 카드 끝에 이어 붙이고 line_index 카드를 제거.
    이후 인덱스 자산 파일은 -1 시프트.
    """
    job = get_user_job(db, job_id, _user)
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")
    if job.status != "preview_ready":
        raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")
    if _ai_in_flight_count(job_id) > 0 or active_task_exists(db, job_id):
        raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

    lines = json.loads(job.script_json or "[]")
    sources = json.loads(job.line_sources_json or "[]")
    ensure_line_ids(lines)
    n = len(lines)
    if len(sources) != n:
        raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")
    if not (1 <= body.line_index < n):
        raise HTTPException(status_code=400, detail="잘못된 줄 인덱스")

    L = body.line_index
    # 텍스트 단순 연결 — 분할 때 보존된 공백을 그대로 복원
    lines[L - 1]["text"] = (lines[L - 1].get("text") or "") + (lines[L].get("text") or "")

    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    prev_source = sources[L - 1]
    if _is_ai_owned_asset(job_dir, L - 1, prev_source):
        await _discard_line_assets(job_id, job_dir, L - 1)
        clear_line_visual_fields(lines[L - 1], status="pending")
        sources[L - 1] = "ai"
    await _discard_line_assets(job_id, job_dir, L)
    renames: list[tuple[str, str]] = []
    try:
        renames = _shift_assets_down_in_place(job_dir, n, L)
    except OSError as e:
        for src, dst in reversed(renames):
            try:
                os.rename(dst, src)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"자산 파일 시프트 실패: {e}")

    try:
        await _r2_shift_down(job_id, job_dir, n, L)
    except Exception as e:
        logger.warning("[merge-line] R2 시프트 실패 job=%s: %s", job_id, e)

    new_lines = lines[:L] + lines[L + 1:]
    new_sources = sources[:L] + sources[L + 1:]
    job.script_json = json.dumps(new_lines, ensure_ascii=False)
    job.line_sources_json = json.dumps(new_sources, ensure_ascii=False)
    invalidate_visual_plan(job)
    db.commit()

    return SplitLineResponse(
        lines=[ScriptLine(**l) for l in new_lines],
        sources=new_sources,
    )


@router.post("/{job_id}/delete-line", response_model=SplitLineResponse)
async def delete_line(
    body: DeleteLineRequest,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 전용: line_index 카드 자체를 제거. 이후 인덱스 자산 파일은 -1 시프트."""
    job = get_user_job(db, job_id, _user)
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")
    if job.status != "preview_ready":
        raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")
    if _ai_in_flight_count(job_id) > 0 or active_task_exists(db, job_id):
        raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

    lines = json.loads(job.script_json or "[]")
    sources = json.loads(job.line_sources_json or "[]")
    ensure_line_ids(lines)
    n = len(lines)
    if len(sources) != n:
        raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")
    if not (0 <= body.line_index < n):
        raise HTTPException(status_code=400, detail="잘못된 줄 인덱스")
    if n <= 1:
        raise HTTPException(status_code=400, detail="마지막 줄은 삭제할 수 없습니다")

    L = body.line_index
    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    _delete_line_assets(job_dir, L)
    try:
        _shift_assets_down_in_place(job_dir, n, L)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"자산 파일 시프트 실패: {e}")
    try:
        await _r2_shift_down(job_id, job_dir, n, L)
    except Exception as e:
        logger.warning("[delete-line] R2 시프트 실패 job=%s: %s", job_id, e)

    new_lines = lines[:L] + lines[L + 1:]
    new_sources = sources[:L] + sources[L + 1:]
    job.script_json = json.dumps(new_lines, ensure_ascii=False)
    job.line_sources_json = json.dumps(new_sources, ensure_ascii=False)
    invalidate_visual_plan(job)
    db.commit()

    return SplitLineResponse(
        lines=[ScriptLine(**l) for l in new_lines],
        sources=new_sources,
    )


@router.get("/{job_id}/preview", response_model=PreviewResponse)
async def get_preview(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """생성된 이미지 + 대본 미리보기"""
    job = get_user_job(db, job_id, _user)
    if job.status not in ("preview_ready", "awaiting_confirmation", "regenerating_image"):
        raise HTTPException(status_code=400, detail=f"미리보기 불가 (상태: {job.status})")

    lines = [ScriptLine(**l) for l in json.loads(job.script_json)]
    image_urls = [f"/api/jobs/{job_id}/images/{i}" for i in range(len(lines))]

    return PreviewResponse(title=job.title, lines=lines, image_urls=image_urls)


@router.get("/{job_id}/visual-plan")
async def get_visual_plan_debug(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 디버그: AI에 넘어간 visual plan/줄별 프롬프트/QA 결과 확인."""
    job = get_user_job(db, job_id, _user)
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")

    lines = json.loads(job.script_json or "[]")
    sources = json.loads(job.line_sources_json or "[]")
    plan = parse_visual_plan(getattr(job, "visual_plan_json", ""))
    suffix = style_suffix(job.style)
    current_hash = visual_plan_script_hash(lines)

    debug_lines = []
    for i, line in enumerate(lines):
        prompt = line.get("image_prompt") or ""
        final_prompt = f"{prompt}, {suffix}" if prompt and suffix else prompt
        debug_lines.append({
            "index": i + 1,
            "line_id": line.get("line_id"),
            "text": line.get("text") or "",
            "source": sources[i] if i < len(sources) else "ai",
            "status": line.get("status"),
            "visual_anchor": line.get("visual_anchor"),
            "visual_intent": line.get("visual_intent"),
            "reference_line_index": line.get("reference_line_index"),
            "image_prompt": prompt,
            "final_image_prompt": final_prompt,
            "motion": line.get("motion"),
            "qa_status": line.get("qa_status"),
            "qa_result": line.get("qa_result"),
        })

    return {
        "job_id": job_id,
        "plan_valid": bool(plan) and plan.get("script_hash") == current_hash,
        "current_script_hash": current_hash,
        "plan_script_hash": plan.get("script_hash"),
        "inferred_topic": plan.get("inferred_topic"),
        "narrative_summary": plan.get("narrative_summary"),
        "visual_bible": plan.get("visual_bible"),
        "continuity_anchors": plan.get("continuity_anchors"),
        "plan_lines": plan.get("lines", []),
        "lines": debug_lines,
    }


@router.post("/{job_id}/confirm")
async def confirm_and_render(
    request: Request,
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """미리보기 확인 → AI 영상이면 클립 생성, Ken Burns면 바로 영상 조립.

    카드 B(generation_mode == 'user_assets')일 때는 body에 voice_id, bgm 등
    음성/BGM 설정이 함께 전송된다. 이 시점에서 Job을 보강하고 자산 실재를 검증한다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    video_mode = body.get("video_mode", "kenburns") or "kenburns"
    print(f"[DEBUG confirm] job_id={job_id}, raw_body={body}, video_mode={video_mode}")

    job = get_user_job(db, job_id, _user)
    if job.status not in ("preview_ready", "awaiting_confirmation"):
        raise HTTPException(status_code=400, detail=f"확정 불가 (상태: {job.status})")
    _require_generation_storage()

    # ─── 카드 B 분기: 자산 실재 검증 + 음성/BGM 설정 흡수 ───
    if job.generation_mode == "user_assets":
        lines = json.loads(job.script_json or "[]")
        sources = json.loads(job.line_sources_json or "[]")
        if len(sources) != len(lines):
            raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")

        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        for i, src in enumerate(sources):
            if lines[i].get("status") != "ready":
                raise HTTPException(status_code=400, detail=f"{i + 1}번째 줄의 자산이 아직 준비되지 않았습니다")
            if src == "clip":
                if not _job_asset_exists(job_id, os.path.join("clips", f"clip_raw_{i:02d}.mp4")):
                    raise HTTPException(status_code=400, detail=f"{i + 1}번째 줄에 영상이 없습니다")
            else:  # "ai" or "image"
                if not _job_asset_exists(job_id, os.path.join("images", f"img_{i:02d}.png")):
                    raise HTTPException(status_code=400, detail=f"{i + 1}번째 줄에 이미지가 없습니다")

        # 음성/BGM 설정 흡수
        job.video_mode = "kenburns"  # 카드 B는 ai_clips 경로 미사용 (사용자 업로드 영상은 별도 분기)
        if body.get("voice_id"):
            job.voice_id = body["voice_id"]
        if body.get("tts_engine"):
            job.tts_engine = body["tts_engine"]
        if body.get("tts_speed") is not None:
            job.tts_speed = float(body["tts_speed"])
        if body.get("emotion") is not None:
            job.emotion = body["emotion"]
        if body.get("tts_session_id"):
            job.tts_session_id = body["tts_session_id"]
        if body.get("bgm_filename") is not None:
            job.bgm_filename = body["bgm_filename"]
        if body.get("bgm_start_sec") is not None:
            job.bgm_start_sec = float(body["bgm_start_sec"])
        if body.get("bgm_volume") is not None:
            job.bgm_volume = float(body["bgm_volume"])
        if body.get("title_line1") is not None:
            job.title_line1 = body["title_line1"]
        if body.get("title_line2") is not None:
            job.title_line2 = body["title_line2"]
        # title이 비어 있으면 video_assembler.py:306의 조건(if title_text and font_title)을
        # 통과하지 못해 제목 자체가 영상에 안 박힌다. 카드 B draft는 title=""로 시작하므로 여기서 흡수.
        if body.get("title") is not None:
            job.title = body["title"]

        # TTS 세션 디렉터리가 별도에 있으면 job_dir/tts/로 이동
        if job.tts_session_id:
            tts_session_dir = os.path.join(settings.STORAGE_DIR, "tts_sessions", job.tts_session_id)
            if os.path.exists(tts_session_dir):
                import shutil
                tts_dst = os.path.join(job_dir, "tts")
                os.makedirs(tts_dst, exist_ok=True)
                try:
                    for fname in os.listdir(tts_session_dir):
                        shutil.move(os.path.join(tts_session_dir, fname), os.path.join(tts_dst, fname))
                    os.rmdir(tts_session_dir)
                except Exception as e:
                    job.tts_session_id = None
                    print(f"[confirm user_assets] TTS 세션 이동 실패, 재생성 경로로 폴백: {e}")

        job.status = "awaiting_confirmation"
        job.current_step = "영상 제작 준비 중..."
        db.commit()
        task, already_running = enqueue_task(
            db,
            job=job,
            kind="render_video",
            payload={},
            dedupe_key="render",
            max_attempts=80,
        )
        return {"message": "영상 제작을 시작합니다", "job_id": job_id, "next": "render", "task_id": task.id, "already_running": already_running}

    # ─── 카드 A: 기존 흐름 ───
    job.video_mode = video_mode

    if video_mode in ("hailuo", "hailuo23", "wan", "kling", "veo", "veo_lite"):
        # AI 영상 모드: 이미지 확인 → AI 클립 생성 단계로
        job.status = "generating_clips"
        job.current_step = "AI 영상 클립 생성 준비 중..."
        db.commit()
        task, already_running = enqueue_task(
            db,
            job=job,
            kind="card_a_clips",
            payload={},
            dedupe_key="card_a_clips",
            max_attempts=80,
        )
        return {"message": "AI 영상 클립 생성을 시작합니다", "job_id": job_id, "next": "clips", "task_id": task.id, "already_running": already_running}
    else:
        # Ken Burns 모드: 바로 영상 조립
        job.status = "awaiting_confirmation"
        job.current_step = "영상 제작 준비 중..."
        db.commit()
        task, already_running = enqueue_task(
            db,
            job=job,
            kind="render_video",
            payload={},
            dedupe_key="render",
            max_attempts=80,
        )
        return {"message": "영상 제작을 시작합니다", "job_id": job_id, "next": "render", "task_id": task.id, "already_running": already_running}


class RegenerateRequest(BaseModel):
    korean_request: Optional[str] = None
    english_prompt: Optional[str] = None


@router.post("/{job_id}/regenerate-image/{line_index}")
async def regenerate_image(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    line_index: int = 0,
    body: RegenerateRequest = RegenerateRequest(),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """특정 이미지 재생성 (한글 요청어 → 영어 프롬프트 변환).

    카드 B에서는 Job 전체 상태를 바꾸지 않는다(한 줄 실패가 Job 전체 실패로 번지면 안 됨).
    """
    job = get_user_job(db, job_id, _user)
    _require_generation_storage()

    lines = json.loads(job.script_json)
    ids_changed = ensure_line_ids(lines)
    if line_index < 0 or line_index >= len(lines):
        raise HTTPException(status_code=400, detail="잘못된 이미지 인덱스")

    if job.generation_mode != "user_assets":
        if ids_changed:
            job.script_json = json.dumps(lines, ensure_ascii=False)
        job.status = "regenerating_image"
        db.commit()
    else:
        if job.status != "preview_ready":
            raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")
        if active_task_exists(db, job_id):
            raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")
        if not (lines[line_index].get("text") or "").strip():
            raise HTTPException(status_code=400, detail="빈 텍스트 줄은 이미지 생성할 수 없습니다")
        # 줄별 상태만 'pending'으로 표시 (UI에 로딩 스피너 등)
        set_line_asset_progress(lines[line_index], "ai_image", "queued", "AI 이미지 생성 대기 중")
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()

    line_id = lines[line_index].get("line_id")
    task, already_running = enqueue_task(
        db,
        job=job,
        kind="regenerate_image",
        payload={
            "line_index": line_index,
            "line_id": line_id,
            "korean_request": body.korean_request,
            "english_prompt": body.english_prompt,
        },
        dedupe_key=f"image:{line_id or line_index}",
        max_attempts=80,
    )
    return {
        "message": f"이미지 {line_index + 1} 재생성 시작",
        "task_id": task.id,
        "already_running": already_running,
    }


@router.post("/{job_id}/generate-missing-images")
async def generate_missing_images(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """카드 B 전용: line_sources가 'ai'이고 이미지 파일이 없는 줄을 일괄 생성한다."""
    job = get_user_job(db, job_id, _user)
    _require_generation_storage()
    if job.generation_mode != "user_assets":
        raise HTTPException(status_code=400, detail="이 작업은 카드 B 모드가 아닙니다")
    if job.status != "preview_ready":
        raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")

    existing = get_active_task(db, job_id, kind="card_b_missing_images", dedupe_key="missing_images")
    if existing:
        payload = task_payload(existing)
        queued_ids = set(payload.get("line_ids") or [])
        current_lines = json.loads(job.script_json or "[]")
        queued_indexes = [
            i for i, line in enumerate(current_lines)
            if str(line.get("line_id")) in queued_ids
        ]
        return {
            "queued": queued_indexes,
            "task_id": existing.id,
            "status": existing.status,
            "already_running": True,
        }

    lines = json.loads(job.script_json or "[]")
    ids_changed = ensure_line_ids(lines)
    sources = json.loads(job.line_sources_json or "[]")
    if len(sources) != len(lines):
        raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")

    images_dir = os.path.join(settings.STORAGE_DIR, job_id, "images")
    os.makedirs(images_dir, exist_ok=True)

    queued = []
    queued_line_ids = []
    for i, src in enumerate(sources):
        if src != "ai":
            continue
        if not (lines[i].get("text") or "").strip():
            raise HTTPException(status_code=400, detail=f"{i + 1}번째 줄이 비어 있습니다")
        has_asset = _job_asset_exists(job_id, os.path.join("images", f"img_{i:02d}.png"))
        if has_asset:
            if lines[i].get("status") != "ready":
                mark_line_asset_ready(lines[i])
            continue
        queued.append(i)
        queued_line_ids.append(str(lines[i].get("line_id")))
        set_line_asset_progress(lines[i], "ai_image", "queued", "AI 이미지 생성 대기 중")

    job.script_json = json.dumps(lines, ensure_ascii=False)
    if ids_changed:
        invalidate_visual_plan(job)
    db.commit()

    task = None
    already_running = False
    if queued:
        task, already_running = enqueue_task(
            db,
            job=job,
            kind="card_b_missing_images",
            payload={
                "line_indexes": queued,
                "line_ids": queued_line_ids,
                "completed_line_ids": [],
            },
            dedupe_key="missing_images",
            max_attempts=120,
        )

    return {
        "queued": queued,
        "task_id": task.id if task else None,
        "status": task.status if task else "completed",
        "already_running": already_running,
    }


@router.post("/{job_id}/upload-image/{line_index}")
async def upload_image(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    line_index: int = 0,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """사용자 이미지 업로드 — AI 이미지 대체"""
    job = get_user_job(db, job_id, _user)
    _require_generation_storage()
    if active_task_exists(db, job_id):
        raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

    lines = json.loads(job.script_json)
    if line_index < 0 or line_index >= len(lines):
        raise HTTPException(status_code=400, detail="잘못된 이미지 인덱스")

    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status_code=400, detail="PNG, JPG, WebP 이미지만 업로드 가능합니다")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기는 10MB 이하만 가능합니다")

    img = Image.open(io.BytesIO(contents))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    # 9:16 비율로 cover-crop
    target_w, target_h = 1080, 1920
    target_ratio = target_w / target_h

    src_w, src_h = img.size
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        offset = (src_w - new_w) // 2
        img = img.crop((offset, 0, offset + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        offset = (src_h - new_h) // 2
        img = img.crop((0, offset, src_w, offset + new_h))

    img = img.resize((target_w, target_h), Image.LANCZOS)

    output_path = os.path.join(settings.STORAGE_DIR, job_id, "images", f"img_{line_index:02d}.png")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG")

    # 카드 B: 줄별 자산 출처/상태 갱신 + 이전 클립 파일 정리
    if job.generation_mode == "user_assets":
        prev_clip = os.path.join(settings.STORAGE_DIR, job_id, "clips", f"clip_raw_{line_index:02d}.mp4")
        if os.path.exists(prev_clip):
            try:
                os.remove(prev_clip)
            except Exception:
                pass
        _set_line_source(job, line_index, "image", status="ready")
        db.commit()

    from core.r2_storage import upload_file as r2_upload, is_r2_enabled
    if is_r2_enabled():
        ok = await r2_upload(output_path, f"jobs/{job_id}/images/img_{line_index:02d}.png")
        if not ok:
            raise HTTPException(status_code=500, detail="R2 이미지 업로드 실패")

    return {"message": f"이미지 {line_index + 1} 업로드 완료", "image_url": f"/api/jobs/{job_id}/images/{line_index}"}


async def _render_video_task(job_id: str):
    """백그라운드: TTS + 영상 조립"""
    from jobs_queue.worker import render_video_for_job

    await render_video_for_job(job_id)


async def _generate_clips_task(job_id: str):
    """백그라운드: AI 영상 클립 생성"""
    from jobs_queue.worker import generate_clips_for_job

    await generate_clips_for_job(job_id)


async def _regenerate_single_image(job_id: str, line_index: int, korean_request: str = None, english_prompt: str = None):
    """백그라운드: 단일 이미지 재생성"""
    from jobs_queue.worker import regenerate_image_for_job

    await _mark_ai_started(job_id, line_index)
    try:
        await regenerate_image_for_job(job_id, line_index, korean_request, english_prompt)
    finally:
        await _mark_ai_finished(job_id, line_index)


# ─────────────────────────────────────
# AI 클립 미리보기 / 재생성 / 확인
# ─────────────────────────────────────


@router.get("/{job_id}/clip-preview", response_model=ClipPreviewResponse)
async def get_clip_preview(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """AI 클립 미리보기 데이터"""
    job = get_user_job(db, job_id, _user)
    if job.status not in ("clips_ready", "awaiting_confirmation"):
        raise HTTPException(status_code=400, detail=f"클립 미리보기 불가 (상태: {job.status})")

    lines = [ScriptLine(**l) for l in json.loads(job.script_json)]
    clip_urls = [f"/api/jobs/{job_id}/clips/{i}" for i in range(len(lines))]
    image_urls = [f"/api/jobs/{job_id}/images/{i}" for i in range(len(lines))]

    return ClipPreviewResponse(title=job.title, lines=lines, clip_urls=clip_urls, image_urls=image_urls)


@router.get("/{job_id}/clips/{index}")
async def get_clip_file(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    index: int = 0,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """개별 클립 파일 서빙"""
    from fastapi.responses import StreamingResponse
    from core.r2_storage import is_r2_enabled, r2_file_exists, stream_from_r2

    get_user_job(db, job_id, _user)
    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    clip_path = os.path.join(job_dir, "clips", f"clip_raw_{index:02d}.mp4")

    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4")

    r2_key = f"jobs/{job_id}/clips/clip_raw_{index:02d}.mp4"
    if is_r2_enabled() and r2_file_exists(r2_key):
        return StreamingResponse(stream_from_r2(r2_key), media_type="video/mp4")

    raise HTTPException(status_code=404, detail="클립 파일 없음")


@router.post("/{job_id}/regenerate-clip/{line_index}")
async def regenerate_clip(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    line_index: int = 0,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """특정 AI 클립 재생성"""
    job = get_user_job(db, job_id, _user)
    _require_generation_storage()

    lines = json.loads(job.script_json)
    if line_index < 0 or line_index >= len(lines):
        raise HTTPException(status_code=400, detail="잘못된 클립 인덱스")

    if job.generation_mode == "user_assets":
        if job.status != "preview_ready":
            raise HTTPException(status_code=409, detail=f"카드 편집 단계가 아닙니다 (상태: {job.status})")
        if active_task_exists(db, job_id):
            raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

        sources = json.loads(job.line_sources_json or "[]")
        if len(sources) != len(lines):
            raise HTTPException(status_code=400, detail="줄별 자산 정보가 올바르지 않습니다")
        if sources[line_index] not in ("ai", "image"):
            raise HTTPException(status_code=400, detail="이미지가 준비된 줄만 AI 영상으로 변환할 수 있습니다")
        if lines[line_index].get("status") != "ready":
            raise HTTPException(status_code=400, detail="이미지가 준비된 줄만 AI 영상으로 변환할 수 있습니다")

        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        image_path = os.path.join(job_dir, "images", f"img_{line_index:02d}.png")
        if not _job_asset_exists(job_id, os.path.join("images", f"img_{line_index:02d}.png")):
            raise HTTPException(status_code=400, detail=f"{line_index + 1}번째 줄에 이미지가 없습니다")

        clip_path = os.path.join(job_dir, "clips", f"clip_raw_{line_index:02d}.mp4")
        if os.path.exists(clip_path):
            try:
                os.remove(clip_path)
            except Exception:
                pass
        from core.r2_storage import delete_object as r2_delete
        await r2_delete(f"jobs/{job_id}/clips/clip_raw_{line_index:02d}.mp4")

        set_line_asset_progress(lines[line_index], "ai_clip", "queued", "AI 영상 변환 대기 중")
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()

    line_id = lines[line_index].get("line_id")
    task, already_running = enqueue_task(
        db,
        job=job,
        kind="regenerate_clip",
        payload={"line_index": line_index, "line_id": line_id},
        dedupe_key=f"clip:{line_id or line_index}",
        max_attempts=80,
    )
    return {
        "message": f"클립 {line_index + 1} 재생성 시작",
        "task_id": task.id,
        "already_running": already_running,
    }


@router.post("/{job_id}/confirm-clips")
async def confirm_clips_and_render(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """AI 클립 확인 → TTS + 영상 조립 시작"""
    job = get_user_job(db, job_id, _user)
    if job.status not in ("clips_ready", "awaiting_confirmation"):
        raise HTTPException(status_code=400, detail=f"확정 불가 (상태: {job.status})")
    _require_generation_storage()

    job.status = "awaiting_confirmation"
    job.current_step = "영상 제작 준비 중..."
    db.commit()

    task, already_running = enqueue_task(
        db,
        job=job,
        kind="render_video",
        payload={},
        dedupe_key="render",
        max_attempts=80,
    )
    return {"message": "영상 제작을 시작합니다", "job_id": job_id, "task_id": task.id, "already_running": already_running}


@router.post("/{job_id}/upload-clip/{line_index}")
async def upload_clip(
    job_id: str = Path(..., pattern=r"^[a-f0-9]{12}$"),
    line_index: int = 0,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_approved_user),
):
    """사용자 영상 업로드 — AI 클립 대체"""
    job = get_user_job(db, job_id, _user)
    _require_generation_storage()
    if active_task_exists(db, job_id):
        raise HTTPException(status_code=409, detail="AI 자산 생성이 진행 중입니다. 잠시 후 다시 시도하세요")

    lines = json.loads(job.script_json)
    if line_index < 0 or line_index >= len(lines):
        raise HTTPException(status_code=400, detail="잘못된 클립 인덱스")

    allowed_types = ("video/mp4", "video/quicktime", "video/webm", "video/x-msvideo")
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="MP4, MOV, WebM, AVI 영상만 업로드 가능합니다")

    contents = await file.read()
    if len(contents) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기는 50MB 이하만 가능합니다")

    clips_dir = os.path.join(settings.STORAGE_DIR, job_id, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    output_path = os.path.join(clips_dir, f"clip_raw_{line_index:02d}.mp4")

    if file.content_type == "video/mp4":
        # MP4는 그대로 저장
        with open(output_path, "wb") as f:
            f.write(contents)
    else:
        # MOV/WebM/AVI → FFmpeg로 MP4 변환
        ext = {
            "video/quicktime": ".mov",
            "video/webm": ".webm",
            "video/x-msvideo": ".avi",
        }.get(file.content_type, ".tmp")

        tmp_path = os.path.join(clips_dir, f"_upload_tmp{ext}")
        with open(tmp_path, "wb") as f:
            f.write(contents)

        try:
            cmd = f'ffmpeg -y -i "{tmp_path}" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -an "{output_path}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:300])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # 영상 최소 길이 검사 (0.5초 미만 거부) — TTS 길이와의 정밀 비교는 조립 단계에서 수행
    duration = _ffprobe_duration(output_path)
    if duration < 0.5:
        try:
            os.remove(output_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"영상이 너무 짧습니다 ({duration:.2f}초). 1초 이상 영상을 올려주세요.")

    # 카드 B: 줄별 자산 출처/상태 갱신 + 이전 이미지 파일 정리
    if job.generation_mode == "user_assets":
        prev_img = os.path.join(settings.STORAGE_DIR, job_id, "images", f"img_{line_index:02d}.png")
        if os.path.exists(prev_img):
            try:
                os.remove(prev_img)
            except Exception:
                pass
        _set_line_source(job, line_index, "clip", status="ready")
        db.commit()

    from core.r2_storage import upload_file as r2_upload, is_r2_enabled
    clip_output = os.path.join(clips_dir, f"clip_raw_{line_index:02d}.mp4")
    if is_r2_enabled() and os.path.exists(clip_output):
        ok = await r2_upload(clip_output, f"jobs/{job_id}/clips/clip_raw_{line_index:02d}.mp4")
        if not ok:
            raise HTTPException(status_code=500, detail="R2 영상 업로드 실패")

    return {"message": f"클립 {line_index + 1} 업로드 완료", "clip_url": f"/api/jobs/{job_id}/clips/{line_index}"}


async def _regenerate_single_clip(job_id: str, line_index: int):
    """백그라운드: 단일 AI 클립 재생성"""
    from jobs_queue.worker import regenerate_clip_for_job

    await _mark_ai_started(job_id, line_index)
    try:
        await regenerate_clip_for_job(job_id, line_index)
    finally:
        await _mark_ai_finished(job_id, line_index)


async def _regenerate_missing_images_sequence(job_id: str, line_indexes: list[int]):
    """백그라운드: 카드 B 빈 AI 이미지 줄을 순서대로 생성."""
    from jobs_queue.worker import regenerate_missing_images_for_job

    for i in line_indexes:
        await _mark_ai_started(job_id, i)
    try:
        await regenerate_missing_images_for_job(job_id, line_indexes)
    finally:
        for i in line_indexes:
            await _mark_ai_finished(job_id, i)
