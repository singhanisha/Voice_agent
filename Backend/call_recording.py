"""Passive dual-channel PCM session recorder.

This module records already-existing audio at source/sink boundaries. It does
not alter live audio frames, WebSocket payloads, STT chunks, or TTS chunks.
"""

from __future__ import annotations

import os
import re
import threading
import time
import wave
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

FALSE_ENV_VALUES = {"0", "false", "off", "no"}


def recording_path_and_url(prefix: str, call_key: str, recordings_dir: str = "recordings") -> tuple[str, str]:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", call_key or "call").strip("._")[:96] or "call"
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix or "rec").strip("._") or "rec"
    Path(recordings_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{safe_prefix}_{safe_key}.wav"
    return os.path.join(recordings_dir, filename), f"/recordings/{filename}"


class SessionRecorder:
    """Record user and agent PCM into a timeline-aligned stereo WAV.

    Left channel is the user/customer. Right channel is the agent. Incoming
    chunks may be any PCM16 mono sample rate; they are resampled only for the
    saved WAV, never for the live runtime path.
    """

    def __init__(self, sample_rate: int = 24000):
        self.sample_rate = int(sample_rate or 24000)
        self._started_at = time.monotonic()
        self._tracks: dict[str, list[tuple[int, np.ndarray]]] = {
            "user": [],
            "agent": [],
        }
        self._lock = threading.Lock()

    def add_user_audio(self, pcm_data: bytes, sample_rate: int = 16000) -> None:
        self.add_audio("user", pcm_data, sample_rate)

    def add_agent_audio(self, pcm_data: bytes, sample_rate: int = 24000) -> None:
        self.add_audio("agent", pcm_data, sample_rate)

    def add_audio(self, speaker: str, pcm_data: bytes, sample_rate: int) -> None:
        if speaker not in self._tracks or not pcm_data:
            return

        samples = _pcm16_to_samples(pcm_data)
        if samples.size == 0:
            return

        source_rate = int(sample_rate or self.sample_rate)
        samples = _resample_samples(samples, source_rate, self.sample_rate)
        if samples.size == 0:
            return

        start_sample = max(0, int((time.monotonic() - self._started_at) * self.sample_rate))
        with self._lock:
            self._tracks[speaker].append((start_sample, samples))

    def finalize(self, filepath: str) -> float:
        with self._lock:
            tracks = {
                speaker: list(chunks)
                for speaker, chunks in self._tracks.items()
            }

        total_samples = 0
        for chunks in tracks.values():
            for start_sample, samples in chunks:
                total_samples = max(total_samples, start_sample + int(samples.size))

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        if total_samples <= 0:
            _write_stereo_wav(filepath, self.sample_rate, np.zeros((0, 2), dtype=np.int16))
            return 0.0

        user = np.zeros(total_samples, dtype=np.int16)
        agent = np.zeros(total_samples, dtype=np.int16)
        _overlay_track(user, tracks["user"])
        _overlay_track(agent, tracks["agent"])
        user, agent = _prepare_playback_tracks(user, agent, self.sample_rate)

        stereo = np.column_stack((user, agent)).astype(np.int16, copy=False)
        _write_stereo_wav(filepath, self.sample_rate, stereo)
        return total_samples / float(self.sample_rate)


def _pcm16_to_samples(pcm_data: bytes) -> np.ndarray:
    even_len = len(pcm_data) - (len(pcm_data) % 2)
    if even_len <= 0:
        return np.zeros(0, dtype=np.int16)
    return np.frombuffer(pcm_data[:even_len], dtype=np.int16).copy()


def _resample_samples(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return samples
    rate_gcd = gcd(source_rate, target_rate)
    resampled = resample_poly(
        samples.astype(np.float32),
        target_rate // rate_gcd,
        source_rate // rate_gcd,
    )
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def _overlay_track(target: np.ndarray, chunks: list[tuple[int, np.ndarray]]) -> None:
    for start_sample, samples in chunks:
        if start_sample >= target.size:
            continue
        end_sample = min(target.size, start_sample + samples.size)
        existing = target[start_sample:end_sample].astype(np.int32)
        incoming = samples[: end_sample - start_sample].astype(np.int32)
        target[start_sample:end_sample] = np.clip(existing + incoming, -32768, 32767).astype(np.int16)


def _prepare_playback_tracks(
    user: np.ndarray,
    agent: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    cleaned_user = user
    cleaned_agent = _apply_gain(agent, _recording_float("RECORDING_AGENT_GAIN", 1.25, 0.25, 4.0))

    if _recording_bool("RECORDING_ECHO_SUPPRESSION", True):
        active = _activity_mask(cleaned_agent, sample_rate)
        if active.any():
            duck = _recording_float("RECORDING_USER_DUCK_DURING_AGENT", 0.18, 0.0, 1.0)
            cleaned_user = cleaned_user.copy()
            cleaned_user[active] = np.clip(
                cleaned_user[active].astype(np.float32) * duck,
                -32768,
                32767,
            ).astype(np.int16)

    return cleaned_user, cleaned_agent


def _apply_gain(samples: np.ndarray, gain: float) -> np.ndarray:
    if gain == 1.0 or samples.size == 0:
        return samples
    return np.clip(samples.astype(np.float32) * gain, -32768, 32767).astype(np.int16)


def _activity_mask(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    if samples.size == 0:
        return np.zeros(0, dtype=bool)
    threshold = int(_recording_float("RECORDING_AGENT_ACTIVE_THRESHOLD", 450.0, 50.0, 5000.0))
    active = np.abs(samples.astype(np.int32)) > threshold
    if not active.any():
        return active

    pad_ms = _recording_float("RECORDING_ECHO_DUCK_PAD_MS", 80.0, 0.0, 500.0)
    pad = int((max(sample_rate, 1) * pad_ms) / 1000.0)
    if pad <= 0:
        return active

    expanded = np.zeros_like(active, dtype=bool)
    active_indexes = np.flatnonzero(active)
    breaks = np.where(np.diff(active_indexes) > 1)[0] + 1
    for group in np.split(active_indexes, breaks):
        if group.size == 0:
            continue
        start = max(0, int(group[0]) - pad)
        end = min(active.size, int(group[-1]) + pad + 1)
        expanded[start:end] = True
    return expanded


def _recording_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in FALSE_ENV_VALUES


def _recording_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _write_stereo_wav(filepath: str, sample_rate: int, stereo: np.ndarray) -> None:
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(stereo.tobytes())
