"""wav 파일을 분할 지점(초)에서 2조각으로 자르는 유틸.

계획서 R1에 따라 '메타만 저장'이 아닌 **실제 파일 분할 + 인덱스 재배치** 방식을
채택. 이유: audio_utils.speed_up_sentences / build_aligned_narration 이
`sent_{i:02d}.wav` 패턴을 순차 인덱스로 읽기 때문에, 파일 자체를 나눠
번호를 다시 매기는 게 기존 조립 코드를 건드리지 않는 최소 침습 방식이다.

pydub + ffmpeg 필요. 원본 wav는 그대로 두고 조각 wav만 새로 쓴다.
"""

import logging
import os

logger = logging.getLogger(__name__)


def _count_korean_syllables(text: str) -> int:
    """한국어 음절 수 대충 추정. 한글 음절 + 기타 문자는 0.5배로 계수.

    음성 합성 속도는 한글 음절 기준 ~0.15~0.18초/음절이므로, 분할 지점
    계산 시 이 추정치를 가중치로 사용한다.
    """
    count = 0
    for c in text:
        if '가' <= c <= '힣':
            count += 1
        elif c.isalnum():
            count += 0.5
    return max(1, int(count))


def calculate_split_point(
    wav_path: str,
    part1_text: str,
    part2_text: str,
) -> float:
    """분할 시각(초) 계산. 한글 음절 비율 기반 + 가장 가까운 침묵 구간 보정.

    pydub.silence.detect_silence 로 문장 내 자연스러운 pause 위치를 찾아
    음절 비율과 가장 가까운 지점을 고른다. 침묵이 없으면 순수 음절 비율.
    """
    from pydub import AudioSegment
    from pydub.silence import detect_silence

    audio = AudioSegment.from_wav(wav_path)
    total_sec = len(audio) / 1000.0

    s1 = _count_korean_syllables(part1_text)
    s2 = _count_korean_syllables(part2_text)
    ratio = s1 / (s1 + s2)
    estimated_cut = total_sec * ratio

    # pydub 침묵 감지 (50ms 이상, -40dBFS 미만)
    try:
        silences = detect_silence(audio, min_silence_len=50, silence_thresh=-40)
        # silences: [[start_ms, end_ms], ...] — 각 침묵 구간의 중심 시각(초)
        centers = [(a + b) / 2.0 / 1000.0 for a, b in silences]
        # 양 끝 침묵은 제외 (시작·종료 직전의 침묵은 분할 의미 없음)
        inner = [c for c in centers if 0.5 < c < total_sec - 0.5]
        if inner:
            # 음절 비율 예측치와 가장 가까운 침묵 중심 선택
            best = min(inner, key=lambda t: abs(t - estimated_cut))
            # 너무 멀면(±30% 이상) 침묵 무시하고 비율만 사용
            if abs(best - estimated_cut) < total_sec * 0.3:
                return round(best, 2)
    except Exception as e:
        logger.warning("[audio_splitter] 침묵 감지 실패, 비율만 사용: %s", e)

    return round(estimated_cut, 2)


def cut_wav_at(src_path: str, cut_sec: float, out_path_a: str, out_path_b: str):
    """src_path 의 wav 를 cut_sec 지점에서 2조각으로 나눠 저장."""
    from pydub import AudioSegment

    audio = AudioSegment.from_wav(src_path)
    cut_ms = int(cut_sec * 1000)
    part_a = audio[:cut_ms]
    part_b = audio[cut_ms:]
    part_a.export(out_path_a, format="wav")
    part_b.export(out_path_b, format="wav")


def get_wav_duration(wav_path: str) -> float:
    """wav 길이(초)."""
    from pydub import AudioSegment
    audio = AudioSegment.from_wav(wav_path)
    return len(audio) / 1000.0
