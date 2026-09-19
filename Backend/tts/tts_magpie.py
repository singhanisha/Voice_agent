import os
import time
from typing import Iterator, Optional

import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()


# =========================================================
# NVIDIA MAGPIE TTS CONFIGURATION
# =========================================================

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

NVIDIA_TTS_ENDPOINT = (
    "https://877104f7-e885-42b9-8de8-f6e4c6303969"
    ".invocation.api.nvcf.nvidia.com"
    "/v1/audio/synthesize_online"
)

# NVIDIA API input/output sample rate
MAGPIE_SAMPLE_RATE = 22_050

# IMPORTANT:
# Your application's TTS provider contract is 24kHz.
APPLICATION_SAMPLE_RATE = 24_000

# =========================================================
# MAGPIE VOICES (English & Hindi)
# =========================================================

# Default voices
HINDI_DEFAULT_VOICE = "Magpie-Multilingual.HI-IN.Sofia"
ENGLISH_DEFAULT_VOICE = "Magpie-Multilingual.EN-US.Aria"

# Recognized speakers in Magpie Multilingual
MAGPIE_SPEAKERS = {
    # Female voices
    "sofia": "Sofia",
    "aria": "Aria",
    "mia": "Mia",

    # Male voices
    "jason": "Jason",
    "leo": "Leo",
    "pascal": "Pascal",
    "ray": "Ray",
    "siwei": "Siwei",
}


# =========================================================
# LANGUAGE MAPPING (English & Hindi)
# =========================================================

LANGUAGE_MAP = {
    # English variants
    "en": "en-US",
    "en-us": "en-US",
    "en-in": "en-US",
    "en-gb": "en-US",
    "english": "en-US",

    # Hindi / Hinglish variants
    "hi": "hi-IN",
    "hi-in": "hi-IN",
    "hindi": "hi-IN",
    "hinglish": "hi-IN",
}


# =========================================================
# LANGUAGE RESOLUTION
# =========================================================

def _resolve_language_code(
    preferred_language: Optional[str]
) -> str:
    """Resolve input language name/code to NVIDIA BCP-47 locale (en-US or hi-IN)."""
    if not preferred_language:
        return "en-US"

    key = str(preferred_language).strip().lower()
    return LANGUAGE_MAP.get(key, "en-US")


# =========================================================
# VOICE RESOLUTION (Cross-Lingual English & Hindi)
# =========================================================

def _resolve_voice(
    language_code: str,
    speaker: Optional[str] = None
) -> str:
    """
    Resolve the NVIDIA Magpie voice identifier.

    1. Full ID: 'Magpie-Multilingual.HI-IN.Sofia' (preserves emotion styles like .Calm)
       and dynamically aligns the locale (HI-IN / EN-US) to the current language.
    2. Speaker name: 'sofia', 'aria', 'jason', 'leo' etc. -> maps to the active language.
    3. Fallback: Hindi defaults to Sofia, English defaults to Aria.
    """
    locale_upper = "HI-IN" if language_code == "hi-IN" else "EN-US"

    if speaker:
        speaker_clean = speaker.strip()

        # 1. Full Magpie voice identifier provided
        if speaker_clean.startswith("Magpie-Multilingual."):
            parts = speaker_clean.split(".")
            # Magpie-Multilingual.{LOCALE}.{Speaker}[.{Emotion}]
            if len(parts) >= 3:
                speaker_name = parts[2]
                emotion = f".{parts[3]}" if len(parts) >= 4 else ""
                # Dynamically sync locale with active language (HI-IN or EN-US)
                return f"Magpie-Multilingual.{locale_upper}.{speaker_name}{emotion}"
            return speaker_clean

        # 2. Speaker alias / persona name (e.g. 'sofia', 'jason')
        speaker_key = speaker_clean.lower()
        if speaker_key in MAGPIE_SPEAKERS:
            speaker_title = MAGPIE_SPEAKERS[speaker_key]
            return f"Magpie-Multilingual.{locale_upper}.{speaker_title}"

    # 3. Automatic language-based default
    if language_code == "hi-IN":
        return HINDI_DEFAULT_VOICE
    return ENGLISH_DEFAULT_VOICE

# =========================================================
# RESAMPLE 22.05 kHz -> 24 kHz
# =========================================================

def _resample_to_24khz(pcm_bytes: bytes) -> bytes:
    """
    Convert mono PCM16 from 22050 Hz to 24000 Hz.

    NVIDIA Magpie hosted API returns 22050 Hz.
    Application TTS contract requires 24000 Hz.
    """

    if not pcm_bytes:
        return b""

    # PCM16 = 2 bytes/sample
    usable_length = len(pcm_bytes) - (len(pcm_bytes) % 2)

    if usable_length <= 0:
        return b""

    audio = np.frombuffer(
        pcm_bytes[:usable_length],
        dtype=np.int16
    ).astype(np.float32)

    if len(audio) == 0:
        return b""

    output_length = int(
        round(
            len(audio)
            * APPLICATION_SAMPLE_RATE
            / MAGPIE_SAMPLE_RATE
        )
    )

    if output_length <= 1:
        return audio.astype(np.int16).tobytes()

    old_positions = np.arange(len(audio))

    new_positions = np.linspace(
        0,
        len(audio) - 1,
        output_length
    )

    resampled = np.interp(
        new_positions,
        old_positions,
        audio
    )

    resampled = np.clip(
        resampled,
        -32768,
        32767
    ).astype(np.int16)

    return resampled.tobytes()


# =========================================================
# MAGPIE TTS
# =========================================================

def generate_speech_stream(
    text: str,
    preferred_language: Optional[str] = None,
    agent_id: str = "default",
    speaker: Optional[str] = None,
    custom_voice: Optional[str] = None,
) -> Iterator[bytes]:

    if not NVIDIA_API_KEY:
        print("❌ NVIDIA_API_KEY is not configured.")
        return

    if not text or not text.strip():
        return

    text = text.strip()

    # -----------------------------------------------------
    # NVIDIA input limit
    # -----------------------------------------------------

    if len(text) > 2000:
        print(
            "⚠️ NVIDIA Magpie text exceeds "
            "2000 characters."
        )
        return

    # -----------------------------------------------------
    # Language
    # -----------------------------------------------------

    language_code = _resolve_language_code(
        preferred_language
    )

    # -----------------------------------------------------
    # Voice
    # -----------------------------------------------------

    voice = _resolve_voice(
        language_code,
        custom_voice or speaker
    )

    print(
        f"🎙️ NVIDIA Magpie TTS | "
        f"language={language_code} | "
        f"voice={voice}"
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
    }

    form_data = {
        "text": (None, text),
        "language": (None, language_code),
        "voice": (None, voice),
        "encoding": (None, "LINEAR_PCM"),
        "sample_rate_hz": (
            None,
            str(MAGPIE_SAMPLE_RATE)
        ),
    }

    start_time = time.perf_counter()

    try:

        # =================================================
        # NVIDIA STREAMING REQUEST
        # =================================================

        response = requests.post(
            NVIDIA_TTS_ENDPOINT,
            headers=headers,
            files=form_data,
            stream=True,
            timeout=(10, 60),
        )

        if response.status_code != 200:

            print(
                f"❌ NVIDIA Magpie TTS failed: "
                f"{response.status_code}"
            )

            try:
                print(response.text)
            except Exception:
                pass

            response.close()
            return

        # -------------------------------------------------
        # Collect raw Magpie PCM
        # -------------------------------------------------

        audio_parts = []
        first_chunk_received = False

        for chunk in response.iter_content(
            chunk_size=4096
        ):

            if not chunk:
                continue

            if not first_chunk_received:

                first_chunk_received = True

                ttfb_ms = (
                    time.perf_counter()
                    - start_time
                ) * 1000

                print(
                    f"⚡ Magpie first audio chunk: "
                    f"{ttfb_ms:.0f} ms"
                )

            audio_parts.append(chunk)

        response.close()

        # -------------------------------------------------
        # Combine raw PCM
        # -------------------------------------------------

        raw_audio = b"".join(audio_parts)

        if not raw_audio:
            print(
                "⚠️ NVIDIA Magpie returned no audio."
            )
            return

        # -------------------------------------------------
        # Convert 22050 -> 24000 Hz
        # -------------------------------------------------

        audio_24khz = _resample_to_24khz(
            raw_audio
        )

        if not audio_24khz:
            print(
                "⚠️ Magpie resampling produced "
                "no audio."
            )
            return

        # -------------------------------------------------
        # Yield application-compatible chunks
        # -------------------------------------------------

        chunk_size = 4096

        for i in range(
            0,
            len(audio_24khz),
            chunk_size
        ):
            yield audio_24khz[
                i:i + chunk_size
            ]

    except requests.exceptions.Timeout:

        print(
            "❌ NVIDIA Magpie TTS request timed out."
        )

    except requests.exceptions.RequestException as exc:

        print(
            f"❌ NVIDIA Magpie TTS network error: "
            f"{exc}"
        )

    except Exception as exc:

        print(
            f"❌ NVIDIA Magpie TTS unexpected error: "
            f"{exc}"
        )