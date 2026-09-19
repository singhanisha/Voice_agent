"""
tts_smallest.py — Smallest AI Lightning v3.1 (Waves) REST TTS Adapter.

File Path: Backend/tts/tts_smallest.py

Contract: generate_speech_stream(text, preferred_language, agent_id, speaker) -> Iterator[bytes]
          Yields raw PCM16 mono chunks at 24kHz.

API Docs: https://docs.smallest.ai
Endpoint: POST https://api.smallest.ai/waves/v1/tts
Auth:     Authorization: Bearer SMALLEST_API_KEY
"""

from __future__ import annotations

import io
import logging
import os
import struct
import time
from typing import Iterator

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration & Endpoint ─────────────────────────────────────────────────
SMALLEST_TTS_ENDPOINT = "https://api.smallest.ai/waves/v1/tts"
DEFAULT_MODEL = "lightning_v3.1"
SAMPLE_RATE = 24_000
CHUNK_BYTES = 2048

# ── Voice Catalog ─────────────────────────────────────────────────────────────
# All verified against the live Smallest AI /get_voices API endpoint.
# accent=indian, supports English, Hindi, Marathi, Gujarati, Punjabi, Bengali, Tamil, Telugu, Kannada, Malayalam.
SMALLEST_FEMALE_VOICES = {
    "anika", "divya", "avni", "kavya", "naina", "nikita", "siya",
    "aditi", "maya", "rishika", "maithili", "aisha", "tanvi", "gargi",
    "srishti", "chinmayi", "anuja", "khushi", "sakshi", "sunidhi",
    "zoya", "simran", "amaira", "aanya", "aadya", "harleen", "soumya",
    "radhika", "meera", "kiara", "advika", "yuvika", "ishani", "sana",
    "aarushi", "shivangi", "sameera", "irisha", "vaishnavi", "harshita",
    "nirupma", "imogen", "ganya",
}
SMALLEST_MALE_VOICES = {
    "devansh", "dhruv", "karan", "arjun", "vivaan", "atharv", "veer",
    "kunal", "wasim", "siddharth", "kaustubh", "virat", "yash", "chirag",
    "parth", "arnab", "rajan", "harsh", "samarth", "neel", "lakshya",
    "aneer", "krish", "shreyas", "rehan", "ishan", "vedant", "sudarshan",
    "gurpreet", "yuvan", "abir", "navdeep", "chinmay", "pritam", "kartik",
    "vikram", "sanjay", "ranbir", "harjot", "balwinder", "mohit", "mihir",
    "souvik", "aadhav", "arun", "venkat", "harish", "surya", "ashwin",
    "reyan", "nithya", "jaspal", "ram", "debashis", "karan", "veer",
    "reva", "saanvi", "myra",
}

# Persona / alias → Smallest AI voice_id mapping (all verified real IDs)
SMALLEST_SPEAKER_MAPPING: dict[str, str] = {
    # Primary female voices
    "anika": "anika",         # Sales / Conversational — RECOMMENDED default female
    "divya": "divya",         # Customer Support / Conversational
    "avni": "avni",           # Customer Support / Conversational
    "kavya": "kavya",         # Insurance Agent
    "naina": "naina",         # Conversational / Narration
    "nikita": "nikita",       # Conversational / Narration
    "siya": "siya",           # Customer Support / Conversational
    "aditi": "aditi",         # Narration / Educational
    "maya": "maya",           # Conversational / Advertisement
    "rishika": "rishika",     # Insurance Agent / Narration
    "maithili": "maithili",   # Customer Support / Narration
    "aisha": "aisha",         # Sales / Advertisement
    "tanvi": "tanvi",         # Sales / Narration
    "gargi": "gargi",         # Conversational / Educational
    "meera": "meera",         # Narration / Educational
    "simran": "simran",       # Insurance / Educational
    # Primary male voices
    "devansh": "devansh",     # Sales / Advertisement — RECOMMENDED default male
    "dhruv": "dhruv",         # Sales / Narration
    "karan": "karan",         # Insurance Agent / Narration
    "arjun": "arjun",         # Insurance Agent / Educational
    "vivaan": "vivaan",       # Sales / Narration
    "atharv": "atharv",       # Sales / Conversational
    "veer": "veer",           # Customer Support / Conversational
    "kunal": "kunal",         # Narration / Educational
    "wasim": "wasim",         # Sales / Advertisement
    "siddharth": "siddharth", # Narration / Educational
    "kaustubh": "kaustubh",   # Insurance Agent / Narration
    "virat": "virat",         # Insurance Agent / Narration
    "yash": "yash",           # Insurance Agent / Educational
    "chirag": "chirag",       # Insurance Agent / Narration
    "harsh": "harsh",         # Sales / Advertisement
    "rajan": "rajan",         # Conversational / Educational
    # Gender-neutral aliases
    "female": "anika",
    "male": "devansh",
    "default_female": "anika",
    "default_male": "devansh",
    # Cross-provider aliases (map Sarvam-style names to closest Smallest equivalents)
    "shreya": "anika",        # Sarvam Professional Female → Anika
    "ishita": "divya",        # Sarvam Dynamic Female → Divya
    "shubh": "devansh",       # Sarvam Friendly Male → Devansh
    "priya": "anika",         # Legacy female fallback
    "aditya": "karan",        # Legacy male fallback
    "default": "anika",
}


# Language code → BCP-47 mapping (Smallest AI accepts ISO codes)
LANGUAGE_MAP: dict[str, str] = {
    "en": "en",
    "english": "en",
    "hi": "hi",
    "hindi": "hi",
    "hinglish": "hi",
    "mr": "mr",
    "marathi": "mr",
    "ta": "ta",
    "tamil": "ta",
    "te": "te",
    "telugu": "te",
    "kn": "kn",
    "kannada": "kn",
    "ml": "ml",
    "malayalam": "ml",
    "bn": "bn",
    "bengali": "bn",
    "gu": "gu",
    "gujarati": "gu",
    "pa": "pa",
    "punjabi": "pa",
}


def _resolve_speaker(speaker_or_persona: str | None) -> str:
    """Resolve persona name / voice alias to a Smallest AI voice_id."""
    if not speaker_or_persona:
        return SMALLEST_SPEAKER_MAPPING["default"]
    key = speaker_or_persona.strip().lower()
    return SMALLEST_SPEAKER_MAPPING.get(key, SMALLEST_SPEAKER_MAPPING["default"])


def _resolve_language_code(preferred_language: str | None) -> str:
    """Map user language preference to an ISO language code for Smallest AI."""
    if not preferred_language:
        return "en"
    lang = preferred_language.strip().lower()
    return LANGUAGE_MAP.get(lang, "en")


def _pcm_bytes_from_response(response_bytes: bytes) -> bytes:
    """
    Extract raw PCM16 from the API response.

    Smallest AI returns a WAV file. Strip the 44-byte standard WAV header
    to yield raw PCM16 bytes (identical to what tts_sarvam.py does).
    """
    if response_bytes[:4] == b"RIFF" and len(response_bytes) > 44:
        return response_bytes[44:]
    return response_bytes


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    agent_id: str = "default",
    speaker: str | None = None,
) -> Iterator[bytes]:
    """
    Synthesize speech using the Smallest AI Lightning v3.1 (Waves) REST API.

    Yields raw PCM16 mono chunks at 24 kHz so the voice loop can stream
    them directly to the caller — same contract as tts_sarvam.generate_speech_stream.
    """
    if not text or not text.strip():
        return

    api_key = os.getenv("SMALLEST_API_KEY", "").strip()
    if not api_key:
        logger.error(
            "[SmallestTTS] SMALLEST_API_KEY environment variable is missing or empty. "
            "Get a key at https://smallest.ai"
        )
        return

    voice_id = _resolve_speaker(speaker)
    lang_code = _resolve_language_code(preferred_language)
    clean_text = text.strip()

    payload = {
        "text": clean_text,
        "voice_id": voice_id,
        "model": DEFAULT_MODEL,
        "sample_rate": SAMPLE_RATE,
        "language": lang_code,
        "speed": 1.05,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start_time = time.perf_counter()

    try:
        response = requests.post(
            SMALLEST_TTS_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=5.0,
        )
        response.raise_for_status()

        raw_bytes = response.content
        if not raw_bytes:
            logger.error("[SmallestTTS] API returned empty audio payload.")
            return

        pcm_bytes = _pcm_bytes_from_response(raw_bytes)

        ttfb_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "[SmallestTTS] Synthesized %.1fms TTFB | %d PCM bytes | model=%s | lang=%s | voice=%s",
            ttfb_ms,
            len(pcm_bytes),
            DEFAULT_MODEL,
            lang_code,
            voice_id,
        )

        for i in range(0, len(pcm_bytes), CHUNK_BYTES):
            yield pcm_bytes[i: i + CHUNK_BYTES]

    except requests.exceptions.HTTPError as http_err:
        logger.error(
            "[SmallestTTS] HTTP Error (%s): %s",
            response.status_code,
            response.text[:500],
        )
    except requests.exceptions.Timeout:
        logger.error("[SmallestTTS] Request timed out calling Smallest AI API after 10s.")
    except requests.exceptions.RequestException as req_err:
        logger.error("[SmallestTTS] Request failed: %s", req_err)
    except Exception as exc:
        logger.error("[SmallestTTS] Unexpected synthesis error: %s", exc, exc_info=True)
