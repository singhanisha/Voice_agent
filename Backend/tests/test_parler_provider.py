import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tts import provider
from tts import tts_indic_parler as tts_parler


class ParlerProviderTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._load_provider = provider._load_provider
        self._schema_cache = provider._AGENT_CONFIG_CACHE.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        provider._load_provider = self._load_provider
        provider._AGENT_CONFIG_CACHE = self._schema_cache

    def test_provider_normalizes_parler(self):
        self.assertEqual(provider._normalize_provider("parler"), "indic_parler")

    @patch("urllib.request.urlopen")
    def test_agent_schema_can_select_parler(self, mock_urlopen):
        # Mock API response for agents schema
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "provider_config": {
                "tts_provider": "parler",
                "parler_description": "A customized calm voice"
            }
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Clear cache and fetch
        provider._AGENT_CONFIG_CACHE.clear()
        self.assertEqual(provider._configured_provider("tts-test-agent"), "indic_parler")
        
        config = provider._provider_config_from_agent_schema("tts-test-agent")
        self.assertEqual(config.get("tts_provider"), "indic_parler")
        self.assertEqual(config.get("indic_parler_voice_description"), "A customized calm voice")

    @patch("httpx.stream")
    def test_parler_api_streaming(self, mock_stream):
        # Setup mock for httpx.stream context manager
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_bytes.return_value = [b"chunk1", b"chunk2"]
        mock_stream.return_value.__enter__.return_value = mock_response

        os.environ["PARLER_API_URL"] = "http://localhost:8001/tts"
        os.environ["PARLER_LOCAL"] = "false"

        chunks = list(tts_parler.generate_speech_stream("hello test", "en"))
        # Filter out empty chunks/fades
        nonempty_chunks = [c for c in chunks if c]
        
        # Verify we got our mocked chunks (though they might have fade processing applied)
        self.assertTrue(len(nonempty_chunks) >= 2)

    def test_parler_description_resolution(self):
        # Default description for Hindi
        desc = tts_parler._description_for("hi")
        self.assertIn("Hindi", desc)

        # Custom description overrides defaults
        desc = tts_parler._description_for("hi", "A cheerful male voice")
        self.assertEqual(desc, "A cheerful male voice")


if __name__ == "__main__":
    unittest.main()
