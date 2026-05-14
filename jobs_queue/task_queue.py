"""Durable DB-backed task queue for Railway-safe background work."""

from __future__ import annotations

import json
import socket
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.time_utils import utc_now_naive
from db.models import Job, JobTask


ACTIVE_STATUSES = ("queued", "running", "retrying")
WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
LOCK_TTL_SECONDS = 20 * 60


class RetryableTaskError(RuntimeError):
    pass


class BlockedTaskError(RuntimeError):
    pass


def task_payload(task: JobTask) -> dict[str, Any]:
    try:
        data = json.loads(task.payload_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_task_payload(task: JobTask, payload: dict[str, Any]) -> None:
    task.payload_json = json.dumps(payload, ensure_ascii=False)
    task.updated_at = utc_now_naive()


def enqueue_task(
    db: Session,
    *,
    job: Job,
    kind: str,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 80,
) -> tuple[JobTask, bool]:
    """Create a durable task unless the same active task already exists."""
    if dedupe_key:
        existing = (
            db.query(JobTask)
            .filter(
                JobTask.job_id == job.id,
                JobTask.kind == kind,
                JobTask.dedupe_key == dedupe_key,
                JobTask.status.in_(ACTIVE_STATUSES),
            )
            .order_by(JobTask.created_at.desc())
            .first()
        )
        if existing:
            return existing, True

    now = utc_now_naive()
    task = JobTask(
        id=uuid.uuid4().hex[:12],
        job_id=job.id,
        user_id=job.user_id,
        kind=kind,
        dedupe_key=dedupe_key,
        status="queued",
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        attempt_count=0,
        max_attempts=max_attempts,
        next_run_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task, False


def active_task_exists(db: Session, job_id: str, *, kinds: tuple[str, ...] | None = None) -> bool:
    q = db.query(JobTask).filter(JobTask.job_id == job_id, JobTask.status.in_(ACTIVE_STATUSES))
    if kinds:
        q = q.filter(JobTask.kind.in_(kinds))
    return db.query(q.exists()).scalar()


def get_active_task(db: Session, job_id: str, *, kind: str | None = None, dedupe_key: str | None = None) -> JobTask | None:
    q = db.query(JobTask).filter(JobTask.job_id == job_id, JobTask.status.in_(ACTIVE_STATUSES))
    if kind:
        q = q.filter(JobTask.kind == kind)
    if dedupe_key:
        q = q.filter(JobTask.dedupe_key == dedupe_key)
    return q.order_by(JobTask.created_at.desc()).first()


def claim_next_task(db: Session) -> JobTask | None:
    now = utc_now_naive()
    q = (
        db.query(JobTask)
        .filter(JobTask.status.in_(ACTIVE_STATUSES))
        .filter(or_(JobTask.next_run_at.is_(None), JobTask.next_run_at <= now))
        .filter(or_(JobTask.status != "running", JobTask.locked_until.is_(None), JobTask.locked_until <= now))
        .order_by(JobTask.created_at.asc())
    )
    task = q.with_for_update(skip_locked=True).first()
    if not task:
        return None

    task.status = "running"
    task.locked_by = WORKER_ID
    task.locked_until = now + timedelta(seconds=LOCK_TTL_SECONDS)
    task.heartbeat_at = now
    task.updated_at = now
    task.attempt_count = (task.attempt_count or 0) + 1
    if not task.started_at:
        task.started_at = now
    db.commit()
    db.refresh(task)
    return task


def heartbeat_task(db: Session, task: JobTask) -> None:
    now = utc_now_naive()
    task.heartbeat_at = now
    task.locked_until = now + timedelta(seconds=LOCK_TTL_SECONDS)
    task.updated_at = now
    db.commit()


def mark_task_completed(db: Session, task: JobTask) -> None:
    now = utc_now_naive()
    task.status = "completed"
    task.locked_by = None
    task.locked_until = None
    task.error_message = None
    task.updated_at = now
    task.finished_at = now
    db.commit()


def mark_task_blocked(db: Session, task: JobTask, message: str) -> None:
    now = utc_now_naive()
    task.status = "blocked"
    task.locked_by = None
    task.locked_until = None
    task.error_message = (message or "")[:1000]
    task.updated_at = now
    task.finished_at = now
    db.commit()


def mark_task_failed(db: Session, task: JobTask, message: str) -> None:
    now = utc_now_naive()
    task.status = "failed"
    task.locked_by = None
    task.locked_until = None
    task.error_message = (message or "")[:1000]
    task.updated_at = now
    task.finished_at = now
    db.commit()


def mark_task_retrying(db: Session, task: JobTask, message: str) -> None:
    now = utc_now_naive()
    wait = retry_delay_seconds(message, task.attempt_count or 1)
    task.status = "retrying"
    task.locked_by = None
    task.locked_until = None
    task.error_message = (message or "")[:1000]
    task.next_run_at = now + timedelta(seconds=wait)
    task.updated_at = now
    db.commit()


def is_retryable_error(message: str) -> bool:
    s = (message or "").lower()
    retry_terms = (
        "429",
        "resource_exhausted",
        "500",
        "502",
        "503",
        "504",
        "unavailable",
        "internal",
        "deadline_exceeded",
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "temporarily",
        "temporary",
        "rate limit",
        "quota",
        "r2 upload failed",
        "r2 이미지 업로드 실패",
        "r2 영상 업로드 실패",
        "r2 최종 영상 업로드 실패",
        "visual plan",
        "json 생성 실패",
    )
    blocked_terms = (
        "api key not valid",
        "invalid api key",
        "permission_denied",
        "unauthenticated",
        "billing",
        "api 키가 설정되지",
        "api_key_invalid",
    )
    if any(term in s for term in blocked_terms):
        return False
    return any(term in s for term in retry_terms)


def is_blocked_error(message: str) -> bool:
    s = (message or "").lower()
    return any(
        term in s
        for term in (
            "api key not valid",
            "invalid api key",
            "permission_denied",
            "unauthenticated",
            "billing",
            "api 키가 설정되지",
            "api_key_invalid",
        )
    )


def retry_delay_seconds(message: str, attempt_count: int) -> int:
    s = (message or "").lower()
    if "429" in s or "resource_exhausted" in s or "rate limit" in s or "quota" in s:
        return min(600, 60 * max(1, attempt_count))
    return min(600, 15 * (2 ** max(0, attempt_count - 1)))
