"""Cloudflare R2 스토리지 — 업로드, 스트리밍, presigned URL"""

import os
import glob
import asyncio
import logging
from config import settings

logger = logging.getLogger(__name__)

_r2_client = None


def is_r2_enabled() -> bool:
    return bool(settings.R2_BUCKET_NAME)


def get_r2_client():
    global _r2_client
    if _r2_client is None:
        import boto3
        _r2_client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
    return _r2_client


# ── 업로드 ──

def _upload_sync(local_path: str, r2_key: str) -> bool:
    """동기 업로드 (asyncio.to_thread에서 호출)"""
    try:
        get_r2_client().upload_file(local_path, settings.R2_BUCKET_NAME, r2_key)
        return True
    except Exception as e:
        logger.warning(f"R2 upload failed: {r2_key} — {e}")
        return False


async def upload_file(local_path: str, r2_key: str, max_retries: int = 2) -> bool:
    """로컬 → R2 업로드 (비동기 + 재시도)"""
    if not is_r2_enabled() or not os.path.exists(local_path):
        return False

    for attempt in range(max_retries + 1):
        success = await asyncio.to_thread(_upload_sync, local_path, r2_key)
        if success:
            return True
        if attempt < max_retries:
            logger.info(f"R2 upload retry {attempt + 1}/{max_retries}: {r2_key}")
            await asyncio.sleep(1)

    logger.error(f"R2 upload failed after {max_retries + 1} attempts: {r2_key}")
    return False


async def upload_job_files(job_id: str, file_type: str) -> bool:
    """job의 파일 일괄 업로드. file_type: 'images', 'clips', 'output'"""
    if not is_r2_enabled():
        return False

    job_dir = os.path.join(settings.STORAGE_DIR, job_id)
    all_ok = True

    if file_type == "images":
        files = sorted(glob.glob(os.path.join(job_dir, "images", "img_*.png")))
        for f in files:
            r2_key = f"jobs/{job_id}/images/{os.path.basename(f)}"
            if not await upload_file(f, r2_key):
                all_ok = False

    elif file_type == "clips":
        files = sorted(glob.glob(os.path.join(job_dir, "clips", "clip_raw_*.mp4")))
        for f in files:
            r2_key = f"jobs/{job_id}/clips/{os.path.basename(f)}"
            if not await upload_file(f, r2_key):
                all_ok = False

    elif file_type == "output":
        output = os.path.join(job_dir, "output", "shorts_final.mp4")
        if os.path.exists(output):
            r2_key = f"jobs/{job_id}/output/shorts_final.mp4"
            if not await upload_file(output, r2_key):
                all_ok = False

    return all_ok


# ── 스트리밍 / 다운로드 ──

def download_file_sync(r2_key: str, local_path: str) -> bool:
    """R2 → 로컬 동기 다운로드 (워커에서 asyncio.to_thread로 호출)"""
    if not is_r2_enabled():
        return False
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        get_r2_client().download_file(settings.R2_BUCKET_NAME, r2_key, local_path)
        return True
    except Exception as e:
        logger.error(f"R2 download failed: {r2_key} — {e}")
        return False


def stream_from_r2(r2_key: str):
    """R2에서 직접 스트리밍 (StreamingResponse용 generator)"""
    try:
        resp = get_r2_client().get_object(Bucket=settings.R2_BUCKET_NAME, Key=r2_key)
        body = resp["Body"]
        while True:
            chunk = body.read(64 * 1024)  # 64KB chunks
            if not chunk:
                break
            yield chunk
    except Exception as e:
        logger.error(f"R2 stream failed: {r2_key} — {e}")
        return


def generate_presigned_url(r2_key: str, expires: int = None) -> str:
    """다운로드용 presigned URL 생성"""
    if expires is None:
        expires = settings.R2_PRESIGN_EXPIRE_SECONDS
    return get_r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET_NAME, "Key": r2_key},
        ExpiresIn=expires,
    )


def r2_file_exists(r2_key: str) -> bool:
    """R2에 파일 존재 확인"""
    try:
        get_r2_client().head_object(Bucket=settings.R2_BUCKET_NAME, Key=r2_key)
        return True
    except Exception:
        return False


# ── 객체 단위 복사/삭제 (카드 분할 시 인덱스 시프트용) ──

async def copy_object(src_key: str, dst_key: str) -> bool:
    """R2 객체 복사. is_r2_enabled() 거짓이면 True 반환 후 no-op."""
    if not is_r2_enabled():
        return True

    def _sync():
        client = get_r2_client()
        client.copy_object(
            Bucket=settings.R2_BUCKET_NAME,
            CopySource={"Bucket": settings.R2_BUCKET_NAME, "Key": src_key},
            Key=dst_key,
        )
        return True

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logger.warning(f"R2 copy_object 실패 {src_key} → {dst_key}: {e}")
        return False


async def delete_object(r2_key: str) -> bool:
    """R2 객체 단일 삭제. is_r2_enabled() 거짓이면 True 반환 후 no-op."""
    if not is_r2_enabled():
        return True

    def _sync():
        client = get_r2_client()
        client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=r2_key)
        return True

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logger.warning(f"R2 delete_object 실패 {r2_key}: {e}")
        return False


# ── 삭제 ──

async def delete_job_files(job_id: str):
    """특정 Job의 R2 파일 전체 삭제"""
    if not is_r2_enabled():
        return

    def _delete_sync():
        client = get_r2_client()
        prefix = f"jobs/{job_id}/"
        try:
            resp = client.list_objects_v2(Bucket=settings.R2_BUCKET_NAME, Prefix=prefix)
            objects = resp.get("Contents", [])
            if objects:
                client.delete_objects(
                    Bucket=settings.R2_BUCKET_NAME,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                )
        except Exception as e:
            logger.error(f"R2 delete failed for job {job_id}: {e}")

    await asyncio.to_thread(_delete_sync)
