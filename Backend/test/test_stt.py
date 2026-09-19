"""
Quick smoke-test for the STT module.
Run from the project root:  python test_stt.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
import numpy as np

print("=" * 50)
print("       AI Voice Agent — STT Module Test")
print("=" * 50)

# ── 1. Model load ────────────────────────────────────
print("\n[1/3] Loading Whisper model …")
t0 = time.time()

try:
    from stt import transcribe_audio
except Exception as e:
    print(f"\n[FAIL] Import failed: {e}")
    sys.exit(1)

load_time = time.time() - t0
print(f"     [SUCCESS] Model loaded in {load_time:.2f}s")

# ── 2. Silence test (VAD should return "") ───────────
def safe_str(s: str) -> str:
    return (s or "").encode("ascii", errors="replace").decode("ascii")

print("\n[2/3] Transcribing 1 second of silence …")
silent_chunk = np.zeros(16000, dtype=np.int16).tobytes()   # 16kHz, mono, PCM16

t1 = time.time()
result = transcribe_audio(silent_chunk)
latency = time.time() - t1

print(f"     Result  : '{safe_str(result)}'")
print(f"     Latency : {latency:.3f}s")
if result == "":
    print("     [SUCCESS] Silence handled correctly (empty string returned)")
else:
    print(f"     [WARNING] Unexpected text for silence: '{safe_str(result)}'")

# ── 3. Synthetic tone test (non-silent audio) ────────
print("\n[3/3] Transcribing 2 seconds of 440 Hz sine wave …")
sample_rate = 16000
duration    = 2
t_arr       = np.linspace(0, duration, sample_rate * duration, endpoint=False)
sine_wave   = (np.sin(2 * np.pi * 440 * t_arr) * 0.5 * 32767).astype(np.int16)
sine_chunk  = sine_wave.tobytes()

t2 = time.time()
tone_result = transcribe_audio(sine_chunk)
tone_latency = time.time() - t2

print(f"     Result  : '{safe_str(tone_result)}'")
print(f"     Latency : {tone_latency:.3f}s")
print("     [INFO] A tone is not speech — empty or noise text is expected.")

# ── Summary ──────────────────────────────────────────
print("\n" + "=" * 50)
print("  Summary")
print("=" * 50)
print(f"  Model load time  : {load_time:.2f}s")
print(f"  Silence latency  : {latency:.3f}s")
print(f"  Tone latency     : {tone_latency:.3f}s")
target_ok = latency < 0.8 and tone_latency < 0.8
print(f"  Latency target   : {'[SUCCESS] within 0.8s' if target_ok else '[WARNING] exceeded 0.8s'}")
print("\n[SUCCESS] Basic STT sanity-check complete.\n")
