"""Deepgram REST STT adapter.

This module intentionally matches the legacy STT contract:
    transcribe_audio(audio_chunk: bytes) -> str

Input must already be PCM16 mono at 16kHz. The runtime processor owns VAD,
buffering, resampling, duplicate filtering, and barge-in behavior.
"""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from collections import deque

import httpx
import numpy as np
import scipy.io.wavfile as wavfile

from . import config
from metrics.provider_metrics import record_provider_metric

logger = logging.getLogger(__name__)

_latency_samples = deque(maxlen=200)

_HALLUCINATIONS = {
    "thank you.", "thank you", "you.", "you", "thanks.", "thanks",
    "thank you for watching.", "thanks for watching.", "please subscribe.",
    "subscribe.", "if you have any questions, please let me know.",
    "bye.", "bye", "bye bye.", "bye bye", "goodbye.", "goodbye",
    "i'm sorry.", "i'm sorry", "sorry.", "sorry",
    "i love you.", "i love you", "(laughs)", "(sighs)",
    "\uac10\uc0ac\ud569\ub2c8\ub2e4", "\uac10\uc0ac\ud569\ub2c8\ub2e4.",
    "\u3042\u308a\u304c\u3068\u3046",
    "\u3042\u308a\u304c\u3068\u3046\u3054\u3056\u3044\u307e\u3059",
    "\u0634\u0643\u0631\u0627",
    "merci", "danke", "gracias", "obrigado",
    ".", "..", "...", "um", "uh", "hmm", "mm", "ah", "oh",
}
_SHORT_VALID_UTTERANCES = {"hello", "hi", "yeah", "yes", "no", "ok", "okay"}


def _percentile(values, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def _safe_log_text(text: str) -> str:
    return (text or "").encode("ascii", errors="replace").decode("ascii", errors="replace")


def _record_latency(latency_s: float) -> None:
    _latency_samples.append(latency_s)
    logger.info(
        "[STT METRICS] provider=deepgram latest_ms=%.1f p50_ms=%.1f p95_ms=%.1f samples=%d",
        latency_s * 1000.0,
        _percentile(_latency_samples, 0.50) * 1000.0,
        _percentile(_latency_samples, 0.95) * 1000.0,
        len(_latency_samples),
    )
    record_provider_metric("stt_latency", "deepgram", latency_s * 1000.0)


def _bytes_to_pcm16(audio_bytes: bytes) -> np.ndarray:
    if audio_bytes[:4] == b"RIFF":
        _, data = wavfile.read(io.BytesIO(audio_bytes))
        if data.dtype == np.int16:
            return data
        if data.dtype == np.float32:
            return (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
        return (data.astype(np.float32) * 32767).astype(np.int16)
    return np.frombuffer(audio_bytes, dtype=np.int16)


def _pcm16_to_wav_bytes(audio_chunk: bytes) -> bytes:
    pcm_array = _bytes_to_pcm16(audio_chunk)
    if pcm_array.size == 0:
        return b""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.TARGET_SAMPLE_RATE)
        wf.writeframes(pcm_array.tobytes())
    return buffer.getvalue()


def _is_hallucination(text: str) -> bool:
    norm_text = (text or "").lower().replace(" ", "")
    return any(h.lower().replace(" ", "") == norm_text for h in _HALLUCINATIONS)


def _clean_transcript(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    raw_norm_text = text.lower().replace(" ", "").replace(".", "").replace(",", "").replace("!", "").replace("?", "")
    if raw_norm_text in _SHORT_VALID_UTTERANCES:
        return text

    if _is_hallucination(text):
        logger.info("STT (Deepgram REST) ignored hallucination: '%s'", _safe_log_text(text))
        return ""

    non_latin = sum(1 for ch in text if ch.isalpha() and not ch.isascii())
    if non_latin > 0:
        has_devanagari = any("\u0900" <= ch <= "\u097f" for ch in text)
        if not has_devanagari and len(text) < 8 and non_latin / len(text) > 0.5:
            logger.info("STT (Deepgram REST) rejected short non-Latin noise: '%s'", _safe_log_text(text))
            return ""

    return text


def _extract_transcript(payload: dict) -> str:
    try:
        alternatives = payload["results"]["channels"][0]["alternatives"]
        if not alternatives:
            return ""
        return (alternatives[0].get("transcript") or "").strip()
    except Exception:
        logger.warning("STT (Deepgram REST) response missing transcript fields: %s", payload)
        return ""


def transcribe_audio(audio_chunk: bytes, language: str | None = None) -> str:
    """Transcribe a VAD-finalized PCM16 mono 16kHz chunk through Deepgram REST."""
    if not audio_chunk:
        return ""

    api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY not set. Deepgram STT cannot run.")

    wav_bytes = _pcm16_to_wav_bytes(audio_chunk)
    if not wav_bytes:
        return ""

    active_lang = language or os.getenv("DEEPGRAM_LANGUAGE", "hi")
    if active_lang == "hinglish":
        active_lang = "hi"

    params = {
        "model": os.getenv("DEEPGRAM_MODEL", "nova-2-general"),
        "smart_format": "true",
        "punctuate": "true",
    }
    
    # H5 FIX: Ensure detect_language and language are mutually exclusive.
    if not language:
        params["detect_language"] = "true"
    else:
        params["language"] = active_lang
    timeout_s = float(os.getenv("DEEPGRAM_TIMEOUT_SECONDS", "3.0"))

    started_at = time.perf_counter()
    try:
        response = httpx.post(
            "https://api.deepgram.com/v1/listen",
            params=params,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "audio/wav",
            },
            content=wav_bytes,
            timeout=timeout_s,
        )
        response.raise_for_status()
        latency = time.perf_counter() - started_at
        _record_latency(latency)

        text = _clean_transcript(_extract_transcript(response.json()))
        if text:
            logger.info("STT (Deepgram REST) produced '%s' in %.3fs", _safe_log_text(text), latency)
        return text
    except Exception as exc:
        logger.exception("Deepgram REST transcription failed: %s", exc)
        raise
