"""제품 이미지 템플릿 CRUD — CTA 라인에 삽입할 사용자 제품 이미지 관리"""

import io
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from api.deps import get_approved_user
from api.models import UserProductResponse
from config import settings
from core.r2_storage import (
    is_r2_enabled,
    require_r2_for_generation,
    r2_file_exists,
    stream_from_r2,
    upload_file as r2_upload,
)
from db.database import get_db
from db.models import User, UserProduct

router = APIRouter(prefix="/api/products", tags=["products"])

ALLOWED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_SIZE = 10 * 1024 * 1024  # 10MB
MAX_LONG_SIDE = 1024  # 긴 변 리사이즈
MAX_PER_USER = 20


def _local_path(user_id: str, product_id: str) -> str:
    return os.path.join(settings.STORAGE_DIR, "user_products", user_id, f"{product_id}.png")


def _r2_key(user_id: str, product_id: str) -> str:
    return f"products/{user_id}/{product_id}.png"


@router.post("", response_model=UserProductResponse)
async def upload_product(
    file: UploadFile = File(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_approved_user),
):
    """제품 이미지 업로드 — 1024px 리사이즈 후 PNG로 저장"""
    try:
        require_r2_for_generation()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="제품명을 입력해주세요")
    if len(name) > 50:
        raise HTTPException(status_code=400, detail="제품명은 50자 이하로 입력해주세요")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="PNG, JPG, WebP, BMP 이미지만 업로드 가능합니다")

    count = db.query(UserProduct).filter(UserProduct.user_id == user.id).count()
    if count >= MAX_PER_USER:
        raise HTTPException(status_code=400, detail=f"제품은 최대 {MAX_PER_USER}개까지 등록 가능합니다")

    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="파일 크기는 10MB 이하만 가능합니다")

    try:
        img = Image.open(io.BytesIO(contents))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="유효한 이미지 파일이 아닙니다")

    # 긴 변 1024px로 리사이즈 (비율 유지)
    w, h = img.size
    if max(w, h) > MAX_LONG_SIDE:
        if w >= h:
            new_w = MAX_LONG_SIDE
            new_h = int(h * MAX_LONG_SIDE / w)
        else:
            new_h = MAX_LONG_SIDE
            new_w = int(w * MAX_LONG_SIDE / h)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # 레코드 먼저 만들고 ID로 파일 저장
    product = UserProduct(
        user_id=user.id,
        name=name,
        filename=file.filename or "product",
        r2_key="",
    )
    db.add(product)
    db.flush()  # id 확보, 아직 커밋 전

    local_path = _local_path(user.id, product.id)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    img.save(local_path, "PNG", optimize=True)

    if is_r2_enabled():
        r2_key = _r2_key(user.id, product.id)
        ok = await r2_upload(local_path, r2_key)
        if not ok:
            # R2 실패 시 롤백
            os.remove(local_path)
            db.rollback()
            raise HTTPException(status_code=500, detail="파일 업로드에 실패했습니다")
        product.r2_key = r2_key

    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=list[UserProductResponse])
async def list_products(
    db: Session = Depends(get_db),
    user: User = Depends(get_approved_user),
):
    """본인 소유 제품 목록 (최신순)"""
    products = db.query(UserProduct).filter(
        UserProduct.user_id == user.id
    ).order_by(UserProduct.created_at.desc()).all()
    return products


@router.delete("/{product_id}")
async def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_approved_user),
):
    """제품 삭제 (본인 소유만) — 로컬 + R2 + DB 전부 제거"""
    product = db.query(UserProduct).filter(UserProduct.id == product_id).first()
    if not product or product.user_id != user.id:
        raise HTTPException(status_code=404, detail="제품을 찾을 수 없습니다")

    local_path = _local_path(user.id, product.id)
    if os.path.exists(local_path):
        try:
            os.remove(local_path)
        except Exception:
            pass

    if is_r2_enabled() and product.r2_key:
        from core.r2_storage import get_r2_client
        try:
            get_r2_client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=product.r2_key)
        except Exception:
            pass

    db.delete(product)
    db.commit()
    return {"message": "제품이 삭제되었습니다"}


@router.get("/{product_id}/image")
async def get_product_image(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_approved_user),
):
    """제품 이미지 서빙 (로컬 우선, 없으면 R2)"""
    product = db.query(UserProduct).filter(UserProduct.id == product_id).first()
    if not product or product.user_id != user.id:
        raise HTTPException(status_code=404, detail="제품을 찾을 수 없습니다")

    local_path = _local_path(user.id, product.id)
    if os.path.exists(local_path):
        return FileResponse(local_path, media_type="image/png")

    if is_r2_enabled() and product.r2_key and r2_file_exists(product.r2_key):
        return StreamingResponse(stream_from_r2(product.r2_key), media_type="image/png")

    raise HTTPException(status_code=404, detail="제품 이미지 파일을 찾을 수 없습니다")
