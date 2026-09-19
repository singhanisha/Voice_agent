import os
import sys
import json
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("GROQ_API_KEY", "phase-stt-provider-test-key")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from stt import provider
from stt import stt_deepgram


class STTProviderTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ.pop("STT_DISABLE_AGENT_OVERRIDES", None)
        self._run_provider = provider._run_provider
        self._schema_dir = provider._AGENT_SCHEMA_DIR

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        provider._run_provider = self._run_provider
        provider._AGENT_SCHEMA_DIR = self._schema_dir
        provider._AGENT_CONFIG_CACHE.clear()

    def test_default_provider_is_groq(self):
        os.environ.pop("STT_PROVIDER", None)
        os.environ.pop("DEEPGRAM_AGENT_IDS", None)
        self.assertEqual(provider._configured_provider(), "groq")

    def test_agent_allowlist_can_select_deepgram(self):
        os.environ["STT_PROVIDER"] = "groq"
        os.environ["DEEPGRAM_AGENT_IDS"] = "demo-agent,enterprise-agent"
        self.assertEqual(provider._configured_provider("enterprise-agent"), "deepgram")
        self.assertEqual(provider._configured_provider("other-agent"), "groq")

    def test_agent_schema_can_select_deepgram(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider._AGENT_SCHEMA_DIR = Path(tmpdir)
            provider._AGENT_CONFIG_CACHE.clear()
            (Path(tmpdir) / "enterprise-agent.json").write_text(
                json.dumps({"provider_config": {"stt_provider": "deepgram"}}),
                encoding="utf-8",
            )
            os.environ["STT_PROVIDER"] = "groq"

            self.assertEqual(provider._configured_provider("enterprise-agent"), "deepgram")

            os.environ["STT_DISABLE_AGENT_OVERRIDES"] = "true"
            provider._AGENT_CONFIG_CACHE.clear()
            self.assertEqual(provider._configured_provider("enterprise-agent"), "groq")

    def test_shadow_mode_returns_primary_text_only(self):
        calls = []

        def fake_run_provider(provider_name, audio_chunk):
            calls.append(provider_name)
            if provider_name == "groq":
                return "authoritative transcript", 0.010
            return "shadow transcript", 0.020

        provider._run_provider = fake_run_provider
        os.environ["STT_PROVIDER"] = "groq"
        os.environ["STT_SHADOW_MODE"] = "true"

        result = provider.transcribe_audio(b"\x01\x00" * 160)

        self.assertEqual(result, "authoritative transcript")
        self.assertEqual(calls, ["groq", "deepgram"])

    def test_primary_failure_falls_back_to_groq(self):
        calls = []

        def fake_run_provider(provider_name, audio_chunk):
            calls.append(provider_name)
            if provider_name == "deepgram":
                raise RuntimeError("simulated deepgram outage")
            return "fallback transcript", 0.010

        provider._run_provider = fake_run_provider
        os.environ["STT_PROVIDER"] = "deepgram"
        os.environ["STT_FALLBACK_ENABLED"] = "true"
        os.environ["STT_SHADOW_MODE"] = "false"

        result = provider.transcribe_audio(b"\x01\x00" * 160)

        self.assertEqual(result, "fallback transcript")
        self.assertEqual(calls, ["deepgram", "groq"])

    def test_empty_primary_transcript_falls_back_to_alternate_provider(self):
        calls = []

        def fake_run_provider(provider_name, audio_chunk):
            calls.append(provider_name)
            if provider_name == "groq":
                return "", 0.010
            return "deepgram transcript", 0.020

        provider._run_provider = fake_run_provider
        os.environ["STT_PROVIDER"] = "groq"
        os.environ["STT_FALLBACK_ENABLED"] = "true"
        os.environ["STT_FALLBACK_ON_EMPTY"] = "true"
        os.environ["STT_SHADOW_MODE"] = "false"
        os.environ["DEEPGRAM_API_KEY"] = "test-deepgram-key"

        result = provider.transcribe_audio(b"\x01\x00" * 160)

        self.assertEqual(result, "deepgram transcript")
        self.assertEqual(calls, ["groq", "deepgram"])

    def test_empty_primary_transcript_skips_unconfigured_fallback(self):
        calls = []

        def fake_run_provider(provider_name, audio_chunk):
            calls.append(provider_name)
            return "", 0.010

        provider._run_provider = fake_run_provider
        os.environ["STT_PROVIDER"] = "groq"
        os.environ["STT_FALLBACK_ENABLED"] = "true"
        os.environ["STT_FALLBACK_ON_EMPTY"] = "true"
        os.environ["STT_SHADOW_MODE"] = "false"
        os.environ["DEEPGRAM_API_KEY"] = ""

        result = provider.transcribe_audio(b"\x01\x00" * 160)

        self.assertEqual(result, "")
        self.assertEqual(calls, ["groq"])

    def test_deepgram_missing_key_raises_for_provider_fallback(self):
        os.environ["DEEPGRAM_API_KEY"] = ""
        with self.assertRaises(RuntimeError):
            stt_deepgram.transcribe_audio(b"\x00\x00" * 160)


if __name__ == "__main__":
    unittest.main()
