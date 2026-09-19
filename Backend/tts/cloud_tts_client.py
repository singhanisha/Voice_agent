"""
cloud_tts_client.py — Premium TTS Client with Vocal Stability & Breath Injection

This module provides a high-quality streaming TTS layer compatible with the existing
RealEstateTTSProcessor._run_tts() contract (yields PCM16 bytes chunks).

Key features:
  - Vocal stability settings (stability=0.75, similarity_boost=0.85 for ElevenLabs;
    speed=0.92 for Cartesia — slightly slower = warmer, more grounded)
  - Automated text pre-processing: inject logical commas and ellipsis at natural
    breath points so the synthesiser introduces human-like pauses
  - Dual-path: ElevenLabs if ELEVENLABS_API_KEY is set, Cartesia otherwise
  - Async generator interface: `stream_tts_audio(text, language)` → AsyncGenerator[bytes]
  - Sync generator interface: `generate_speech_stream(text, language)` → Iterator[bytes]
    (drop-in replacement for tts_cartesia.generate_speech_stream)

Breath injection strategy:
  ● Transition / hedging words  → insert "..." after them
  ● Conjunctions joining clauses → insert "," before them
  ● Long sub-clauses (> 8 words) → insert "," at natural syllable break
  ● End of sentence              → preserved as-is
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from typing import AsyncGenerator, Iterator, Optional

import numpy as np
import websockets

logger = logging.getLogger("tts.cloud_tts_client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE_HZ = 24_000           # Cartesia / ElevenLabs PCM16 output rate
_FADE_SAMPLES = int(SAMPLE_RATE_HZ * 0.04)  # 40ms fade-in/out to prevent clicks
_SENTINEL = object()              # Queue EOF marker

""" # Cartesia defaults
_CARTESIA_SPEED = 0.90            # Steady, grounded vocal tone """

# Cartesia defaults
_CARTESIA_SPEED = 0.90            # Steady, grounded vocal tone

# Fallback Cartesia voice ID.
# Prefer language-specific env vars, then generic CARTESIA_VOICE_ID.
_DEFAULT_CARTESIA_VOICE_ID = os.getenv(
    "CARTESIA_VOICE_ID"
) or ""

# ElevenLabs voice settings — these directly map to their API schema
_ELEVENLABS_VOICE_SETTINGS = {
    "stability": 0.75,            # Lower = more expressive; 0.75 = calm but not monotone
    "similarity_boost": 0.85,     # High similarity to original voice cloning target
    "style": 0.25,                # Subtle expressiveness enhancement
    "use_speaker_boost": True,
}
_DEFAULT_ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
_ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")  # Fastest latency model

# ---------------------------------------------------------------------------
# Breath & Rhythm Injection
# ---------------------------------------------------------------------------

# Words after which an ellipsis "..." is injected to force a thinking pause
_HEDGE_WORDS = frozenset({
    "well", "actually", "so", "honestly", "basically", "honestly",
    "truthfully", "clearly", "naturally", "obviously", "right", "now",
    "look", "listen", "okay", "alright", "anyway",
})

# Conjunctions where a comma before them creates a natural breath
_CONJUNCTION_RE = re.compile(
    r"\b(and|but|or|because|so|though|although|whereas|while|since)\b",
    re.IGNORECASE,
)

# Sentence-internal clause break (no existing punctuation) — 8-word window
_LONG_CLAUSE_RE = re.compile(
    r"(\w[\w\s]{40,}?\w)(\s+(?:and|but|or|so|because|which|who|that)\b)",
    re.IGNORECASE,
)


def inject_breath_cues(text: str) -> str:
    """
    Insert natural pause markers into raw LLM text before sending to TTS.
    Also normalizes ALL-CAPS words (e.g. WOW -> Wow) to prevent letter spelling and pitch spikes.
    """
    if not text or not text.strip():
        return text

    # Normalize ALL-CAPS words (e.g. WOW -> Wow, REALLY -> really)
    try:
        from tts.speech_formatter import normalize_caps
        text = normalize_caps(text)
    except Exception:
        pass

    words = text.split()
    result: list[str] = []

    for i, word in enumerate(words):
        clean = word.rstrip(".,!?;:").lower()

        # Rule 1: hedge / transition word at start of sentence or after another sentence end → add gentle comma
        is_sentence_start = (i == 0 or (result and result[-1][-1] in (".", "!", "?")))
        if clean in _HEDGE_WORDS and is_sentence_start:
            # Strip trailing comma/period from the word itself, then append comma micro-pause
            result.append(word.rstrip(".,") + ",")
            continue

        # Rule 2: conjunction joining two clauses → prepend comma (no space before conjunction)
        if (
            _CONJUNCTION_RE.match(clean)
            and i > 0
            and result
            and not result[-1].endswith(",")
            and i < len(words) - 1  # not the last word
        ):
            result.append(word)
            # Add comma to the PREVIOUS word instead (cleaner TTS phrasing)
            result[-2] = result[-2].rstrip() + ","
            continue

        result.append(word)

    processed = " ".join(result)

    # Rule 3: long clause (> ~7 words) with a joining word — insert comma at junction
    processed = _LONG_CLAUSE_RE.sub(r"\1,\2", processed)

    # Clean up any double punctuation artifacts (avoid heavy ellipsis)
    processed = re.sub(r"([,]){2,}", r"\1", processed)
    processed = re.sub(r"(\.){3,}", r".", processed)
    processed = re.sub(r"\s+", " ", processed).strip()

    return processed


# ---------------------------------------------------------------------------
# Fade helpers (prevent audio pops)
# ---------------------------------------------------------------------------

def _apply_fade_in(chunk: bytes, faded: int) -> tuple[bytes, int]:
    if not chunk or faded >= _FADE_SAMPLES:
        return chunk, faded
    if len(chunk) % 2:
        chunk = chunk[:-1]
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    fade_n = min(len(samples), _FADE_SAMPLES - faded)
    if fade_n <= 0:
        return chunk, faded
    start = faded / _FADE_SAMPLES
    stop = (faded + fade_n) / _FADE_SAMPLES
    samples[:fade_n] *= np.linspace(start, stop, fade_n, endpoint=False)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes(), faded + fade_n


def _apply_fade_out(chunk: bytes) -> bytes:
    if not chunk:
        return chunk
    if len(chunk) % 2:
        chunk = chunk[:-1]
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    n = min(len(samples), _FADE_SAMPLES)
    if n > 0:
        samples[-n:] *= np.linspace(1.0, 0.0, n)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# Cartesia TTS path
# ---------------------------------------------------------------------------

def _language_code(preferred_language: str | None) -> str:
    if preferred_language == "mr":
        return "mr"
    if preferred_language in ("hi", "hinglish"):
        return "hi"
    return "en"


def _voice_id_for(preferred_language: str | None) -> str:
    lang = _language_code(preferred_language)
    if lang == "mr":
        return os.getenv("CARTESIA_VOICE_ID_MR") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_CARTESIA_VOICE_ID
    if lang == "hi":
        return os.getenv("CARTESIA_VOICE_ID_HI") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_CARTESIA_VOICE_ID
    return os.getenv("CARTESIA_VOICE_ID_EN") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_CARTESIA_VOICE_ID


async def _stream_cartesia_async(
    text: str,
    preferred_language: str | None,
    out_q: queue.Queue,
) -> None:
    """Drive a single Cartesia TTS request over WebSocket, placing PCM chunks in out_q."""
    api_key = os.getenv("CARTESIA_API_KEY", "").strip()
    if not api_key:
        logger.error("[CloudTTS-Cartesia] CARTESIA_API_KEY not set.")
        return

    version = os.getenv("CARTESIA_VERSION", "2026-03-01")
    ws_url = os.getenv("CARTESIA_WS_URL", "wss://api.cartesia.ai/tts/websocket")
    if "cartesia_version=" not in ws_url:
        sep = "&" if "?" in ws_url else "?"
        ws_url = f"{ws_url}{sep}cartesia_version={version}"

    context_id = str(uuid.uuid4())
    language = _language_code(preferred_language)
    selected_voice = _voice_id_for(preferred_language)

    # Cartesia vocal tone tuning: speed < 1.0 → warmer, less rushed delivery
    request = {
        "model_id": os.getenv("CARTESIA_MODEL_ID", "sonic-3.5"),
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": selected_voice,
            "experimental_controls": {
                "speed": _CARTESIA_SPEED,   # 0.92 → calm, grounded (stability analogue)
                "emotion": [],              # Neutral emotion — no artificial excitement
            },
        },
        "language": language,
        "context_id": context_id,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": SAMPLE_RATE_HZ,
        },
        "add_timestamps": False,
        "continue": False,
    }

    headers = {"X-API-Key": api_key}
    timeout_s = float(os.getenv("CARTESIA_TIMEOUT_SECONDS", "15.0"))

    try:
        try:
            ws = await websockets.connect(ws_url, additional_headers=headers, ping_interval=None, close_timeout=2)
        except TypeError:
            ws = await websockets.connect(ws_url, extra_headers=headers, ping_interval=None, close_timeout=2)

        async with ws:
            await ws.send(json.dumps(request))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                msg = json.loads(raw)

                if msg.get("type") == "chunk" and msg.get("data"):
                    out_q.put(base64.b64decode(msg["data"]))
                elif msg.get("type") == "error":
                    raise RuntimeError(msg.get("message") or "Cartesia error")

                if msg.get("done"):
                    break

    except Exception as exc:
        logger.error("[CloudTTS-Cartesia] Stream error: %s", exc)


def _run_cartesia_producer(text: str, language: str | None, out_q: queue.Queue) -> None:
    try:
        asyncio.run(_stream_cartesia_async(text, language, out_q))
    except Exception as exc:
        logger.exception("[CloudTTS-Cartesia] Producer thread error: %s", exc)
    finally:
        out_q.put(_SENTINEL)


# ---------------------------------------------------------------------------
# ElevenLabs TTS path
# ---------------------------------------------------------------------------

async def _stream_elevenlabs_async(
    text: str,
    preferred_language: str | None,
    out_q: queue.Queue,
) -> None:
    """
    Stream from ElevenLabs HTTP streaming API (SSE/chunked).
    Uses stability=0.75, similarity_boost=0.85 for warm, grounded vocal delivery.
    """
    import httpx  # lightweight HTTP client — already in most ML envs

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = _DEFAULT_ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    payload = {
        "text": text,
        "model_id": _ELEVENLABS_MODEL,
        "voice_settings": _ELEVENLABS_VOICE_SETTINGS,
        "output_format": "pcm_24000",
    }

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        out_q.put(chunk)
    except Exception as exc:
        logger.error("[CloudTTS-ElevenLabs] Stream error: %s", exc)
    finally:
        out_q.put(_SENTINEL)


def _run_elevenlabs_producer(text: str, language: str | None, out_q: queue.Queue) -> None:
    try:
        asyncio.run(_stream_elevenlabs_async(text, language, out_q))
    except Exception as exc:
        logger.exception("[CloudTTS-ElevenLabs] Producer thread error: %s", exc)
    finally:
        out_q.put(_SENTINEL)


# ---------------------------------------------------------------------------
# Public API — matches tts_cartesia.generate_speech_stream contract
# ---------------------------------------------------------------------------

def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    voice_id: str | None = None,
) -> Iterator[bytes]:
    """
    Synchronous generator yielding PCM16 mono 24kHz audio chunks.

    Drop-in replacement for `tts_cartesia.generate_speech_stream` used by
    `RealEstateTTSProcessor._run_tts()`.

    Pipeline:
    1. inject_breath_cues(text) — add natural pauses
    2. Route to ElevenLabs (if key set) or Cartesia
    3. Apply 40ms fade-in on first chunk, 40ms fade-out on last
    4. Yield raw PCM16 bytes
    """
    if not text or not text.strip():
        yield b""
        return

    # ── Step 1: breath injection ───────────────────────────────────────
    processed_text = inject_breath_cues(text.strip())
    logger.debug("[CloudTTS] Original: %r → Processed: %r", text[:80], processed_text[:80])

    # ── Step 2: route to provider ──────────────────────────────────────
    use_elevenlabs = bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    out_q: queue.Queue = queue.Queue(maxsize=128)

    if use_elevenlabs:
        producer_fn = _run_elevenlabs_producer
        logger.info("[CloudTTS] Provider: ElevenLabs (stability=0.75 similarity=0.85)")
    else:
        producer_fn = _run_cartesia_producer
        logger.info("[CloudTTS] Provider: Cartesia (speed=%.2f)", _CARTESIA_SPEED)

    producer = threading.Thread(
        target=producer_fn,
        args=(processed_text, preferred_language, out_q),
        daemon=True,
    )

    started_at = time.perf_counter()
    first_chunk_at: Optional[float] = None
    faded_in = 0
    prev_chunk: Optional[bytes] = None
    chunk_count = 0
    bytes_total = 0

    producer.start()

    # ── Step 3 & 4: yield with fade ────────────────────────────────────
    while True:
        item = out_q.get()
        if item is _SENTINEL:
            break
        if not isinstance(item, (bytes, bytearray)) or not item:
            continue

        chunk, faded_in = _apply_fade_in(bytes(item), faded_in)

        if prev_chunk is not None:
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            chunk_count += 1
            bytes_total += len(prev_chunk)
            yield prev_chunk

        prev_chunk = chunk

    # Flush final chunk with fade-out
    if prev_chunk:
        final = _apply_fade_out(prev_chunk)
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter()
        chunk_count += 1
        bytes_total += len(final)
        yield final

    elapsed = time.perf_counter() - started_at
    ttfb = (first_chunk_at - started_at) if first_chunk_at else elapsed
    logger.info(
        "[CloudTTS] ttfb=%.0fms total=%.0fms chunks=%d bytes=%d provider=%s",
        ttfb * 1000,
        elapsed * 1000,
        chunk_count,
        bytes_total,
        "elevenlabs" if use_elevenlabs else "cartesia",
    )


async def stream_tts_audio(
    text: str,
    preferred_language: str | None = None,
) -> AsyncGenerator[bytes, None]:
    """
    Async generator interface — wraps the sync generator for use in async contexts
    (e.g. future fully-async pipeline refactors).

    Usage:
        async for chunk in stream_tts_audio(reply, language="en"):
            await websocket.send_bytes(chunk)
    """
    loop = asyncio.get_running_loop()
    # Run the blocking sync generator in a thread
    gen = generate_speech_stream(text, preferred_language)
    while True:
        try:
            chunk = await loop.run_in_executor(None, next, gen)
            yield chunk
        except StopIteration:
            break
        except Exception as exc:
            logger.error("[CloudTTS] async wrapper error: %s", exc)
            break
