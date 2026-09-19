import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from call_recording import SessionRecorder, recording_path_and_url


def _tone(rate: int, hz: int, seconds: float) -> bytes:
    count = int(rate * seconds)
    samples = [
        int(12000 * math.sin(2 * math.pi * hz * (idx / rate)))
        for idx in range(count)
    ]
    return np.array(samples, dtype=np.int16).tobytes()


class SessionRecorderTest(unittest.TestCase):
    def test_writes_stereo_wav_with_user_and_agent_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "call.wav")
            recorder = SessionRecorder(sample_rate=24000)
            recorder.add_user_audio(_tone(16000, 440, 0.2), sample_rate=16000)
            recorder.add_agent_audio(_tone(24000, 880, 0.2), sample_rate=24000)

            duration = recorder.finalize(path)

            self.assertGreater(duration, 0)
            with wave.open(path, "rb") as wf:
                self.assertEqual(wf.getnchannels(), 2)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 24000)
                frames = wf.readframes(wf.getnframes())

            stereo = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2)
            self.assertGreater(np.abs(stereo[:, 0]).max(), 0)
            self.assertGreater(np.abs(stereo[:, 1]).max(), 0)

    def test_ducks_user_channel_when_agent_audio_is_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "call.wav")
            recorder = SessionRecorder(sample_rate=24000)
            user_echo = np.full(24000 // 5, 9000, dtype=np.int16).tobytes()
            agent_audio = np.full(24000 // 5, 9000, dtype=np.int16).tobytes()
            recorder.add_user_audio(user_echo, sample_rate=24000)
            recorder.add_agent_audio(agent_audio, sample_rate=24000)

            recorder.finalize(path)

            with wave.open(path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
            stereo = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2)
            middle = stereo[1000:3000]

            self.assertLess(np.abs(middle[:, 0]).mean(), 3000)
            self.assertGreater(np.abs(middle[:, 1]).mean(), 10000)

    def test_recording_path_and_url_sanitizes_call_key(self):
        path, url = recording_path_and_url("rec twilio", "call/id:123", recordings_dir="recordings")
        self.assertTrue(path.endswith("rec_twilio_call_id_123.wav"))
        self.assertEqual(url, "/recordings/rec_twilio_call_id_123.wav")


if __name__ == "__main__":
    unittest.main()
