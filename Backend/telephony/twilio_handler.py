"""
Twilio Telephony Handler — Production Outbound Call Integration.

Handles:
  - Twilio Media Streams (bidirectional audio WebSocket)
  - TwiML webhook endpoint
  - Inbound μ-law 8kHz audio → Pipecat pipeline → 8kHz PCM back to Twilio

v2.2 changes:
  - handle_twilio_stream() now accepts db, ws_manager, campaign_id, lead_id,
    lead_name, phone, client_id so it can persist the call result to SQLite
    and broadcast call_completed via WebSocket when the stream ends.
  - _persist_call_result() extracts state_manager.conversation_data and
    llm.history from the live pipeline objects after cleanup.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from scipy.signal import resample_poly

from call_recording import SessionRecorder, recording_path_and_url

from pipecat.frames.frames import AudioRawFrame, EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger("telephony.twilio")


class TwilioSource(FrameProcessor):
    """Queues audio frames from the Twilio WebSocket into the Pipecat pipeline."""

    def __init__(self, recorder: SessionRecorder | None = None):
        super().__init__()
        self.recorder = recorder
        self._queue: asyncio.Queue = asyncio.Queue()

    async def process_frame(self, frame, direction):
        await self.push_frame(frame, direction)

    def queue_audio(self, pcm16_bytes: bytes, sample_rate: int = 8000) -> None:
        frame = AudioRawFrame(audio=pcm16_bytes, sample_rate=sample_rate, num_channels=1)
        self._queue.put_nowait(frame)
        if self.recorder:
            self.recorder.add_user_audio(pcm16_bytes, sample_rate=sample_rate)

    async def run_queue_loop(self):
        """Must be run as a task alongside the Pipecat runner."""
        while True:
            frame = await self._queue.get()
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)
            self._queue.task_done()


class TwilioSink(FrameProcessor):
    """Encodes Pipecat TTS audio as base64 μ-law and sends it back to Twilio."""

    def __init__(
        self,
        websocket: WebSocket,
        call_id: str = "",
        ws_manager=None,
        recorder: SessionRecorder | None = None,
    ):
        super().__init__()
        self.ws = websocket
        self.call_id = call_id
        self.ws_manager = ws_manager
        self.recorder = recorder

    async def process_frame(self, frame, direction):
        if isinstance(frame, AudioRawFrame):
            # Resample to 8kHz μ-law for Twilio
            pcm8 = _resample_pcm16(frame.audio, frame.sample_rate, 8000)
            if self.recorder:
                self.recorder.add_agent_audio(pcm8, sample_rate=8000)
            ulaw = _pcm16_to_ulaw(pcm8)
            payload = base64.b64encode(ulaw).decode("utf-8")
            msg = json.dumps({
                "event": "media",
                "streamSid": self.call_id,
                "media": {"payload": payload},
            })
            try:
                await self.ws.send_text(msg)
            except Exception as e:
                logger.error("[TWILIO SINK] Send error: %s", e)
        await self.push_frame(frame, direction)


async def handle_twilio_stream(
    websocket: WebSocket,
    call_id: str,
    agent_schema_path: str,
    ws_manager=None,
    db=None,
    campaign_id: str = "",
    lead_id: str = "",
    lead_name: str = "Lead",
    phone: str = "",
    client_id: str = "global",
):
    """
    Main Twilio Media Streams WebSocket handler.
    Called for each active call — creates an isolated Pipecat pipeline.

    After the call ends (stop event or disconnect) the conversation data
    is extracted from the live LLM processor and persisted to the database.
    A call_completed WebSocket event is also broadcast to the dashboard.
    """
    await websocket.accept()
    logger.info(
        "[TWILIO] Stream connected — call_id=%s campaign=%s lead=%s",
        call_id, campaign_id, lead_name
    )

    from flows.runtime import RealEstateSTTProcessor, RealEstateLLMProcessor, RealEstateTTSProcessor, VoiceTurnState
    from llm.state_manager import StateManager

    recorder = SessionRecorder(sample_rate=8000)
    source = TwilioSource(recorder=recorder)
    agent_id = os.path.splitext(os.path.basename(agent_schema_path or "default"))[0] or "default"
    turn_state = VoiceTurnState()
    stt = RealEstateSTTProcessor(turn_state=turn_state, agent_id=agent_id)
    llm = RealEstateLLMProcessor(turn_state=turn_state)

    # Load agent schema FRESH from disk on every call (auto-reload)
    if os.path.exists(agent_schema_path):
        llm.state_manager = StateManager(agent_schema_path)

    tts = RealEstateTTSProcessor(turn_state=turn_state, agent_id=agent_id)
    sink = TwilioSink(websocket, call_id=call_id, ws_manager=ws_manager, recorder=recorder)

    pipeline = Pipeline([source, stt, llm, tts, sink])
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    runner_task = asyncio.create_task(runner.run(task))
    queue_task = asyncio.create_task(source.run_queue_loop())

    # Emit ringing/connected events for dashboard live feed
    if ws_manager and campaign_id:
        try:
            await ws_manager.send_call_event(
                "call_connected",
                campaign_id=campaign_id, lead_id=lead_id, lead_name=lead_name,
                status="Connected", provider="twilio", client_id=client_id,
            )
        except Exception as e:
            logger.warning("[TWILIO] WS connected event failed: %s", e)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "connected":
                logger.info("[TWILIO] Media stream connected")

            elif event == "start":
                stream_sid = msg.get("start", {}).get("streamSid", "")
                logger.info("[TWILIO] Stream started: %s", stream_sid)

            elif event == "media":
                payload = msg["media"]["payload"]
                ulaw_bytes = base64.b64decode(payload)
                # Convert μ-law to PCM16
                pcm16 = _ulaw_to_pcm16(ulaw_bytes)
                source.queue_audio(pcm16, sample_rate=8000)

            elif event == "stop":
                logger.info("[TWILIO] Stream stop received")
                break

    except WebSocketDisconnect:
        logger.info("[TWILIO] WebSocket disconnected — call_id=%s", call_id)
    except Exception as e:
        logger.error("[TWILIO] Stream error: %s", e)
    finally:
        queue_task.cancel()
        try:
            await source.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.3)
            runner_task.cancel()
            await runner_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("[TWILIO] Pipeline cleanup complete — call_id=%s", call_id)

        recording_url = ""
        recording_duration_s = 0.0
        try:
            filepath, recording_url = recording_path_and_url("rec_twilio", lead_id or call_id)
            recording_duration_s = recorder.finalize(filepath)
            if recording_duration_s <= 0:
                recording_url = ""
        except Exception as rec_err:
            logger.warning("[TWILIO] Recording finalize failed: %s", rec_err)

        # ── Persist call result to DB ─────────────────────────────────────────
        # This is the critical step that was missing before v2.2.
        # We always attempt a persist if we have both db and a campaign_id.
        if db and campaign_id:
            await _persist_call_result(
                db=db,
                ws_manager=ws_manager,
                llm=llm,
                campaign_id=campaign_id,
                lead_id=lead_id or call_id,
                lead_name=lead_name,
                phone=phone,
                client_id=client_id,
                recording_url=recording_url,
                recording_duration_s=recording_duration_s,
            )


async def _persist_call_result(
    db,
    ws_manager,
    llm,
    campaign_id: str,
    lead_id: str,
    lead_name: str,
    phone: str,
    client_id: str,
    recording_url: str = "",
    recording_duration_s: float = 0.0,
) -> None:
    """
    Extract conversation_data from the live LLM processor's StateManager and
    write a completed call result row to SQLite. Then broadcast call_completed.

    This mirrors exactly what DemoCallEngine._build_result() produces so that
    the frontend results page treats Twilio and demo calls identically.
    """
    try:
        sm = getattr(llm, "state_manager", None)
        data: dict = {}
        if sm:
            data = getattr(sm, "conversation_data", {}) or {}

        history: list[dict] = getattr(llm, "history", []) or []
        turns = len(history) // 2  # each turn = 1 user + 1 assistant msg

        interested = "Yes" if (data.get("location") or data.get("intent_value")) else "No"
        callback_val = data.get("timeline") or data.get("callback") or "—"

        result = {
            "name":          lead_name,
            "phone":         phone,
            "calledAt":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration":      int(recording_duration_s) if recording_duration_s > 0 else f"{turns * 12}s",
            "status":        "Connected",
            "interested":    interested,
            "budget":        data.get("budget", "—"),
            "callback":      callback_val,
            "location":      data.get("location", "—"),
            "transcription": history,
            "provider":      "twilio",
            "processed":     True,
            "lead_data":     data,
            "recording_url": recording_url,
        }

        await db.append_call_result(campaign_id, result)
        await db.update_live_state(
            lead_id, campaign_id, lead_name,
            "Completed", "Call ended", history, "twilio"
        )
        logger.info(
            "[TWILIO] Result persisted — campaign=%s lead=%s turns=%d interested=%s",
            campaign_id, lead_name, turns, interested
        )

        # Broadcast call_completed so the dashboard live feed updates
        if ws_manager:
            await ws_manager.send_call_event(
                "call_completed",
                campaign_id=campaign_id,
                lead_id=lead_id,
                lead_name=lead_name,
                status="Completed",
                snippet="Call ended",
                transcripts=history,
                result=result,
                provider="twilio",
                client_id=client_id,
            )
    except Exception as e:
        logger.error("[TWILIO] _persist_call_result error: %s", e)


def build_twiml(stream_url: str) -> str:
    """
    Returns TwiML XML that connects Twilio to our Media Stream WebSocket.
    Called by the /telephony/twiml/{call_id} webhook.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}">
      <Parameter name="encoding" value="audio/x-mulaw"/>
    </Stream>
  </Connect>
</Response>"""


# ── Audio Codec Helpers ───────────────────────────────────────────────────────

def _resample_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    if not audio:
        return audio
    samples = np.frombuffer(audio, dtype=np.int16)
    if source_rate == target_rate or samples.size == 0:
        return audio
    gcd = np.gcd(source_rate, target_rate)
    resampled = resample_poly(samples.astype(np.float32), target_rate // gcd, source_rate // gcd)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


# μ-law lookup tables (standard G.711)
_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635

_ENCODE_TABLE = bytes([
    0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
    4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
])


def _pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit PCM to 8-bit μ-law."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    out = bytearray(len(samples))
    for i, sample in enumerate(samples):
        sign = 0 if sample >= 0 else 0x80
        if sign:
            sample = -sample
        sample = min(sample, _ULAW_CLIP)
        sample += _ULAW_BIAS
        exp = _ENCODE_TABLE[sample >> 7]
        mantissa = (sample >> (exp + 3)) & 0x0F
        out[i] = ~(sign | (exp << 4) | mantissa) & 0xFF
    return bytes(out)


def _ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Convert 8-bit μ-law to 16-bit PCM."""
    out = bytearray(len(ulaw_bytes) * 2)
    for i, byte in enumerate(ulaw_bytes):
        byte = ~byte & 0xFF
        sign   = byte & 0x80
        exp    = (byte >> 4) & 0x07
        mant   = byte & 0x0F
        sample = (mant << (exp + 3)) + (0x21 << exp) - 33
        if sign:
            sample = -sample
        packed = max(-32768, min(32767, sample))
        out[i * 2]     = packed & 0xFF
        out[i * 2 + 1] = (packed >> 8) & 0xFF
    return bytes(out)
