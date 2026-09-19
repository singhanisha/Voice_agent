"""
Test Pipeline for AI Voice Agent (STT -> LLM -> TTS)

This script runs a complete local test of the voice agent pipeline, bypassing the
telephony and microphone/speaker interfaces. It reads an input WAV file, passes it
through the STT, feeds the transcription to the LLM, and passes the text response
to the TTS engine, finally saving the generated audio.

Audio Input Requirements:
test_input.wav must be:
- WAV format, mono, 16kHz, PCM16
- Duration: 3–10 seconds of speech recommended

To generate a compliant test file using ffmpeg:
    ffmpeg -i input.mp3 -ar 16000 -ac 1 -sample_fmt s16 audio_samples/test_input.wav
"""

import os
import sys
import time
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_AUDIO_PATH  = "audio_samples/test_input.wav"
OUTPUT_AUDIO_PATH = "output/test_output.wav"
VERBOSE           = True
EXPECTED_MIN_STT_CHARS  = 5
EXPECTED_MAX_LLM_WORDS  = 80

# ==========================================
# IMPORTS
# ==========================================
project_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(project_root))

try:
    from stt.stt import transcribe_audio
except ImportError as e:
    print(f"Failed to import STT module: {e}")
    sys.exit(1)

try:
    from llm.llm import generate_response
except ImportError as e:
    print(f"Failed to import LLM module: {e}")
    sys.exit(1)

try:
    from tts import generate_speech_stream
    def generate_speech(text):
        return b"".join(generate_speech_stream(text))
except ImportError as e:
    print(f"Failed to import TTS module: {e}")
    sys.exit(1)

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def _load_wav(path: str) -> bytes:
    """Read and return raw bytes."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing input file: {path}. Please provide a valid WAV file.")
    with open(path, "rb") as f:
        return f.read()

def _validate_wav_bytes(audio: bytes) -> bool:
    """Return True if audio starts with b'RIFF' and len > 100."""
    return audio.startswith(b"RIFF") and len(audio) > 100

def _save_wav(audio: bytes, path: str) -> None:
    """Create parent directory if it does not exist and write bytes to path."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(audio)

def _truncate(text: str, max_chars: int = 60) -> str:
    """Return text[:max_chars] + '...' if longer, else text as-is."""
    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text

# ==========================================
# MAIN PIPELINE
# ==========================================
def run_pipeline() -> None:
    """Executes the STT > LLM > TTS pipeline gracefully, measuring time and validating types."""
    stt_latency, llm_latency, tts_latency = 0.0, 0.0, 0.0
    stt_text, llm_text = "", ""
    tts_saved, output_kb = False, 0.0
    
    # ----------------------------------
    # STEP 1: Load audio
    # ----------------------------------
    try:
        if not INPUT_AUDIO_PATH.lower().endswith(".wav"):
            print(f"[ERROR] Input audio path must end with .wav: {INPUT_AUDIO_PATH}")
            return
            
        audio_bytes = _load_wav(INPUT_AUDIO_PATH)
        file_size = len(audio_bytes)
        if file_size == 0:
            print(f"[ERROR] Input audio file {INPUT_AUDIO_PATH} is empty (0 bytes).")
            return
            
        if VERBOSE:
            print(f"Loaded input audio {INPUT_AUDIO_PATH} ({file_size / 1024:.2f} KB)")
            
    except Exception as e:
        print(f"[ERROR] Step 1 (Load audio) failed: {e}")
        return

    # ----------------------------------
    # STEP 2: STT
    # ----------------------------------
    try:
        t0 = time.perf_counter()
        stt_result = transcribe_audio(audio_bytes)
        stt_latency = time.perf_counter() - t0
        
        if not isinstance(stt_result, str):
            print(f"[ERROR] STT returned an invalid type: {type(stt_result)}")
        else:
            stt_text = stt_result.strip()
            
            if len(stt_text) == 0:
                print("[WARN] STT returned empty — check audio quality")
            elif len(stt_text) < EXPECTED_MIN_STT_CHARS:
                print(f"[WARN] STT transcription is unusually short ({len(stt_text)} chars)")
                
            if VERBOSE:
                print(f"[STT] transcription: {stt_text}")
                print(f"[TIME] STT latency: {stt_latency:.2f}s")
                
    except Exception as e:
        print(f"[ERROR] STT step failed: {e}")
        
    if not stt_text:
        print("[INFO] Skipping subsequent steps (LLM, TTS) because STT returned no text.")

    # ----------------------------------
    # STEP 3: LLM
    # ----------------------------------
    if stt_text:
        try:
            t0 = time.perf_counter()
            llm_result = generate_response(stt_text)
            llm_latency = time.perf_counter() - t0
            
            if not isinstance(llm_result, str):
                print(f"[ERROR] LLM returned an invalid type: {type(llm_result)}")
            else:
                llm_text = llm_result.strip()
                
                if len(llm_text) == 0:
                    print("[WARN] LLM returned an empty response.")
                else:
                    word_count = len(llm_text.split())
                    if word_count > EXPECTED_MAX_LLM_WORDS:
                        print(f"[WARN] LLM response may be too long for TTS latency target ({word_count} words).")
                        
                if VERBOSE:
                    print(f"[LLM] response: {llm_text}")
                    print(f"[TIME] LLM latency: {llm_latency:.2f}s")
                    
        except Exception as e:
            print(f"[ERROR] LLM step failed: {e}")
            
        if not llm_text:
            print("[INFO] Skipping TTS because LLM returned no text.")

    # ----------------------------------
    # STEP 4: TTS
    # ----------------------------------
    if llm_text:
        try:
            t0 = time.perf_counter()
            tts_result = generate_speech(llm_text)
            tts_latency = time.perf_counter() - t0
            
            if not isinstance(tts_result, bytes):
                print(f"[ERROR] TTS returned an invalid type: {type(tts_result)}")
            elif not _validate_wav_bytes(tts_result):
                print("[ERROR] TTS returned invalid audio")
            else:
                _save_wav(tts_result, OUTPUT_AUDIO_PATH)
                output_kb = len(tts_result) / 1024
                tts_saved = True
                
                if VERBOSE:
                    print(f"[TTS] audio saved: {OUTPUT_AUDIO_PATH} ({output_kb:.2f} KB)")
                    print(f"[TIME] TTS latency: {tts_latency:.2f}s")
                    
        except Exception as e:
            print(f"[ERROR] TTS step failed: {e}")

    # ----------------------------------
    # STEP 5: Summary
    # ----------------------------------
    status_stt = "✓" if stt_text else "✗"
    status_llm = "✓" if llm_text else "✗"
    status_tts = "✓" if tts_saved else "✗"
    
    total_time = stt_latency + llm_latency + tts_latency
    
    success_count = sum(bool(x) for x in (stt_text, llm_text, tts_saved))
    if success_count == 3:
        status_flag = "PASS"
    elif success_count > 0:
        status_flag = "PARTIAL"
    else:
        status_flag = "FAIL"

    print("\n  ─────────────────────────────────────")
    print("  PIPELINE RESULT")
    print("  ─────────────────────────────────────")
    print(f"  STT   : {status_stt}  {stt_latency:.2f}s   \"{_truncate(stt_text)}\"")
    print(f"  LLM   : {status_llm}  {llm_latency:.2f}s   \"{_truncate(llm_text)}\"")
    print(f"  TTS   : {status_tts}  {tts_latency:.2f}s   {OUTPUT_AUDIO_PATH if tts_saved else 'None'} ({output_kb:.0f} KB)")
    print(f"  TOTAL : {total_time:.2f}s")
    print(f"  STATUS: {status_flag}")
    print("  ─────────────────────────────────────\n")

if __name__ == "__main__":
    run_pipeline()
