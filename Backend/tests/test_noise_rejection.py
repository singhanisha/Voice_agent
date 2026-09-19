import asyncio
import sys
from audio.mic_utils import VADBuffer
import numpy as np

def test_noise_rejection():
    print("Running Noise Rejection Test for VADBuffer...")
    
    # Initialize VAD Buffer with standard params and payload_speech_threshold=300
    vad = VADBuffer(
        sample_rate=16000,
        energy_threshold=500.0,
        silence_duration_s=0.4,
        min_speech_duration_s=0.3,
        payload_speech_threshold=300.0
    )
    
    # Generate 350ms of "low energy" noise (below 300 RMS)
    # Since numpy RMS of `ones(x) * 200` is 200:
    noise_chunk_1 = (np.ones(int(16000 * 0.35), dtype=np.int16) * 200).tobytes()
    
    # Generate 50ms of "high energy" noise spike (above 500) to trigger VAD `is_speaking = True`
    spike_chunk = (np.ones(int(16000 * 0.05), dtype=np.int16) * 800).tobytes()
    
    # Generate 450ms of complete silence to trigger endpoint
    silence_chunk = (np.zeros(int(16000 * 0.45), dtype=np.int16)).tobytes()

    # Step 1: Push noise (should be ignored since it's below energy_threshold 500)
    res = vad.process_chunk(noise_chunk_1)
    assert res is None, "VAD triggered on low energy noise"
    
    # Step 2: Push spike (triggers VAD to start speaking state)
    res = vad.process_chunk(spike_chunk)
    assert res is None, "VAD emitted payload too early"
    assert vad.is_speaking is True, "VAD did not trigger speaking state on spike"
    
    # Step 3: Push long silence to force endpoint
    # The payload will contain the 50ms spike + 450ms silence. 
    # Let's calculate its expected RMS.
    # Total length: 500ms = 8000 samples.
    # RMS = sqrt( ( (800^2)*800 + (0^2)*7200 ) / 8000 ) = sqrt( 512,000,000 / 8000 ) = sqrt(64,000) = 252.9
    # This is < 300, so it should be dropped!
    res = vad.process_chunk(silence_chunk)
    
    # Because length is 500ms (>= 300ms min_speech_duration), the old VAD would have emitted it.
    # The new VAD should drop it because RMS (252.9) < 300.0.
    assert res is None, "VAD emitted low-energy hallucination payload instead of dropping it!"
    
    print("[SUCCESS] Low-energy noise spike was successfully dropped and prevented from reaching STT.")

if __name__ == "__main__":
    test_noise_rejection()
