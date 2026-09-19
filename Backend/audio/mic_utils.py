"""
Voice Activity Detection (VAD) buffer for streaming microphone input.

Key changes vs. the previous version:
- silence_duration_s default: 1.0s → 0.4s  (400ms aggressive endpointing)
- min_speech_duration_s default: 1.0s → 0.3s (captures short utterances like "yes")
- RMS calculation: Python sum() loop → numpy vectorised (≈20x faster on large chunks)
- New async flush_to_thread() method dispatches buffer to STT via asyncio.to_thread()
  immediately upon silence detection, keeping the event loop non-blocking.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger("audio.mic_utils")


class VADBuffer:
    """
    Energy-based VAD buffer.

    Aggregates incoming PCM16 mono chunks and emits a complete speech payload when
    trailing silence exceeds `silence_duration_s`. Designed for 16 kHz @ 16-bit mono.

    Parameters
    ----------
    sample_rate : int
        Audio sample rate in Hz. Default 16000.
    energy_threshold : float
        RMS energy threshold (0–32767 scale) that separates speech from silence.
        Default 500 — conservative enough for most microphones.
    silence_duration_s : float
        How long (in seconds) silence must persist before a speech segment is
        considered complete. **Default 0.4 s (400 ms)** — reduced from 1.0 s for
        aggressive low-latency endpointing.
    min_speech_duration_s : float
        Minimum speech duration (in seconds) before a payload is emitted.
        **Default 0.3 s (300 ms)** — keeps short affirmations like "yes" / "okay".
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float = 500.0,
        silence_duration_s: float = 0.4,        # ← 400 ms (was 1.0 s)
        min_speech_duration_s: float = 0.3,     # ← 300 ms (was 1.0 s)
        payload_speech_threshold: float = 300.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.payload_speech_threshold = payload_speech_threshold

        # Pre-compute byte thresholds (PCM16 = 2 bytes per sample)
        self.silence_bytes_threshold: int = int(silence_duration_s * sample_rate * 2)
        self.min_speech_bytes: int = int(min_speech_duration_s * sample_rate * 2)

        self._buffer = bytearray()
        self._current_silence_bytes: int = 0
        self._is_speaking: bool = False

    # ------------------------------------------------------------------
    # RMS energy (numpy-vectorised — ~20x faster than pure-Python loop)
    # ------------------------------------------------------------------

    def _calculate_rms(self, pcm_bytes: bytes) -> float:
        """
        Compute Root Mean Square energy of a PCM16 LE chunk.
        Returns a value on the same 0–32767 scale as `energy_threshold`.
        """
        if not pcm_bytes:
            return 0.0
        # Reinterpret bytes as signed int16 samples and compute RMS in one shot
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(samples ** 2)))

    # ------------------------------------------------------------------
    # Core processing loop
    # ------------------------------------------------------------------

    def process_chunk(self, chunk: bytes) -> Optional[bytes]:
        """
        Feed a raw PCM16 chunk into the VAD state machine.

        Returns
        -------
        bytes or None
            A complete speech payload when an endpoint is detected, else None.
        """
        if not chunk:
            return None

        rms = self._calculate_rms(chunk)
        is_speech = rms > self.energy_threshold

        if is_speech:
            self._is_speaking = True
            self._current_silence_bytes = 0
            self._buffer.extend(chunk)
            return None

        # Silence frame
        if not self._is_speaking:
            return None  # Pre-speech silence — ignore

        self._buffer.extend(chunk)
        self._current_silence_bytes += len(chunk)

        if self._current_silence_bytes < self.silence_bytes_threshold:
            return None  # Not enough silence yet

        # ── Endpoint detected ──────────────────────────────────────────
        payload = bytes(self._buffer)
        self._reset()

        if len(payload) >= self.min_speech_bytes:
            # Phase 1: Payload energy validation
            payload_rms = self._calculate_rms(payload)
            if payload_rms < self.payload_speech_threshold:
                logger.debug(
                    "[VAD] Dropping hallucination payload: RMS %.1f < %.1f", 
                    payload_rms, self.payload_speech_threshold
                )
                return None
                
            logger.debug(
                "[VAD] Endpoint detected — emitting %.0f ms of speech (%d bytes)",
                len(payload) / (self.sample_rate * 2) * 1000,
                len(payload),
            )
            return payload

        logger.debug("[VAD] Dropping short utterance (%d bytes < min %d)", len(payload), self.min_speech_bytes)
        return None

    # ------------------------------------------------------------------
    # Async flush (non-blocking STT dispatch)
    # ------------------------------------------------------------------

    async def flush_to_thread(
        self,
        stt_fn: Callable[[bytes], str],
    ) -> Optional[str]:
        """
        Immediately flush whatever is in the buffer to the STT function via
        `asyncio.to_thread()`, bypassing the 400 ms silence wait.

        Useful for barge-in scenarios or forced endpoint signals from the
        transport layer (e.g. WebSocket close, SIP BYE).

        Parameters
        ----------
        stt_fn : callable
            Synchronous STT function accepting `bytes` and returning `str`.

        Returns
        -------
        str or None
            Transcript if speech was present and above the minimum threshold.
        """
        payload = bytes(self._buffer)
        self._reset()

        if len(payload) < self.min_speech_bytes:
            logger.debug("[VAD] flush_to_thread: buffer too short to transcribe (%d bytes)", len(payload))
            return None

        logger.info("[VAD] flush_to_thread: dispatching %d bytes to STT", len(payload))
        try:
            transcript: str = await asyncio.to_thread(stt_fn, payload)
            return transcript.strip() or None
        except Exception as exc:
            logger.error("[VAD] flush_to_thread STT error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._buffer = bytearray()
        self._current_silence_bytes = 0
        self._is_speaking = False

    def force_emit(self) -> Optional[bytes]:
        """
        Synchronously emit whatever is currently buffered (used for end-of-call cleanup).
        """
        payload = bytes(self._buffer)
        self._reset()
        if len(payload) >= self.min_speech_bytes:
            return payload
        return None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def buffered_ms(self) -> float:
        """Current buffer duration in milliseconds."""
        return len(self._buffer) / (self.sample_rate * 2) * 1000.0

    @property
    def silence_ms(self) -> float:
        """Current trailing silence duration in milliseconds."""
        return self._current_silence_bytes / (self.sample_rate * 2) * 1000.0
