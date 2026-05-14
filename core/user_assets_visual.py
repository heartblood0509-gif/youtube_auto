"""Card B visual planning helpers.

These helpers keep user-provided script lines stable while users split,
merge, and delete cards.  The AI visual plan is keyed by line_id instead of
the current array index so prompts do not silently drift when lines move.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


VISUAL_PLAN_VERSION = 2
ASSET_PROGRESS_KEYS = ("asset_action", "asset_step", "asset_message")


def new_line_id() -> str:
    return uuid.uuid4().hex[:12]


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


def mark_line_asset_ready(line: dict[str, Any]) -> None:
    line["status"] = "ready"
    line["fail_reason"] = None
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
