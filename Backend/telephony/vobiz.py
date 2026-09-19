"""
VoBiz Telephony WebSocket Server.

This handles concurrent bidirectional audio streaming with VoBiz infrastructure.
Built to support 20+ concurrent SIP calls simultaneously using FastAPI and async Pipecat Pipelines.
"""

import os
import json
import base64
import logging
import asyncio

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from scipy.signal import resample_poly

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.frames.frames import AudioRawFrame, EndFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from flows.runtime import RealEstateSTTProcessor, RealEstateLLMProcessor, RealEstateTTSProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VOBIZ-SERVER")
load_dotenv()

app = FastAPI()


class VoBizSource(FrameProcessor):
    """Entry point for audio data received from the WebSocket."""
    def __init__(self):
        super().__init__()

    async def process_frame(self, frame, direction):
        await self.push_frame(frame, direction)

class VoBizSink(FrameProcessor):
    """Exit point that encodes TTS audio and sends it back out the WebSocket."""
    def __init__(self, websocket: WebSocket):
        super().__init__()
        self.ws = websocket

    async def process_frame(self, frame, direction):
        if isinstance(frame, AudioRawFrame):
            audio_bytes = frame.audio
            if frame.sample_rate != 8000:
                audio_bytes = _resample_pcm16(frame.audio, frame.sample_rate, 8000)

            # VoBiz expects base64 encoded audio
            payload = base64.b64encode(audio_bytes).decode("utf-8")
            msg = json.dumps({
                "event": "media",
                "media": {"payload": payload}
            })
            try:
                await self.ws.send_text(msg)
            except Exception as e:
                logger.error(f"Failed to send audio to VoBiz: {e}")
        
        await self.push_frame(frame, direction)


@app.get("/")
async def health_check():
    return {"status": "online", "message": "VoBiz AI Voice Agent scalable server running."}


@app.websocket("/vobiz/stream")
async def vobiz_stream(websocket: WebSocket):
    """
    Handles exactly one concurrent phone call. 
    FastAPI will spawn multiple coroutines for this endpoint to handle up to 20 calls at once.
    """
    await websocket.accept()
    logger.info("New VoBiz call connected.")

    # 1. Spin up isolated instances of our Pipecat processors for THIS CALL ONLY
    source = VoBizSource()
    stt = RealEstateSTTProcessor()
    llm = RealEstateLLMProcessor()
    tts = RealEstateTTSProcessor()
    sink = VoBizSink(websocket)

    pipeline = Pipeline([source, stt, llm, tts, sink])
    runner = PipelineRunner()
    task = PipelineTask(pipeline)

    # 2. Start the Pipecat Task Runner
    runner_task = asyncio.create_task(runner.run(task))

    # 3. Read incoming packets from WebSocket asynchronously and push them into Pipecat
    async def vobiz_receiver():
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                
                if msg.get("event") == "media":
                    audio_b64 = msg["media"]["payload"]
                    audio_bytes = base64.b64decode(audio_b64)
                    
                    # Push incoming 8kHz mono audio directly into the STT engine
                    await source.push_frame(AudioRawFrame(audio=audio_bytes, sample_rate=8000, num_channels=1), FrameDirection.DOWNSTREAM)
                
                elif msg.get("event") == "stop":
                    logger.info("Call stopped by VoBiz.")
                    break
        except WebSocketDisconnect:
            logger.info("Call client disconnected.")
        except Exception as e:
            logger.error(f"WebSocket Error: {e}")
        finally:
            # Tell Pipecat to shut down this pipeline
            await source.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)

    receiver_task = asyncio.create_task(vobiz_receiver())

    # Wait for either the pipeline to finish or the connection to close
    await asyncio.gather(receiver_task, runner_task, return_exceptions=True)
    logger.info("VoBiz call ended and cleaned up.")


def _resample_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    samples = np.frombuffer(audio, dtype=np.int16)
    if samples.size == 0 or source_rate == target_rate:
        return audio

    gcd = np.gcd(source_rate, target_rate)
    up = target_rate // gcd
    down = source_rate // gcd
    resampled = resample_poly(samples.astype(np.float32), up, down)
    resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
    return resampled.tobytes()

