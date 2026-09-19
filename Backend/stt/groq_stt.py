"""Groq Whisper STT - ultra-low-latency transcription (~30-80ms)."""
from __future__ import annotations
import asyncio
import io
import logging
import os
import time
import wave
import re as _re

logger = logging.getLogger("stt.groq_whisper")
_MIN_AUDIO_BYTES = 16000
_DEVANAGARI_RE = _re.compile(r"[\u0900-\u097F]")
_WHISPER_ARTIFACTS = frozenset({
    "thank you", "thanks for watching", "thanks for listening", "you",
    ".", "..", "...", "the", "[music]", "[applause]", "subscribe", "bye",
    "okay", "ok", "hmm", "um", "uh", "you.",
})


def _pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw PCM16-LE mono bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _sanitize(text: str) -> str:
    """Drop known Whisper hallucinations and Devanagari fragments."""
    if not text:
        return ""
    stripped = text.strip().lower().rstrip(".")
    if stripped in _WHISPER_ARTIFACTS:
        logger.warning("[GroqSTT] Artifact dropped: %s", text)
        return ""
    if len(text.split()) < 3 and _DEVANAGARI_RE.search(text):
        logger.warning("[GroqSTT] Devanagari hallucination dropped: %s", text)
        return ""
    return text.strip()


class GroqSTT:
    """
    Async STT backed by Groq Whisper large-v3-turbo.
    Latency: 30-80ms vs 800-2000ms for local IndicSeamless (20x faster).
    Hard-locked to English output to prevent Hindi hallucinations.
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "")
        if not self.api_key:
            logger.error("[GroqSTT] GROQ_API_KEY not set.")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from groq import AsyncGroq
            self._client = AsyncGroq(api_key=self.api_key)
        return self._client

    async def async_transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe PCM16-LE mono bytes. Returns empty string on silence/error."""
        if not audio_bytes or len(audio_bytes) < _MIN_AUDIO_BYTES:
            return ""
        start = time.perf_counter()
        try:
            wav_bytes = _pcm16_to_wav(audio_bytes, sample_rate=sample_rate)
            client = self._get_client()
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.wav", wav_bytes, "audio/wav"),
                language="en",
                response_format="text",
                temperature=0.0,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            transcript = _sanitize((response or "").strip())
            logger.info(
                "[GroqSTT] %d bytes in %.0fms -> %r",
                len(audio_bytes), elapsed_ms, transcript,
            )
            return transcript
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error("[GroqSTT] Failed after %.0fms: %s", elapsed_ms, exc)
            return ""

    def transcribe_audio_chunk(self, audio_bytes: bytes, target_lang: str = "eng") -> str:
        """Synchronous adapter for legacy callers."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(
                        asyncio.run, self.async_transcribe(audio_bytes)
                    )
                    return future.result(timeout=10)
            return loop.run_until_complete(self.async_transcribe(audio_bytes))
        except Exception as exc:
            logger.error("[GroqSTT] Sync transcription error: %s", exc)
            return ""
