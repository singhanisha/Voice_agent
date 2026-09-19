"""
Main conversation loop for the AI Voice Agent using local microphone and speakers.
Integration Note: Orchestrates STT, LLM, and TTS modules for real-time interaction.
"""

import sys
import os
import time
import asyncio
import math
from time import perf_counter

# Ensure project root is in sys.path for module imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import local audio utilities
from audio.mic_utils import record_audio, play_audio, convert_to_wav_bytes
from llm.language_utils import LanguageTracker, analyze_user_text

# CONFIGURATION BLOCK
INPUT_SAMPLE_RATE    = 16000    # Hz — microphone recording
OUTPUT_SAMPLE_RATE   = 24000    # Hz — TTS output
RECORD_DURATION_S    = 4        # seconds per recording window
POST_RESPONSE_PAUSE_S = 1.0     # silence gap after AI speaks before next listen
MIN_TRANSCRIPTION_CHARS = 3     # ignore STT output shorter than this
VERBOSE              = True
BARGE_IN_GUARD_S     = 0.9      # do not allow interruptions in the first part of playback
BARGE_IN_MIN_VOICE_S = 0.20     # require sustained voice, not a single loud spike
BARGE_IN_RELEASE_S   = 0.12     # reset detector after a brief quiet gap
BARGE_IN_CHUNK_SIZE  = 512
BARGE_IN_RMS_MULTIPLIER = 8.0
BARGE_IN_MIN_RMS     = 0.10
CALL_CONNECTED_TRIGGER = "[System: The call has just been connected. No user has spoken yet. Speak only for the current conversation node and do not transition.]"


def audio_devices_available() -> bool:
    """Return whether the default microphone and speaker are available."""
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
        sd.query_devices(kind="output")
        return True
    except Exception as exc:
        print(f"[ERROR] Audio device unavailable: {exc}")
        return False


def interruptible_play(speech_gen, start_time):
    """Plays audio from a generator while listening for barge-in interruptions."""
    import sounddevice as sd
    import numpy as np
    import threading
    from stt.config import ENERGY_THRESHOLD
    
    try:
        stream = sd.OutputStream(samplerate=OUTPUT_SAMPLE_RATE, channels=1, dtype='int16')
        stream.start()
    except Exception as exc:
        print(f"[WARN] Speaker output unavailable: {exc}")
        return
    
    interrupted = threading.Event()
    stop_monitor = threading.Event()
    monitor_thread = None
    
    def check_interruption(playback_started_at: float):
        chunk_duration_s = BARGE_IN_CHUNK_SIZE / INPUT_SAMPLE_RATE
        required_voiced_chunks = max(3, math.ceil(BARGE_IN_MIN_VOICE_S / chunk_duration_s))
        release_chunks = max(2, math.ceil(BARGE_IN_RELEASE_S / chunk_duration_s))
        barrage_threshold = max(ENERGY_THRESHOLD * BARGE_IN_RMS_MULTIPLIER, BARGE_IN_MIN_RMS)
        voiced_chunks = 0
        quiet_chunks = 0

        try:
            with sd.InputStream(
                samplerate=INPUT_SAMPLE_RATE,
                channels=1,
                dtype='float32',
                blocksize=BARGE_IN_CHUNK_SIZE,
            ) as in_stream:
                while not stop_monitor.is_set():
                    chunk, _ = in_stream.read(BARGE_IN_CHUNK_SIZE)
                    if stop_monitor.is_set():
                        break

                    if perf_counter() - playback_started_at < BARGE_IN_GUARD_S:
                        voiced_chunks = 0
                        quiet_chunks = 0
                        continue

                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    if rms >= barrage_threshold:
                        voiced_chunks += 1
                        quiet_chunks = 0
                        if voiced_chunks >= required_voiced_chunks:
                            interrupted.set()
                            break
                    else:
                        quiet_chunks += 1
                        if quiet_chunks >= release_chunks:
                            voiced_chunks = 0
        except Exception:
            pass

    try:
        first_chunk = True
        was_interrupted = False
        for pcm16 in speech_gen:
            if first_chunk:
                tts_time = perf_counter() - start_time
                if VERBOSE:
                    print(f" [TTFB: {tts_time:.2f}s]")
                playback_started_at = perf_counter()
                monitor_thread = threading.Thread(
                    target=check_interruption,
                    args=(playback_started_at,),
                    daemon=True,
                )
                monitor_thread.start()
                first_chunk = False

            if interrupted.is_set():
                was_interrupted = True
                break
            if pcm16:
                stream.write(np.frombuffer(pcm16, dtype=np.int16))
                if interrupted.is_set():
                    was_interrupted = True
                    break

        if was_interrupted:
            print(" (INTERRUPTED - Flushing Echo...)")
            time.sleep(0.25)
    finally:
        stop_monitor.set()
        if monitor_thread and monitor_thread.is_alive():
            monitor_thread.join(timeout=0.2)
        try:
            stream.stop()
        finally:
            stream.close()


def run_conversation():
    """
    Implements the core conversation loop: Listen -> STT -> LLM -> TTS -> Playback.
    Handles individual module imports and provides clean exit on Ctrl+C.
    """
    
    # STEP 1: Startup - Individual module imports with error handling
    try:
        from stt.stt import transcribe_audio
    except ImportError:
        print("[ERROR] Failed to import STT module (stt/stt.py)")
        sys.exit(1)

    try:
        from llm.llm import generate_response
    except ImportError:
        print("[ERROR] Failed to import LLM module (llm/llm.py)")
        sys.exit(1)

    try:
        from tts.tts_edge import generate_speech_stream
    except ImportError:
        print("[ERROR] Failed to import TTS module (tts/tts_edge.py)")
        sys.exit(1)

    try:
        from llm.state_manager import StateManager
        schema_path = os.path.join(project_root, "Updated_Real_Estate_Agent.json")
        state_manager = StateManager(schema_path)
    except ImportError:
        print("[ERROR] Failed to import state_manager")
        sys.exit(1)

    if not audio_devices_available():
        print("Mic mode needs a default microphone and speaker. Check macOS audio permissions/devices, then run again.")
        sys.exit(1)

    # Startup Header
    print("---------------------------------")
    print(" AI Voice Agent — Mic Mode")
    print(" Press Ctrl+C to end conversation")
    print("---------------------------------")

    conversation_history = []
    current_language = "en"
    language_tracker = LanguageTracker(initial_language=current_language)
    
    # Text buffers for multi-turn thought accumulation
    accumulated_text = ""
    
    try:
        # STEP 0: Initial Greeting (AI Speaks First)
        state_manager.reset_state()
        print("[AI] Initializing greeting...")
        start_time_tts = perf_counter()
        response_text = asyncio.run(generate_response(
            CALL_CONNECTED_TRIGGER,
            conversation_history, 
            language=current_language,
            state_manager=state_manager,
            allow_transition=False,
        ))
        if response_text:
            print(f"[AI]   {response_text}")
            conversation_history.append({"role": "assistant", "content": response_text})
            
            # Start TTS for greeting
            speech_gen = generate_speech_stream(response_text, current_language)
            if speech_gen:
                interruptible_play(speech_gen, start_time_tts)

        while True:
            try:
                # STEP 1 — Listen
                audio = record_audio(3.0, INPUT_SAMPLE_RATE)
                
                if audio.size == 0:
                    continue

                # STEP 2 — STT
                t0 = perf_counter()
                wav_bytes = convert_to_wav_bytes(audio, INPUT_SAMPLE_RATE)
                text = transcribe_audio(wav_bytes)
                stt_time = perf_counter() - t0
                
                if not text or len(text.strip()) < 1:
                    continue
                
                current_text = (accumulated_text + " " + text).strip()
                
                # Removed 'thought incomplete' arbitrary blockers.
                # All detected speech drops to LLM to evaluate State Rules directly.
                
                # Thought is complete
                final_text = current_text
                accumulated_text = "" # Reset buffer

                user_analysis = analyze_user_text(final_text, fallback=current_language)
                if not user_analysis.actionable:
                    if VERBOSE:
                        print(f"[STT] Ignored unclear transcription ({user_analysis.reason}): {final_text}")
                    continue
                final_text = user_analysis.cleaned_text
                
                print(f"[USER] {final_text}")
                current_language, _ = language_tracker.observe(final_text)

                # STEP 3 — LLM
                t1 = perf_counter()
                response_text = asyncio.run(generate_response(
                    final_text,
                    conversation_history,
                    language=current_language,
                    state_manager=state_manager,
                ))
                llm_time = perf_counter() - t1

                conversation_history.append({"role": "user", "content": final_text})
                
                if not response_text:
                    continue
                    
                print(f"[AI]   {response_text}")
                conversation_history.append({"role": "assistant", "content": response_text})

                # STEP 4 — TTS
                t2 = perf_counter()
                speech_gen = generate_speech_stream(response_text, current_language)
                
                if speech_gen:
                    interruptible_play(speech_gen, t2)
                    time.sleep(POST_RESPONSE_PAUSE_S)

                # Check if the conversation has reached a terminal node
                if getattr(state_manager, '_session_ended', False):
                    print("\n[AI] Session ended gracefully.")
                    raise KeyboardInterrupt

            except Exception as e:
                print(f"[ERROR] {e}")
                continue

    except KeyboardInterrupt:
        print("\nConversation ended.")
        sys.exit(0)

if __name__ == "__main__":
    run_conversation()
