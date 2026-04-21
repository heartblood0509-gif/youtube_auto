"""긴 나레이션 줄을 2조각으로 분리하는 유틸.

용도: `promo_comment` 콘텐츠에서 TTS duration이 6초를 초과하는 줄을
veo 3.1 lite(6초/클립) 제약에 맞게 2조각으로 나눠야 한다.

전략 (사용자 합의):
- Gemini에 "자연스러운 의미 단위로 2조각으로 나눠줘" 요청 (1순위)
- 실패 시 → 마침표·쉼표 위치 중 중앙에 가장 가까운 지점에서 분할 (폴백)
- 구두점도 없으면 → 공백 중앙 분할 (최후 폴백)

재귀 분리 없음. 실측상 한 줄은 12초를 넘지 않아 1회 분할로 충분 (각 조각 ≤6초).
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

_PUNCTUATION = re.compile(r'[.!?,、。！？，]')


def detect_overlong_lines(durations: list[float], threshold: float = 6.0) -> list[int]:
    """duration이 threshold(초) 초과인 줄의 인덱스 목록 반환."""
    return [i for i, d in enumerate(durations) if d > threshold]


def split_by_punctuation(line: str) -> list[str]:
    """마침표·쉼표 위치 중 문장 길이 중앙에 가장 가까운 지점에서 2분할.
    구두점이 없으면 공백 중앙 분할. 단어 경계만 보장한다."""
    midpoint = len(line) // 2

    # 모든 구두점 위치 수집 (구두점 바로 뒤 공백까지 포함한 경계로 자른다)
    punct_positions = [m.end() for m in _PUNCTUATION.finditer(line)]
    # 너무 앞/뒤에 붙은 구두점은 의미 있는 분할 아님 → 중앙 ±30% 범위만 허용
    min_pos = int(len(line) * 0.2)
    max_pos = int(len(line) * 0.8)
    valid = [p for p in punct_positions if min_pos <= p <= max_pos]

    if valid:
        cut = min(valid, key=lambda p: abs(p - midpoint))
        first, second = line[:cut].strip(), line[cut:].strip()
        if first and second:
            return [first, second]

    # 구두점 폴백 실패 → 공백 중앙
    space_positions = [i for i, c in enumerate(line) if c == ' ']
    valid_spaces = [p for p in space_positions if min_pos <= p <= max_pos]
    if valid_spaces:
        cut = min(valid_spaces, key=lambda p: abs(p - midpoint))
        first, second = line[:cut].strip(), line[cut + 1:].strip()
        if first and second:
            return [first, second]

    # 공백도 없으면 그대로 중앙 잘라내기 (마지막 안전망)
    return [line[:midpoint].strip() or line[:1], line[midpoint:].strip() or line[-1:]]


async def split_long_line_with_gemini(
    line: str,
    topic: str,
    style: str,
    api_key: str,
) -> list[str] | None:
    """Gemini에 자연스러운 2조각 분할을 요청. 실패/부적절 응답 시 None 반환.

    `api_key`는 **사용자 키가 우선** (CLAUDE.md 규칙: 사용자 키 폴백 금지).
    호출자가 resolve_user_api_keys로 해석한 키를 명시적으로 넘겨야 한다.
    """
    if not api_key:
        logger.warning("[line_splitter] api_key 없음 → 폴백 사용")
        return None

    try:
        from google import genai
        from config import settings

        client = genai.Client(api_key=api_key)
        prompt = f"""다음 한국어 나레이션 한 줄이 6초를 넘겨 영상 한 컷에 담을 수 없습니다.
이 줄을 **의미 단위로 자연스러운 2조각**으로 나눠주세요.

규칙:
- 정확히 2조각 (그 이상 금지)
- 각 조각이 독립적으로 의미가 통하고 자연스럽게 이어져야 함
- 원문 텍스트를 **수정하지 말고** 분할 위치만 정함 (단어 삭제·추가 금지)
- 분할 전·후 조각의 길이가 비슷할수록 좋음 (한쪽이 너무 짧으면 피함)

주제: {topic}
스타일: {style}
원문: {line}

반드시 JSON 형식으로 응답:
{{"parts": ["첫 번째 조각", "두 번째 조각"]}}"""

        response = client.models.generate_content(
            model=settings.GEMINI_TEXT_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text)
        parts = data.get("parts")
        if not isinstance(parts, list) or len(parts) != 2:
            logger.warning("[line_splitter] parts 구조 이상 → 폴백: %s", data)
            return None
        first, second = parts[0].strip(), parts[1].strip()
        if not first or not second:
            logger.warning("[line_splitter] 빈 조각 발생 → 폴백")
            return None
        return [first, second]
    except Exception as e:
        logger.warning("[line_splitter] Gemini 분할 실패 → 폴백: %s", e)
        return None
