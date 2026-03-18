"""TTS 엔진 3종 통합 (Edge TTS / Typecast / Qwen)"""

import asyncio
import json
import os
import time

from core.audio_utils import (
    extract_sentence_from_warmup,
    trim_trailing_silence,
    apply_fade,
)


async def generate_tts_edge(tts_dir, sentences):
    """
    Edge TTS로 나레이션 생성 (무료, 빠름).
    반환: (narration_path, timings)
    """
    import edge_tts

    text = " ".join(sentences)
    voice = "ko-KR-InJoonNeural"
    communicate = edge_tts.Communicate(text, voice, rate="+20%")
    sent_timings = []
    mp3_path = os.path.join(tts_dir, "narration.mp3")
    with open(mp3_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                sent_timings.append(
                    {
                        "text": chunk["text"],
                        "offset": chunk["offset"] / 10_000_000,
                        "duration": chunk["duration"] / 10_000_000,
                        "end": (chunk["offset"] + chunk["duration"]) / 10_000_000,
                    }
                )
    return mp3_path, sent_timings


def generate_tts_typecast(tts_dir, sentences):
    """
    Typecast API TTS (고품질 한국어).
    반환: raw_timings (문장별 duration 목록)
    """
    import requests
    import soundfile as sf
    from config import settings

    api_key = settings.TYPECAST_API_KEY
    if not api_key:
        raise RuntimeError("TYPECAST_API_KEY가 설정되지 않았습니다")

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    raw_timings = []

    for i, sent in enumerate(sentences):
        payload = {
            "text": sent,
            "voice_id": "tc_62e8f21e979b3860fe2f6a24",
            "model": "ssfm-v30",
            "output": {"format": "wav", "sample_rate": 44100, "tempo": 1.1},
        }
        resp = requests.post(
            "https://api.typecast.ai/v1/text-to-speech",
            headers=headers,
            json=payload,
        )
        out_path = os.path.join(tts_dir, f"sent_{i:02d}.wav")

        content_type = resp.headers.get("Content-Type", "")
        if "audio" in content_type or "octet-stream" in content_type:
            with open(out_path, "wb") as f:
                f.write(resp.content)
        else:
            result = resp.json()
            speak_url = result.get("result", {}).get("speak_v2_url")
            if speak_url:
                for _ in range(30):
                    time.sleep(2)
                    poll = requests.get(speak_url, headers=headers)
                    if poll.status_code == 200:
                        data = poll.json()
                        if data.get("result", {}).get("status") == "done":
                            audio_url = data["result"].get(
                                "audio_download_url"
                            ) or data["result"].get("audio_url")
                            if audio_url:
                                with open(out_path, "wb") as f:
                                    f.write(requests.get(audio_url).content)
                            break

        wav, sr = sf.read(out_path)
        duration = len(wav) / sr
        raw_timings.append({"text": sent, "duration": round(duration, 2)})
        time.sleep(0.3)

    with open(os.path.join(tts_dir, "timings_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw_timings, f, ensure_ascii=False, indent=2)

    return raw_timings


def generate_tts_qwen(tts_dir, sentences):
    """
    Qwen3-TTS 워밍업 접두어 방식 (로컬, Apple Silicon MPS).
    반환: raw_timings (문장별 duration 목록)
    """
    import torch
    import soundfile as sf
    import numpy as np
    from qwen_tts import Qwen3TTSModel

    torch.manual_seed(42)
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        torch.mps.manual_seed(42)

    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        device_map="mps",
        dtype=torch.float32,
    )

    raw_timings = []
    # 경고: 이 instruct를 변경하면 중국어 억양이 발생함
    instruct = "반드시 한국어 발음으로만 읽어주세요. 외래어도 한국식으로 발음하세요"
    warmup_prefix = "음. "

    for i, sent in enumerate(sentences):
        warmup_text = warmup_prefix + sent
        wavs, sr = model.generate_custom_voice(
            text=warmup_text,
            language="Korean",
            speaker="Sohee",
            instruct=instruct,
        )

        raw_wav = wavs[0]
        wav = extract_sentence_from_warmup(raw_wav, sr)
        wav = trim_trailing_silence(wav, sr)
        wav = apply_fade(wav, sr)

        duration = len(wav) / sr
        sent_path = os.path.join(tts_dir, f"sent_{i:02d}.wav")
        sf.write(sent_path, wav, sr)

        raw_timings.append({"text": sent, "duration": round(duration, 2)})

    with open(os.path.join(tts_dir, "timings_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw_timings, f, ensure_ascii=False, indent=2)

    return raw_timings
