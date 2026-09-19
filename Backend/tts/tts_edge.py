"""
TTS module powered by Microsoft Edge-TTS API.

Replaces local CPU-bound engine with a fully cloud-hosted API.
Features a charismatic Indian-English bilingual voice natively.
Latency target is immediate stream yield.
"""

from __future__ import annotations

import asyncio
import logging
import io
import time
import warnings
from collections import deque

import edge_tts
import numpy as np

from tts.config import EDGE_SPEECH_RATE
from metrics.provider_metrics import record_provider_metric

logger = logging.getLogger(__name__)
_tts_ttfb_samples = deque(maxlen=200)
_tts_total_samples = deque(maxlen=200)


def _percentile(values, percentile: float) -> float:
    """Return a simple nearest-rank percentile for small rolling samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def _record_tts_latency(
    *,
    provider: str,
    voice: str,
    ttfb_s: float,
    total_s: float,
    chunks: int,
    bytes_out: int,
) -> None:
    _tts_ttfb_samples.append(ttfb_s)
    _tts_total_samples.append(total_s)
    logger.info(
        "[TTS METRICS] provider=%s voice=%s ttfb_ms=%.1f total_ms=%.1f "
        "p50_total_ms=%.1f p95_total_ms=%.1f chunks=%d bytes=%d samples=%d",
        provider,
        voice,
        ttfb_s * 1000.0,
        total_s * 1000.0,
        _percentile(_tts_total_samples, 0.50) * 1000.0,
        _percentile(_tts_total_samples, 0.95) * 1000.0,
        chunks,
        bytes_out,
        len(_tts_total_samples),
    )
    record_provider_metric("tts_ttfb", provider, ttfb_s * 1000.0)
    record_provider_metric("tts_total", provider, total_s * 1000.0)

VOICE_CATALOG = {
    # Indian English & Hindi Neural Voices
    "neerja": "en-IN-NeerjaExpressiveNeural",
    "prabhat": "en-IN-PrabhatNeural",
    "swara": "hi-IN-SwaraNeural",
    "madhur": "hi-IN-MadhurNeural",
    "aarohi": "mr-IN-AarohiNeural",
    "manohar": "mr-IN-ManoharNeural",
    "jenny": "en-US-JennyNeural",
    "guy": "en-US-GuyNeural",
    "pallavi": "ta-IN-PallaviNeural",
    "shruti": "te-IN-ShrutiNeural",
    # New persona names → mapped to appropriate neural voices
    "divya": "en-IN-NeerjaExpressiveNeural",   # warm female Indian English
    "adhiti": "en-IN-NeerjaExpressiveNeural",   # warm female Indian English
    "aditi": "en-IN-NeerjaExpressiveNeural",    # warm female Indian English
    "rohit": "en-IN-PrabhatNeural",             # clear male Indian English
    "karan": "en-IN-PrabhatNeural",             # clear male Indian English
    "priya": "en-IN-NeerjaExpressiveNeural",    # legacy alias
    "neha": "en-IN-NeerjaExpressiveNeural",     # legacy alias
    "rani": "en-IN-NeerjaExpressiveNeural",     # legacy alias
}

VOICE_MAP = {
    "en": "en-IN-NeerjaExpressiveNeural",
    "hi": "hi-IN-SwaraNeural",
    "hinglish": "en-IN-NeerjaExpressiveNeural",  # Bilingual voice — handles both English and Hindi naturally
    "mr": "mr-IN-AarohiNeural"
}

DEFAULT_VOICE = VOICE_MAP["en"]


def _get_agent_name_for_voice(agent_id: str) -> str:
    """Look up the agent's display name from the DB or JSON schema for voice resolution."""
    if not agent_id or agent_id == "default":
        return ""
    try:
        import json as _json
        import os as _os
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        # 1. Fast SQLite lookup
        backend_dir = _Path(__file__).resolve().parent.parent
        db_path = backend_dir / "db" / "platform.db"
        if db_path.exists():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT name FROM agents WHERE id = ?", (agent_id,))
            row = cur.fetchone()
            conn.close()
            if row and row["name"]:
                return str(row["name"]).strip()

        # 2. JSON schema fallback
        json_path = backend_dir / "db" / "agents" / f"{agent_id}.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
                return data.get("agent_name", data.get("name", "")).strip()
    except Exception:
        pass
    return ""


def _resolve_voice(
    preferred_language: str | None = None,
    agent_id: str = "default",
    custom_voice: str | None = None,
) -> str:
    """Resolve distinct, persona-specific voice per agent."""
    # 1. Use explicit custom_voice if provided (e.g. from DB 'voice' field)
    if custom_voice and custom_voice.strip():
        cv = custom_voice.strip()
        # Direct Edge TTS neural voice name
        if "Neural" in cv:
            return cv
        cv_lower = cv.lower()
        for k, v in VOICE_CATALOG.items():
            if k in cv_lower:
                return v

    agent_key = (agent_id or "").strip().lower()

    # 2. Also try to resolve by agent name from DB (handles UUID-based agent_ids)
    agent_name = _get_agent_name_for_voice(agent_id).lower()

    # ── New voice personas (Divya, Adhiti, Rohit, Karan) ────────────────────
    # Divya / Adhiti / Aditi → warm expressive Indian English female
    if any(n in agent_name for n in ("divya", "adhiti", "aditi")):
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-SwaraNeural"
        elif preferred_language == "mr":
            return "mr-IN-AarohiNeural"
        return "en-IN-NeerjaExpressiveNeural"

    # Rohit / Karan → clear male Indian English voice
    if any(n in agent_name for n in ("rohit", "karan")):
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-MadhurNeural"
        elif preferred_language == "mr":
            return "mr-IN-ManoharNeural"
        return "en-IN-PrabhatNeural"

    # ── Legacy agent matching by agent_id UUID or name ───────────────────────
    # Indic-Test / Education Agent → Hindi Male/Female or Indian English Male
    if "indic" in agent_key or "3af81a07" in agent_key or "education" in agent_key or "indic" in agent_name:
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-MadhurNeural"
        elif preferred_language == "mr":
            return "mr-IN-ManoharNeural"
        return "en-IN-PrabhatNeural"  # Clear, articulate male Indian English voice

    # Neha → Warm Expressive Female
    if "neha" in agent_key or "8e812315" in agent_key or "ea2e03c2" in agent_key or "neha" in agent_name:
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-SwaraNeural"
        return "en-IN-NeerjaExpressiveNeural"

    # Rani / Real Estate → Distinct Female
    if "rani" in agent_key or "7cc26eed" in agent_key or "07a84293" in agent_key or "real_estate" in agent_key or "rani" in agent_name:
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-SwaraNeural"
        return "en-IN-NeerjaExpressiveNeural"

    # Integration Test Agent → default female
    if "integration" in agent_key or "integration" in agent_name:
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-SwaraNeural"
        return "en-IN-NeerjaExpressiveNeural"

    # Arjun / Finance → Executive Male Voice
    if "arjun" in agent_key or "finance" in agent_key or "arjun" in agent_name:
        if preferred_language in ("hi", "hinglish"):
            return "hi-IN-MadhurNeural"
        return "en-IN-PrabhatNeural"

    # Sarah / Recruitment → Western/Neutral Female Voice
    if "sarah" in agent_key or "recruitment" in agent_key or "sarah" in agent_name:
        return "en-US-JennyNeural"

    # Standard fallback by language
    if preferred_language == "mr":
        return VOICE_MAP["mr"]
    elif preferred_language in ("hi", "hinglish"):
        return VOICE_MAP["hi"]
    return DEFAULT_VOICE


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    agent_id: str = "default",
    custom_voice: str | None = None,
):
    """
    Synchronous wrapper that yields PCM16 bytes chunks sequentially.
    Uses language-aware and agent-aware voice selection for unique agent sounds.
    """
    if not text or not text.strip():
        yield b""
        return
    started_at = time.perf_counter()
    first_yield_at = None
    chunk_count = 0
    bytes_out = 0

    # Select distinct persona voice for this agent
    voice = _resolve_voice(preferred_language, agent_id, custom_voice)

    # ── Text normalisation for faster-paced speech ──────────────────────
    # Expand abbreviations and clean up text so the neural voice stays
    # clear even at an elevated speaking rate.
    try:
        from tts.speech_formatter import optimize_for_tts
        text = optimize_for_tts(text)
    except Exception:
        logger.debug("speech_formatter unavailable, using raw text")

    # Edge-TTS streams mp3 chunks usually. We must decode them to raw PCM16 for Pipecat/sounddevice.
    # We will use soundfile (libsndfile) to decode the mp3 payloads in memory.
    try:
        import soundfile as sf
    except ImportError:
        logger.error("soundfile not installed. Please `pip install soundfile`.")
        yield b""
        return

    try:
        logger.info(f"[TTS] Started synthesis for text length: {len(text)}, voice: {voice}")
        # Run asynchronously and collect stream blocks.
        communicate = edge_tts.Communicate(text, voice, rate=EDGE_SPEECH_RATE)
        
        async def _collect_mp3():
            try:
                # ── SSL CERT FIX ──────────────────────────────────────────────────────
                # When running inside uvicorn's ThreadPoolExecutor the module-level
                # edge_tts._SSL_CTX can become stale or use the system cert store
                # which may not include Microsoft's intermediate CA.  Recreate it from
                # certifi on every call so we always have a fresh, correct context.
                import ssl as _ssl
                try:
                    import certifi as _certifi
                    _fresh_ctx = _ssl.create_default_context(cafile=_certifi.where())
                    edge_tts._SSL_CTX = _fresh_ctx  # patch module-level ctx
                except Exception:
                    pass  # if certifi is unavailable, let edge_tts use its own ctx
                # ─────────────────────────────────────────────────────────────────────
                audio_buffer = bytearray()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_buffer.extend(chunk["data"])
                logger.info("[TTS] Edge TTS received mp3 stream of %d bytes", len(audio_buffer))
                return bytes(audio_buffer)
            except Exception as e:
                logger.exception("[TTS] Edge TTS _collect_mp3 failed internally: %s", e)
                return b""
        
        # Execute — always use asyncio.run() since this function is called
        # from a ThreadPoolExecutor where no event loop is running.
        mp3_bytes = asyncio.run(_collect_mp3())

        if not mp3_bytes:
            logger.error("[TTS] Edge TTS mp3_bytes is empty.")
            yield b""
            return

        # Decode MP3 to PCM using soundfile (which supports MP3 since v1.1.0)
        logger.info("[TTS] Decoding MP3 with soundfile...")
        try:
            with io.BytesIO(mp3_bytes) as mp3_file:
                # We explicitly specify the format to help soundfile
                data, samplerate = sf.read(mp3_file)
            logger.info("[TTS] Completed: Soundfile decoded MP3 to PCM. shape=%s samplerate=%d duration=%.2fs", data.shape, samplerate, len(data)/samplerate)
        except Exception as sfe:
            logger.exception("[TTS] ERROR: Soundfile sf.read failed internally! This often means libsndfile lacks MP3 support in the deployed OS. Error: %s", sfe)
            yield b""
            return

        # Force Mono if audio is stereo
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        # Apply a 50ms fade-in and fade-out to prevent audio pops/clicks at start and end
        fade_samples = int(samplerate * 0.05)
        if len(data) > fade_samples * 2:
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            data[:fade_samples] *= fade_in
            data[-fade_samples:] *= fade_out
        
        # Force to target 24000Hz for browser playback
        import scipy.signal
        import math
        target_sr = 24000
        if samplerate != target_sr:
            gcd = math.gcd(samplerate, target_sr)
            up = target_sr // gcd
            down = samplerate // gcd
            data = scipy.signal.resample_poly(data, up, down)

        # Clip values to prevent integer overflow distortion after resampling
        data = np.clip(data, -1.0, 1.0)

        # soundfile returns float arrays. Convert to PCM16.
        pcm16 = (data * 32767).astype(np.int16)
        pcm_bytes = pcm16.tobytes()

        # Yield in sensible chunks (e.g. 4096 bytes) for streaming
        chunk_size = 4096
        logger.info(f"[TTS] Audio Bytes Generated: {len(pcm_bytes)} bytes. Beginning chunking.")
        for i in range(0, len(pcm_bytes), chunk_size):
            chunk = pcm_bytes[i:i + chunk_size]
            if first_yield_at is None:
                first_yield_at = time.perf_counter()
            chunk_count += 1
            bytes_out += len(chunk)
            # logger.info("[TTS] Yielding PCM chunk %d, size %d", chunk_count, len(chunk))
            yield chunk
            
        logger.info(f"[TTS] Completed yielding {chunk_count} chunks, total bytes: {bytes_out}.")

        total_s = time.perf_counter() - started_at
        ttfb_s = (first_yield_at - started_at) if first_yield_at is not None else total_s
        _record_tts_latency(
            provider="edge",
            voice=voice,
            ttfb_s=ttfb_s,
            total_s=total_s,
            chunks=chunk_count,
            bytes_out=bytes_out,
        )

    except Exception as e:
        logger.exception("Edge TTS Cloud Generation failed: %s", e)
        yield b""
