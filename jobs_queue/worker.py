"""백그라운드 워커 - 이미지 생성 및 영상 조립"""

import json
import os
import glob
import asyncio
from typing import Any

from db.database import SessionLocal
from db.models import Job
from jobs_queue.job_manager import update_job_progress, mark_job_failed, set_video_path
from api.deps import resolve_user_api_keys
from core.r2_storage import is_r2_enabled, require_r2_for_generation
from core.user_assets_visual import mark_line_asset_failed, mark_line_asset_ready, set_line_asset_progress
from config import settings


def _update_r2_sync(job_id: str, status: str):
    """R2 동기화 상태 업데이트"""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.r2_synced = status
            db.commit()
    finally:
        db.close()


async def _ensure_r2_asset_local(job_id: str, relative_path: str) -> bool:
    """Ensure a generated job asset exists locally, downloading from R2 if needed."""
    local_path = os.path.join(settings.STORAGE_DIR, job_id, relative_path)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return True
    if not is_r2_enabled():
        return False
    from core.r2_storage import download_file_sync, r2_file_exists

    r2_key = f"jobs/{job_id}/{relative_path.replace(os.sep, '/')}"
    if not await asyncio.to_thread(r2_file_exists, r2_key):
        return False
    return await asyncio.to_thread(download_file_sync, r2_key, local_path)


def _job_sources(job: Job, n: int) -> list[str]:
    try:
        sources = json.loads(job.line_sources_json or "[]")
    except Exception:
        sources = []
    if len(sources) < n:
        sources = sources + ["ai"] * (n - len(sources))
    return sources[:n]


def _plan_line_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(line.get("line_id")): line
        for line in plan.get("lines", [])
        if isinstance(line, dict) and line.get("line_id")
    }


def _set_line_progress(db, job: Job, line_index: int, action: str, step: str, message: str) -> list[dict[str, Any]]:
    db.refresh(job)
    lines = json.loads(job.script_json or "[]")
    if 0 <= line_index < len(lines):
        set_line_asset_progress(lines[line_index], action, step, message)
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()
    return lines


async def _ensure_user_assets_visual_plan(db, job: Job, keys: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from core.gemini_client import generate_user_assets_visual_plan
    from core.time_utils import utc_isoformat, utc_now_naive
    from core.user_assets_visual import VISUAL_PLAN_VERSION, ensure_line_ids, parse_visual_plan, visual_plan_script_hash

    lines = json.loads(job.script_json or "[]")
    changed = ensure_line_ids(lines)
    script_hash = visual_plan_script_hash(lines)
    plan = parse_visual_plan(getattr(job, "visual_plan_json", ""))

    if plan.get("script_hash") != script_hash or plan.get("version") != VISUAL_PLAN_VERSION:
        sources = _job_sources(job, len(lines))
        plan = await generate_user_assets_visual_plan(
            lines=lines,
            sources=sources,
            style=job.style,
            api_key=keys["gemini"],
        )
        plan["script_hash"] = script_hash
        plan["generated_at"] = utc_isoformat(utc_now_naive())
        job.visual_plan_json = json.dumps(plan, ensure_ascii=False)
        changed = True

    if changed:
        job.script_json = json.dumps(lines, ensure_ascii=False)
        db.commit()

    return plan, lines


def _reference_image_for_anchor(
    job_dir: str,
    lines: list[dict[str, Any]],
    sources: list[str],
    plan: dict[str, Any],
    line_index: int,
    anchor: str | None,
):
    if not anchor:
        return None, None

    by_id = _plan_line_by_id(plan)
    for i in range(line_index - 1, -1, -1):
        if i >= len(lines):
            continue
        prev_plan = by_id.get(str(lines[i].get("line_id")))
        if not prev_plan or prev_plan.get("continuity_anchor") != anchor:
            continue
        if lines[i].get("status") != "ready":
            continue
        source = sources[i] if i < len(sources) else "ai"
        if source not in ("ai", "image", "clip"):
            continue
        img_path = os.path.join(job_dir, "images", f"img_{i:02d}.png")
        if not os.path.exists(img_path):
            continue
        try:
            from PIL import Image

            img = Image.open(img_path)
            img.load()
            return img, i
        except Exception:
            return None, None
    return None, None


async def generate_images_for_job(job_id: str):
    """이미지 생성 백그라운드 태스크"""
    from core.gemini_client import generate_all_images
    from PIL import Image

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        require_r2_for_generation()
        update_job_progress(job_id, "generating_images", 0.0, "이미지 생성 준비 중...")

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)

        # CTA용 제품 이미지 스냅샷 로드 (있으면)
        product_image = None
        snapshot_path = os.path.join(job_dir, "product", "product.png")
        if job.product_image_id and os.path.exists(snapshot_path):
            try:
                product_image = Image.open(snapshot_path)
                product_image.load()  # 지연 로딩 방지
            except Exception:
                product_image = None

        keys = resolve_user_api_keys(db, job.user_id)

        try:
            await generate_all_images(
                job_id=job_id,
                lines=lines,
                style=job.style,
                storage_dir=job_dir,
                progress_callback=update_job_progress,
                api_key=keys["gemini"],
                product_image=product_image,
            )

            # R2 업로드
            from core.r2_storage import upload_job_files, is_r2_enabled
            if is_r2_enabled():
                ok = await upload_job_files(job_id, "images")
                if not ok:
                    raise RuntimeError("R2 이미지 업로드 실패")
                _update_r2_sync(job_id, "partial")

            update_job_progress(job_id, "preview_ready", 0.4, "이미지 생성 완료 - 미리보기 확인")

        except Exception as e:
            mark_job_failed(job_id, f"이미지 생성 실패: {str(e)}")
    finally:
        db.close()


async def generate_clips_for_job(job_id: str):
    """AI 영상 클립 생성 백그라운드 태스크 (fal.ai)"""
    from core.fal_video import generate_clips_batch

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        require_r2_for_generation()
        update_job_progress(job_id, "generating_clips", 0.25, "AI 영상 클립 생성 준비 중...")

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        clips_dir = os.path.join(job_dir, "clips")

        for i in range(len(lines)):
            await _ensure_r2_asset_local(job_id, os.path.join("images", f"img_{i:02d}.png"))
        images = sorted(glob.glob(os.path.join(job_dir, "images", "img_*.png")))
        if len(images) < len(lines):
            raise RuntimeError("AI 클립 생성에 필요한 이미지 파일이 부족합니다")

        # video_mode에서 모델 키 결정 (hailuo / wan)
        model_key = getattr(job, "video_mode", "hailuo") or "hailuo"
        keys = resolve_user_api_keys(db, job.user_id)

        try:
            await generate_clips_batch(
                images=images,
                output_dir=clips_dir,
                model_key=model_key,
                progress_callback=update_job_progress,
                job_id=job_id,
                api_key=keys["fal"],
            )

            from core.r2_storage import upload_job_files, is_r2_enabled
            if is_r2_enabled():
                ok = await upload_job_files(job_id, "clips")
                if not ok:
                    raise RuntimeError("R2 영상 클립 업로드 실패")

            update_job_progress(job_id, "clips_ready", 0.50, "AI 영상 클립 생성 완료 - 미리보기 확인")

        except Exception as e:
            mark_job_failed(job_id, f"AI 클립 생성 실패: {str(e)}")
    finally:
        db.close()


async def regenerate_clip_for_job(job_id: str, line_index: int):
    """단일 AI 영상 클립 재생성"""
    from core.fal_video import generate_video_clip

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        require_r2_for_generation()
        is_user_assets = getattr(job, "generation_mode", "ai_full") == "user_assets"
        lines = json.loads(job.script_json or "[]")
        if line_index < 0 or line_index >= len(lines):
            return

        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        image_path = os.path.join(job_dir, "images", f"img_{line_index:02d}.png")
        output_path = os.path.join(job_dir, "clips", f"clip_raw_{line_index:02d}.mp4")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        model_key = "veo_lite" if is_user_assets else (getattr(job, "video_mode", "hailuo") or "hailuo")
        keys = resolve_user_api_keys(db, job.user_id)

        try:
            await _ensure_r2_asset_local(job_id, os.path.join("images", f"img_{line_index:02d}.png"))
            if not os.path.exists(image_path):
                raise RuntimeError("변환할 이미지 파일이 없습니다")

            if is_user_assets:
                lines = _set_line_progress(db, job, line_index, "ai_clip", "converting", "AI 영상 변환 중")

            await generate_video_clip(
                image_path=image_path,
                output_path=output_path,
                model_key=model_key,
                api_key=keys["fal"],
            )

            if is_user_assets:
                lines = _set_line_progress(db, job, line_index, "ai_clip", "saving", "영상 저장 중")

            from core.r2_storage import upload_file as r2_upload, is_r2_enabled
            if is_r2_enabled():
                ok = await r2_upload(output_path, f"jobs/{job_id}/clips/clip_raw_{line_index:02d}.mp4")
                if not ok:
                    raise RuntimeError("R2 영상 업로드 실패")

            if is_user_assets:
                db.refresh(job)
                lines = json.loads(job.script_json or "[]")
                sources = json.loads(job.line_sources_json or "[]")
                if line_index < len(lines):
                    while len(sources) < len(lines):
                        sources.append("ai")
                    sources[line_index] = "clip"
                    mark_line_asset_ready(lines[line_index], bump_version=True)
                    job.line_sources_json = json.dumps(sources[: len(lines)], ensure_ascii=False)
                    job.script_json = json.dumps(lines, ensure_ascii=False)
                    db.commit()

        except Exception as e:
            if is_user_assets:
                try:
                    db.rollback()
                    db.refresh(job)
                    lines = json.loads(job.script_json or "[]")
                    if line_index < len(lines):
                        mark_line_asset_failed(lines[line_index], str(e), action="ai_clip")
                        job.script_json = json.dumps(lines, ensure_ascii=False)
                        db.commit()
                except Exception:
                    pass
            else:
                mark_job_failed(job_id, f"클립 재생성 실패: {str(e)}")
    finally:
        db.close()


async def render_video_for_job(job_id: str):
    """영상 조립 백그라운드 태스크"""
    from core.video_assembler import assemble_shorts

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        require_r2_for_generation()
        update_job_progress(job_id, "generating_tts", 0.4, "TTS 나레이션 생성 중...")

        lines = json.loads(job.script_json)
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)

        # ─── 줄별 자산 매니페스트 (카드 B에서만 사용) ───
        line_sources = None
        asset_paths = None
        if getattr(job, "generation_mode", "ai_full") == "user_assets":
            line_sources = json.loads(job.line_sources_json or "[]")
            if len(line_sources) != len(lines):
                # 안전 차원: 부족분은 'ai'로 채움
                line_sources = (line_sources + ["ai"] * len(lines))[: len(lines)]
            asset_paths = []
            for i, src in enumerate(line_sources):
                if src == "clip":
                    rel = os.path.join("clips", f"clip_raw_{i:02d}.mp4")
                    if not await _ensure_r2_asset_local(job_id, rel):
                        raise RuntimeError(f"{i + 1}번째 줄 영상 파일을 찾을 수 없습니다")
                    asset_paths.append(os.path.join(job_dir, rel))
                else:
                    rel = os.path.join("images", f"img_{i:02d}.png")
                    if not await _ensure_r2_asset_local(job_id, rel):
                        raise RuntimeError(f"{i + 1}번째 줄 이미지 파일을 찾을 수 없습니다")
                    asset_paths.append(os.path.join(job_dir, rel))

        # 이미지 파일 목록 (카드 A 호환 — Ken Burns 전 줄 경로)
        if line_sources is not None:
            # 카드 B: 줄 인덱스 0..N-1를 직접 순회. assembler가 asset_paths를 우선 사용.
            images = [
                os.path.join(job_dir, "images", f"img_{i:02d}.png")
                for i in range(len(lines))
            ]
        else:
            for i in range(len(lines)):
                await _ensure_r2_asset_local(job_id, os.path.join("images", f"img_{i:02d}.png"))
            images = sorted(glob.glob(os.path.join(job_dir, "images", "img_*.png")))
            if len(images) < len(lines):
                raise RuntimeError("영상 조립에 필요한 이미지 파일이 부족합니다")

        # BGM 파일 결정: 로컬 → R2 다운로드 폴백
        bgm_path = None
        if job.bgm_filename:
            # 1. 로컬에 있으면 그대로 사용 (개발 모드)
            local_bgm = os.path.join(settings.BGM_DIR, job.bgm_filename)
            if os.path.exists(local_bgm):
                bgm_path = local_bgm
            # 2. 없으면 R2에서 다운로드 (배포 모드)
            elif is_r2_enabled() and job.user_id:
                from core.r2_storage import download_file_sync
                import asyncio
                r2_key = f"bgm/{job.user_id}/{job.bgm_filename}"
                temp_bgm = os.path.join(job_dir, "temp", f"bgm_{job.bgm_filename}")
                ok = await asyncio.to_thread(download_file_sync, r2_key, temp_bgm)
                if ok:
                    bgm_path = temp_bgm
        if not bgm_path:
            bgm_files = glob.glob(os.path.join(settings.BGM_DIR, "*.mp3"))
            if bgm_files:
                bgm_path = bgm_files[0]

        # AI 클립이 있으면 경로 수집 (카드 A 전용)
        clips_dir = os.path.join(job_dir, "clips")
        if line_sources is None:
            if (getattr(job, "video_mode", "kenburns") or "kenburns") != "kenburns":
                for i in range(len(lines)):
                    await _ensure_r2_asset_local(job_id, os.path.join("clips", f"clip_raw_{i:02d}.mp4"))
            ai_clips = sorted(glob.glob(os.path.join(clips_dir, "clip_raw_*.mp4")))
            if (getattr(job, "video_mode", "kenburns") or "kenburns") != "kenburns" and len(ai_clips) < len(lines):
                raise RuntimeError("영상 조립에 필요한 AI 클립 파일이 부족합니다")
        else:
            ai_clips = None  # 카드 B는 매니페스트로 처리

        keys = resolve_user_api_keys(db, job.user_id)

        # 음성 단계에서 미리 생성한 TTS가 있으면 재사용
        prebuilt_tts = bool(getattr(job, "tts_session_id", None))

        config = {
            "job_dir": job_dir,
            "images": images,
            "lines": lines,
            "title": job.title,
            "title_line1": getattr(job, "title_line1", None),
            "title_line2": getattr(job, "title_line2", None),
            "video_mode": getattr(job, "video_mode", "kenburns") or "kenburns",
            "ai_clips": ai_clips if ai_clips else None,
            "line_sources": line_sources,
            "asset_paths": asset_paths,
            "tts_engine": job.tts_engine,
            "tts_speed": job.tts_speed,
            "voice_id": job.voice_id,
            "emotion": job.emotion,
            "prebuilt_tts": prebuilt_tts,
            "bgm_path": bgm_path,
            "bgm_volume": job.bgm_volume,
            "bgm_start_sec": job.bgm_start_sec or 0.0,
            "font_title": settings.FONT_TITLE,
            "font_sub": settings.FONT_SUB,
            "typecast_api_key": keys["typecast"],
        }

        try:
            video_path = await assemble_shorts(
                job_id=job_id,
                config=config,
                progress_callback=update_job_progress,
            )

            from core.r2_storage import upload_job_files, is_r2_enabled
            if is_r2_enabled():
                ok = await upload_job_files(job_id, "output")
                if not ok:
                    raise RuntimeError("R2 최종 영상 업로드 실패")
                from core.r2_storage import delete_job_intermediate_files
                await delete_job_intermediate_files(job_id)
                _update_r2_sync(job_id, "synced")

            set_video_path(job_id, video_path)

        except Exception as e:
            mark_job_failed(job_id, f"영상 조립 실패: {str(e)}")
    finally:
        db.close()


async def regenerate_image_for_job(job_id: str, line_index: int, korean_request: str = None, english_prompt: str = None):
    """단일 이미지 재생성 — 영어 프롬프트 직접 사용 또는 한글→영어 변환.

    카드 B(generation_mode=='user_assets')에서는 Job 전체 상태를 바꾸지 않고
    줄별 status만 갱신한다. 실패해도 다른 줄과 사용자 업로드 경로로 복구 가능.
    """
    from core.gemini_client import (
        generate_image,
        korean_to_nb2_prompt,
        PRODUCT_REFERENCE_PREFIX,
    )
    from core.user_assets_visual import line_text_hash
    from PIL import Image

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        require_r2_for_generation()
        is_user_assets = getattr(job, "generation_mode", "ai_full") == "user_assets"

        lines = json.loads(job.script_json)
        if line_index < 0 or line_index >= len(lines):
            return
        line = lines[line_index]
        job_dir = os.path.join(settings.STORAGE_DIR, job_id)
        output_path = os.path.join(job_dir, "images", f"img_{line_index:02d}.png")

        keys = resolve_user_api_keys(db, job.user_id)

        try:
            plan = {}
            line_plan = {}
            sources = _job_sources(job, len(lines))
            if is_user_assets:
                lines = _set_line_progress(db, job, line_index, "ai_image", "planning", "프롬프트 구성 중")
                if line_index < 0 or line_index >= len(lines):
                    return
                line = lines[line_index]
                plan, lines = await _ensure_user_assets_visual_plan(db, job, keys)
                if line_index < 0 or line_index >= len(lines):
                    return
                line = lines[line_index]
                sources = _job_sources(job, len(lines))
                line_plan = _plan_line_by_id(plan).get(str(line.get("line_id")), {})

            if english_prompt:
                prompt = english_prompt
            elif is_user_assets and not korean_request and line_plan:
                prompt = str(line_plan.get("image_prompt") or "").strip()
                if not prompt:
                    raise RuntimeError("카드 B visual plan에 이미지 프롬프트가 없습니다")
            else:
                prompt = await korean_to_nb2_prompt(
                    korean_request=korean_request or line["text"],
                    narration_text=line["text"],
                    api_key=keys["gemini"],
                )

            # 새 프롬프트를 script_json에 반영 (접두어 없는 원본만 저장)
            line["image_prompt"] = prompt
            if is_user_assets:
                line["visual_text_hash"] = line_text_hash(line.get("text") or "")
                if line_plan:
                    line["motion"] = line_plan.get("motion") or line.get("motion") or "zoom_in"
                    line["visual_anchor"] = line_plan.get("continuity_anchor")
                    line["visual_intent"] = line_plan.get("visual_intent")
                    line.pop("qa_status", None)
                    line.pop("qa_result", None)
                    line.pop("qa_retry_instruction", None)
            job.script_json = json.dumps(lines, ensure_ascii=False)
            db.commit()

            # CTA 라인이고 스냅샷 존재 시 호출 시점에만 접두어 + 참조 이미지 추가
            final_prompt = prompt
            refs = None
            reference_line_index = None
            if is_user_assets:
                ref, reference_line_index = _reference_image_for_anchor(
                    job_dir=job_dir,
                    lines=lines,
                    sources=sources,
                    plan=plan,
                    line_index=line_index,
                    anchor=line_plan.get("continuity_anchor") if line_plan else None,
                )
                if ref is not None:
                    refs = [ref]
                    line["reference_line_index"] = reference_line_index
                else:
                    line.pop("reference_line_index", None)
                job.script_json = json.dumps(lines, ensure_ascii=False)
                db.commit()
            else:
                is_last_line = (line_index == len(lines) - 1)
                snapshot_path = os.path.join(job_dir, "product", "product.png")
                if is_last_line and os.path.exists(snapshot_path):
                    try:
                        product_img = Image.open(snapshot_path)
                        product_img.load()
                        refs = [product_img]
                        final_prompt = PRODUCT_REFERENCE_PREFIX + prompt
                    except Exception:
                        pass

            if is_user_assets:
                lines = _set_line_progress(db, job, line_index, "ai_image", "generating", "이미지 생성 중")
                if line_index < len(lines):
                    line = lines[line_index]

            await generate_image(
                prompt=final_prompt,
                style=job.style,
                output_path=output_path,
                api_key=keys["gemini"],
                reference_images=refs,
            )

            # 사용자 업로드 영상이 있던 줄에 AI 이미지를 새로 생성한 경우 정리
            if is_user_assets:
                lines = _set_line_progress(db, job, line_index, "ai_image", "saving", "결과 저장 중")
                prev_clip = os.path.join(job_dir, "clips", f"clip_raw_{line_index:02d}.mp4")
                if os.path.exists(prev_clip):
                    try:
                        os.remove(prev_clip)
                    except Exception:
                        pass
                from core.r2_storage import delete_object as r2_delete
                await r2_delete(f"jobs/{job_id}/clips/clip_raw_{line_index:02d}.mp4")

                # 줄별 자산 출처와 상태 갱신
                sources = json.loads(job.line_sources_json or "[]")
                while len(sources) < len(lines):
                    sources.append("ai")
                sources[line_index] = "ai"
                job.line_sources_json = json.dumps(sources, ensure_ascii=False)
                job.script_json = json.dumps(lines, ensure_ascii=False)
                db.commit()
            else:
                job.status = "preview_ready"
                db.commit()

            from core.r2_storage import upload_file as r2_upload, is_r2_enabled
            if is_r2_enabled():
                ok = await r2_upload(output_path, f"jobs/{job_id}/images/img_{line_index:02d}.png")
                if not ok:
                    raise RuntimeError("R2 이미지 업로드 실패")

            if is_user_assets:
                db.refresh(job)
                lines = json.loads(job.script_json or "[]")
                if line_index < len(lines):
                    mark_line_asset_ready(lines[line_index], bump_version=True)
                    job.script_json = json.dumps(lines, ensure_ascii=False)
                    db.commit()

        except Exception as e:
            if is_user_assets:
                # 줄별 상태만 failed로 갱신
                try:
                    db.rollback()
                    db.refresh(job)
                    lines = json.loads(job.script_json or "[]")
                    if line_index < len(lines):
                        mark_line_asset_failed(lines[line_index], str(e), action="ai_image")
                    job.script_json = json.dumps(lines, ensure_ascii=False)
                    db.commit()
                except Exception:
                    pass
            else:
                mark_job_failed(job_id, f"이미지 재생성 실패: {str(e)}")
    finally:
        db.close()


async def regenerate_missing_images_for_job(job_id: str, line_indexes: list[int]):
    """카드 B 일괄 이미지 생성: 순서대로 생성해 같은 anchor의 이전 이미지를 reference로 쓸 수 있게 한다."""
    for line_index in line_indexes:
        await regenerate_image_for_job(job_id, line_index)
