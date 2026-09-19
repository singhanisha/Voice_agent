"""
tts_indic_parler.py — AI4Bharat Indic Parler TTS adapter.

Loads `ai4bharat/indic-parler-tts` from local HuggingFace cache and generates
natural Indian-English / Hindi / Marathi audio using a description-controlled
Parler TTS model.

Voice character: Priya — calm, slow, warm Indian female voice.

Contract: generate_speech_stream(text, preferred_language) → Iterator[bytes]
          Yields raw PCM16 mono chunks at 24kHz.

This module keeps the model in memory (singleton) after first load to avoid
repeated ~3s cold-start latency. Model is loaded on first call (lazy init)
to avoid blocking the server startup.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Iterator

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000   # Target output sample rate (24kHz mono PCM16)
_CHUNK_BYTES = 4096    # Stream in 4KB chunks (≈42ms at 24kHz/16bit/mono)

# ── Singleton model state ──────────────────────────────────────────────────────
_model = None
_tokenizer = None
_desc_tokenizer = None
_device = "cpu"
_model_lock = threading.Lock()
_init_done = False
_init_error: str | None = None

# ── Voice description (Parler TTS style prompt) ────────────────────────────────
# This controls the voice character. "Swara" is the AI4Bharat Indian English female.
_VOICE_DESCRIPTIONS = {
    "en": (
        "Swara's voice is calm, warm, and measured. She speaks slowly and clearly "
        "with a natural Indian English accent. Her tone is conversational, slightly "
        "low-pitched, and genuinely helpful — never robotic or rushed. She pauses "
        "naturally between phrases."
    ),
    "hi": (
        "Swara ki awaaz calm aur warm hai. Woh dheere dheere bolti hai, saaf Hindi mein, "
        "bilkul natural Indian accent ke saath. Uski tone bahut genuine aur helpful hai."
    ),
    "mr": (
        "Swara chi awaaz shant aur ushah ahe. Ti sahajpane Marathi bolte, halki Indian "
        "English accent sah, ani tone genuine ani madat-kar ahe."
    ),
}

# Env override for voice description (full override, not just language)
_INDIC_DESCRIPTION = os.getenv(
    "INDIC_PARLER_DESCRIPTION",
    _VOICE_DESCRIPTIONS["en"],
)

HF_HOME = os.getenv("HF_HOME", "")
MODEL_ID = "ai4bharat/indic-parler-tts"


def _initialize_model() -> bool:
    """Load model and tokenizers into memory once. Returns True on success."""
    global _model, _tokenizer, _desc_tokenizer, _device, _init_done, _init_error

    if _init_done:
        return _model is not None

    with _model_lock:
        if _init_done:
            return _model is not None

        logger.info("[IndicParler] Initializing %s model...", MODEL_ID)
        start = time.perf_counter()

        try:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer

            if HF_HOME:
                os.environ["HF_HOME"] = HF_HOME

            _device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("[IndicParler] Using device: %s", _device)

            # Load in float16 on GPU to fit within 4GB VRAM
            dtype = torch.float16 if _device == "cuda" else torch.float32

            _model = ParlerTTSForConditionalGeneration.from_pretrained(
                MODEL_ID,
                local_files_only=True,
                torch_dtype=dtype,
            ).to(_device)
            _model.eval()

            _desc_tokenizer = AutoTokenizer.from_pretrained(
                MODEL_ID,
                local_files_only=True,
            )
            _tokenizer = AutoTokenizer.from_pretrained(
                MODEL_ID,
                local_files_only=True,
            )

            elapsed = time.perf_counter() - start
            logger.info("[IndicParler] Model loaded in %.1fs on %s.", elapsed, _device)
            _init_done = True
            return True

        except ImportError as e:
            _init_error = str(e)
            logger.error("[IndicParler] Missing deps: %s. Run: pip install parler-tts transformers torch", e)
        except Exception as e:
            _init_error = str(e)
            logger.error("[IndicParler] Model load failed: %s", e)

        _init_done = True
        return False


def _resample_to_24k(audio_np: np.ndarray, source_rate: int) -> np.ndarray:
    """Resample audio array from source_rate to 24kHz using scipy."""
    if source_rate == SAMPLE_RATE:
        return audio_np
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(SAMPLE_RATE, source_rate)
        up = SAMPLE_RATE // g
        down = source_rate // g
        return resample_poly(audio_np, up, down).astype(np.float32)
    except Exception as e:
        logger.warning("[IndicParler] Resample failed (%s); returning original rate audio", e)
        return audio_np


def _float32_to_pcm16(audio_np: np.ndarray) -> bytes:
    """Convert float32 [-1, 1] numpy audio to raw PCM16 LE bytes."""
    clipped = np.clip(audio_np, -1.0, 1.0)
    return (clipped * 32767).astype(np.int16).tobytes()


def _get_description(preferred_language: str | None) -> str:
    """Return the voice description for the given language."""
    # If env override exists, use it for all languages
    env_desc = os.getenv("INDIC_PARLER_DESCRIPTION", "")
    if env_desc:
        return env_desc

    lang = (preferred_language or "en").lower()
    if lang in ("hi", "hinglish"):
        return _VOICE_DESCRIPTIONS["hi"]
    if lang == "mr":
        return _VOICE_DESCRIPTIONS["mr"]
    return _VOICE_DESCRIPTIONS["en"]


def _description_for(preferred_language: str | None, custom_desc: str | None = None) -> str:
    """Helper alias for tests and custom description resolution."""
    if custom_desc:
        return custom_desc
    return _get_description(preferred_language)


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
) -> Iterator[bytes]:
    """
    Synthesize speech using ai4bharat/indic-parler-tts.

    Yields raw PCM16 mono chunks at 24kHz.
    Falls back to empty bytes on any error (caller handles silence gracefully).
    """
    if not text or not text.strip():
        return

    # Lazy-load model on first call
    if not _initialize_model() or _model is None:
        logger.error("[IndicParler] Model not available (init_error=%s); yielding nothing.", _init_error)
        return

    try:
        import torch
        from tts.speech_formatter import optimize_for_tts
    except ImportError:
        optimize_for_tts = lambda t: t  # noqa: E731

    # Pre-process text for natural pacing
    try:
        text = optimize_for_tts(text)
    except Exception:
        pass

    description = _get_description(preferred_language)

    try:
        import torch

        ttfb_start = time.perf_counter()

        with torch.inference_mode():
            desc_inputs = _desc_tokenizer(description, return_tensors="pt").to(_device)
            text_inputs = _tokenizer(text, return_tensors="pt").to(_device)

            generation = _model.generate(
                input_ids=desc_inputs.input_ids,
                attention_mask=desc_inputs.attention_mask,
                prompt_input_ids=text_inputs.input_ids,
                prompt_attention_mask=text_inputs.attention_mask,
            )

        # Move audio to CPU numpy
        audio = generation.cpu().numpy().squeeze()  # float32 array

        # Parler TTS default sample rate is model-specific; get from config
        model_sr = getattr(_model.config, "sampling_rate", 44100)
        audio = _resample_to_24k(audio, model_sr)

        pcm_bytes = _float32_to_pcm16(audio)
        ttfb_ms = (time.perf_counter() - ttfb_start) * 1000

        logger.info(
            "[IndicParler] Synthesized %.1fms TTFB | %d bytes | lang=%s",
            ttfb_ms,
            len(pcm_bytes),
            preferred_language or "en",
        )

        # Yield in chunks for streaming
        for i in range(0, len(pcm_bytes), _CHUNK_BYTES):
            yield pcm_bytes[i : i + _CHUNK_BYTES]

    except Exception as e:
        logger.error("[IndicParler] Generation failed: %s", e, exc_info=True)
        return
