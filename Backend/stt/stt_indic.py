import asyncio
import logging
import time
from typing import Optional

import torch
import numpy as np

logger = logging.getLogger("stt.indic_seamless")

try:
    from transformers import SeamlessM4Tv2ForSpeechToText, SeamlessM4TFeatureExtractor
except ImportError:
    logger.warning("transformers not available. IndicSeamlessSTT will fail to load.")


class IndicSeamlessSTT:
    """
    Local STT Engine using ai4bharat/indic-seamless (SeamlessM4Tv2).
    Runs heavy inference inside asyncio.to_thread to prevent event loop blocking.

    Language cross-talk fix: `forced_bos_token_id` is injected into every generate()
    call, hard-locking the decoder to English output regardless of input speech language
    distribution. This prevents the model's internal Hindi language prior from overriding
    the tgt_lang parameter when processing low-confidence or mixed Indian speech.
    """

    # Minimum audio length before attempting transcription (1.0s @ 16kHz PCM16 = 32000 bytes)
    _MIN_AUDIO_BYTES = 32000

    def __init__(self, model_id: str = "ai4bharat/indic-seamless"):
        self.model_id = model_id
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        # float16 only on CUDA — CPU ops are unstable with fp16 on some kernels
        if self.device.startswith("cuda"):
            self.dtype = torch.float16
            torch.backends.cudnn.benchmark = True  # Maximize repetitive inference speed
        else:
            self.dtype = torch.float32

        logger.info(
            "Loading IndicSeamless STT model (%s) on %s with %s...",
            self.model_id, self.device, self.dtype,
        )

        self.model = None
        self.processor = None
        # Cached BOS token ID for forced English decoding
        self._eng_bos_token_id: Optional[int] = None

        try:
            from transformers import AutoProcessor
            self.processor = AutoProcessor.from_pretrained(
                self.model_id, local_files_only=True
            )
            self.model = SeamlessM4Tv2ForSpeechToText.from_pretrained(
                self.model_id,
                torch_dtype=self.dtype,
                local_files_only=True,
            ).to(self.device)

            # Pre-resolve the English forced BOS token so we never look it up at
            # inference time. SeamlessM4T uses a dedicated language token per target.
            self._eng_bos_token_id = self._resolve_bos_token("eng")
            logger.info(
                "IndicSeamless STT loaded. eng_bos_token_id=%s",
                self._eng_bos_token_id,
            )
        except Exception as exc:
            logger.error("Failed to load IndicSeamless model: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_bos_token(self, tgt_lang: str) -> Optional[int]:
        """
        Look up the forced-BOS token ID for a given SeamlessM4T target language.
        This locks the decoder's very first generated token, preventing the model
        from selecting a different language path in the early beam steps.
        """
        if self.processor is None:
            return None
        try:
            # SeamlessM4T tokenizers expose convert_tokens_to_ids for lang tokens
            token = f"__{tgt_lang}__"
            token_id = self.processor.tokenizer.convert_tokens_to_ids(token)
            if token_id and token_id != self.processor.tokenizer.unk_token_id:
                return token_id
            # Fallback: some builds use a different naming convention
            token_id = self.processor.tokenizer.lang_code_to_id.get(tgt_lang)
            return token_id
        except Exception as exc:
            logger.debug("BOS token resolution failed for %s: %s", tgt_lang, exc)
            return None

    def _process_audio_sync(self, audio_bytes: bytes, target_lang: str = "eng") -> str:
        """
        Synchronous inference block — always offload to a thread, never call directly
        from the event loop.

        audio_bytes : PCM16 LE @ 16 kHz mono
        target_lang : ISO 639-3 code (default "eng" — NEVER left unspecified)
        """
        if not self.model or not self.processor:
            return ""

        if not audio_bytes or len(audio_bytes) < self._MIN_AUDIO_BYTES:
            logger.info(
                "[IndicSeamless] Dropped short audio chunk: %d bytes (min %d)",
                len(audio_bytes) if audio_bytes else 0,
                self._MIN_AUDIO_BYTES,
            )
            return ""

        try:
            # PCM16 → float32 normalised [-1, 1]
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            inputs = self.processor(
                audios=samples,
                sampling_rate=16000,
                return_tensors="pt",
            )
            input_features = inputs.input_features.to(self.device, dtype=self.dtype)
            attention_mask = inputs.attention_mask.to(self.device)

            # Build generate kwargs — always specify tgt_lang explicitly
            gen_kwargs: dict = {
                "tgt_lang": target_lang,
                "max_new_tokens": 256,
            }

            # CRITICAL LANGUAGE LOCK: inject forced_bos_token_id so the very first
            # decoder step is pinned to English, defeating the model's Hindi prior on
            # ambiguous Indian-accented input.
            bos_id = self._eng_bos_token_id if target_lang == "eng" else self._resolve_bos_token(target_lang)
            if bos_id is not None:
                gen_kwargs["forced_bos_token_id"] = bos_id

            with torch.no_grad():
                output_tokens = self.model.generate(
                    input_features,
                    attention_mask=attention_mask,
                    **gen_kwargs,
                )

            transcript = self.processor.decode(
                output_tokens[0].cpu().tolist(), skip_special_tokens=True
            )
            transcript = transcript.strip()

            # Phase 2: STT Output Sanitization for hallucinations
            # Extremely short transcripts with Indic script characters are almost always 
            # the SeamlessM4T model hallucinating over a background noise pop.
            import re
            if len(transcript.split()) < 3 and re.search(r'[\u0900-\u097F]', transcript):
                logger.warning("[IndicSeamless] STT Hallucination Intercepted: '%s'", transcript)
                return ""

            return transcript

        except (ValueError, RuntimeError) as exc:
            logger.error("[IndicSeamless] Transformer generation failure: %s", exc)
            return ""
        except Exception as exc:
            logger.error("[IndicSeamless] Unexpected generation failure: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Public synchronous API
    # ------------------------------------------------------------------

    def transcribe_audio_chunk(self, audio_bytes: bytes, target_lang: str = "eng") -> str:
        """
        Synchronous transcription. `target_lang` is forwarded explicitly — never left
        to model defaults. Callers must pass "eng" for English-only sessions.
        """
        return self._process_audio_sync(audio_bytes, target_lang=target_lang)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def async_transcribe(self, audio_bytes: bytes, target_lang: str = "eng") -> str:
        """
        Async transcription — offloads blocking GPU/CPU work to a thread pool so the
        LangGraph / WebSocket event loop is never blocked.
        """
        if not audio_bytes or len(audio_bytes) < self._MIN_AUDIO_BYTES:
            return ""

        start = time.time()
        try:
            transcript = await asyncio.to_thread(
                self._process_audio_sync,
                audio_bytes,
                target_lang,
            )
        except (ValueError, RuntimeError) as exc:
            logger.error("[IndicSeamless] Async transcription failure: %s", exc)
            return ""
        except Exception as exc:
            logger.error("[IndicSeamless] Unexpected async transcription failure: %s", exc)
            return ""

        elapsed = time.time() - start
        logger.info(
            "[IndicSeamless] Transcribed %d bytes in %.2fs → '%s'",
            len(audio_bytes), elapsed, transcript,
        )
        return transcript


# ---------------------------------------------------------------------------
# Module-level singleton — shared across all pipeline workers
# ---------------------------------------------------------------------------

_stt_instance: Optional[IndicSeamlessSTT] = None


def transcribe_audio(
    audio_chunk: bytes,
    agent_id: str = "default",
    language: str | None = None,
) -> str:
    """
    Drop-in synchronous adapter used by the Pipecat pipeline's thread-pool executor.
    Always forces English output regardless of the `language` hint passed in —
    language detection for response routing is handled downstream by LanguageTracker.
    """
    global _stt_instance
    if _stt_instance is None:
        _stt_instance = IndicSeamlessSTT()
    # Explicitly pass target_lang="eng" — do NOT rely on the default argument
    return _stt_instance.transcribe_audio_chunk(audio_chunk, target_lang="eng")
