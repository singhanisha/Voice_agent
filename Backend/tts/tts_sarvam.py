"""
tts_sarvam.py — Sarvam AI Bulbul V3 REST TTS Adapter.

File Path: Backend/tts/tts_sarvam.py
Migration: Upgraded legacy voice models ("Priya", "Neha", "Rani") to Sarvam AI Bulbul V3 enterprise speakers.

Contract: generate_speech_stream(text, preferred_language, agent_id, speaker) -> Iterator[bytes]
          Yields raw PCM16 mono chunks at 24kHz.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Iterator
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration & Endpoint ─────────────────────────────────────────────────
SARVAM_TTS_ENDPOINT = "https://api.sarvam.ai/text-to-speech"
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_PACE = 1.08      # 1.08× natural pace — slightly faster speech, no distortion
SAMPLE_RATE = 24_000
CHUNK_BYTES = 2048       # Halved from 4096 — first audio chunk arrives ~80ms sooner

SARVAM_SPEAKER_MAPPING = {
    "shreya": "shreya",   # Sarvam Female - Authoritative/Professional (UNTOUCHED)
    "ishita": "priya",    # Sarvam Female - Conversational/Dynamic (Official Sarvam Priya)
    "shubh": "aditya",    # Sarvam Male - Deep/Professional (Official Sarvam Aditya Male)
    "priya": "priya",
    "neha": "priya",
    "aditya": "aditya",
    "ashutosh": "aditya",
    "default": "shreya",
}

# Language mapping to Sarvam AI BCP-47 codes
LANGUAGE_MAP = {
    "en": "en-IN",
    "english": "en-IN",
    "hi": "hi-IN",
    "hindi": "hi-IN",
    "hinglish": "hi-IN",
    "mr": "mr-IN",
    "marathi": "mr-IN",
    "ta": "ta-IN",
    "tamil": "ta-IN",
    "te": "te-IN",
    "telugu": "te-IN",
    "kn": "kn-IN",
    "kannada": "kn-IN",
    "ml": "ml-IN",
    "malayalam": "ml-IN",
    "bn": "bn-IN",
    "bengali": "bn-IN",
    "gu": "gu-IN",
    "gujarati": "gu-IN",
    "pa": "pa-IN",
    "punjabi": "pa-IN",
}


def _resolve_speaker(speaker_or_persona: str | None) -> str:
    """Resolve legacy persona names ("Priya", "Neha", "Rani") or voice inputs to Sarvam speakers."""
    if not speaker_or_persona:
        return SARVAM_SPEAKER_MAPPING["default"]
    key = speaker_or_persona.strip().lower()
    return SARVAM_SPEAKER_MAPPING.get(key, SARVAM_SPEAKER_MAPPING["default"])


def _resolve_language_code(preferred_language: str | None) -> str:
    """Resolve user/agent preferred language to Sarvam target_language_code."""
    if not preferred_language:
        return "en-IN"
    lang = preferred_language.strip().lower()
    return LANGUAGE_MAP.get(lang, "en-IN")


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    agent_id: str = "default",
    speaker: str | None = None,
) -> Iterator[bytes]:
    """
    Synthesize speech using Sarvam AI Bulbul V3 REST API.

    Decodes response['audios'][0] base64 binary stream into 24kHz PCM16 mono chunks.
    """
    if not text or not text.strip():
        return

    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        logger.error("[SarvamTTS] SARVAM_API_KEY environment variable is missing or empty.")
        return

    target_lang = _resolve_language_code(preferred_language)
    sarvam_speaker = _resolve_speaker(speaker)
    clean_text = text.strip()

    # Part 1.4: Payload Construction
    payload = {
        "inputs": [clean_text],
        "target_language_code": target_lang,
        "speaker": sarvam_speaker,
        "model": DEFAULT_MODEL,
        "pace": DEFAULT_PACE,
        "speech_sample_rate": SAMPLE_RATE,
        "enable_preprocessing": True,
    }

    # Part 1.3: Headers Configuration
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }

    start_time = time.perf_counter()

    try:
        # Part 1.3: API Integration (REST POST)
        response = requests.post(
            SARVAM_TTS_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=5.0,   # tightened from 8s — fail fast to unblock the pipeline
        )
        response.raise_for_status()

        # Part 1.5: Response Handling & Base64 Decoding
        data = response.json()
        audios = data.get("audios", [])
        if not audios:
            logger.error("[SarvamTTS] API returned empty 'audios' array.")
            return

        b64_audio = audios[0]
        raw_bytes = base64.b64decode(b64_audio)

        # Handle 44-byte WAV header container if present to yield raw binary PCM audio
        if raw_bytes.startswith(b"RIFF") and len(raw_bytes) > 44:
            pcm_bytes = raw_bytes[44:]
        else:
            pcm_bytes = raw_bytes

        ttfb_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "[SarvamTTS] Synthesized %.1fms TTFB | %d PCM bytes | model=%s | lang=%s | speaker=%s",
            ttfb_ms,
            len(pcm_bytes),
            DEFAULT_MODEL,
            target_lang,
            sarvam_speaker,
        )

        for i in range(0, len(pcm_bytes), CHUNK_BYTES):
            yield pcm_bytes[i : i + CHUNK_BYTES]

    except requests.exceptions.HTTPError as http_err:
        logger.error("[SarvamTTS] HTTP Error (%s): %s", response.status_code, response.text)
    except requests.exceptions.Timeout:
        logger.error("[SarvamTTS] Request timed out calling Sarvam API after 8s.")
    except requests.exceptions.RequestException as req_err:
        logger.error("[SarvamTTS] Request failed: %s", req_err)
    except Exception as exc:
        logger.error("[SarvamTTS] Unexpected synthesis error: %s", exc, exc_info=True)
