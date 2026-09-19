"""
Simple script to test the TTS engine.
Usage: python check_voice.py "Text to speak"
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
import sounddevice as sd
import soundfile as io
import numpy as np

try:
    from tts import generate_speech_stream
    def generate_speech(text):
        return b"".join(generate_speech_stream(text))
except ImportError:
    print("❌ Error: Could not import tts module. Ensure dependencies are installed.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)

def main():
    if len(sys.argv) < 2:
        text = "Hello! I am Neha, your real estate assistant. How can I help you today?"
        print(f"No text provided. Using default: '{text}'")
    else:
        text = sys.argv[1]

    print(f"🚀 Synthesizing: '{text}'...")
    
    # generate_speech returns WAV bytes
    audio_bytes = generate_speech(text)
    
    if not audio_bytes:
        print("❌ Error: TTS generated no audio.")
        return

    print("✅ Synthesis complete. Playing audio...")
    
    # Convert WAV bytes to numpy array for playback
    import io
    import soundfile as sf
    
    data, fs = sf.read(io.BytesIO(audio_bytes))
    
    # Play the audio
    sd.play(data, fs)
    sd.wait() # Wait until audio is finished playing
    
    print("👋 Playback finished.")

if __name__ == "__main__":
    main()
