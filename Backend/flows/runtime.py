"""Runtime processors for the local voice pipeline."""

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import os
import time
import concurrent.futures
import uuid
import random

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

try:
    import torch
except ImportError:
    torch = None

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=30)

# ── TTS generator sentinel (Fix #1: StopIteration in run_in_executor) ─────
# PEP 479 converts StopIteration raised inside a Future into RuntimeError.
# We catch it inside the thread before it can escape.
_GENERATOR_SENTINEL = object()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_next(gen):
    """Call next(gen) inside a thread. Returns _GENERATOR_SENTINEL on StopIteration."""
    try:
        return next(gen)
    except StopIteration:
        return _GENERATOR_SENTINEL
_ml_semaphore = asyncio.Semaphore(4)

try:
    from pipecat.frames.frames import AudioRawFrame, Frame, TextFrame, CancelFrame, EndFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:
    logging.error("pipecat-ai is not installed. Pipeline will fail.")
    FrameProcessor = object
    FrameDirection = None
    Frame = None
    TextFrame = None
    AudioRawFrame = None
    CancelFrame = None
    EndFrame = None

#from llm.llm import generate_response
from llm.llm import analyze_user_intent
from llm.llm_response_generator import generate_response_for_turn
from llm.language_utils import LanguageTracker, analyze_user_text, localize_template
from llm.state_manager import StateManager
from llm.pipeline_logger import pipeline_logger
from pipecat.frames.frames import StartFrame
from stt import config as stt_cfg

# Root-relative path for the agent schema
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA_PATH = os.path.join(_ROOT, "db", "agents", "real_estate")
STATE_SCHEMA_PATH = DEFAULT_SCHEMA_PATH

CALL_CONNECTED_TRIGGER = "[System: The call has just been connected. No user has spoken yet. Speak only for the current conversation node and do not transition.]"

try:
    from stt.stt import transcribe_audio
except ImportError:
    logging.warning("stt.stt.transcribe_audio not available yet. Using mock STT.")

    def transcribe_audio(audio_chunk: bytes, agent_id: str = "default", language: str | None = None) -> str:
        return "mock transcription"

try:
    from tts import generate_speech_stream
except ImportError:
    logging.warning("tts engine not found. Using mock TTS.")

    def generate_speech_stream(
        text: str,
        preferred_language: str | None = None,
        agent_id: str = "default",
    ):
        return iter([])

logger = logging.getLogger(__name__)

_GARBAGE_SINGLE_WORDS = {
    "ah", "bb", "eh", "er", "hm", "hmm", "mm", "oh", "uh", "um",
}
_SHORT_VALID_UTTERANCES = {"hello", "hi", "yeah", "yes", "no", "ok", "okay"}
_KNOWN_HALLUCINATION_PHRASES = (
    "if you have any questions please let me know",
    "mbc news",
    "please subscribe",
    "thanks for watching",
    "thank you for watching",
)


class AgentTextFrame(TextFrame):
    def __init__(self, text: str, language: str = "en"):
        super().__init__(text)
        self.language = language


class VoiceTurnState:
    """Shared speaking state to prevent STT from transcribing agent playback/echo."""

    def __init__(self):
        self.tts_active = False
        self.tts_release_at = 0.0
        self.session_language = "en"

    def is_stt_blocked(self) -> bool:
        return self.tts_active or time.monotonic() < self.tts_release_at

    def mark_tts_started(self) -> None:
        self.tts_active = True
        self.tts_release_at = 0.0

    def mark_tts_finished(self, cooldown_ms: int = stt_cfg.POST_TTS_STT_COOLDOWN_MS, duration_s: float = 0.0) -> None:
        self.tts_active = False
        # Do not block STT for full playback duration so user speech is immediately captured
        self.tts_release_at = time.monotonic() + (cooldown_ms / 1000.0)


class RealEstateLLMProcessor(FrameProcessor):
    """Turn user transcripts into LLM responses and manage node states with GenID sync."""

    def __init__(self, turn_state: VoiceTurnState | None = None, schema_path: str | None = None):
        super().__init__()
        self.turn_state = turn_state
        self.history: list[dict[str, str]] = []
        self.current_language = "en"
        self.language_tracker = LanguageTracker(initial_language=self.current_language)
        self.last_user_text = ""
        self.last_user_at = 0.0
        self._booted = False
        self.state_manager = StateManager(schema_path or STATE_SCHEMA_PATH)
        self._current_gen_id = 0
        self._fallback_replies = [
            "Sorry, I didn't catch that clearly. Could you repeat that once?",
            "I'm sorry, I missed that. Could you say it again?",
            "Apologies, my line dropped for a second. What was that?",
        ]
        

    async def process_frame(self, frame: Frame, direction: FrameDirection = None):  # type: ignore
        print("LLM RECEIVED:", type(frame), frame, direction)
        frame_type = type(frame).__name__
        frame_text = getattr(frame, "text", None)
        if frame_text is not None:
            logger.info(
                "[PIPELINE] LLM <- Frame type=%s direction=%s text=%s",
                frame_type,
                direction,
                str(frame_text),
            )
        else:
            logger.info("[PIPELINE] LLM <- Frame type=%s direction=%s", frame_type, direction)

        # Keep barge-in cancel signalling flowing to TTS, but avoid tripping
        # base-processor cancellation state for this LLM stage.
        if isinstance(frame, CancelFrame):
            logger.info("[PIPELINE] LLM -> Forwarding CancelFrame downstream")
            try:
                await self.push_frame(frame, direction)
                logger.info("[PIPELINE] LLM -> Forwarded non-text frame type=%s", frame_type)
            except BaseException as exc:
                logger.error("[PIPELINE] LLM -> Failed forwarding non-text frame type=%s: %s", frame_type, exc)
            return

        try:
            await super().process_frame(frame, direction)
        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> super().process_frame failed for type=%s: %s", frame_type, exc)
            # Continue for transcript frames so STT -> LLM -> TTS cannot be blocked.
            is_text_like = isinstance(frame, TextFrame) or hasattr(frame, "text")
            if not is_text_like and not isinstance(frame, StartFrame):
                return

        if isinstance(frame, StartFrame) and not self._booted:
            self._booted = True
            self.state_manager.reset_state()
            pipeline_logger.log_event("call_started", {"start_node": self.state_manager.start_node_id})
            await self.push_frame(frame, direction)

            logger.info("[PIPELINE] LLM -> Received StartFrame. Loading configured Smart Greeting...")
            try:
                    current_node = self.state_manager.get_current_node()

                    if isinstance(current_node, dict):
                        reply = str(current_node.get("response") or "").strip()
                    else:
                        reply = ""

                    if not reply:
                        reply = "Hello, how can I help you today?"

            except Exception as exc:
                    logger.warning(
                        "[PIPELINE] LLM -> Failed to load configured greeting: %s",
                        exc,
                    )
                    reply = "Hello, how can I help you today?"

            if reply:
                self.history.append({"role": "assistant", "content": reply})
                frame_out = AgentTextFrame(reply, language=self.current_language)
                _ensure_frame_runtime_attrs(frame_out)
                frame_out.gen_id = self._current_gen_id
                await self.push_frame(frame_out, direction)
            return

        # Accept text-like frames defensively to avoid strict class-mismatch drops.
        is_text_like = isinstance(frame, TextFrame) or hasattr(frame, "text")
        if not is_text_like:
            logger.info("[PIPELINE] LLM -> Passing through non-text frame type=%s", frame_type)
            try:
                await self.push_frame(frame, direction)
                logger.info("[PIPELINE] LLM -> Forwarded non-text frame type=%s", frame_type)
            except BaseException as exc:
                logger.error("[PIPELINE] LLM -> Failed forwarding non-text frame type=%s: %s", frame_type, exc)
            return

        user_text = str(getattr(frame, "text", "")).strip()
        if not user_text:
            logger.info("[PIPELINE] LLM -> Empty text payload received from frame type=%s", frame_type)
            return

        # ISSUE 2 FIX: Ignore all transcripts after reaching a terminal state
        if self.state_manager and getattr(self.state_manager, "_session_ended", False):
            logger.warning("[PIPELINE] LLM -> Transcript ignored. Conversation already completed.")
            return

        logger.info("[PIPELINE] LLM <- User transcript (%s): %s", type(frame).__name__, user_text)

        # Always process transcript frames (no strict intent/node filtering at this layer).
        user_analysis = analyze_user_text(user_text, fallback=self.current_language)
        if user_analysis.actionable and user_analysis.cleaned_text:
            user_text = user_analysis.cleaned_text
        last_assistant = next(
            (
                item.get("content", "")
                for item in reversed(self.history)
                if item.get("role") == "assistant"
            ),
            "",
        )
        current_node = self.state_manager.get_current_node() if self.state_manager else None
        current_prompt = ""
        if isinstance(current_node, dict):
            current_prompt = str(current_node.get("response") or "")
        if _is_likely_agent_echo(user_text, last_assistant, current_prompt):
            logger.info("[PIPELINE] LLM -> Dropping likely agent echo transcript: %s", user_text)
            return

        # 1. INCREMENT GEN_ID on every non-empty user turn that reaches LLM
        self._current_gen_id += 1
        logger.info("New User Turn: gen_id = %d", self._current_gen_id)
        self.last_user_text = user_text
        self.last_user_at = time.monotonic()
        if user_analysis.actionable:
            self.current_language, _ = self.language_tracker.observe(user_text)

            # Sync the detected language with StateManager so
            # execute_transition() and response generation use
            # the language of the CURRENT user turn.
            if self.state_manager:
                self.state_manager.set_active_language(self.current_language)

            if self.turn_state:
                self.turn_state.session_language = self.current_language
        
        # Sync User Text Frame with current GenID
        _ensure_frame_runtime_attrs(frame)
        frame.gen_id = self._current_gen_id
        try:
            await self.push_frame(frame, direction)
            logger.info("[PIPELINE] LLM -> Forwarded user transcript frame gen_id=%d", self._current_gen_id)
        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> Failed forwarding user transcript frame: %s", exc)

        logger.info("[PIPELINE] LLM -> Generating response for transcript: %s", user_text)
        # PRE-TTS FILLER (Latency Masking) — only enabled when TTS_FILLER_ENABLED=true
        # Disabled by default when using local Indic Parler (no cloud latency to mask)
        _filler_enabled = os.getenv("TTS_FILLER_ENABLED", "false").strip().lower() in ("1", "true", "yes")
        if _filler_enabled:
            try:
                from flows.audio_fillers import get_random_filler
                filler_audio = get_random_filler()
                if filler_audio:
                    filler_frame = AudioRawFrame(audio=filler_audio, sample_rate=24000, num_channels=1)
                    _ensure_frame_runtime_attrs(filler_frame)
                    await self.push_frame(filler_frame, direction)
                    logger.info("[PIPELINE] LLM -> Injected pre-TTS filler audio (%d bytes)", len(filler_audio))
            except Exception as e:
                logger.warning("[PIPELINE] LLM -> Failed to inject filler audio: %s", e)

        


        response = None
        reply = None
        llm_failed = False
        is_terminal = False

        try:
            # 1. Convert conversation history into text context
            conversation_context = "\n".join(
                f"{message.get('role', 'unknown')}: "
                f"{str(message.get('content', '')).strip()}"
                for message in self.history[-8:]
                if str(message.get("content", "")).strip()
            )

            # 2. Analyze user's intent
            schema = self.state_manager.schema
            domain = str(
                (schema.get("agent_metadata") or {}).get("agent_type")
                or schema.get("agent_type")
                or "real_estate"
            )
            intent_analysis = await analyze_user_intent(
                user_text,
                conversation_context,
                domain=domain,
                schema=self.state_manager.schema,
            )

            entities_obj = intent_analysis.entities
            entities_dump = (
                entities_obj.model_dump() if hasattr(entities_obj, "model_dump")
                else entities_obj.dict()
            )
            intent_data = {
                "intent": intent_analysis.intent,
                "confidence_score": intent_analysis.confidence_score,
                "entities": {k: v for k, v in entities_dump.items() if v is not None},
            }

            # 3. Execute state-machine transition
            turn = self.state_manager.execute_transition(
                user_text,
                intent_data
            )

            # 4. Generate response for the current state/node
            response = await generate_response_for_turn(
                turn,
                self.state_manager
            )

            # 5. Normalize generate_response_for_turn() output
            if isinstance(response, tuple):
                reply = str(response[0] or "").strip()

                if len(response) > 1:
                    is_terminal = bool(response[1])

            else:
                reply = str(response or "").strip()

                is_terminal = bool(
                    getattr(turn, "is_terminal", False)
                    or getattr(turn, "terminal", False)
                    or getattr(turn, "end_call", False)
                )

            logger.info(
                "[PIPELINE] LLM -> Response generated: %r | terminal=%s",
                reply,
                is_terminal,
            )

        except asyncio.CancelledError:
            raise

        except asyncio.TimeoutError:
            logger.warning(
                "[PIPELINE] LLM Timeout — emitting system busy signal."
            )
            llm_failed = True
            reply = localize_template(
                "Give me just one moment...",
                self.current_language
            )

        except Exception as exc:
            logger.exception(
                "[PIPELINE] LLM -> Generation error after STT handoff: %s",
                exc
            )
            llm_failed = True

            reply = localize_template(
                random.choice(self._fallback_replies),
                self.current_language
            )
            # Keep session alive even if state logic reaches a terminal branch.
            if isinstance(exc, KeyboardInterrupt):
                try:
                    self.state_manager.reset_state()
                    logger.warning("[PIPELINE] LLM -> State reset after terminal-state interrupt.")
                except Exception:
                    pass
            reply = localize_template(random.choice(self._fallback_replies), self.current_language)

        # ---------------------------------------------------------------------
        # FIX A3 — final safety net against word-for-word repetition.
        # If the state machine stalls (no matching edge), the same node template
        # is regenerated and the caller hears the identical sentence again.
        # Swap in a localized rephrase instead of repeating verbatim.
        # ---------------------------------------------------------------------
        try:
            last_bot = next(
                (
                    str(m.get("content", "") or "")
                    for m in reversed(self.history)
                    if m.get("role") == "assistant"
                ),
                "",
            )
            if (
                reply
                and last_bot
                and reply.strip().lower() == last_bot.strip().lower()
            ):
                from llm.llm_response_generator import _get_anti_repeat_nudge

                current = self.state_manager.get_current_node() or {}
                nudge = _get_anti_repeat_nudge(
                    current,
                    self.current_language,
                    context=getattr(self.state_manager, "conversation_data", {}) or {},
                )
                if nudge and nudge.strip().lower() != last_bot.strip().lower():
                    logger.warning(
                        "[ANTI-REPEAT] Identical reply blocked on node=%r; using rephrase",
                        getattr(self.state_manager, "current_node_id", None),
                    )
                    reply = nudge
        except Exception as exc:
            logger.warning("[ANTI-REPEAT] Guard failed (non-fatal): %s", exc)

        """ logger.warning(
            "[PIPELINE DEBUG] reply=%r | llm_failed=%s | state_manager_node=%r | language=%r",
            reply,
            llm_failed,
            getattr(self.state_manager, "current_node_id", None),
            self.current_language,
        ) """


        # Only use fallback when the model actually failed.
        if not reply:
            if llm_failed:
                logger.warning(
                    "[PIPELINE] LLM -> Empty reply after LLM failure, using fallback response."
                )
                reply = localize_template(
                    random.choice(self._fallback_replies),
                    self.current_language
                )
            else:
                logger.warning(
                    "[PIPELINE] LLM -> Empty response from StateManager/ResponseGenerator."
                )
                self.history.append({
                    "role": "user",
                    "content": user_text
                })

                if len(self.history) > 12:
                    self.history = self.history[-12:]

                return

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        # M4 FIX: Increase history cap from 8 to 12 messages to preserve early context
        if len(self.history) > 12:
            self.history = self.history[-12:]
            
        logger.info("[LLM] Response Generated len=%d text=%s...", len(reply), reply[:50])
        
        frame_out = AgentTextFrame(reply, language=self.current_language)
        _ensure_frame_runtime_attrs(frame_out)
        frame_out.gen_id = self._current_gen_id # TAG THE REPLY
        try:
            await self.push_frame(frame_out, direction)
            logger.info("[PIPELINE] LLM -> Forwarded agent reply frame gen_id=%d", self._current_gen_id)

            # ISSUE 1 FIX: Push EndFrame to gracefully tear down pipecat pipeline when terminal state reached
            if is_terminal:
                logger.info("[PIPELINE] LLM -> Terminal state reached. Sending EndFrame.")
                from pipecat.frames.frames import EndFrame
                await asyncio.sleep(0.5)  # Ensure TTS has time to generate the goodbye audio before tear-down
                await self.push_frame(EndFrame(), direction)

        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> Failed forwarding agent reply frame: %s", exc)


class VADProcessor(FrameProcessor):
    """Voice Activity Detection preprocessing layer."""

    def __init__(
        self,
        turn_state: VoiceTurnState | None = None,
        min_voice_start_ms: int | None = None,
        max_speech_duration_ms: int | None = None,
        silence_timeout_ms: int | None = None,
        noise_multiplier: float | None = None,
        base_threshold: float | None = None,
        max_threshold: float | None = None,
    ):
        super().__init__()
        self.turn_state = turn_state
        self.audio_buffer = bytearray()
        
        # Expose parameters as configurable, falling back to stt_cfg
        effective_min_ms = min_voice_start_ms or getattr(stt_cfg, "VAD_MIN_VOICE_START_MS", stt_cfg.MIN_CHUNK_MS)
        effective_max_ms = max_speech_duration_ms or getattr(stt_cfg, "VAD_MAX_SPEECH_DURATION_MS", 4500)
        effective_trailing_ms = silence_timeout_ms or getattr(stt_cfg, "VAD_SILENCE_TIMEOUT_MS", 600)
        
        self.min_chunk_bytes = _ms_to_bytes(effective_min_ms, stt_cfg.TARGET_SAMPLE_RATE)
        self.max_chunk_bytes = _ms_to_bytes(effective_max_ms, stt_cfg.TARGET_SAMPLE_RATE)
        self.trailing_window_bytes = _ms_to_bytes(effective_trailing_ms, stt_cfg.TARGET_SAMPLE_RATE)
        
        self.noise_floor_percentile = getattr(stt_cfg, "VAD_NOISE_FLOOR_PERCENTILE", 10.0)
        self.noise_multiplier = noise_multiplier or getattr(stt_cfg, "VAD_NOISE_MULTIPLIER", 6.0)
        self.base_threshold = base_threshold or getattr(stt_cfg, "VAD_BASE_THRESHOLD", 0.007)
        self.max_threshold = max_threshold or getattr(stt_cfg, "VAD_MAX_THRESHOLD", 0.018)
        self.is_speaking = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        self._last_voice_at = 0.0
        self._speech_end_silence_ms = float(effective_trailing_ms)
        self._barge_in_min_ms = 550.0
        self._barge_in_sent = False
        self._was_stt_blocked = False
        self._cooldown_until = 0.0
        self.noise_floor = 0.001
        self._rms_history = []

    async def process_frame(self, frame: Frame, direction: FrameDirection = None):  # type: ignore
        await super().process_frame(frame, direction)
        if not isinstance(frame, AudioRawFrame):
            await self.push_frame(frame, direction)
            return

        pcm16 = _ensure_pcm16(frame.audio, frame.sample_rate, stt_cfg.TARGET_SAMPLE_RATE)
        if not pcm16: return

        if time.monotonic() < self._cooldown_until:
            return

        is_blocked = bool(self.turn_state and self.turn_state.is_stt_blocked())
        if is_blocked and not self._was_stt_blocked:
            self._voiced_ms = 0.0
            self._barge_in_sent = False
        self._was_stt_blocked = is_blocked

        # Calculate current RMS
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        chunk_rms = float(np.sqrt(np.mean(samples**2)) / 32768.0)
        chunk_duration_ms = (len(samples) / stt_cfg.TARGET_SAMPLE_RATE) * 1000.0
        if chunk_rms > 0.0005:
            logger.info(
                "[VAD] <- Audio frame bytes=%d duration_ms=%.1f rms=%.4f speaking=%s blocked=%s",
                len(pcm16),
                chunk_duration_ms,
                chunk_rms,
                self.is_speaking,
                bool(self.turn_state and self.turn_state.is_stt_blocked()),
            )
        
        # Continuous Calibration: Track bottom percentile of energy as noise floor
        if not self.is_speaking:
            self._rms_history.append(chunk_rms)
            if len(self._rms_history) > 50:
                self._rms_history.pop(0)
                self.noise_floor = float(np.percentile(self._rms_history, self.noise_floor_percentile))
        
        # Adaptive activation threshold tuned for normal/soft mic speech:
        dynamic_threshold = max(self.noise_floor * 1.8, self.noise_floor + 0.0008, 0.0012)
        dynamic_threshold = min(dynamic_threshold, self.max_threshold)
        now_mono = time.monotonic()
        voice_presence_threshold = max(dynamic_threshold * 0.50, self.noise_floor + 0.0005)

        # Detect speech start
        if not self.is_speaking:
            if chunk_rms > dynamic_threshold:
                self._voice_hits += 1
            else:
                self._voice_hits = max(0, self._voice_hits - 1)
                self._voiced_ms = 0.0
                return
            if self._voice_hits < 1:
                return
            self.is_speaking = True
            self._voice_hits = 0
            self._voiced_ms = 0.0
            self._barge_in_sent = False
            self._last_voice_at = now_mono
            self.audio_buffer.clear()
            logger.info(
                "[VAD EVENT] speech_start timestamp=%.3f rms=%.4f threshold=%.4f floor=%.4f source_rate=%d",
                time.time(),
                chunk_rms,
                dynamic_threshold,
                self.noise_floor,
                getattr(frame, "sample_rate", 0),
            )

        self._voiced_ms += chunk_duration_ms
        if chunk_rms >= voice_presence_threshold:
            self._last_voice_at = now_mono

        # Barge-in cancellation: only when explicitly enabled and speech is loud & sustained.
        barge_in_enabled = _env_bool("ENABLE_BARGE_IN", False)
        barge_in_threshold = max(dynamic_threshold * 2.5, 0.008)
        if (
            barge_in_enabled
            and self.turn_state
            and self.turn_state.is_stt_blocked()
            and chunk_rms >= barge_in_threshold
            and self._voiced_ms >= max(self._barge_in_min_ms, 1200.0)
        ):
            if not self._barge_in_sent:
                self._barge_in_sent = True
                logger.info(
                    "[VAD] -> Sustained speech detected during TTS (%.0fms). Emitting CancelFrame.",
                    self._voiced_ms,
                )
                await self.push_frame(CancelFrame(), direction)
                if hasattr(self.turn_state, "mark_tts_finished"):
                    self.turn_state.mark_tts_finished(0)

        self.audio_buffer.extend(pcm16)
        if len(self.audio_buffer) < self.min_chunk_bytes:
            return

        silence_elapsed_ms = (now_mono - self._last_voice_at) * 1000.0
        if len(self.audio_buffer) < self.max_chunk_bytes and silence_elapsed_ms < min(self._speech_end_silence_ms, 400.0):
            return
        if len(self.audio_buffer) < self.max_chunk_bytes and not _has_trailing_silence(
            self.audio_buffer,
            self.trailing_window_bytes,
            voice_presence_threshold,
        ):
            return

        chunk = bytes(self.audio_buffer)
        buffered_ms = (len(self.audio_buffer) / 2.0) / float(stt_cfg.TARGET_SAMPLE_RATE) * 1000.0
        logger.info(
            "[VAD EVENT] speech_end timestamp=%.3f buffered_ms=%.1f bytes=%d silence_ms=%.1f voiced_ms=%.1f",
            time.time(),
            buffered_ms,
            len(self.audio_buffer),
            silence_elapsed_ms,
            self._voiced_ms,
        )
        self.audio_buffer.clear()
        self.is_speaking = False
        self._barge_in_sent = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        
        # Emit complete buffered audio frame to downstream STT
        out_frame = AudioRawFrame(audio=chunk, sample_rate=stt_cfg.TARGET_SAMPLE_RATE, num_channels=1)
        _ensure_frame_runtime_attrs(out_frame)
        await self.push_frame(out_frame, direction)


""" class RealEstateSTTProcessor(FrameProcessor):
    Low-latency STT with Adaptive VAD (Noise Floor Calibration).

    def __init__(self, turn_state: VoiceTurnState | None = None, agent_id: str = "default", vad_enabled: bool = True):
        super().__init__()
        self.agent_id = agent_id or "default"
        self.vad_enabled = vad_enabled
        self.audio_buffer = bytearray()
        effective_max_ms = max(stt_cfg.MAX_CHUNK_MS, 4500)
        effective_trailing_ms = max(stt_cfg.TRAILING_SILENCE_MS, int(os.getenv("STT_EFFECTIVE_TRAILING_MS", "600")))
        self.min_chunk_bytes = _ms_to_bytes(stt_cfg.MIN_CHUNK_MS, stt_cfg.TARGET_SAMPLE_RATE)
        # Keep phrase-level chunks, but do not wait over a second after the user stops speaking.
        self.max_chunk_bytes = _ms_to_bytes(effective_max_ms, stt_cfg.TARGET_SAMPLE_RATE)
        self.trailing_window_bytes = _ms_to_bytes(effective_trailing_ms, stt_cfg.TARGET_SAMPLE_RATE)
        self.last_emitted_text = ""
        self.last_emit_at = 0.0
        self.is_speaking = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        self._last_voice_at = 0.0
        self._speech_end_silence_ms = float(effective_trailing_ms)
        self._barge_in_min_ms = 550.0
        self._barge_in_sent = False
         self._cooldown_until = 0.0
        self.turn_state = turn_state
        # 6. ADAPTIVE VAD (Continuous Calibration)
        self.noise_floor = 0.001 # Start low and adapt
        self._rms_history = [] 

        self._cooldown_until = 0.0
        self.turn_state = turn_state

            # Language tracking for dynamic STT language detection
        self.language_tracker = LanguageTracker(initial_language="en")

            # 6. ADAPTIVE VAD (Continuous Calibration)
        self.noise_floor = 0.001  # Start low and adapt
        self._rms_history = []

    async def process_frame(self, frame: Frame, direction: FrameDirection = None):  # type: ignore
        await super().process_frame(frame, direction)
        if not isinstance(frame, AudioRawFrame):
            await self.push_frame(frame, direction)
            return

        if not self.vad_enabled:
            pcm16 = _ensure_pcm16(frame.audio, frame.sample_rate, stt_cfg.TARGET_SAMPLE_RATE)
            if not pcm16: return

            logger.info("[PIPELINE] STT -> Pre-segmented speech received, transcribing bytes=%d", len(pcm16))
            try:
                # 2. CIRCUIT BREAKER (STT Timeout)
                async with _ml_semaphore:
                     session_lang = "en"
                    if self.turn_state and hasattr(self.turn_state, "session_language"):
                        session_lang = self.turn_state.session_language 

                    session_lang = None

                    if self.turn_state and hasattr(self.turn_state, "session_language"):
                        session_lang = self.turn_state.session_language

                        if (
                            session_lang == "en"
                            and self.language_tracker
                            and not self.language_tracker._determined
                        ):
                            session_lang = None

                    # Dynamic STT timeout: if running on CPU, allow more time (45s) to avoid dropping chunks.
                    stt_timeout = 3.5 if (torch and torch.cuda.is_available()) else 45.0
                    text = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            _executor,
                            lambda: transcribe_audio(pcm16, self.agent_id, language=session_lang),
                        ),
                        timeout=stt_timeout
                    )
            except asyncio.TimeoutError:
                logger.error("STT Timeout (Skipping chunk)")
                return
            except Exception as e:
                logger.error("STT Execution Error: %s", e)
                return

            normalized_text = _normalize_text(text)
            if not normalized_text or len(normalized_text) < stt_cfg.MIN_TRANSCRIPT_CHARS or not _is_actionable_transcript(text):
                logger.warning(
                    "[PIPELINE] STT -> Dropped empty/non-actionable transcript bytes=%d text=%r",
                    len(pcm16),
                    text,
                )
                self._cooldown_until = time.monotonic() + 0.35
                return

            now = time.monotonic()
            if _is_duplicate_text(text, self.last_emitted_text) and (now - self.last_emit_at) < stt_cfg.DUPLICATE_TEXT_WINDOW_S:
                self._cooldown_until = now + 0.35
                return

            self.last_emitted_text = text
            self.last_emit_at = now
            self._cooldown_until = now + 0.20
            logger.info("[PIPELINE] STT -> Emitting transcript: %s", text)
            text_frame = TextFrame(text=text)
            _ensure_frame_runtime_attrs(text_frame)
            await self.push_frame(text_frame, direction)
            return

        pcm16 = _ensure_pcm16(frame.audio, frame.sample_rate, stt_cfg.TARGET_SAMPLE_RATE)
        if not pcm16: return

        # Calculate current RMS
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        chunk_rms = float(np.sqrt(np.mean(samples**2)) / 32768.0) if samples.size > 0 else 0.0
        chunk_duration_ms = (len(samples) / stt_cfg.TARGET_SAMPLE_RATE) * 1000.0

        # Continuous Calibration: Track bottom 10% of energy as noise floor
        if not self.is_speaking:
            self._rms_history.append(chunk_rms)
            if len(self._rms_history) > 50: # tracking ~1s window for faster startup
                self._rms_history.pop(0)
                self.noise_floor = float(np.percentile(self._rms_history, 10))

        dynamic_threshold = max(self.noise_floor * 3.0, self.noise_floor + 0.0015, 0.002)
        dynamic_threshold = min(dynamic_threshold, 0.018)
        now_mono = time.monotonic()
        voice_presence_threshold = max(dynamic_threshold * 0.40, self.noise_floor + 0.001)
        strong_voice_threshold = max(dynamic_threshold * 1.10, self.noise_floor + 0.002)

        # Ignore inbound mic audio while TTS is actively speaking, unless strong barge-in speech occurs.
        if self.turn_state and self.turn_state.is_stt_blocked():
            if chunk_rms >= strong_voice_threshold:
                logger.info("[PIPELINE] STT -> Barge-in speech detected during TTS block (rms=%.4f threshold=%.4f). Unblocking STT.", chunk_rms, strong_voice_threshold)
                self.turn_state.tts_active = False
                self.turn_state.tts_release_at = 0.0
                await self.push_frame(CancelFrame(), direction)
            else:
                self.audio_buffer.clear()
                self.is_speaking = False
                self._voice_hits = 0
                self._voiced_ms = 0.0
                self._barge_in_sent = False
                return

        if time.monotonic() < self._cooldown_until:
            return

        if logger.isEnabledFor(logging.INFO) and chunk_rms > 0.0005:
            logger.info(
                "[PIPELINE] STT <- Audio frame bytes=%d duration_ms=%.1f rms=%.4f speaking=%s blocked=%s",
                len(pcm16),
                chunk_duration_ms,
                chunk_rms,
                self.is_speaking,
                bool(self.turn_state and self.turn_state.is_stt_blocked()),
            )

        # Crucial: do not send silence/background chunks to cloud STT.
        if not self.is_speaking:
            if chunk_rms >= strong_voice_threshold:
                # One clearly strong frame is enough to start speech immediately.
                self._voice_hits = 2
            elif chunk_rms > dynamic_threshold:
                self._voice_hits += 1
            else:
                # Decay instead of hard-reset to tolerate tiny dips between syllables.
                self._voice_hits = max(0, self._voice_hits - 1)
                self._voiced_ms = 0.0
                return
            # Require two voice hits for normal speech starts.
            if self._voice_hits < 2:
                return
            self.is_speaking = True
            self._voice_hits = 0
            self._voiced_ms = 0.0
            self._barge_in_sent = False
            self._last_voice_at = now_mono
            self.audio_buffer.clear()
            logger.info(
                "[VAD DEBUG] RMS=%.4f threshold=%.4f floor=%.4f speaking=True",
                chunk_rms,
                dynamic_threshold,
                self.noise_floor,
            )
            logger.info(
                "[VAD EVENT] speech_start timestamp=%.3f rms=%.4f threshold=%.4f floor=%.4f source_rate=%d",
                time.time(),
                chunk_rms,
                dynamic_threshold,
                self.noise_floor,
                getattr(frame, "sample_rate", 0),
            )

        self._voiced_ms += chunk_duration_ms
        if chunk_rms >= voice_presence_threshold:
            self._last_voice_at = now_mono

        # Barge-in cancellation: only when TTS is actively speaking and speech is sustained.
        if (
            not self._barge_in_sent
            and self.turn_state
            and self.turn_state.tts_active
            and self._voiced_ms >= self._barge_in_min_ms
        ):
            self._barge_in_sent = True
            logger.info(
                "[PIPELINE] STT -> Sustained speech detected during TTS (%.0fms). Emitting CancelFrame.",
                self._voiced_ms,
            )
            await self.push_frame(CancelFrame(), direction)

        self.audio_buffer.extend(pcm16)
        if len(self.audio_buffer) < self.min_chunk_bytes:
            return

        silence_elapsed_ms = (now_mono - self._last_voice_at) * 1000.0
        if len(self.audio_buffer) < self.max_chunk_bytes and silence_elapsed_ms < self._speech_end_silence_ms:
            return
        if len(self.audio_buffer) < self.max_chunk_bytes and not _has_trailing_silence(
            self.audio_buffer,
            self.trailing_window_bytes,
            voice_presence_threshold,
        ):
            return

        chunk = bytes(self.audio_buffer)
        buffered_ms = (len(self.audio_buffer) / 2.0) / float(stt_cfg.TARGET_SAMPLE_RATE) * 1000.0
        logger.info(
            "[VAD EVENT] speech_end timestamp=%.3f buffered_ms=%.1f bytes=%d silence_ms=%.1f voiced_ms=%.1f",
            time.time(),
            buffered_ms,
            len(self.audio_buffer),
            silence_elapsed_ms,
            self._voiced_ms,
        )
        self.audio_buffer.clear()
        self.is_speaking = False
        self._barge_in_sent = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        logger.info("[PIPELINE] STT -> Sending chunk to transcription bytes=%d", len(chunk))

        try:
    # 2. CIRCUIT BREAKER (STT Timeout)
            async with _ml_semaphore:
                session_lang = "en"

            if self.turn_state and hasattr(self.turn_state, "session_language"):
                session_lang = self.turn_state.session_language

            # If language has not been determined yet,
            # let STT auto-detect instead of forcing English.
            if (
                session_lang == "en"
                and self.language_tracker
                and not self.language_tracker._determined
            ):
                session_lang = None

            logger.info(
                "[PIPELINE] STT -> Transcribing with language=%s",
                session_lang or "auto",
            )

                # Dynamic STT timeout:
                # CPU -> 45s, GPU -> 3.5s
            stt_timeout = 3.5 if (torch and torch.cuda.is_available()) else 45.0

            text = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _executor,
                    lambda: transcribe_audio(
                        chunk,
                        self.agent_id,
                        language=session_lang,
                    ),
                ),
                timeout=stt_timeout
            )

        except asyncio.TimeoutError:
            logger.error("STT Timeout (Skipping chunk)")
            return

        except Exception as e:
            logger.error("STT Execution Error: %s", e)
            return
            
        normalized_text = _normalize_text(text)
        if not normalized_text or len(normalized_text) < stt_cfg.MIN_TRANSCRIPT_CHARS or not _is_actionable_transcript(text):
            logger.warning(
                "[PIPELINE] STT -> Dropped empty/non-actionable transcript bytes=%d text=%r",
                len(chunk),
                text,
            )
            self._cooldown_until = time.monotonic() + 0.35
            return

        now = time.monotonic()
        if _is_duplicate_text(text, self.last_emitted_text) and (now - self.last_emit_at) < stt_cfg.DUPLICATE_TEXT_WINDOW_S:
            self._cooldown_until = now + 0.35
            return

        self.last_emitted_text = text
        self.last_emit_at = now
        self._cooldown_until = now + 0.20
        logger.info("[PIPELINE] STT -> Emitting transcript: %s", text)
        text_frame = TextFrame(text=text)
        logger.info(
            "[PIPELINE] STT -> Transcript frame_type=%s is_text_frame=%s",
            type(text_frame).__name__,
            isinstance(text_frame, TextFrame),
        )
        _ensure_frame_runtime_attrs(text_frame)
        await self.push_frame(text_frame, direction) """


class RealEstateSTTProcessor(FrameProcessor):
    """
    STT processor without the old VADProcessor.

    Mic audio arrives in very small chunks (usually ~32 ms).
    We buffer those chunks and use simple audio-energy endpointing
    so Groq STT receives one complete utterance instead of every frame.
    """

    def __init__(self, turn_state=None, agent_id="default", vad_enabled=False):
        super().__init__()

        self.agent_id = agent_id or "default"
        self.turn_state = turn_state

        # Kept only for compatibility with main.py.
        # The old VADProcessor is NOT used.
        self.vad_enabled = False

        self.audio_buffer = bytearray()

        # Audio endpointing settings
        self.target_sample_rate = stt_cfg.TARGET_SAMPLE_RATE

        self.min_speech_ms = float(
            os.getenv("STT_MIN_SPEECH_MS", "300")
        )

        self.endpoint_silence_ms = float(
            os.getenv("STT_ENDPOINT_SILENCE_MS", "650")
        )

        self.max_utterance_ms = float(
            os.getenv("STT_MAX_UTTERANCE_MS", "6000")
        )

        self.speech_rms_threshold = float(
            os.getenv("STT_SPEECH_RMS_THRESHOLD", "0.008")
        )

        # Current utterance state
        self.speech_started = False
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.utterance_ms = 0.0

        # Last emitted transcript
        self.last_emitted_text = ""
        self.last_emit_at = 0.0

        # STT task state
        self._transcribing = False
        self._pending_utterances = []

        self.language_tracker = LanguageTracker(initial_language="en")

        # Noise estimation
        self.noise_floor = 0.001

        logger.info(
            "[STT] Initialized | endpoint=%sms | min_speech=%sms | max=%sms | rms_threshold=%s",
            self.endpoint_silence_ms,
            self.min_speech_ms,
            self.max_utterance_ms,
            self.speech_rms_threshold,
        )

    def _reset_utterance(self):
        self.audio_buffer.clear()
        self.speech_started = False
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        self.utterance_ms = 0.0

    def _calculate_rms(self, pcm16: bytes) -> float:
        if not pcm16:
            return 0.0

        try:
            samples = np.frombuffer(pcm16, dtype=np.int16)

            if samples.size == 0:
                return 0.0

            samples_float = samples.astype(np.float32) / 32768.0

            return float(
                np.sqrt(np.mean(np.square(samples_float)))
            )

        except Exception:
            return 0.0

    def _is_speech(self, rms: float) -> bool:
        """
        Lightweight energy gate.

        This is NOT the old VADProcessor.
        It only prevents 32 ms microphone frames from being
        sent individually to the STT API.
        """

        # Slowly learn the microphone noise floor while idle.
        if not self.speech_started:

            if self.noise_floor <= 0:
                self.noise_floor = rms

            elif rms < max(
                self.speech_rms_threshold,
                self.noise_floor * 2.0,
            ):
                self.noise_floor = (
                    self.noise_floor * 0.95
                    + rms * 0.05
                )

        dynamic_threshold = max(
            self.speech_rms_threshold,
            self.noise_floor * 2.5,
        )

        return rms >= dynamic_threshold

    async def _transcribe_audio(self, pcm16: bytes):
        if not pcm16:
            return

        duration_ms = (
            len(pcm16)
            / 2
            / self.target_sample_rate
            * 1000
        )

        logger.info(
            "[PIPELINE] STT -> Complete utterance ready | bytes=%d | duration=%.0fms",
            len(pcm16),
            duration_ms,
        )

        try:
            async with _ml_semaphore:

                session_lang = None

                if (
                    self.turn_state
                    and self.turn_state.session_language
                ):
                    session_lang = (
                        self.turn_state.session_language
                    )

                    if (
                        session_lang == "en"
                        and self.language_tracker
                        and not self.language_tracker._determined
                    ):
                        session_lang = None

                stt_timeout = 45.0

                text = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _executor,
                        lambda: transcribe_audio(
                            pcm16,
                            language=session_lang,
                        ),
                    ),
                    timeout=stt_timeout,
                )

            if not text:
                logger.info("[STT] Empty transcript ignored.")
                return

            text = text.strip()

            if not text:
                return

            normalized_text = _normalize_text(text)

            if not normalized_text:
                return

            if len(normalized_text) < 2:
                logger.info(
                    "[STT] Short transcript ignored: %r",
                    text,
                )
                return

            if not _is_actionable_transcript(text):
                logger.info(
                    "[STT] Non-actionable/hallucinated transcript ignored: %r",
                    text,
                )
                return

            """ now = time.monotonic() """

            """ if _is_duplicate_text(
                text,
                self.last_emitted_text,
                self.last_emit_at,
            ):
                logger.info(
                    "[STT] Duplicate transcript ignored: %r",
                    text,
                )
                return """

            if _is_duplicate_text(
                text,
                self.last_emitted_text,
            ):
                logger.info(
                    "[STT] Duplicate transcript ignored: %r",
                    text,
                )
                return      

            """ if _is_likely_agent_echo(text):
                logger.info(
                    "[STT] Possible agent echo ignored: %r",
                    text,
                )
                return

            self.last_emitted_text = text
            self.last_emit_at = time.monotonic()

            logger.info(
                "[PIPELINE] STT -> User transcript: %r",
                text,
            )"""

            text_frame = TextFrame(text=text)

            try:
                _ensure_frame_runtime_attrs(text_frame)
            except Exception:
                pass

            await self.push_frame(
                text_frame,
                FrameDirection.DOWNSTREAM,
            )

        except asyncio.TimeoutError:
            logger.warning(
                "[STT] Groq transcription timed out."
            )

        except Exception as exc:
            logger.exception(
                "[STT] Transcription failed: %s",
                exc,
            )

    async def _run_transcription(self, pcm16: bytes):

        self._transcribing = True

        try:
            await self._transcribe_audio(pcm16)

        finally:
            self._transcribing = False

            # Process any utterance that arrived while
            # the previous STT request was running.
            if self._pending_utterances:

                next_audio = self._pending_utterances.pop(0)

                if next_audio:
                    asyncio.create_task(
                        self._run_transcription(next_audio)
                    )

    def _submit_utterance(self, pcm16: bytes):

        if not pcm16:
            return

        if self._transcribing:
            self._pending_utterances.append(pcm16)

            logger.info(
                "[STT] STT busy; queued next utterance. queue=%d",
                len(self._pending_utterances),
            )

            return

        asyncio.create_task(
            self._run_transcription(pcm16)
        )

    def _finish_utterance(self):

        if not self.audio_buffer:
            self._reset_utterance()
            return

        # Do not send extremely short noises/clicks.
        if self.speech_ms < self.min_speech_ms:

            logger.info(
                "[STT] Ignoring short audio: %.0fms",
                self.speech_ms,
            )

            self._reset_utterance()
            return

        audio = bytes(self.audio_buffer)

        # Remove trailing silence from the end of the utterance.
        trailing_bytes = _ms_to_bytes(
            self.silence_ms,
            self.target_sample_rate,
        )

        if (
            trailing_bytes > 0
            and trailing_bytes < len(audio)
        ):
            audio = audio[:-trailing_bytes]

        duration_ms = (
            len(audio)
            / 2
            / self.target_sample_rate
            * 1000
        )

        logger.info(
            "[STT] Endpoint detected | speech=%.0fms | silence=%.0fms | final=%.0fms",
            self.speech_ms,
            self.silence_ms,
            duration_ms,
        )

        self._reset_utterance()

        if audio:
            self._submit_utterance(audio)

    async def process_frame(self, frame, direction):

        await super().process_frame(frame, direction)

        # -----------------------------
        # START
        # -----------------------------
        if isinstance(frame, StartFrame):

            self._reset_utterance()

            self.last_emitted_text = ""
            self.last_emit_at = 0.0
            self.noise_floor = 0.001
            self._pending_utterances.clear()

            await self.push_frame(
                frame,
                direction,
            )

            return

        # -----------------------------
        # END
        # -----------------------------
        if isinstance(frame, EndFrame):

            # We intentionally do not wait for Groq here.
            # The websocket/session is already ending.
            self._reset_utterance()

            await self.push_frame(
                frame,
                direction,
            )

            return

        # -----------------------------
        # NON AUDIO
        # -----------------------------
        if not isinstance(frame, AudioRawFrame):

            await self.push_frame(
                frame,
                direction,
            )

            return

        # -----------------------------
        # TTS / BARGE-IN BLOCK
        # -----------------------------
        if (
            self.turn_state
            and self.turn_state.is_stt_blocked()
        ):

            # Ignore microphone audio while agent TTS
            # is speaking. This prevents browser speaker
            # audio from becoming a fake user transcript.
            self._reset_utterance()

            return

        # -----------------------------
        # NORMALIZE AUDIO
        # -----------------------------
        pcm16 = _ensure_pcm16(
            frame.audio,
            frame.sample_rate,
            self.target_sample_rate,
        )

        if not pcm16:
            return

        frame_ms = (
            len(pcm16)
            / 2
            / self.target_sample_rate
            * 1000
        )

        rms = self._calculate_rms(pcm16)

        speech_now = self._is_speech(rms)

        # -----------------------------
        # USER STARTED SPEAKING
        # -----------------------------
        if speech_now:

            if not self.speech_started:

                self.speech_started = True
                self.speech_ms = 0.0
                self.silence_ms = 0.0
                self.utterance_ms = 0.0

                logger.info(
                    "[STT] Speech started | rms=%.5f | threshold=%.5f",
                    rms,
                    max(
                        self.speech_rms_threshold,
                        self.noise_floor * 2.5,
                    ),
                )

            self.audio_buffer.extend(pcm16)

            self.speech_ms += frame_ms
            self.utterance_ms += frame_ms
            self.silence_ms = 0.0

        # -----------------------------
        # SILENCE AFTER SPEECH
        # -----------------------------
        else:

            if not self.speech_started:
                # Ignore idle microphone silence completely.
                return

            # Keep silence temporarily so we can detect
            # the end of the user's sentence.
            self.audio_buffer.extend(pcm16)

            self.silence_ms += frame_ms
            self.utterance_ms += frame_ms

            # User stopped speaking.
            if self.silence_ms >= self.endpoint_silence_ms:

                self._finish_utterance()
                return

        # -----------------------------
        # MAX UTTERANCE SAFETY LIMIT
        # -----------------------------
        if (
            self.speech_started
            and self.utterance_ms >= self.max_utterance_ms
        ):

            logger.info(
                "[STT] Maximum utterance duration reached."
            )

            self._finish_utterance()


class RealEstateTTSProcessor(FrameProcessor):
    """Turn assistant text into speech, tagged with generation_id for client-side filtering."""

    def __init__(self, turn_state: VoiceTurnState | None = None, agent_id: str = "default"):
        super().__init__()
        self.agent_id = agent_id or "default"
        self.last_reply = ""
        self.last_reply_at = 0.0
        self._tts_task = None
        self._active_gen_id = 0
        self.turn_state = turn_state

    async def process_frame(self, frame: Frame, direction: FrameDirection = None):  # type: ignore
        print("TTS RECEIVED:", frame)
        frame_type = type(frame).__name__
        frame_text = getattr(frame, "text", None)
        if frame_text is not None:
            logger.info(
                "[PIPELINE] TTS <- Frame type=%s direction=%s text=%s",
                frame_type,
                direction,
                str(frame_text),
            )
        else:
            logger.info("[PIPELINE] TTS <- Frame type=%s direction=%s", frame_type, direction)

        # Keep barge-in cancel signalling flowing, but avoid setting this stage into
        # a canceled state that can block subsequent agent text replies.
        if isinstance(frame, CancelFrame):
            logger.info("[PIPELINE] TTS -> Caught CancelFrame. Stopping current task.")
            had_active_tts = bool(self._tts_task and not self._tts_task.done())
            if self._tts_task and not self._tts_task.done():
                self._tts_task.cancel()
            if self.turn_state:
                if had_active_tts:
                    self.turn_state.mark_tts_finished()
                else:
                    self.turn_state.tts_active = False
                    self.turn_state.tts_release_at = 0.0
            await self.push_frame(frame, direction)
            return

        try:
            await super().process_frame(frame, direction)
        except BaseException as exc:
            logger.error("[PIPELINE] TTS -> super().process_frame failed for type=%s: %s", frame_type, exc)
            is_text_like = isinstance(frame, TextFrame) or hasattr(frame, "text")
            if not is_text_like:
                return

        if not isinstance(frame, TextFrame):
            if isinstance(frame, EndFrame):
                # Wait for active synthesis to complete before ending the pipeline and closing the socket
                if self._tts_task and not self._tts_task.done():
                    try:
                        logger.info("[PIPELINE] TTS -> Waiting for active synthesis to complete before sending EndFrame.")
                        await self._tts_task
                    except Exception as e:
                        logger.warning("[PIPELINE] TTS -> Error waiting for active synthesis task: %s", e)
            await self.push_frame(frame, direction)
            return

        # Always forward transcript frames so the frontend transcript updates live.
        await self.push_frame(frame, direction)

        # Synthesize audio only for assistant replies.
        if not isinstance(frame, AgentTextFrame):
            return

        # Read the gen_id injected by the LLM layer
        gen_id = getattr(frame, "gen_id", 0)
        self._active_gen_id = gen_id

        text = frame.text.strip()
        now = time.monotonic()
        if not text or (_is_duplicate_text(text, self.last_reply) and (now - self.last_reply_at) < 1.5):
            return

        preferred_language = getattr(frame, "language", None)
        logger.info("[PIPELINE] TTS -> gen_id=%d text=%s", gen_id, text)

        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        if self.turn_state:
            self.turn_state.mark_tts_started()
            
        self._tts_task = asyncio.create_task(self._run_tts(text, preferred_language, gen_id, direction))
        
    async def _run_tts(self, text, preferred_lang, gen_id, direction):
        speech_gen = generate_speech_stream(text, preferred_lang, self.agent_id)
        if not speech_gen: return

        chunk_count = 0
        total_bytes = 0
        try:
            self.last_reply = text
            self.last_reply_at = time.monotonic()
            logger.info("[TTS] Started synthesis gen_id=%d chars=%d language=%s", gen_id, len(text), preferred_lang or "auto")
            logger.info(
                "[PIPELINE] TTS -> Starting synthesis gen_id=%d chars=%d language=%s",
                gen_id,
                len(text),
                preferred_lang or "auto",
            )
            async with _ml_semaphore:
                while True:
                    # 2. CIRCUIT BREAKER (Executor Timeout)
                    try:
                        chunk_bytes = await asyncio.wait_for(
                            asyncio.get_running_loop().run_in_executor(_executor, _safe_next, speech_gen),
                            timeout=12.0
                        )
                    except asyncio.TimeoutError:
                        logger.error("TTS Generator Timeout (Timeout at source)")
                        break

                    if chunk_bytes is _GENERATOR_SENTINEL:
                        break
                    if chunk_bytes:
                        chunk_count += 1
                        total_bytes += len(chunk_bytes)
                        logger.info(f"[PIPELINE] TTS -> Audio chunk received from generator: size {len(chunk_bytes)} bytes")
                        if chunk_count == 1 or (chunk_count % 10) == 0:
                            logger.info(
                                "[PIPELINE] TTS -> Generated audio chunk=%d bytes=%d gen_id=%d",
                                chunk_count,
                                len(chunk_bytes),
                                gen_id,
                            )
                        # 1. TAG AUDIO WITH GEN ID
                        out_frame = AudioRawFrame(audio=chunk_bytes, sample_rate=24000, num_channels=1)
                        _ensure_frame_runtime_attrs(out_frame)
                        out_frame.gen_id = gen_id
                        await self.push_frame(out_frame, direction)
        except asyncio.CancelledError:
            logger.info("[PIPELINE] TTS -> Synthesis task cancelled gen_id=%d", gen_id)
        except Exception as exc:
            logger.error("[PIPELINE] TTS -> Error: %s", exc)
        finally:
            audio_duration_s = (total_bytes / (24000.0 * 2.0)) if total_bytes > 0 else 0.0
            if chunk_count == 0:
                logger.warning("[PIPELINE] TTS -> No audio chunks produced gen_id=%d", gen_id)
                logger.error("[TTS] Completed synthesis with 0 bytes gen_id=%d", gen_id)
            else:
                logger.info(f"[PIPELINE] TTS -> Completed synthesis block: {total_bytes} bytes across {chunk_count} chunks (~{audio_duration_s:.2f}s audio).")
                logger.info(
                    "[PIPELINE] TTS -> Completed synthesis gen_id=%d chunks=%d bytes=%d duration=%.2fs",
                    gen_id,
                    chunk_count,
                    total_bytes,
                    audio_duration_s,
                )
                logger.info("[TTS] Completed synthesis gen_id=%d chunks=%d total_bytes=%d", gen_id, chunk_count, total_bytes)
                logger.info("[TTS] Audio Size = %d bytes", total_bytes)
            if self.turn_state:
                self.turn_state.mark_tts_finished(duration_s=audio_duration_s)


def _ms_to_bytes(duration_ms: int, sample_rate: int) -> int:
    return int(sample_rate * (duration_ms / 1000.0) * 2)


def _ensure_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    if not audio:
        return b""

    # Detect Float32 input buffers (e.g. WebAudio or array('f')) and convert to PCM16 Int16
    if len(audio) % 4 == 0:
        try:
            arr_f32 = np.frombuffer(audio, dtype=np.float32)
            if arr_f32.size > 0 and np.all(np.abs(arr_f32) <= 1.0) and (np.any(arr_f32 < 0) or np.any((arr_f32 > 0) & (arr_f32 < 1.0))):
                i16 = (np.clip(arr_f32, -1.0, 1.0) * 32767.0).astype(np.int16)
                audio = i16.tobytes()
        except Exception:
            pass

    if source_rate == target_rate:
        return audio

    samples = np.frombuffer(audio, dtype=np.int16)
    gcd = np.gcd(source_rate, target_rate)
    up = target_rate // gcd
    down = source_rate // gcd
    resampled = resample_poly(samples.astype(np.float32), up, down)
    resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
    return resampled.tobytes()


def _has_trailing_silence(audio_buffer: bytearray, trailing_window_bytes: int, threshold: float) -> bool:
    if len(audio_buffer) < trailing_window_bytes:
        return False
    tail = np.frombuffer(bytes(audio_buffer[-trailing_window_bytes:]), dtype=np.int16).astype(np.float32) / 32768.0
    if tail.size == 0:
        return False
    rms = float(np.sqrt(np.mean(np.square(tail))))
    return rms <= threshold


def _normalize_text(text: str) -> str:
    cleaned = text.casefold()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in cleaned)
    return " ".join(cleaned.split())


def _is_duplicate_text(current: str, previous: str) -> bool:
    current_norm = _normalize_text(current)
    previous_norm = _normalize_text(previous)
    if not current_norm or not previous_norm:
        return False
    if current_norm == previous_norm:
        return True
    if current_norm in previous_norm or previous_norm in current_norm:
        return True
    return SequenceMatcher(None, current_norm, previous_norm).ratio() >= 0.88


def _is_likely_agent_echo(transcript: str, last_reply: str, current_prompt: str = "") -> bool:
    transcript_norm = _normalize_text(transcript)
    if not transcript_norm:
        return False
    words = transcript_norm.split()
    if len(words) < 4:
        return False
    for candidate in (last_reply, current_prompt):
        candidate_norm = _normalize_text(candidate)
        if not candidate_norm:
            continue
        if transcript_norm in candidate_norm:
            return True
        if SequenceMatcher(None, transcript_norm, candidate_norm).ratio() >= 0.86:
            return True
    return False


def _is_actionable_transcript(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if any(phrase in normalized for phrase in _KNOWN_HALLUCINATION_PHRASES):
        return False

    words = normalized.split()
    if len(words) != 1:
        return True

    word = words[0]
    if word in _SHORT_VALID_UTTERANCES:
        return True
    if word in _GARBAGE_SINGLE_WORDS:
        return False
    if len(word) <= 2 and word not in {"hi", "no", "ok"}:
        return False
    if len(word) >= 2 and len(set(word)) == 1:
        return False
    return True


def _ensure_frame_runtime_attrs(frame: Frame) -> None:
    """Pipecat observers in some builds expect id/broadcast_sibling_id on every frame."""
    try:
        if not hasattr(frame, "id") or getattr(frame, "id", None) is None:
            frame.id = f"local_{uuid.uuid4().hex[:12]}"
    except Exception:
        pass
    try:
        if not hasattr(frame, "broadcast_sibling_id"):
            frame.broadcast_sibling_id = None
    except Exception:
        pass



""" try:
            # 2. CIRCUIT BREAKER (STT Timeout)
            async with _ml_semaphore:
                session_lang = "en"
                if self.turn_state and hasattr(self.turn_state, "session_language"):
                    session_lang = self.turn_state.session_language

                # Dynamic STT timeout: if running on CPU, allow more time (45s) to avoid dropping chunks.
                stt_timeout = 3.5 if (torch and torch.cuda.is_available()) else 45.0
                text = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _executor,
                        lambda: transcribe_audio(chunk, self.agent_id, language=session_lang),
                    ),
                    timeout=stt_timeout
                ) """