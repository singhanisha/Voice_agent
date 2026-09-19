import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tts import provider
from tts import tts_sarvam


class SarvamProviderTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._load_provider = provider._load_provider
        self._schema_cache = provider._AGENT_CONFIG_CACHE.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        provider._load_provider = self._load_provider
        provider._AGENT_CONFIG_CACHE = self._schema_cache

    def test_provider_normalizes_sarvam(self):
        self.assertEqual(provider._normalize_provider("sarvam"), "sarvam")
        self.assertEqual(provider._normalize_provider("parler"), "indic_parler")
        self.assertEqual(provider._normalize_provider("indic"), "indic_parler")

    @patch("requests.post")
    def test_sarvam_api_streaming(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        # Valid base64 payload
        mock_response.json.return_value = {
            "audios": ["SGVsbG8gV29ybGQ="]
        }
        mock_post.return_value = mock_response

        os.environ["SARVAM_API_KEY"] = "test-key"

        chunks = list(tts_sarvam.generate_speech_stream("namaste world", "hi"))
        self.assertTrue(len(chunks) > 0)
        self.assertEqual(b"".join(chunks), b"Hello World")

    def test_language_resolution(self):
        self.assertEqual(tts_sarvam._resolve_language_code("hindi"), "hi-IN")
        self.assertEqual(tts_sarvam._resolve_language_code("english"), "en-IN")
        self.assertEqual(tts_sarvam._resolve_language_code("marathi"), "mr-IN")


if __name__ == "__main__":
    unittest.main()
