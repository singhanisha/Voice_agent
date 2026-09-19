import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tts.speech_formatter import optimize_for_tts, normalize_caps, normalize_fillers
from tts.cloud_tts_client import inject_breath_cues


class SpeechFormatterTest(unittest.TestCase):
    def test_normalize_caps_wow(self):
        # WOW should be converted to Wow / wow, NOT spelled out as W O W
        self.assertEqual(normalize_caps("WOW"), "Wow")
        self.assertEqual(normalize_caps("WOW! That is GREAT"), "Wow! That is great")
        self.assertEqual(normalize_caps("W.O.W."), "Wow")

    def test_normalize_caps_preserves_acronyms(self):
        # Legitimate domain acronyms like BHK, RERA, GST, PIN, OTP should be preserved in uppercase
        self.assertEqual(normalize_caps("3 BHK in Pune with RERA registration"), "3 BHK in Pune with RERA registration")
        self.assertEqual(normalize_caps("Pay via GST and OTP"), "Pay via GST and OTP")

    def test_optimize_for_tts_wow_and_bhk(self):
        formatted = optimize_for_tts("WOW! This is a 2BHK flat in Hinjewadi.")
        # "WOW" -> "Wow", "2BHK" -> "two B H K"
        self.assertIn("Wow!", formatted)
        self.assertIn("B H K", formatted)
        self.assertNotIn("W O W", formatted)

    def test_optimize_for_tts_eliminates_stacked_punctuation(self):
        # Stacked exclamation marks and question marks cause pitch spikes; they should be flattened
        formatted = optimize_for_tts("REALLY?? That is AMAZING!!!")
        self.assertIn("Really?", formatted)
        self.assertIn("amazing!", formatted)
        self.assertNotIn("???", formatted)
        self.assertNotIn("!!!", formatted)

    def test_normalize_fillers_preserves_and_formats_human_fillers(self):
        # Fillers should NOT be deleted, but standardized with a clean trailing comma for smooth TTS synthesis
        formatted = optimize_for_tts("Umm... okay, so for a 2BHK, honestly, we have great options.")
        self.assertIn("Um,", formatted)
        self.assertIn("honestly,", formatted)
        self.assertNotIn("Umm...", formatted)

    def test_inject_breath_cues_uses_gentle_commas(self):
        # Heavy ellipsis pause injections (...) should be replaced with soft commas (,)
        cues = inject_breath_cues("So I was looking at that property and it seems great")
        self.assertNotIn("So...", cues)
        self.assertIn("So,", cues)


if __name__ == "__main__":
    unittest.main()
