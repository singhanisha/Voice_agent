"""Runtime processors for the local voice pipeline."""

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import os
import time
import concurrent.futures
import uuid

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=30)

# ΓöÇΓöÇ TTS generator sentinel (Fix #1: StopIteration in run_in_executor) ΓöÇΓöÇΓöÇΓöÇΓöÇ
# PEP 479 converts StopIteration raised inside a Future into RuntimeError.
# We catch it inside the thread before it can escape.
_GENERATOR_SENTINEL = object()


def _safe_next(gen):
    """Call next(gen) inside a thread. Returns _GENERATOR_SENTINEL on StopIteration."""
    try:
        return next(gen)
    except StopIteration:
        return _GENERATOR_SENTINEL
_ml_semaphore = asyncio.Semaphore(4)

try:
    from pipecat.frames.frames import AudioRawFrame, Frame, TextFrame, CancelFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
except ImportError:
    logging.error("pipecat-ai is not installed. Pipeline will fail.")
    FrameProcessor = object
    FrameDirection = None
    Frame = None
    TextFrame = None
    AudioRawFrame = None
    CancelFrame = None

from llm.llm import generate_response
from llm.language_utils import LanguageTracker, analyze_user_text, localize_template
from llm.state_manager import StateManager
from llm.pipeline_logger import pipeline_logger
from pipecat.frames.frames import StartFrame
from stt import config as stt_cfg

# Root-relative path for the agent schema
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_SCHEMA_PATH = os.path.join(_ROOT, "Updated_Real_Estate_Agent.json")
CALL_CONNECTED_TRIGGER = "[System: The call has just been connected. No user has spoken yet. Speak only for the current conversation node and do not transition.]"

try:
    from stt.stt import transcribe_audio
except ImportError:
    logging.warning("stt.stt.transcribe_audio not available yet. Using mock STT.")

    def transcribe_audio(audio_chunk: bytes) -> str:
        return "mock transcription"

try:
    from tts import generate_speech_stream
except ImportError:
    logging.warning("tts engine not found. Using mock TTS.")

    def generate_speech_stream(text: str, preferred_language: str | None = None):
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


@dataclass
class AgentTextFrame(TextFrame):
    language: str = "en"


class VoiceTurnState:
    """Shared speaking state to prevent STT from transcribing agent playback/echo."""

    def __init__(self):
        self.tts_active = False
        self.tts_release_at = 0.0

    def is_stt_blocked(self) -> bool:
        return self.tts_active or time.monotonic() < self.tts_release_at

    def mark_tts_started(self) -> None:
        self.tts_active = True
        self.tts_release_at = 0.0

    def mark_tts_finished(self, cooldown_ms: int = stt_cfg.POST_TTS_STT_COOLDOWN_MS) -> None:
        self.tts_active = False
        self.tts_release_at = time.monotonic() + (cooldown_ms / 1000.0)


class RealEstateLLMProcessor(FrameProcessor):
    """Turn user transcripts into LLM responses and manage node states with GenID sync."""

    def __init__(self):
        super().__init__()
        self.history: list[dict[str, str]] = []
        self.current_language = "en"
        self.language_tracker = LanguageTracker(initial_language=self.current_language)
        self.last_user_text = ""
        self.last_user_at = 0.0
        self._booted = False
        self.state_manager = StateManager(STATE_SCHEMA_PATH)
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

            logger.info("[PIPELINE] LLM -> Received StartFrame. Triggering initial greeting...")
            try:
                # 2. CIRCUIT BREAKER (Timeout)
                reply = await asyncio.wait_for(generate_response(
                    user_text=CALL_CONNECTED_TRIGGER,
                    conversation_history=self.history,
                    language=self.current_language,
                    state_manager=self.state_manager,
                    allow_transition=False,
                ), timeout=3.5)
            except BaseException as exc:
                logger.warning("[PIPELINE] LLM -> Start greeting fallback due to error: %s", exc)
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
        logger.info("[PIPELINE] LLM <- User transcript (%s): %s", type(frame).__name__, user_text)

        # Always process transcript frames (no strict intent/node filtering at this layer).
        user_analysis = analyze_user_text(user_text, fallback=self.current_language)
        if user_analysis.actionable and user_analysis.cleaned_text:
            user_text = user_analysis.cleaned_text

        # 1. INCREMENT GEN_ID on every non-empty user turn that reaches LLM
        self._current_gen_id += 1
        logger.info("New User Turn: gen_id = %d", self._current_gen_id)
        self.last_user_text = user_text
        self.last_user_at = time.monotonic()
        if user_analysis.actionable:
            self.current_language, _ = self.language_tracker.observe(user_text)
        
        # Sync User Text Frame with current GenID
        _ensure_frame_runtime_attrs(frame)
        frame.gen_id = self._current_gen_id
        try:
            await self.push_frame(frame, direction)
            logger.info("[PIPELINE] LLM -> Forwarded user transcript frame gen_id=%d", self._current_gen_id)
        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> Failed forwarding user transcript frame: %s", exc)

        logger.info("[PIPELINE] LLM -> Generating response for transcript: %s", user_text)
        llm_failed = False
        try:
            # 2. CIRCUIT BREAKER (Timeout)
            reply = await asyncio.wait_for(generate_response(
                user_text,
                self.history,
                self.current_language,
                state_manager=self.state_manager
            ), timeout=4.0)
        except asyncio.TimeoutError:
            logger.warning("LLM Timeout ΓÇö emitting system busy signal.")
            llm_failed = True
            reply = localize_template("Give me just one moment...", self.current_language)
        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> Generation error after STT handoff: %s", exc)
            llm_failed = True
            # Keep session alive even if state logic reaches a terminal branch.
            if isinstance(exc, KeyboardInterrupt):
                try:
                    self.state_manager.reset_state()
                    logger.warning("[PIPELINE] LLM -> State reset after terminal-state interrupt.")
                except Exception:
                    pass
            reply = localize_template(self._fallback_reply, self.current_language)

        # Only use fallback when the model actually failed.
        if not reply:
            if llm_failed:
                logger.warning("[PIPELINE] LLM -> Empty reply after LLM failure, using fallback response.")
                reply = localize_template(random.choice(self._fallback_replies), self.current_language)
            else:
                logger.warning("[PIPELINE] LLM -> Empty reply from state manager. Skipping fallback override.")
                self.history.append({"role": "user", "content": user_text})
                if len(self.history) > 8:
                    self.history = self.history[-8:]
                return

        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        if len(self.history) > 8:
            self.history = self.history[-8:]
        
        frame_out = AgentTextFrame(reply, language=self.current_language)
        _ensure_frame_runtime_attrs(frame_out)
        frame_out.gen_id = self._current_gen_id # TAG THE REPLY
        try:
            await self.push_frame(frame_out, direction)
            logger.info("[PIPELINE] LLM -> Forwarded agent reply frame gen_id=%d", self._current_gen_id)
        except BaseException as exc:
            logger.error("[PIPELINE] LLM -> Failed forwarding agent reply frame: %s", exc)


class RealEstateSTTProcessor(FrameProcessor):
    """Low-latency STT with Adaptive VAD (Noise Floor Calibration)."""

    def __init__(self, turn_state: VoiceTurnState | None = None):
        super().__init__()
        self.audio_buffer = bytearray()
        self.min_chunk_bytes = _ms_to_bytes(stt_cfg.MIN_CHUNK_MS, stt_cfg.TARGET_SAMPLE_RATE)
        # Keep larger chunks so sentences are finalized after a real pause, not mid-thought.
        self.max_chunk_bytes = _ms_to_bytes(max(stt_cfg.MAX_CHUNK_MS, 5000), stt_cfg.TARGET_SAMPLE_RATE)
        self.trailing_window_bytes = _ms_to_bytes(max(stt_cfg.TRAILING_SILENCE_MS, 1200), stt_cfg.TARGET_SAMPLE_RATE)
        self.last_emitted_text = ""
        self.last_emit_at = 0.0
        self.is_speaking = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        self._last_voice_at = 0.0
        self._speech_end_silence_ms = float(max(stt_cfg.TRAILING_SILENCE_MS, 1200))
        self._barge_in_min_ms = 550.0
        self._barge_in_sent = False
        self._cooldown_until = 0.0
        self.turn_state = turn_state
        # 6. ADAPTIVE VAD (Continuous Calibration)
        self.noise_floor = 0.010 # Start low and adapt
        self._rms_history = []

    async def process_frame(self, frame: Frame, direction: FrameDirection = None):  # type: ignore
        await super().process_frame(frame, direction)
        if not isinstance(frame, AudioRawFrame):
            await self.push_frame(frame, direction)
            return

        pcm16 = _ensure_pcm16(frame.audio, frame.sample_rate, stt_cfg.TARGET_SAMPLE_RATE)
        if not pcm16: return

        # Ignore inbound mic audio while TTS is actively speaking (or in short post-TTS cooldown).
        if self.turn_state and self.turn_state.is_stt_blocked():
            self.audio_buffer.clear()
            self.is_speaking = False
            self._voice_hits = 0
            self._voiced_ms = 0.0
            self._barge_in_sent = False
            return
        if time.monotonic() < self._cooldown_until:
            return

        # Calculate current RMS
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        chunk_rms = float(np.sqrt(np.mean(samples**2)) / 32768.0)
        chunk_duration_ms = (len(samples) / stt_cfg.TARGET_SAMPLE_RATE) * 1000.0
        if logger.isEnabledFor(logging.INFO) and chunk_rms > 0.005:
            logger.info(
                "[PIPELINE] STT <- Audio frame bytes=%d duration_ms=%.1f rms=%.4f speaking=%s blocked=%s",
                len(pcm16),
                chunk_duration_ms,
                chunk_rms,
                self.is_speaking,
                bool(self.turn_state and self.turn_state.is_stt_blocked()),
            )
        
        # Continuous Calibration: Track bottom 10% of energy as noise floor
        self._rms_history.append(chunk_rms)
        if len(self._rms_history) > 50: # tracking ~1s window for faster startup
            self._rms_history.pop(0)
            self.noise_floor = float(np.percentile(self._rms_history, 10))
        
        # Adaptive activation threshold:
        # - Lower floor so quieter microphones can still trigger speech.
        # - Keep a cap so we don't become too strict in mildly noisy rooms.
        dynamic_threshold = max(self.noise_floor * 8.0, self.noise_floor + 0.006, 0.010)
        dynamic_threshold = min(dynamic_threshold, 0.022)
        now_mono = time.monotonic()
        voice_presence_threshold = max(dynamic_threshold * 0.55, self.noise_floor + 0.0035)
        strong_voice_threshold = max(dynamic_threshold * 1.35, self.noise_floor + 0.012)

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
                logger.info(
                    "[VAD DEBUG] RMS=%.4f threshold=%.4f floor=%.4f speaking=False",
                    chunk_rms,
                    dynamic_threshold,
                    self.noise_floor,
                )
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
        self.audio_buffer.clear()
        self.is_speaking = False
        self._barge_in_sent = False
        self._voice_hits = 0
        self._voiced_ms = 0.0
        logger.info("[PIPELINE] STT -> Sending chunk to transcription bytes=%d", len(chunk))
        
        try:
            # 2. CIRCUIT BREAKER (STT Timeout)
            async with _ml_semaphore:
                text = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(_executor, transcribe_audio, chunk),
                    timeout=3.5
                )
        except asyncio.TimeoutError:
            logger.error("STT Timeout (Skipping chunk)")
            return
        except Exception:
            return
            
        normalized_text = _normalize_text(text)
        if not normalized_text or len(normalized_text) < stt_cfg.MIN_TRANSCRIPT_CHARS or not _is_actionable_transcript(text):
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
        await self.push_frame(text_frame, direction)


class RealEstateTTSProcessor(FrameProcessor):
    """Turn assistant text into speech, tagged with generation_id for client-side filtering."""

    def __init__(self, turn_state: VoiceTurnState | None = None):
        super().__init__()
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
        speech_gen = generate_speech_stream(text, preferred_lang)
        if not speech_gen: return

        chunk_count = 0
        total_bytes = 0
        try:
            self.last_reply = text
            self.last_reply_at = time.monotonic()
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
                            timeout=3.0
                        )
                    except asyncio.TimeoutError:
                        logger.error("TTS Generator Timeout (Timeout at source)")
                        break

                    if chunk_bytes is _GENERATOR_SENTINEL:
                        break
                    if chunk_bytes:
                        chunk_count += 1
                        total_bytes += len(chunk_bytes)
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
            if chunk_count == 0:
                logger.warning("[PIPELINE] TTS -> No audio chunks produced gen_id=%d", gen_id)
            else:
                logger.info(
                    "[PIPELINE] TTS -> Completed synthesis gen_id=%d chunks=%d bytes=%d",
                    gen_id,
                    chunk_count,
                    total_bytes,
                )
            if self.turn_state:
                self.turn_state.mark_tts_finished()


def _ms_to_bytes(duration_ms: int, sample_rate: int) -> int:
    return int(sample_rate * (duration_ms / 1000.0) * 2)


def _ensure_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    # Guard: some browser clients may accidentally send Float32 PCM frames.
    # Detect that pattern and convert to Int16 before any resampling.
    if len(audio) >= 8 and len(audio) % 4 == 0:
        try:
            f32 = np.frombuffer(audio, dtype=np.float32)
            if f32.size:
                finite_mask = np.isfinite(f32)
                finite_ratio = float(np.mean(finite_mask))
                if finite_ratio > 0.98:
                    finite_vals = f32[finite_mask]
                    peak = float(np.max(np.abs(finite_vals))) if finite_vals.size else 0.0
                    if 0.001 < peak <= 1.25:
                        as_i16 = np.clip(f32, -1.0, 1.0)
                        samples = (as_i16 * 32767.0).astype(np.int16)
                    else:
                        samples = np.frombuffer(audio, dtype=np.int16)
                else:
                    samples = np.frombuffer(audio, dtype=np.int16)
            else:
                samples = np.frombuffer(audio, dtype=np.int16)
        except Exception:
            samples = np.frombuffer(audio, dtype=np.int16)
    else:
        samples = np.frombuffer(audio, dtype=np.int16)

    if samples.size == 0:
        return b""
    if source_rate == target_rate:
        return samples.tobytes()

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
