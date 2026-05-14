"""Async worker loop for durable JobTask execution."""

from __future__ import annotations

import asyncio
import json
import logging

from core.time_utils import utc_now_naive
from core.user_assets_visual import mark_line_asset_failed, set_line_asset_progress
from db.database import SessionLocal
from db.models import Job, JobTask
from jobs_queue.task_queue import (
    BlockedTaskError,
    RetryableTaskError,
    claim_next_task,
    heartbeat_task,
    is_blocked_error,
    is_retryable_error,
    mark_task_blocked,
    mark_task_completed,
    mark_task_failed,
    mark_task_retrying,
    set_task_payload,
    task_payload,
)

logger = logging.getLogger(__name__)


async def task_worker_loop(stop_event: asyncio.Event) -> None:
    """Continuously claim and process DB-backed tasks until shutdown."""
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            task = claim_next_task(db)
            if not task:
                await asyncio.wait_for(stop_event.wait(), timeout=1.5)
                continue

            try:
                await process_task(task.id)
            except BlockedTaskError as e:
                db.refresh(task)
                _mark_task_lines_failed(db, task, str(e))
                mark_task_blocked(db, task, str(e))
            except RetryableTaskError as e:
                db.refresh(task)
                if (task.attempt_count or 0) >= (task.max_attempts or 80):
                    _mark_task_lines_failed(db, task, str(e))
                    mark_task_failed(db, task, str(e))
                else:
                    mark_task_retrying(db, task, str(e))
            except Exception as e:
                db.refresh(task)
                msg = str(e)
                if is_blocked_error(msg):
                    _mark_task_lines_failed(db, task, msg)
                    mark_task_blocked(db, task, msg)
                elif is_retryable_error(msg) and (task.attempt_count or 0) < (task.max_attempts or 80):
                    mark_task_retrying(db, task, msg)
                else:
                    _mark_task_lines_failed(db, task, msg)
                    mark_task_failed(db, task, msg)
            else:
                db.refresh(task)
                mark_task_completed(db, task)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[task_worker] loop error")
            await asyncio.sleep(2)
        finally:
            db.close()


async def process_task(task_id: str) -> None:
    db = SessionLocal()
    try:
        task = db.query(JobTask).filter(JobTask.id == task_id).first()
        if not task:
            return
        heartbeat_task(db, task)

        if task.kind == "card_a_images":
            await _run_card_a_images(db, task)
        elif task.kind == "card_a_clips":
            await _run_card_a_clips(db, task)
        elif task.kind == "render_video":
            await _run_render_video(db, task)
        elif task.kind == "card_b_single_image":
            await _run_card_b_single_image(db, task)
        elif task.kind == "regenerate_image":
            await _run_regenerate_image(db, task)
        elif task.kind == "card_b_missing_images":
            await _run_card_b_missing_images(db, task)
        elif task.kind == "card_b_single_clip":
            await _run_card_b_single_clip(db, task)
        elif task.kind == "regenerate_clip":
            await _run_regenerate_clip(db, task)
        else:
            raise BlockedTaskError(f"알 수 없는 작업 종류입니다: {task.kind}")
    finally:
        db.close()


async def _run_card_a_images(db, task: JobTask) -> None:
    from jobs_queue.worker import generate_images_for_job

    await generate_images_for_job(task.job_id)
    _raise_if_job_failed(db, task.job_id)


async def _run_card_a_clips(db, task: JobTask) -> None:
    from jobs_queue.worker import generate_clips_for_job

    await generate_clips_for_job(task.job_id)
    _raise_if_job_failed(db, task.job_id)


async def _run_render_video(db, task: JobTask) -> None:
    from jobs_queue.worker import render_video_for_job

    await render_video_for_job(task.job_id)
    _raise_if_job_failed(db, task.job_id)


async def _run_card_b_single_image(db, task: JobTask) -> None:
    payload = task_payload(task)
    line_index = _resolve_line_index(db, task.job_id, payload)
    _set_task_current_line(db, task, payload, line_index)
    await _generate_card_b_line_image(task.job_id, line_index)
    _raise_if_line_failed(db, task.job_id, line_index)


async def _run_regenerate_image(db, task: JobTask) -> None:
    from jobs_queue.worker import regenerate_image_for_job

    payload = task_payload(task)
    line_index = _resolve_line_index(db, task.job_id, payload)
    _set_task_current_line(db, task, payload, line_index)
    await regenerate_image_for_job(
        task.job_id,
        line_index,
        payload.get("korean_request"),
        payload.get("english_prompt"),
    )
    job = db.query(Job).filter(Job.id == task.job_id).first()
    if job and getattr(job, "generation_mode", "ai_full") == "user_assets":
        _raise_if_line_failed(db, task.job_id, line_index)
    else:
        _raise_if_job_failed(db, task.job_id)


async def _run_card_b_missing_images(db, task: JobTask) -> None:
    payload = task_payload(task)
    job = db.query(Job).filter(Job.id == task.job_id).first()
    if not job:
        raise BlockedTaskError("작업을 찾을 수 없습니다")

    lines = json.loads(job.script_json or "[]")
    line_ids = payload.get("line_ids") or []
    if not line_ids:
        queued_indexes = payload.get("line_indexes") or []
        line_ids = [
            str(lines[i].get("line_id"))
            for i in queued_indexes
            if 0 <= i < len(lines) and lines[i].get("line_id")
        ]
        payload["line_ids"] = line_ids
        payload.setdefault("completed_line_ids", [])
        set_task_payload(task, payload)
        db.commit()

    completed = set(payload.get("completed_line_ids") or [])
    for line_id in line_ids:
        if line_id in completed:
            continue
        line_index = _line_index_by_id(lines, line_id)
        if line_index is None:
            completed.add(line_id)
            payload["completed_line_ids"] = list(completed)
            set_task_payload(task, payload)
            db.commit()
            continue

        text = (lines[line_index].get("text") or "").strip()
        if not text:
            mark_line_asset_failed(lines[line_index], "빈 텍스트 줄은 이미지 생성할 수 없습니다", action="ai_image")
            job.script_json = json.dumps(lines, ensure_ascii=False)
            db.commit()
            raise BlockedTaskError(f"{line_index + 1}번째 줄이 비어 있습니다")

        _set_task_current_line(db, task, payload, line_index)
        await _generate_card_b_line_image(task.job_id, line_index)
        _raise_if_line_failed(db, task.job_id, line_index)

        db.refresh(job)
        lines = json.loads(job.script_json or "[]")
        completed.add(line_id)
        payload["completed_line_ids"] = list(completed)
        set_task_payload(task, payload)
        heartbeat_task(db, task)


async def _run_card_b_single_clip(db, task: JobTask) -> None:
    from jobs_queue.worker import regenerate_clip_for_job

    payload = task_payload(task)
    line_index = _resolve_line_index(db, task.job_id, payload)
    _set_task_current_line(db, task, payload, line_index)
    await regenerate_clip_for_job(task.job_id, line_index)
    _raise_if_line_failed(db, task.job_id, line_index)


async def _run_regenerate_clip(db, task: JobTask) -> None:
    from jobs_queue.worker import regenerate_clip_for_job

    payload = task_payload(task)
    line_index = _resolve_line_index(db, task.job_id, payload)
    _set_task_current_line(db, task, payload, line_index)
    await regenerate_clip_for_job(task.job_id, line_index)
    job = db.query(Job).filter(Job.id == task.job_id).first()
    if job and getattr(job, "generation_mode", "ai_full") == "user_assets":
        _raise_if_line_failed(db, task.job_id, line_index)
    else:
        _raise_if_job_failed(db, task.job_id)


async def _generate_card_b_line_image(job_id: str, line_index: int) -> None:
    from jobs_queue.worker import regenerate_image_for_job

    await regenerate_image_for_job(job_id, line_index)


def _raise_if_job_failed(db, job_id: str) -> None:
    db.expire_all()
    job = db.query(Job).filter(Job.id == job_id).first()
    if job and job.status == "failed":
        msg = job.error_message or "작업 실패"
        if is_blocked_error(msg):
            raise BlockedTaskError(msg)
        if is_retryable_error(msg):
            raise RetryableTaskError(msg)
        raise RuntimeError(msg)


def _raise_if_line_failed(db, job_id: str, line_index: int) -> None:
    db.expire_all()
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise BlockedTaskError("작업을 찾을 수 없습니다")
    lines = json.loads(job.script_json or "[]")
    if not (0 <= line_index < len(lines)):
        raise BlockedTaskError("줄을 찾을 수 없습니다")
    line = lines[line_index]
    if line.get("status") == "ready":
        return
    if line.get("status") == "failed":
        msg = line.get("fail_reason") or "줄 생성 실패"
        if is_blocked_error(msg):
            raise BlockedTaskError(msg)
        if is_retryable_error(msg):
            _mark_line_retrying(db, job, lines, line_index, msg)
            raise RetryableTaskError(msg)
        raise RuntimeError(msg)
    raise RetryableTaskError(line.get("asset_message") or "줄 생성이 아직 완료되지 않았습니다")


def _mark_line_retrying(db, job: Job, lines: list[dict], line_index: int, message: str) -> None:
    if 0 <= line_index < len(lines):
        set_line_asset_progress(
            lines[line_index],
            lines[line_index].get("asset_action") or "ai_image",
            "retrying",
            f"일시 오류로 재시도 대기 중: {(message or '')[:80]}",
        )
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()


def _resolve_line_index(db, job_id: str, payload: dict) -> int:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise BlockedTaskError("작업을 찾을 수 없습니다")
    lines = json.loads(job.script_json or "[]")
    line_id = payload.get("line_id")
    if line_id:
        idx = _line_index_by_id(lines, str(line_id))
        if idx is not None:
            return idx
    idx = int(payload.get("line_index", -1))
    if 0 <= idx < len(lines):
        return idx
    raise BlockedTaskError("줄을 찾을 수 없습니다")


def _line_index_by_id(lines: list[dict], line_id: str) -> int | None:
    for i, line in enumerate(lines):
        if str(line.get("line_id")) == str(line_id):
            return i
    return None


def _set_task_current_line(db, task: JobTask, payload: dict, line_index: int) -> None:
    payload["current_line_index"] = line_index
    payload["last_heartbeat_at"] = utc_now_naive().isoformat()
    set_task_payload(task, payload)
    heartbeat_task(db, task)


def _mark_task_lines_failed(db, task: JobTask, message: str) -> None:
    payload = task_payload(task)
    job = db.query(Job).filter(Job.id == task.job_id).first()
    if not job:
        return
    try:
        lines = json.loads(job.script_json or "[]")
    except Exception:
        return
    indexes: set[int] = set()
    if payload.get("current_line_index") is not None:
        indexes.add(int(payload["current_line_index"]))
    if task.kind == "card_b_missing_images":
        completed = set(payload.get("completed_line_ids") or [])
        line_ids = payload.get("line_ids") or []
        for line_id in line_ids:
            if line_id in completed:
                continue
            idx = _line_index_by_id(lines, str(line_id))
            if idx is not None:
                indexes.add(idx)
    for idx in indexes:
        if not (0 <= idx < len(lines)):
            continue
        if lines[idx].get("status") == "ready":
            continue
        action = lines[idx].get("asset_action") or ("ai_clip" if "clip" in task.kind else "ai_image")
        mark_line_asset_failed(lines[idx], message, action=action)
    job.script_json = json.dumps(lines, ensure_ascii=False)
    db.commit()
