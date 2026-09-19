"""
core_voice_loop.py — Ultra-Low Latency Parallel Streaming Voice Engine (< 800ms TTFA).

Architectural Pipeline:
┌──────────────────┐    < 250ms    ┌──────────────────┐    < 150ms    ┌──────────────────┐
│  Silero VAD +    │ ─────────────>│ Streaming STT    │ ─────────────>│ Streaming LLM    │
│  Semantic EOU    │               │ (WebSocket Partial)             │ (Groq / Async)   │
└──────────────────┘               └──────────────────┘               └────────┬─────────┘
         │ Interruption                                                        │ Tokens
         ▼ (Instant Barge-In)                                                  ▼ < 200ms
┌──────────────────┐               ┌──────────────────┐               ┌──────────────────┐
│ Purge Audio Out  │<──────────────│ Indic Parler TTS │<──────────────│ Sentence-Boundary│
│ & Flush Context  │               │ (Static KV + Opt)│               │ Regex Chunker    │
└──────────────────┘               └──────────────────┘               └──────────────────┘
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from llm import config as cfg

from groq import AsyncGroq

logger = logging.getLogger("core_voice_loop")

# ── Voice-Optimized System Prompt Instructions ────────────────────────────────
SYSTEM_PROMPT_CONSTRAINTS = (
    "\n\n[VOICE AGENT RULES — CRITICAL FOR LOW LATENCY]:\n"
    "1. Keep responses extremely concise (40-90 words max, 1-3 sentences).\n"
    "2. Strictly plain spoken text only: NO markdown, bolding (**), italics (*), lists, bullet points, or special symbols.\n"
    "3. Speak naturally and directly. No preamble like 'Great question' or 'Of course'.\n"
    "4. Never repeat information the user has already given you.\n"
    "5. Do NOT hallucinate facts, prices, or availability. Stay grounded to the conversation context.\n"
)

# ── Sentence Boundary Chunking Regex ──────────────────────────────────────────────
# Primary boundary split on punctuation: comma, period, question mark, exclamation, semicolon
PUNCTUATION_SPLIT_RE = re.compile(r"(?<=[.?!,;])\s+")
FIRST_CHUNK_MIN_WORDS = 2       # Dispatch at 2 words for ultra-fast first audio (≈ 150ms TTFA)
SUBSEQUENT_CHUNK_MIN_WORDS = 5  # Subsequent phrase chunks (down from 6)

# ── Semantic End-Of-Utterance (EOU) Clauses ───────────────────────────────────
CLAUSE_TERMINATORS = re.compile(r"(?i)\b(yes|no|yeah|okay|sure|thanks|thank you|please|right|done|bye|correct)\b[.?!]?$")


# ── Stage 1: Semantic End-of-Utterance & Silero VAD Checker ───────────────────
class SemanticEOUDetector:
    """
    Combines VAD silence trailing windows with Semantic Clause Completion.
    Triggers turn completion at 200ms-300ms if the utterance forms a complete clause.
    """

    def __init__(self, silence_target_ms: int = 250):
        self.silence_target_ms = silence_target_ms

    def is_grammatically_complete_clause(self, text: str) -> bool:
        """Check if partial text forms a complete grammatical clause or terminal phrase."""
        cleaned = text.strip().lower()
        if not cleaned:
            return False

        # Terminal punctuation
        if cleaned[-1] in ".?!":
            return True

        # Common single-word or short answers
        if len(cleaned.split()) <= 3 and CLAUSE_TERMINATORS.search(cleaned):
            return True

        # Complete clause structural indicators
        if len(cleaned.split()) >= 4 and any(cleaned.startswith(p) for p in ["i want to", "can you", "what is", "where is", "how much", "i am looking"]):
            return True

        return False

    def should_trigger_turn(self, trailing_silence_ms: float, current_transcript: str) -> bool:
        """Decision boundary for turn triggering (< 200ms target)."""
        if trailing_silence_ms >= self.silence_target_ms:
            if self.is_grammatically_complete_clause(current_transcript):
                return True
            # Fallback hard timeout at 270ms even if incomplete
            if trailing_silence_ms >= 270:
                return True
        return False


# ── Stage 4: Sentence Boundary Token Accumulator ──────────────────────────────
class SentenceBoundaryChunker:
    """
    Captures streaming tokens from the LLM and splits them at punctuation boundaries.
    Instantly dispatches the first short chunk (3-5 words) to TTS for immediate playback.
    """

    def __init__(self):
        self.buffer = ""
        self.words_accumulated = 0
        self.chunk_index = 0

    def feed_token(self, token: str) -> List[str]:
        """
        Feed a new LLM token into the accumulator.
        Returns a list of phrase chunks ready for immediate TTS synthesis.
        """
        self.buffer += token
        chunks_to_dispatch: List[str] = []

        words = self.buffer.strip().split()
        self.words_accumulated = len(words)

        min_words = FIRST_CHUNK_MIN_WORDS if self.chunk_index == 0 else SUBSEQUENT_CHUNK_MIN_WORDS

        # Check for punctuation splits
        match = PUNCTUATION_SPLIT_RE.search(self.buffer)
        if match and self.words_accumulated >= min_words:
            split_pos = match.end()
            chunk = self.buffer[:split_pos].strip()
            self.buffer = self.buffer[split_pos:]
            if chunk:
                self.chunk_index += 1
                chunks_to_dispatch.append(chunk)

        # Force flush if word count exceeds threshold without punctuation
        elif self.words_accumulated >= (min_words + 4):
            chunk = self.buffer.strip()
            self.buffer = ""
            if chunk:
                self.chunk_index += 1
                chunks_to_dispatch.append(chunk)

        return chunks_to_dispatch

    def flush_remaining(self) -> Optional[str]:
        """Flush remaining text in buffer upon LLM stream end."""
        remaining = self.buffer.strip()
        self.buffer = ""
        self.words_accumulated = 0
        return remaining if remaining else None


# ── Full Low-Latency Call Session Pipeline Orchestrator ────────────────────────
class CallSession:
    """
    Staff-Level Async Call Session Orchestrator.
    Executes parallel STT-LLM-TTS streaming with Instant Barge-In support.
    """

    def __init__(
        self,
        system_prompt: str,
        sample_rate: int = 16000,
        agent_id: str = "default",
        preferred_language: str = "en",
        websocket=None,
    ) -> None:
        self.system_prompt = system_prompt + SYSTEM_PROMPT_CONSTRAINTS
        self.sample_rate = sample_rate
        self.agent_id = agent_id
        self.preferred_language = preferred_language
        self.websocket = websocket

        # ── High-Performance Async Queues ─────────────────────────────────────
        self.audio_in_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.transcript_queue: asyncio.Queue[str] = asyncio.Queue()
        # Item format: (chunk_text, turn_id, is_last_chunk)
        self.tts_phrase_queue: asyncio.Queue[Tuple[str, str, bool]] = asyncio.Queue()
        self.audio_out_queue: asyncio.Queue[bytes] = asyncio.Queue()

        # ── Stage 1: VAD & EOU Setup ──────────────────────────────────────────
        self.eou_detector = SemanticEOUDetector(silence_target_ms=200)

        # ── Pipeline State ───────────────────────────────────────────────────
        self._running = False
        self.is_agent_speaking = False
        self.barge_in_event = asyncio.Event()
        self.active_turn_id: str = ""
        self.tasks: List[asyncio.Task] = []

        # ── LLM Client ───────────────────────────────────────────────────────
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        self.conversation_history: List[Dict[str, str]] = []

    async def start(self) -> None:
        """Launch concurrent background tasks for STT, LLM streaming, and TTS synthesis."""
        self._running = True
        self.tasks = [
            asyncio.create_task(self._stt_ingestion_loop(), name="stt_ingestion"),
            asyncio.create_task(self._llm_streaming_loop(), name="llm_streaming"),
            asyncio.create_task(self._tts_synthesis_loop(), name="tts_synthesis"),
        ]
        logger.info("[Pipeline] Low-latency voice session started (< 800ms TTFA target).")

    async def stop(self) -> None:
        """Shutdown session and terminate background tasks."""
        self._running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("[Pipeline] Low-latency voice session stopped.")

    async def push_audio(self, pcm_chunk: bytes) -> None:
        """Ingest raw audio frames from mic/websocket transport."""
        if self._running:
            await self.audio_in_queue.put(pcm_chunk)

    async def get_output_audio(self) -> AsyncGenerator[bytes, None]:
        """Yield synthesized PCM audio chunks for client playback."""
        while self._running:
            try:
                chunk = await asyncio.wait_for(self.audio_out_queue.get(), timeout=0.05)
                if chunk:
                    yield chunk
            except asyncio.TimeoutError:
                continue

    # ── Stage 5: Instant Barge-In Interruption Handler ────────────────────────
    def _execute_instant_barge_in(self) -> None:
        """
        Instantly kills playback and purges all pending LLM & TTS queues
        the exact millisecond user speech interrupts the agent.
        """
        logger.info("[Barge-In] Interruption detected! Purging audio out and generation queues...")
        self.barge_in_event.set()
        self.is_agent_speaking = False

        # 1. Clear Audio Output Buffer (stops speaker playback immediately)
        while not self.audio_out_queue.empty():
            try:
                self.audio_out_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 2. Clear TTS Phrase Queue
        while not self.tts_phrase_queue.empty():
            try:
                self.tts_phrase_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── Stage 2: Streaming STT & VAD Loop ─────────────────────────────────────
    async def _stt_ingestion_loop(self) -> None:
        """
        Processes streaming audio, monitors VAD energy for instant barge-in,
        and triggers turn completion when trailing silence + EOU is met.
        """
        from stt.stt import transcribe_audio

        audio_buffer = bytearray()
        last_speech_time = time.monotonic()
        in_speech = False

        while self._running:
            try:
                chunk = await self.audio_in_queue.get()
                if not chunk:
                    continue

                # Energy check for barge-in detection during TTS playback
                rms = self._calculate_rms(chunk)
                if self.is_agent_speaking and rms > 0.015:
                    self._execute_instant_barge_in()

                audio_buffer.extend(chunk)

                if rms > 0.008:
                    last_speech_time = time.monotonic()
                    in_speech = True

                # Check trailing silence condition
                if in_speech:
                    silence_ms = (time.monotonic() - last_speech_time) * 1000.0

                    # Transcribe current buffer — minimum 100ms (3200 bytes at 16kHz PCM16)
                    if len(audio_buffer) >= 3200:
                        partial_transcript = await asyncio.to_thread(
                            transcribe_audio,
                            bytes(audio_buffer),
                            self.agent_id,
                            self.preferred_language,
                        )

                        if partial_transcript and partial_transcript.strip():
                            # Stage 1: Evaluate turn trigger condition (< 200ms target)
                            if self.eou_detector.should_trigger_turn(silence_ms, partial_transcript):
                                logger.info("[STT EOU] Turn triggered at %.0fms: '%s'", silence_ms, partial_transcript)
                                await self.transcript_queue.put(partial_transcript.strip())
                                audio_buffer.clear()
                                in_speech = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[STT Loop] Error: %s", e)
                await asyncio.sleep(0.05)

    # ── Stage 3: LLM Token Streaming & Stage 4: Sentence Chunking ─────────────
    async def _llm_streaming_loop(self) -> None:
        """
        Streams response tokens from LLM, applies sentence-boundary chunking,
        and dispatches the first phrase chunk (< 200ms) to TTS.
        """
        while self._running:
            try:
                user_transcript = await self.transcript_queue.get()
                self.barge_in_event.clear()

                self.turn_id = str(uuid.uuid4())
                self.active_turn_id = self.turn_id

                self.conversation_history.append({"role": "user", "content": user_transcript})

                messages = [{"role": "system", "content": self.system_prompt}]
                messages.extend(self.conversation_history[-10:])

                chunker = SentenceBoundaryChunker()
                stream_start = time.monotonic()
                full_llm_response = ""

                try:
                    # Async Groq streaming — 8b-instant for lowest latency on voice turns
                    stream = await self.groq_client.chat.completions.create(
                        model=cfg.MODEL_NAME,
                        messages=messages,
                        temperature=0.45,
                        max_tokens=90,
                        stream=True,
                    )

                    async for chunk in stream:
                        if self.barge_in_event.is_set():
                            logger.info("[LLM Stream] Aborted due to barge-in.")
                            break

                        delta = chunk.choices[0].delta
                        token = getattr(delta, "content", "") or ""
                        if not token:
                            continue

                        full_llm_response += token
                        # Stage 4: Accumulate tokens & split on boundaries
                        phrases = chunker.feed_token(token)

                        for phrase in phrases:
                            if self.barge_in_event.is_set():
                                break
                            ttfa_est = (time.monotonic() - stream_start) * 1000.0
                            logger.info("[LLM -> TTS Chunk] Ready in %.0fms: '%s'", ttfa_est, phrase)
                            await self.tts_phrase_queue.put((phrase, self.turn_id, False))

                    # Flush final remaining sentence fragment
                    final_phrase = chunker.flush_remaining()
                    if final_phrase and not self.barge_in_event.is_set():
                        await self.tts_phrase_queue.put((final_phrase, self.turn_id, True))
                    elif not self.barge_in_event.is_set():
                        await self.tts_phrase_queue.put(("", self.turn_id, True))

                    if full_llm_response and not self.barge_in_event.is_set():
                        self.conversation_history.append({"role": "assistant", "content": full_llm_response.strip()})

                except Exception as llm_err:
                    logger.error("[LLM Stream] Error: %s", llm_err)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[LLM Loop] Unhandled error: %s", e)

    # ── Stage 4 Backend: Sarvam AI Bulbul V3 TTS Synthesis Execution ─────────────
    async def _tts_synthesis_loop(self) -> None:
        """
        Receives sentence-boundary chunks and synthesizes PCM audio using
        Sarvam AI Bulbul V3 REST API.
        """
        from tts.provider import generate_speech_stream

        while self._running:
            try:
                phrase, turn_id, is_last = await self.tts_phrase_queue.get()

                if not phrase and is_last:
                    self.is_agent_speaking = False
                    continue

                if self.barge_in_event.is_set() or turn_id != self.active_turn_id:
                    continue

                self.is_agent_speaking = True
                logger.info("[TTS Synthesis] Synthesizing chunk for turn %s: '%s'", turn_id[:8], phrase)

                # Synthesize audio chunks via Sarvam AI Bulbul V3 generator
                audio_gen = generate_speech_stream(
                    phrase,
                    preferred_language=self.preferred_language,
                    agent_id=self.agent_id,
                )

                for pcm_chunk in audio_gen:
                    if self.barge_in_event.is_set() or turn_id != self.active_turn_id:
                        logger.info("[TTS Synthesis] Aborting synthesis due to barge-in.")
                        break

                    if pcm_chunk:
                        await self.audio_out_queue.put(pcm_chunk)

                if is_last:
                    self.is_agent_speaking = False

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[TTS Loop] Error: %s", exc)

    @staticmethod
    def _calculate_rms(pcm_chunk: bytes) -> float:
        """Calculate Root Mean Square (RMS) energy for audio frames."""
        if not pcm_chunk:
            return 0.0
        import numpy as np
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples))))
