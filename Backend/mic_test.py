"""
Live Microphone Test for Agent Neha (Pipecat 0.0.108).
Speak into your computer's microphone and listen to Neha through your speakers.
"""

import asyncio
import io
import logging
import os
import sys

from dotenv import load_dotenv
import sounddevice as sd
import soundfile as sf

# Pipecat imports
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from flows.runtime import RealEstateSTTProcessor, RealEstateLLMProcessor, RealEstateTTSProcessor
from tts import check_voice_assets, generate_speech

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MIC-TEST")
load_dotenv()


def _configure_stdout() -> None:
    """Avoid Windows console encoding crashes during local diagnostics."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def _play_startup_greeting() -> bool:
    """Verify speaker playback before starting the live mic loop."""
    ok, message = check_voice_assets()
    if not ok:
        print(f"TTS preflight failed: {message}")
        return False

    greeting = generate_speech("Hello, I am Neha. If you can hear this, local TTS playback is working.")
    if not greeting:
        print("TTS preflight failed: TTS engine returned no audio.")
        return False

    data, sample_rate = sf.read(io.BytesIO(greeting))
    print("Playing startup greeting to verify your speakers...")
    sd.play(data, sample_rate)
    sd.wait()
    return True

async def main():
    _configure_stdout()
    if not os.getenv("GROQ_API_KEY"):
        print("❌ Error: GROQ_API_KEY not found in .env file.")
        sys.exit(1)

    # 1. Initialize Local Audio Transport (PyAudio wrapper)
    # Print device info before starting
    import pyaudio
    pa = pyaudio.PyAudio()
    default_in = pa.get_default_input_device_info()
    default_out = pa.get_default_output_device_info()
    print(f"🎤 Using Input: {default_in['name']}")
    print(f"🔊 Using Output: {default_out['name']}")
    pa.terminate()

    # 1. Initialize Local Audio Transport (PyAudio wrapper)
    print("[INIT] Using system default microphone and speakers.")
    if not _play_startup_greeting():
        return
    
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_sample_rate=16000,
        audio_out_sample_rate=24000
    ))

    # 2. Initialize our Real Estate Processors
    print("⏳ Loading AI Engines...")
    print("   - Initializing STT...")
    stt = RealEstateSTTProcessor()
    print("   - Initializing LLM (Groq)...")
    llm = RealEstateLLMProcessor()
    print("   - Initializing TTS...")
    tts = RealEstateTTSProcessor()
    print("[SUCCESS] AI Engines Ready.")



    # 3. Build the Pipeline
    pipeline = Pipeline([
        transport.input(), # Mic Input
        stt,
        llm,
        tts,
        transport.output()  # Speaker Output
    ])

    runner = PipelineRunner()
    task = PipelineTask(pipeline)

    print("\n" + "="*50)
    print("🚀 Pipeline starting...")
    print("Speak into the microphone after startup. The live pipeline does not reply until it hears input.")
    print("Press Ctrl+C to stop.")
    print("="*50 + "\n")

    try:
        print("🟢 Running... (Neha should speak now)")
        print("Running... Waiting for microphone input.")
        await runner.run(task)



    except KeyboardInterrupt:
        print("\nStopping...")
    except Exception as e:
        logger.error(f"Error in Mic Test: {e}")

if __name__ == "__main__":
    asyncio.run(main())
