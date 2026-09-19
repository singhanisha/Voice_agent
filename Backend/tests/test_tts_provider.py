import os
import sys
import json
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tts import provider
from tts import tts_cartesia


class TTSProviderTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ.pop("TTS_DISABLE_AGENT_OVERRIDES", None)
        self._load_provider = provider._load_provider
        self._schema_dir = provider._AGENT_SCHEMA_DIR

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        provider._load_provider = self._load_provider
        provider._AGENT_SCHEMA_DIR = self._schema_dir
        provider._AGENT_CONFIG_CACHE.clear()

    def test_default_provider_is_edge(self):
        os.environ.pop("TTS_PROVIDER", None)
        os.environ.pop("CARTESIA_AGENT_IDS", None)
        self.assertEqual(provider._configured_provider(), "edge")

    def test_agent_allowlist_can_select_cartesia(self):
        os.environ["TTS_PROVIDER"] = "edge"
        os.environ["CARTESIA_AGENT_IDS"] = "demo-agent,enterprise-agent"
        self.assertEqual(provider._configured_provider("enterprise-agent"), "cartesia")
        self.assertEqual(provider._configured_provider("other-agent"), "edge")

    def test_agent_schema_can_select_cartesia(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider._AGENT_SCHEMA_DIR = Path(tmpdir)
            provider._AGENT_CONFIG_CACHE.clear()
            (Path(tmpdir) / "enterprise-agent.json").write_text(
                json.dumps({
                    "provider_config": {
                        "tts_provider": "cartesia",
                        "cartesia_voice_id": "95d51f79-c397-46f9-b49a-23763d3eaa2d",
                    }
                }),
                encoding="utf-8",
            )
            os.environ["TTS_PROVIDER"] = "edge"

            self.assertEqual(provider._configured_provider("enterprise-agent"), "cartesia")
            self.assertEqual(
                provider._cartesia_voice_id_for_agent("enterprise-agent"),
                "95d51f79-c397-46f9-b49a-23763d3eaa2d",
            )

            os.environ["TTS_DISABLE_AGENT_OVERRIDES"] = "true"
            provider._AGENT_CONFIG_CACHE.clear()
            self.assertEqual(provider._configured_provider("enterprise-agent"), "edge")

    def test_shadow_mode_does_not_replace_primary_audio(self):
        def fake_load_provider(provider_name):
            def fake_stream(text, preferred_language=None, **kwargs):
                if provider_name == "edge":
                    yield b"primary"
                else:
                    yield b"shadow"

            return fake_stream

        provider._load_provider = fake_load_provider
        os.environ["TTS_PROVIDER"] = "edge"
        os.environ["TTS_SHADOW_MODE"] = "true"

        chunks = list(provider.generate_speech_stream("hello", "en"))

        self.assertEqual(chunks, [b"primary"])

    def test_cartesia_empty_audio_falls_back_to_edge(self):
        def fake_load_provider(provider_name):
            def fake_stream(text, preferred_language=None, **kwargs):
                if provider_name == "cartesia":
                    yield b""
                else:
                    yield b"fallback"

            return fake_stream

        provider._load_provider = fake_load_provider
        os.environ["TTS_PROVIDER"] = "cartesia"
        os.environ["TTS_FALLBACK_ENABLED"] = "true"

        chunks = [chunk for chunk in provider.generate_speech_stream("hello", "en") if chunk]

        self.assertEqual(chunks, [b"fallback"])

    def test_cartesia_missing_key_returns_no_audio_without_network(self):
        os.environ["CARTESIA_API_KEY"] = ""
        chunks = list(tts_cartesia.generate_speech_stream("hello", "en"))
        self.assertEqual([chunk for chunk in chunks if chunk], [])


if __name__ == "__main__":
    unittest.main()
