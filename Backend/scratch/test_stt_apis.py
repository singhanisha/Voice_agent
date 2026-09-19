import os
import sys
from pathlib import Path
from dotenv import load_dotenv

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))
load_dotenv(backend_dir / ".env")

import numpy as np
from stt import stt_groq
from stt import stt_deepgram

# Create 1 second of silent audio for smoke testing
silent_chunk = np.zeros(16000, dtype=np.int16).tobytes()

print("--- Testing Groq ---")
try:
    print("GROQ_API_KEY:", os.getenv("GROQ_API_KEY")[:15] + "...")
    text_groq = stt_groq.transcribe_audio(silent_chunk)
    print("Groq transcript:", repr(text_groq))
except Exception as e:
    print("Groq failed:", e)

print("\n--- Testing Deepgram ---")
try:
    print("DEEPGRAM_API_KEY:", os.getenv("DEEPGRAM_API_KEY")[:15] + "...")
    text_dg = stt_deepgram.transcribe_audio(silent_chunk)
    print("Deepgram transcript:", repr(text_dg))
except Exception as e:
    print("Deepgram failed:", e)
