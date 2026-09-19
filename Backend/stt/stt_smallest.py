"""
stt_smallest.py — Smallest AI Pulse Pro Speech-to-Text Adapter.

File Path: Backend/stt/stt_smallest.py

Contract: transcribe_audio(audio_chunk: bytes, language: str | None) -> str

API Docs: https://docs.smallest.ai
Endpoint: POST https://api.smallest.ai/waves/v1/stt/
Auth:     Authorization: Bearer SMALLEST_API_KEY
"""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from collections import deque

import numpy as np
import scipy.io.wavfile as wavfile
import requests
from dotenv import load_dotenv

try:
    from metrics.provider_metrics import record_provider_metric
    _HAS_METRICS = True
except ImportError:
    _HAS_METRICS = False

load_dotenv()

logger = logging.getLogger(__name__)

SMALLEST_STT_ENDPOINT = "https://api.smallest.ai/waves/v1/stt/"
DEFAULT_MODEL = "pulse-pro"
TARGET_SAMPLE_RATE = 16_000  # 16 kHz mono expected by the API

_stt_latency_samples: deque[float] = deque(maxlen=200)

# ── Hallucination filter (mirrors stt_groq.py) ───────────────────────────────
_HALLUCINATIONS = {
    "thank you for watching.", "thanks for watching.", "thank you.", "thank you",
    "please subscribe.", "subscribe.",
    "i love you.", "(laughs)", "(sighs)",
    "झाल", "झालं", "सांगा", "सांगा ना", "बोला", "बोला ना",
    "\uac10\uc0ac\ud569\ub2c8\ub2e4", "\uac10\uc0ac\ud569\ub2c8\ub2e4.",
    "\u3042\u308a\u304c\u3068\u3046",
    "\u3042\u308a\u304c\u3068\u3046\u3054\u3056\u3044\u307e\u3059",
    "\u0634\u0643\u0631\u0627",
    "merci", "danke", "gracias", "obrigado",
    ".", "..", "...", "um", "uh", "hmm", "mm", "ah", "oh",
}
_SHORT_VALID_UTTERANCES = {
    "hello", "hi", "yeah", "yes", "no", "ok", "okay",
    "thanks", "thank you", "bye", "goodbye", "sorry",
}


def _percentile(values: deque, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def _record_stt_latency(latency_s: float) -> None:
    _stt_latency_samples.append(latency_s)
    p50 = _percentile(_stt_latency_samples, 0.50)
    p95 = _percentile(_stt_latency_samples, 0.95)
    logger.info(
        "[STT METRICS] provider=smallest latest_ms=%.1f p50_ms=%.1f p95_ms=%.1f samples=%d",
        latency_s * 1000.0,
        p50 * 1000.0,
        p95 * 1000.0,
        len(_stt_latency_samples),
    )
    if _HAS_METRICS:
        record_provider_metric("stt_latency", "smallest", latency_s * 1000.0)


def _safe_log_text(text: str) -> str:
    return (text or "").encode("ascii", errors="replace").decode("ascii", errors="replace")


def _bytes_to_pcm16(audio_bytes: bytes) -> np.ndarray:
    """Convert raw audio bytes to a 16-bit PCM NumPy array."""
    if audio_bytes[:4] == b"RIFF":
        sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
        if data.dtype == np.int16:
            return data
        if data.dtype == np.float32:
            return (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
        return (data.astype(np.float32) * 32767).astype(np.int16)
    return np.frombuffer(audio_bytes, dtype=np.int16)


def _build_wav_bytes(audio_chunk: bytes) -> bytes:
    """Wrap raw PCM16 bytes in a WAV container at 16 kHz mono."""
    try:
        pcm_array = _bytes_to_pcm16(audio_chunk)
    except Exception:
        logger.warning("[SmallestSTT] Failed to decode audio bytes.")
        return b""

    if pcm_array.size == 0:
        return b""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(TARGET_SAMPLE_RATE)
        wf.writeframes(pcm_array.tobytes())
    return buffer.getvalue()


def transcribe_audio(audio_chunk: bytes, language: str | None = None) -> str:
    """
    Transcribe a short audio chunk using Smallest AI Pulse Pro.

    Accepts raw audio bytes (16 kHz, mono PCM16 or WAV) and returns
    the transcribed text string.
    """
    if not audio_chunk:
        return ""

    api_key = os.getenv("SMALLEST_API_KEY", "").strip()
    if not api_key:
        logger.error(
            "[SmallestSTT] SMALLEST_API_KEY is not set. "
            "Get a key at https://smallest.ai"
        )
        return ""

    wav_bytes = _build_wav_bytes(audio_chunk)
    if not wav_bytes:
        return ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/octet-stream",
    }

    params: dict[str, str] = {"model": DEFAULT_MODEL}
    if language and language.lower() not in {"hinglish", "auto"}:
        # Map common names to ISO codes
        lang_map = {
            "english": "en", "hindi": "hi", "marathi": "mr",
            "tamil": "ta", "telugu": "te", "kannada": "kn",
            "malayalam": "ml", "bengali": "bn", "gujarati": "gu",
            "punjabi": "pa",
        }
        iso = lang_map.get(language.strip().lower(), language.strip().lower())
        params["language"] = iso

    t0 = time.perf_counter()
    try:
        response = requests.post(
            SMALLEST_STT_ENDPOINT,
            params=params,
            headers=headers,
            data=wav_bytes,
            timeout=8.0,
        )
        response.raise_for_status()

        latency = time.perf_counter() - t0
        _record_stt_latency(latency)

        data = response.json()
        # Smallest AI returns {"text": "...", ...}
        text = (data.get("text") or data.get("transcript") or "").strip()

        if not text:
            logger.debug("[SmallestSTT] API returned empty transcript.")
            return ""

        # Normalise for hallucination check
        raw_norm = text.lower().replace(" ", "").replace(".", "").replace(",", "").replace("!", "").replace("?", "")
        if raw_norm in _SHORT_VALID_UTTERANCES:
            logger.info("[SmallestSTT] Accepted short utterance '%s' in %.3fs", _safe_log_text(text), latency)
            return text

        norm_text = text.lower().replace(" ", "")
        for h in _HALLUCINATIONS:
            if h.lower().replace(" ", "") == norm_text:
                logger.info("[SmallestSTT] Ignored hallucination: '%s'", _safe_log_text(text))
                return ""

        logger.info("[SmallestSTT] Transcribed '%s' in %.3fs", _safe_log_text(text), latency)
        return text

    except requests.exceptions.HTTPError:
        logger.error(
            "[SmallestSTT] HTTP Error (%s): %s",
            response.status_code,
            response.text[:300],
        )
        return ""
    except requests.exceptions.Timeout:
        logger.error("[SmallestSTT] Request timed out after 8s.")
        return ""
    except requests.exceptions.RequestException as e:
        logger.error("[SmallestSTT] Request failed: %s", e)
        return ""
    except Exception as exc:
        logger.error("[SmallestSTT] Unexpected error: %s", exc, exc_info=True)
        return ""
