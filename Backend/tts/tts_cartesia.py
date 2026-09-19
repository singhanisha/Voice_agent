"""Cartesia Sonic TTS adapter.

This adapter preserves the existing TTS engine contract:
    generate_speech_stream(text, preferred_language) -> Iterator[bytes]

It requests raw PCM16 little-endian audio at 24kHz mono and yields the raw
audio chunks directly to `RealEstateTTSProcessor._run_tts()`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import queue
import threading
import time
import uuid

import numpy as np
import websockets
from metrics.provider_metrics import record_provider_metric

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_FADE_SAMPLES = int(SAMPLE_RATE * 0.05)
_SENTINEL = object()

_DEFAULT_INDIAN_FEMALE_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"  # Hinglish Speaking Lady.


def _language_for(preferred_language: str | None) -> str:
    if preferred_language == "mr":
        return "mr"
    if preferred_language in ("hi", "hinglish"):
        return "hi"
    return "en"


def _voice_for(preferred_language: str | None, voice_id: str | None = None) -> str:
    if voice_id:
        return voice_id
    lang = _language_for(preferred_language)
    if lang == "mr":
        return os.getenv("CARTESIA_VOICE_ID_MR") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_INDIAN_FEMALE_VOICE_ID
    if lang == "hi":
        return os.getenv("CARTESIA_VOICE_ID_HI") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_INDIAN_FEMALE_VOICE_ID
    return os.getenv("CARTESIA_VOICE_ID_EN") or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_INDIAN_FEMALE_VOICE_ID


def _optimize_text(text: str) -> str:
    try:
        from tts.speech_formatter import optimize_for_tts

        return optimize_for_tts(text)
    except Exception:
        logger.debug("speech_formatter unavailable, using raw text")
        return text


def _append_version(endpoint: str, version: str) -> str:
    if "cartesia_version=" in endpoint:
        return endpoint
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}cartesia_version={version}"


async def _connect(endpoint: str, api_key: str):
    headers = {"X-API-Key": api_key}
    try:
        return await websockets.connect(
            endpoint,
            additional_headers=headers,
            ping_interval=None,
            close_timeout=1,
        )
    except TypeError:
        return await websockets.connect(
            endpoint,
            extra_headers=headers,
            ping_interval=None,
            close_timeout=1,
        )


def _safe_put(output_queue: queue.Queue, item) -> None:
    try:
        output_queue.put(item, timeout=0.5)
    except queue.Full:
        logger.warning("Cartesia TTS output queue full; dropping chunk")


async def _stream_cartesia(
    text: str,
    preferred_language: str | None,
    output_queue: queue.Queue,
    voice_id: str | None = None,
) -> None:
    api_key = os.getenv("CARTESIA_API_KEY", "").strip()
    if not api_key:
        logger.warning("CARTESIA_API_KEY not set. Cartesia TTS cannot run.")
        return

    version = os.getenv("CARTESIA_VERSION", "2026-03-01")
    endpoint = _append_version(os.getenv("CARTESIA_WS_URL", "wss://api.cartesia.ai/tts/websocket"), version)
    context_id = str(uuid.uuid4())
    language = _language_for(preferred_language)
    timeout_s = float(os.getenv("CARTESIA_TIMEOUT_SECONDS", "15.0"))
    selected_voice_id = _voice_for(preferred_language, voice_id)

    request = {
        "model_id": os.getenv("CARTESIA_MODEL_ID", "sonic-3.5"),
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": selected_voice_id,
            "__experimental_controls": {
                "speed": float(os.getenv("CARTESIA_SPEED", "0.88"))
            }
        },
        "language": language,
        "context_id": context_id,
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
        },
        "add_timestamps": False,
        "continue": False,
    }
    logger.info(
        "[TTS CARTESIA] model=%s voice_id=%s language=%s",
        request["model_id"],
        selected_voice_id,
        language,
    )

    websocket = await _connect(endpoint, api_key)
    try:
        await websocket.send(json.dumps(request))
        while True:
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8", errors="replace")
            message = json.loads(raw_message)
            message_type = message.get("type")

            if message_type == "chunk":
                data = base64.b64decode(message.get("data") or b"")
                if data:
                    _safe_put(output_queue, data)
            elif message_type == "error":
                raise RuntimeError(message.get("message") or message.get("title") or "Cartesia TTS error")

            if message.get("done") is True:
                break
    finally:
        await websocket.close()


def _run_producer(
    text: str,
    preferred_language: str | None,
    output_queue: queue.Queue,
    voice_id: str | None = None,
) -> None:
    try:
        asyncio.run(_stream_cartesia(text, preferred_language, output_queue, voice_id))
    except Exception as exc:
        logger.exception("Cartesia TTS generation failed: %s", exc)
    finally:
        _safe_put(output_queue, _SENTINEL)


def _apply_fade_in(chunk: bytes, faded_samples: int) -> tuple[bytes, int]:
    if not chunk or faded_samples >= _FADE_SAMPLES:
        return chunk, faded_samples
    if len(chunk) % 2:
        chunk = chunk[:-1]
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    fade_count = min(len(samples), _FADE_SAMPLES - faded_samples)
    if fade_count <= 0:
        return chunk, faded_samples
    start = faded_samples / float(_FADE_SAMPLES)
    stop = (faded_samples + fade_count) / float(_FADE_SAMPLES)
    samples[:fade_count] *= np.linspace(start, stop, fade_count, endpoint=False)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes(), faded_samples + fade_count


def _apply_fade_out(chunk: bytes) -> bytes:
    if not chunk:
        return chunk
    if len(chunk) % 2:
        chunk = chunk[:-1]
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    fade_count = min(len(samples), _FADE_SAMPLES)
    if fade_count <= 0:
        return chunk
    samples[-fade_count:] *= np.linspace(1.0, 0.0, fade_count)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    voice_id: str | None = None,
):
    """Yield PCM16 mono 24kHz chunks from Cartesia Sonic."""
    if not text or not text.strip():
        yield b""
        return

    optimized_text = _optimize_text(text)
    output_queue: queue.Queue = queue.Queue(maxsize=64)
    producer = threading.Thread(
        target=_run_producer,
        args=(optimized_text, preferred_language, output_queue, voice_id),
        daemon=True,
    )

    started_at = time.perf_counter()
    first_chunk_at = None
    chunk_count = 0
    bytes_out = 0
    faded_samples = 0
    previous_chunk = None

    producer.start()

    while True:
        item = output_queue.get()
        if item is _SENTINEL:
            break
        if not isinstance(item, (bytes, bytearray)) or not item:
            continue

        chunk, faded_samples = _apply_fade_in(bytes(item), faded_samples)
        if previous_chunk is not None:
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            chunk_count += 1
            bytes_out += len(previous_chunk)
            yield previous_chunk
        previous_chunk = chunk

    if previous_chunk:
        final_chunk = _apply_fade_out(previous_chunk)
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter()
        chunk_count += 1
        bytes_out += len(final_chunk)
        yield final_chunk

    total_s = time.perf_counter() - started_at
    ttfb_s = (first_chunk_at - started_at) if first_chunk_at is not None else total_s
    logger.info(
        "[TTS METRICS] provider=cartesia ttfb_ms=%.1f total_ms=%.1f chunks=%d bytes=%d",
        ttfb_s * 1000.0,
        total_s * 1000.0,
        chunk_count,
        bytes_out,
    )
    record_provider_metric("tts_ttfb", "cartesia", ttfb_s * 1000.0)
    record_provider_metric("tts_total", "cartesia", total_s * 1000.0)
