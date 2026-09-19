"""
Speech-to-Text module powered by Groq Cloud API.

Replaces local CPU-bound faster-whisper with lightning-fast cloud offloading.
Latency target is <200ms per audio chunk.
"""

import io
import logging
import time
import threading
import wave
import sys
from collections import deque

import numpy as np
import scipy.io.wavfile as wavfile
from groq import Groq, RateLimitError

from llm.config import GROQ_API_KEY
from . import config
from metrics.provider_metrics import record_provider_metric

logger = logging.getLogger(__name__)

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY not found. STT cannot start.")

_client = Groq(api_key=GROQ_API_KEY)

# ── Rate-limit cooldown (Fix #3) ─────────────────────────────────
# When Groq returns 429, we stop sending STT requests for 5 seconds.
# This prevents the API from being hammered and avoids consuming rate quota
# on audio chunks that will all fail anyway during the cooldown window.
_stt_rate_limited_until: float = 0.0
_stt_lock = threading.Lock()
_stt_latency_samples = deque(maxlen=200)


def _percentile(values, percentile: float) -> float:
    """Return a simple nearest-rank percentile for small rolling samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def _record_stt_latency(latency_s: float, *, provider: str = "groq") -> None:
    _stt_latency_samples.append(latency_s)
    p50 = _percentile(_stt_latency_samples, 0.50)
    p95 = _percentile(_stt_latency_samples, 0.95)
    logger.info(
        "[STT METRICS] provider=%s latest_ms=%.1f p50_ms=%.1f p95_ms=%.1f samples=%d",
        provider,
        latency_s * 1000.0,
        p50 * 1000.0,
        p95 * 1000.0,
        len(_stt_latency_samples),
    )
    record_provider_metric("stt_latency", provider, latency_s * 1000.0)

# ── Hallucination filter (Fix #7: foreign-language noise) ───────────────
_HALLUCINATIONS = {
    # Silence hallucination phrases from Whisper
    "thank you for watching.", "thanks for watching.", "thank you.", "thank you",
    "please subscribe.", "subscribe.", "if you have any questions, please let me know.",
    "i love you.", "(laughs)", "(sighs)", "[music]", "(music)", "subtitles by amara.org",
    "subtitles by", "amara.org", "watching", "you.", "you", "so", "the", "a", "an",
    # Devanagari prompt echo hallucinations on silence
    "झाल", "झालं", "सांगा", "सांगा ना", "बोला", "बोला ना",
    # Very common foreign-language hallucinations from multilingual Whisper
    "\uac10\uc0ac\ud569\ub2c8\ub2e4",     # Korean: gamsahamnida
    "\uac10\uc0ac\ud569\ub2c8\ub2e4.",    # Korean with period
    "mbc 뉴스 김성현입니다.",               # Korean: MBC News Kim Seong-hyun
    "mbc 뉴스 김성현입니다",
    "mbc 뉴스",
    "\u3042\u308a\u304c\u3068\u3046",      # Japanese: arigatou
    "\u3042\u308a\u304c\u3068\u3046\u3054\u3056\u3044\u307e\u3059",  # Japanese: arigatou gozaimasu
    "\u0634\u0643\u0631\u0627",            # Arabic: shukran
    "merci", "danke", "gracias", "obrigado",
    # Noise/silence markers
    ".", "..", "...", "um", "uh", "hmm", "mm", "ah", "oh",
}
_SHORT_VALID_UTTERANCES = {"hello", "hi", "yeah", "yes", "no", "ok", "okay", "thanks", "thank you", "bye", "goodbye", "sorry"}


def _safe_log_text(text: str) -> str:
    # Force ASCII-safe output for logging handlers bound to cp1252 consoles.
    return (text or "").encode("ascii", errors="replace").decode("ascii", errors="replace")


def _bytes_to_pcm16(audio_bytes: bytes) -> np.ndarray:
    """Convert raw audio bytes to a 16-bit PCM NumPy array.
    Supports WAV container, Float32, and raw PCM16 formats.
    """
    if audio_bytes[:4] == b"RIFF":
        sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
        if data.dtype == np.int16:
            return data
        if data.dtype == np.float32:
            return (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
        return (data.astype(np.float32) * 32767).astype(np.int16)

    # Always default to strict PCM16 encoding for raw payload (used by Next.js and frontend)
    return np.frombuffer(audio_bytes, dtype=np.int16)


def transcribe_audio(audio_chunk: bytes, language: str | None = None) -> str:
    """Transcribe a short audio chunk to text using Groq Cloud STT.

    Accepts raw audio bytes (16 kHz, mono) and routes it to `whisper-large-v3-turbo`.
    Features exponential backoff for 429 rate limits.
    """
    global _stt_rate_limited_until
    if not audio_chunk:
        return ""

    # Skip this chunk if we are in a rate-limit cooldown window
    with _stt_lock:
        cooldown = _stt_rate_limited_until
    if time.time() < cooldown:
        logger.debug("STT: In rate-limit cooldown, skipping chunk")
        return ""

    try:
        pcm_array = _bytes_to_pcm16(audio_chunk)
    except Exception:
        logger.warning("Failed to decode audio bytes.")
        return ""

    if pcm_array.size == 0:
        return ""

    # Generate an explicitly formatted WAV container strictly in-memory
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2) # 16-bit
        wf.setframerate(config.TARGET_SAMPLE_RATE)
        wf.writeframes(pcm_array.tobytes())
    
    buffer.name = "chunk.wav"  # Required by API for MIME type extraction

    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            t0 = time.perf_counter()
            buffer.seek(0)
            
            kwargs = {
                "file": (buffer.name, buffer.read()),
                "model": "whisper-large-v3-turbo",
                "response_format": "json",
                "prompt": (
                    "Indian real estate conversation. "
                    "User may speak Hindi, Marathi, English, or Hinglish. "
                    "Hindi words may include: mujhe, mera, meri, chahiye, "
                    "chahti, chahta, hai, hain, mein, se, ko, kya, kaise, "
                    "Jaipur, Pune, Mumbai, flat, ghar, property, lakh, lakhs, "
                    "crore, rupaye, budget. "
                    "Marathi words may include: mala, maza, majha, pahije, "
                    "ahe, aahe, kuthe, kiti. "
                    "Real estate terms: buy, rent, invest, budget, 1 BHK, "
                    "2 BHK, 3 BHK, 4 BHK, lakhs, crores."
                )
            }
            
            # Lock language ONLY if strictly set in config or passed from turn_state,
            # except when the language is Hinglish, where we allow auto-detection with code-mixing.
            active_lang = language or config.LANGUAGE
            if active_lang:
                if active_lang == "hinglish":
                    # Let auto-detect handle code-mixing with the Hinglish prompt
                    pass
                else:
                    kwargs["language"] = active_lang

            transcription = _client.audio.transcriptions.create(**kwargs)
            
            latency = time.perf_counter() - t0
            _record_stt_latency(latency)
            text = transcription.text.strip()
            raw_norm_text = text.lower().replace(" ", "").replace(".", "").replace(",", "").replace("!", "").replace("?", "")
            if raw_norm_text in _SHORT_VALID_UTTERANCES:
                logger.info("STT (Groq Cloud) accepted short utterance '%s' in %.3fs", _safe_log_text(text), latency)
                return text

            # ── Hallucination Filter (Fix #7) ───────────────────────────────────
            # Fuzzy-match common English + multilingual hallucinations
            norm_text = text.lower().replace(" ", "")
            is_hallucination = False
            for h in _HALLUCINATIONS:
                h_norm = h.lower().replace(" ", "")
                if h_norm == norm_text:
                    is_hallucination = True
                    break
            
            if is_hallucination:
                logger.info("STT (Groq Cloud) ignored hallucination: '%s'", _safe_log_text(text))
                return ""

            # Reject very short transcripts that are predominantly non-ASCII
            # (e.g. a 4-char Korean snippet on background noise)
            # EXCEPTION: Allow Devanagari script (Hindi/Marathi)
            if text:
                non_latin = sum(1 for ch in text if ch.isalpha() and not ch.isascii())
                if non_latin > 0:
                    # Check if it's Devanagari (Hindi/Marathi)
                    has_devanagari = any('\u0900' <= ch <= '\u097f' for ch in text)
                    if not has_devanagari and len(text) < 8 and non_latin / len(text) > 0.5:
                        logger.info("STT (Groq Cloud) rejected short non-Latin noise: '%s'", text.encode('ascii', 'replace').decode('ascii'))
                        return ""

            if text:
                safe_text = _safe_log_text(text)
                try:
                    logger.info("STT (Groq Cloud) produced '%s' in %.3fs", safe_text, latency)
                except UnicodeEncodeError:
                    logger.info("STT (Groq Cloud) produced '%s' in %.3fs", text.encode('ascii', 'replace').decode('ascii'), latency)
            return text

        except RateLimitError:
            if attempt < MAX_RETRIES - 1:
                wait_time = (2 ** attempt) + (time.time() % 0.5) # simple jitter
                logger.warning("STT rate limited (429) — retrying in %.1fs (attempt %d/%d)", wait_time, attempt+1, MAX_RETRIES)
                time.sleep(wait_time)
            else:
                # Enter a global 5-second cooldown window so we don't hammer the API after exhausting retries
                with _stt_lock:
                    _stt_rate_limited_until = time.time() + 5.0
                logger.error("STT rate limited (429) out of retries — entering 5s global cooldown. Chunk dropped.")
                return ""
        except Exception as e:
            logger.exception("Groq Transcription failed: %s", e)
            return ""

    return ""
