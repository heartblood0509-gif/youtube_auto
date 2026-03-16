"""Gemini API 래퍼 - 텍스트 생성 + 나노바나나2 이미지 생성"""

from google import genai
from config import settings
import asyncio
import json
import os
import re
import time

_client = None

STYLE_SUFFIXES = {
    "realistic": "photorealistic, 8k, ultra-detailed, high resolution photography",
    "anime": "anime style, vibrant colors, Japanese animation, detailed illustration",
    "3d": "3D rendered, CGI, high quality 3D modeling, octane render",
    "illustration": "digital illustration, artistic, painterly style, concept art",
    "cinematic": "cinematic, dramatic lighting, film still, shallow depth of field",
}

MOTION_TYPES = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


async def generate_script(topic: str, style: str, num_lines: int = 6) -> dict:
    """
    Gemini로 제목 + 대본 + 이미지 프롬프트 + 모션 타입 일괄 생성.
    반환: {"title": "...", "lines": [{"text": "...", "image_prompt": "...", "motion": "..."}]}
    """
    client = get_client()
    style_desc = STYLE_SUFFIXES.get(style, style)

    prompt = f"""You are a YouTube Shorts script writer. Create a short-form video script about: "{topic}"

Rules:
1. Write a catchy, attention-grabbing Korean title (max 16 characters)
2. Write exactly {num_lines} narration lines in Korean. Each line should be 1 short sentence (max 25 characters).
3. For each narration line, create an English image generation prompt that visually represents that line.
   - The image style is: {style_desc}
   - Make prompts specific, visual, and descriptive
   - Each prompt should describe a single clear scene
   - Do NOT include any text/words/letters in the image prompts
   - Use vertical composition suitable for 9:16 aspect ratio
4. For each line, assign a camera motion type from: {MOTION_TYPES}
   - Vary the motions, do not repeat the same motion consecutively
   - Use zoom_in for dramatic reveals, zoom_out for establishing shots
   - Use pan_left/pan_right for horizontal movement scenes

Output ONLY valid JSON in this exact format:
{{
    "title": "제목",
    "lines": [
        {{"text": "나레이션 문장", "image_prompt": "English image description...", "motion": "zoom_in"}},
        ...
    ]
}}"""

    response = client.models.generate_content(
        model=settings.GEMINI_TEXT_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            temperature=0.8,
        ),
    )

    # 응답에서 JSON 추출 (마크다운 코드블록 제거)
    raw = response.text
    # 유니코드 특수문자 정규화 (em dash 등 → 일반 문자)
    raw = raw.replace('\u2014', '-').replace('\u2013', '-')
    raw = raw.replace('\u201c', '"').replace('\u201d', '"')
    raw = raw.replace('\u2018', "'").replace('\u2019', "'")
    raw = re.sub(r"^```json\s*\n?", "", raw.strip())
    raw = re.sub(r"\n?\s*```$", "", raw.strip())
    result = json.loads(raw)

    # 유효성 검증
    if "title" not in result or "lines" not in result:
        raise ValueError("Gemini 응답에 title 또는 lines가 없습니다")
    for line in result["lines"]:
        if line.get("motion") not in MOTION_TYPES:
            line["motion"] = "zoom_in"

    return result


async def generate_image(
    prompt: str,
    style: str,
    output_path: str,
    max_retries: int = 3,
) -> str:
    """
    나노바나나2 (Gemini 이미지 생성)로 이미지 생성.
    429 할당량 초과 시 자동 재시도 (최대 max_retries회).
    반환: 저장된 파일 경로
    """
    client = get_client()
    style_suffix = STYLE_SUFFIXES.get(style, "")
    full_prompt = f"{prompt}, {style_suffix}" if style_suffix else prompt

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=f"Generate an image: {full_prompt}",
                config=genai.types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                ),
            )

            # 이미지 추출 및 저장
            for part in response.parts:
                if part.inline_data is not None:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(part.inline_data.data)
                    return output_path

            raise RuntimeError(f"이미지 생성 실패: 응답에 이미지 없음")

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < max_retries:
                    wait = 30 * (attempt + 1)  # 30초, 60초, 90초
                    print(f"[RETRY] 할당량 초과, {wait}초 후 재시도 ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue
            raise

    raise RuntimeError(f"이미지 생성 실패: {prompt[:50]}...")


async def generate_all_images(
    job_id: str,
    lines: list[dict],
    style: str,
    storage_dir: str,
    progress_callback=None,
) -> list[str]:
    """대본의 모든 줄에 대해 이미지 생성. 반환: 이미지 경로 목록"""
    image_paths = []
    total = len(lines)

    for i, line in enumerate(lines):
        output_path = os.path.join(storage_dir, "images", f"img_{i:02d}.png")

        # 무료 티어 분당 요청 제한 방지: 이미지 간 15초 딜레이
        if i > 0:
            await asyncio.sleep(15)

        path = await generate_image(
            prompt=line["image_prompt"],
            style=style,
            output_path=output_path,
        )
        image_paths.append(path)

        if progress_callback:
            progress_callback(
                job_id=job_id,
                status="generating_images",
                progress=(i + 1) / total * 0.4,
                step=f"이미지 생성 중 ({i + 1}/{total})",
            )

    return image_paths
