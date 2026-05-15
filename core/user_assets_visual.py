"""Card B visual planning helpers.

These helpers keep user-provided script lines stable while users split,
merge, and delete cards.  The AI visual plan is keyed by line_id instead of
the current array index so prompts do not silently drift when lines move.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any


VISUAL_PLAN_VERSION = 2
ASSET_PROGRESS_KEYS = ("asset_action", "asset_step", "asset_message")


def new_line_id() -> str:
    return uuid.uuid4().hex[:12]


def safe_line_id(line: dict[str, Any]) -> str:
    raw = str(line.get("line_id") or "").strip()
    return "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_"))


def legacy_line_asset_rel(kind: str, index: int) -> str:
    if kind == "image":
        return os.path.join("images", f"img_{index:02d}.png")
    if kind == "clip":
        return os.path.join("clips", f"clip_raw_{index:02d}.mp4")
    raise ValueError(f"unknown line asset kind: {kind}")


def line_asset_rel(kind: str, line: dict[str, Any], index: int | None = None) -> str:
    line_id = safe_line_id(line)
    if kind == "image" and line_id:
        return os.path.join("images", f"line_{line_id}.png")
    if kind == "clip" and line_id:
        return os.path.join("clips", f"clip_{line_id}.mp4")
    if index is None:
        raise ValueError("index is required when line_id is missing")
    return legacy_line_asset_rel(kind, index)


def line_asset_rel_candidates(kind: str, line: dict[str, Any], index: int) -> list[str]:
    primary = line_asset_rel(kind, line, index)
    legacy = legacy_line_asset_rel(kind, index)
    if primary == legacy:
        return [primary]
    return [primary, legacy]


def r2_job_asset_key(job_id: str, relative_path: str) -> str:
    return f"jobs/{job_id}/{relative_path.replace(os.sep, '/')}"


def ensure_line_ids(lines: list[dict[str, Any]]) -> bool:
    """Ensure every script line has a stable id. Returns True if mutated."""
    changed = False
    seen: set[str] = set()
    for line in lines:
        line_id = str(line.get("line_id") or "").strip()
        if not line_id or line_id in seen:
            line_id = new_line_id()
            line["line_id"] = line_id
            changed = True
        seen.add(line_id)
    return changed


def line_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def visual_plan_script_hash(lines: list[dict[str, Any]]) -> str:
    payload = [
        {
            "line_id": line.get("line_id") or f"idx:{idx}",
            "text": line.get("text") or "",
        }
        for idx, line in enumerate(lines)
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_line_visual_fields(line: dict[str, Any], *, status: str = "pending") -> None:
    line["image_prompt"] = ""
    line["motion"] = line.get("motion") or "zoom_in"
    line["status"] = status
    line["fail_reason"] = None
    for key in (
        "visual_text_hash",
        "visual_anchor",
        "visual_intent",
        "qa_status",
        "qa_result",
        "qa_retry_instruction",
        "reference_line_index",
        *ASSET_PROGRESS_KEYS,
    ):
        line.pop(key, None)


def set_line_asset_progress(line: dict[str, Any], action: str, step: str, message: str) -> None:
    line["status"] = "pending"
    line["fail_reason"] = None
    line["asset_action"] = action
    line["asset_step"] = step
    line["asset_message"] = message


def clear_line_asset_progress(line: dict[str, Any]) -> None:
    for key in ASSET_PROGRESS_KEYS:
        line.pop(key, None)


def bump_line_asset_version(line: dict[str, Any]) -> int:
    current = int(line.get("asset_version") or 0)
    next_version = max(current + 1, int(time.time() * 1000))
    line["asset_version"] = next_version
    return next_version


def mark_line_asset_ready(line: dict[str, Any], *, bump_version: bool = False) -> None:
    line["status"] = "ready"
    line["fail_reason"] = None
    if bump_version:
        bump_line_asset_version(line)
    clear_line_asset_progress(line)


def mark_line_asset_failed(line: dict[str, Any], reason: str, *, action: str | None = None) -> None:
    line["status"] = "failed"
    line["fail_reason"] = (reason or "")[:200]
    if action:
        line["asset_action"] = action
    line.pop("asset_step", None)
    line.pop("asset_message", None)


def invalidate_visual_plan(job: Any) -> None:
    if hasattr(job, "visual_plan_json"):
        job.visual_plan_json = ""


def parse_visual_plan(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def style_suffix(style: str) -> str:
    from core.gemini_client import STYLE_SUFFIXES

    return STYLE_SUFFIXES.get(style, "")
