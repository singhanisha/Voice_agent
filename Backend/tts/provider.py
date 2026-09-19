"""TTS provider selection.

The runtime contract stays stable:
    generate_speech_stream(text: str, preferred_language: str | None) -> Iterator[bytes]

Providers must yield raw PCM16 mono chunks at 24kHz.
"""

from __future__ import annotations

import logging
import os
import json
import threading
import time
from pathlib import Path
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "edge"

#SUPPORTED_PROVIDERS = {"edge", "cartesia"} | {"sarvam", "indic", "indic_parler", "parler"} | {"smallest"}
SUPPORTED_PROVIDERS = (
    {"edge", "cartesia"}
    | {"sarvam", "indic", "indic_parler", "parler"}
    | {"smallest"}
    | {"magpie"}
)

_AGENT_CONFIG_CACHE: dict[str, tuple[float, dict]] = {}
_AGENT_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "db" / "agents"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_provider(provider: str | None) -> str:
    normalized = (provider or DEFAULT_PROVIDER).strip().lower()
    if normalized in {"indic", "parler"}:
        normalized = "indic_parler"
    if normalized not in SUPPORTED_PROVIDERS:
        logger.warning("Unknown TTS_PROVIDER=%s; falling back to %s", provider, DEFAULT_PROVIDER)
        return DEFAULT_PROVIDER
    return normalized


def _configured_provider(agent_id: str = "default") -> str:
    global_provider = _normalize_provider(os.getenv("TTS_PROVIDER", DEFAULT_PROVIDER))
    if _env_bool("TTS_DISABLE_AGENT_OVERRIDES", False):
        return global_provider

    schema_config = _provider_config_from_agent_schema(agent_id)
    schema_provider = schema_config.get("tts_provider")
    if schema_provider:
        return schema_provider

    cartesia_agent_ids = {
        item.strip()
        for item in os.getenv("CARTESIA_AGENT_IDS", "").split(",")
        if item.strip()
    }
    if agent_id and agent_id in cartesia_agent_ids:
        return "cartesia"
    return global_provider


def _provider_config_from_agent_schema(agent_id: str) -> dict:
    if not agent_id or agent_id == "default":
        return {}

    # Check cache (expire after 10s)
    cached = _AGENT_CONFIG_CACHE.get(agent_id)
    if cached and (time.time() - cached[0]) < 10.0:
        return cached[1]

    config = {}

    # 1. Fast direct SQLite lookup (0.1ms, no network deadlocks)
    try:
        backend_dir = Path(__file__).resolve().parent.parent
        db_path = backend_dir / "db" / "platform.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT tts_provider, voice, cartesia_voice_id, name, agent_type FROM agents WHERE id = ?", (agent_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                row_dict = dict(row)
                if row_dict.get("tts_provider"):
                    config["tts_provider"] = _normalize_provider(row_dict["tts_provider"])
                if row_dict.get("voice"):
                    config["voice"] = str(row_dict["voice"]).strip()
                if row_dict.get("cartesia_voice_id"):
                    config["cartesia_voice_id"] = str(row_dict["cartesia_voice_id"]).strip()
                if row_dict.get("name"):
                    config["name"] = str(row_dict["name"]).strip()
                if row_dict.get("agent_type"):
                    config["agent_type"] = str(row_dict["agent_type"]).strip()
    except Exception as exc:
        logger.debug("SQLite agent lookup failed for %s: %s", agent_id, exc)

    # 2. Fast local JSON schema file lookup
    if not config.get("tts_provider"):
        try:
            json_path = _AGENT_SCHEMA_DIR / f"{agent_id}.json"
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                    provider_config = schema.get("provider_config") or {}
                    provider = provider_config.get("tts_provider") or schema.get("tts_provider")
                    if provider:
                        config["tts_provider"] = _normalize_provider(provider)
                    """ voice = provider_config.get("voice") or schema.get("voice") """

                    voice = (
                        provider_config.get("voice")
                        or schema.get("voice")
                        or schema.get("voice_id")
                    )

                    if voice:
                        config["voice"] = str(voice).strip()
                    cartesia_voice_id = provider_config.get("cartesia_voice_id") or schema.get("cartesia_voice_id")
                    if cartesia_voice_id:
                        config["cartesia_voice_id"] = str(cartesia_voice_id).strip()
                    if schema.get("name"):
                        config["name"] = str(schema["name"]).strip()
                    if schema.get("agent_type"):
                        config["agent_type"] = str(schema["agent_type"]).strip()
                    indic_voice_desc = (
                        provider_config.get("parler_description")
                        or schema.get("parler_description")
                        or provider_config.get("indic_parler_voice_description")
                        or schema.get("indic_parler_voice_description")
                    )
                    if indic_voice_desc:
                        config["indic_parler_voice_description"] = str(indic_voice_desc).strip()
        except Exception as exc:
            logger.debug("JSON agent lookup failed for %s: %s", agent_id, exc)

    # 3. HTTP API fallback
    if not config.get("tts_provider"):
        try:
            port = os.getenv("PORT", "8000")
            url = os.getenv("BACKEND_API_URL", f"http://127.0.0.1:{port}") + f"/api/agents/{agent_id}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as response:
                if response.status == 200:
                    schema = json.loads(response.read().decode())
                    provider_config = schema.get("provider_config") or {}
                    provider = provider_config.get("tts_provider") or schema.get("tts_provider")
                    if provider:
                        config["tts_provider"] = _normalize_provider(provider)
                    voice = provider_config.get("voice") or schema.get("voice")
                    if voice:
                        config["voice"] = str(voice).strip()
                    cartesia_voice_id = provider_config.get("cartesia_voice_id") or schema.get("cartesia_voice_id")
                    if cartesia_voice_id:
                        config["cartesia_voice_id"] = str(cartesia_voice_id).strip()
                    if schema.get("name"):
                        config["name"] = str(schema["name"]).strip()
                    if schema.get("agent_type"):
                        config["agent_type"] = str(schema["agent_type"]).strip()
                    indic_voice_desc = (
                        provider_config.get("parler_description")
                        or schema.get("parler_description")
                        or provider_config.get("indic_parler_voice_description")
                        or schema.get("indic_parler_voice_description")
                    )
                    if indic_voice_desc:
                        config["indic_parler_voice_description"] = str(indic_voice_desc).strip()
        except Exception:
            pass

    _AGENT_CONFIG_CACHE[agent_id] = (time.time(), config)
    return config


def _cartesia_voice_id_for_agent(agent_id: str) -> str | None:
    return _provider_config_from_agent_schema(agent_id).get("cartesia_voice_id")


def _shadow_provider(primary_provider: str) -> str:
    configured = os.getenv("TTS_SHADOW_PROVIDER")
    if configured:
        return _normalize_provider(configured)
    return "cartesia" if primary_provider == "edge" else "edge"


def _load_provider(provider: str):
    if provider == "cartesia":
        # Route through the new premium Cloud TTS client for breath injection and vocal stability
        from .cloud_tts_client import generate_speech_stream as _cartesia
        return _cartesia
    if provider == "sarvam":
        # Sarvam AI Bulbul V3 REST TTS Adapter
        from .tts_sarvam import generate_speech_stream as _sarvam
        return _sarvam
    if provider == "smallest":
        # Smallest AI Lightning v3.1 (Waves) REST TTS Adapter — ultra-low latency
        from .tts_smallest import generate_speech_stream as _smallest
        return _smallest

    if provider == "magpie":
    # NVIDIA Magpie TTS Multilingual - hosted/serverless API
        from .tts_magpie import generate_speech_stream as _magpie
        return _magpie

    if provider == "indic_parler":
        # Local AI4Bharat Indic Parler TTS model
        from .tts_indic_parler import generate_speech_stream as _indic_parler
        return _indic_parler
    from .tts_edge import generate_speech_stream as _edge
    return _edge


def _consume_shadow(provider: str, text: str, preferred_language: str | None) -> None:
    started_at = time.perf_counter()
    first_chunk_at = None
    chunks = 0
    bytes_out = 0
    try:
        for chunk in _load_provider(provider)(text, preferred_language):
            if not chunk:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            chunks += 1
            bytes_out += len(chunk)
        total_s = time.perf_counter() - started_at
        ttfb_s = (first_chunk_at - started_at) if first_chunk_at is not None else total_s
        logger.info(
            "[TTS SHADOW] shadow_provider=%s ttfb_ms=%.1f total_ms=%.1f chunks=%d bytes=%d",
            provider,
            ttfb_s * 1000.0,
            total_s * 1000.0,
            chunks,
            bytes_out,
        )
    except Exception as exc:
        logger.warning("[TTS SHADOW] shadow_provider=%s failed without affecting live audio: %s", provider, exc)


def _stream_provider(
    provider: str,
    text: str,
    preferred_language: str | None,
    cartesia_voice_id: str | None = None,
    agent_id: str = "default",
    custom_voice: str | None = None,
):
    if provider == "cartesia":
        return _load_provider(provider)(text, preferred_language, voice_id=cartesia_voice_id)
    if provider == "sarvam":
        return _load_provider(provider)(text, preferred_language, agent_id=agent_id, speaker=custom_voice)
    if provider == "smallest":
        # Pass speaker / voice_id for male/female selection
        return _load_provider(provider)(text, preferred_language, agent_id=agent_id, speaker=custom_voice)

    if provider == "magpie":
    # NVIDIA Magpie automatically selects
    # Hindi or English voice based on language.
        return _load_provider(provider)(
            text,
            preferred_language,
            agent_id=agent_id,
        custom_voice=custom_voice
    )
    
    if provider == "indic_parler":
        return _load_provider(provider)(text, preferred_language)
    return _load_provider(provider)(text, preferred_language, agent_id=agent_id, custom_voice=custom_voice)


def time_stretch_ola(audio: np.ndarray, rate: float, frame_size: int = 512, hop_size: int = 128) -> np.ndarray:
    """Simple Overlap-Add (OLA) time stretching to speed up audio without changing pitch."""
    if rate == 1.0:
        return audio
    
    hop_out = hop_size
    hop_in = int(round(rate * hop_out))
    
    num_frames = int((len(audio) - frame_size) / hop_in)
    if num_frames <= 0:
        return audio
        
    output_len = num_frames * hop_out + frame_size
    output = np.zeros(output_len, dtype=np.float32)
    window = np.hanning(frame_size)
    window_sum = np.zeros(output_len, dtype=np.float32)
    
    for i in range(num_frames):
        start_in = i * hop_in
        frame = audio[start_in:start_in + frame_size].astype(np.float32)
        if len(frame) < frame_size:
            break
            
        start_out = i * hop_out
        output[start_out:start_out + frame_size] += frame * window
        window_sum[start_out:start_out + frame_size] += window
        
    window_sum[window_sum == 0] = 1.0
    output /= window_sum
    
    return np.clip(output, -32768, 32767).astype(np.int16)


def generate_speech_stream(
    text: str,
    preferred_language: str | None = None,
    agent_id: str = "default",
):
    """Yield live TTS audio from the selected provider."""
    if text and text.strip():
        try:
            from .speech_formatter import optimize_for_tts
            text = optimize_for_tts(text)
        except Exception:
            pass

    primary_provider = _configured_provider(agent_id)
    logger.info(
    "[TTS DEBUG] agent_id=%s | primary_provider=%s",
    agent_id,
    primary_provider,
)
    schema_cfg = _provider_config_from_agent_schema(agent_id)

    logger.info(
    "[TTS DEBUG] schema_cfg=%s",
    schema_cfg,
)

    cartesia_voice_id = schema_cfg.get("cartesia_voice_id")
    custom_voice = schema_cfg.get("voice")

    if _env_bool("TTS_SHADOW_MODE", False):
        shadow = _shadow_provider(primary_provider)
        if shadow != primary_provider:
            threading.Thread(
                target=_consume_shadow,
                args=(shadow, text, preferred_language),
                daemon=True,
            ).start()

    audio_chunks = []
    nonempty_yielded = False
    try:
        for chunk in _stream_provider(
            primary_provider,
            text,
            preferred_language,
            cartesia_voice_id=cartesia_voice_id,
            agent_id=agent_id,
            custom_voice=custom_voice,
        ):
            if chunk:
                nonempty_yielded = True
                audio_chunks.append(chunk)
    except Exception as exc:
        logger.exception("[TTS PROVIDER] primary=%s failed: %s", primary_provider, exc)

    if (
        not nonempty_yielded
        and _env_bool("TTS_FALLBACK_ENABLED", True)
        and text
        and text.strip()
    ):
        fallback_provider = _normalize_provider(
            os.getenv("TTS_FALLBACK_PROVIDER", "cartesia")
        )
        if fallback_provider == primary_provider:
            fallback_provider = "edge"
        logger.warning(
            "[TTS PROVIDER] primary=%s produced no audio; falling back to %s",
            primary_provider,
            fallback_provider,
        )
        try:
            for chunk in _stream_provider(
                fallback_provider,
                text,
                preferred_language,
                cartesia_voice_id=cartesia_voice_id,
                agent_id=agent_id,
                custom_voice=custom_voice,
            ):
                if chunk:
                    nonempty_yielded = True
                    audio_chunks.append(chunk)
        except Exception as fallback_exc:
            logger.exception("[TTS PROVIDER] fallback=%s failed: %s", fallback_provider, fallback_exc)

        if not nonempty_yielded and fallback_provider != "edge":
            logger.warning("[TTS PROVIDER] fallback=%s produced no audio; attempting final fallback to edge", fallback_provider)
            try:
                for chunk in _stream_provider(
                    "edge",
                    text,
                    preferred_language,
                    cartesia_voice_id=cartesia_voice_id,
                    agent_id=agent_id,
                    custom_voice=custom_voice,
                ):
                    if chunk:
                        audio_chunks.append(chunk)
            except Exception as edge_exc:
                logger.exception("[TTS PROVIDER] edge final fallback failed: %s", edge_exc)

    full_audio = b"".join(audio_chunks)
    if not full_audio:
        return

    chunk_size = 4096
    for i in range(0, len(full_audio), chunk_size):
        yield full_audio[i:i+chunk_size]


