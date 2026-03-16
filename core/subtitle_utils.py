"""자막 분리 유틸리티 (build_shorts.py에서 복사)"""

import re


def split_title(text, max_chars=8):
    """타이틀 2줄 분리 (8자 기준)"""
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    line1, line2 = "", ""
    for word in words:
        if not line1 or len(line1 + " " + word) <= max_chars:
            line1 = (line1 + " " + word).strip()
        else:
            line2 = (line2 + " " + word).strip()
    if not line2:
        mid = len(text) // 2
        line1 = text[:mid]
        line2 = text[mid:]
    return [line1, line2]


def split_subtitle_natural(timings):
    """TTS 타이밍 기반 한국어 구문 단위 자막 분할 (최대 12자)"""
    MAX_DISPLAY = 12

    def display_len(text):
        return len(re.sub(r'[?,!.~…·\'""]', "", text))

    def natural_split(text):
        if display_len(text) <= MAX_DISPLAY:
            return [text]
        comma_idx = text.find(",")
        if comma_idx > 0:
            p1 = text[: comma_idx + 1].strip()
            p2 = text[comma_idx + 1 :].strip()
            if p2:
                return natural_split(p1) + natural_split(p2)
        words = text.split(" ")
        if len(words) <= 1:
            return [text]
        best = None
        best_score = float("inf")
        for i in range(1, len(words)):
            p1 = " ".join(words[:i])
            p2 = " ".join(words[i:])
            l1, l2 = display_len(p1), display_len(p2)
            if l1 <= MAX_DISPLAY and l2 <= MAX_DISPLAY:
                score = abs(l1 - l2)
                if score < best_score:
                    best_score = score
                    best = (p1, p2)
        if best:
            return [best[0], best[1]]
        chunks = []
        current = ""
        for word in words:
            test = (current + " " + word).strip() if current else word
            if display_len(test) <= MAX_DISPLAY:
                current = test
            else:
                if current:
                    chunks.append(current)
                current = word
        if current:
            chunks.append(current)
        return chunks

    subs = []
    for t in timings:
        text = t["text"].rstrip(".")
        start = t["offset"]
        end = t["end"]
        duration = end - start
        chunks = natural_split(text)
        chunk_dur = duration / len(chunks) if chunks else duration
        for i, chunk in enumerate(chunks):
            cs = round(start + i * chunk_dur, 2)
            ce = round(start + (i + 1) * chunk_dur, 2)
            subs.append((cs, ce, chunk))
    return subs
