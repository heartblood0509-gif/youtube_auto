"""대본 생성 API"""

from fastapi import APIRouter, HTTPException
from api.models import ScriptRequest, ScriptResponse
import traceback

router = APIRouter(prefix="/api/generate", tags=["generate"])


@router.post("/script", response_model=ScriptResponse)
async def generate_script(request: ScriptRequest):
    """Gemini API로 제목 + 대본 + 이미지 프롬프트 + 모션 생성"""
    from core.gemini_client import generate_script as gen

    try:
        result = await gen(
            topic=request.topic,
            style=request.style.value,
            num_lines=request.num_lines,
        )
        return ScriptResponse(**result)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[ERROR] 대본 생성 실패:\n{tb}")
        raise HTTPException(status_code=500, detail=f"대본 생성 실패: {repr(e)}")
