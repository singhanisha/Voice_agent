"""
STT configuration constants.

All tunable parameters for the faster-whisper speech-to-text pipeline.
Change values here to adjust model size, quantization, VAD behaviour,
and language detection without touching any logic code.
"""

import os

from typing import Optional

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


LANGUAGE: Optional[str] = None     # None = auto-detect (Hindi / Marathi / English)

# Runtime buffering tuned for ultra-low-latency voice calls
TARGET_SAMPLE_RATE: int = 16000
MIN_CHUNK_MS: int = _env_int("STT_MIN_CHUNK_MS", 100)          # 100ms — halved from 250ms for faster first dispatch
MAX_CHUNK_MS: int = _env_int("STT_MAX_CHUNK_MS", 600)          # 600ms cap
TRAILING_SILENCE_MS: int = _env_int("STT_TRAILING_SILENCE_MS", 180)  # 180ms — faster turn trigger
SILENCE_RMS_THRESHOLD: float = _env_float("STT_SILENCE_RMS_THRESHOLD", 0.003)
MIN_TRANSCRIPT_CHARS: int = 1
DUPLICATE_TEXT_WINDOW_S: float = _env_float("STT_DUPLICATE_TEXT_WINDOW_S", 3.0)
ENERGY_THRESHOLD: float = _env_float("STT_ENERGY_THRESHOLD", 0.0015)
MIN_VOICE_START_MS: int = _env_int("STT_MIN_VOICE_START_MS", 120)    # 120ms — down from 200ms
POST_TTS_STT_COOLDOWN_MS: int = _env_int("POST_TTS_STT_COOLDOWN_MS", 120)  # 120ms — down from 180ms
