import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("GROQ_API_KEY", "runtime-guard-test-key")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend-next"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class DemoRuntimeQATest(unittest.TestCase):
    def test_demo_runtime_qa_readiness_is_dry_run_only(self):
        import main

        snapshot = main._build_demo_call_qa_readiness()

        self.assertEqual(snapshot["status"], "ready")
        self.assertTrue(snapshot["ready_for_production_push"])
        self.assertEqual(snapshot["mode"], "read_only_dry_run")
        self.assertEqual(snapshot["blockers"], [])
        self.assertFalse(snapshot["runtime_live_changed"])
        self.assertFalse(snapshot["audio_contract_changed"])
        self.assertFalse(snapshot["websocket_contract_changed"])
        self.assertFalse(snapshot["recording_assets_changed"])

        criteria = {item["key"]: item for item in snapshot["criteria"]}
        self.assertTrue(criteria["availability_loop_guard"]["passed"])
        self.assertTrue(criteria["location_intent_guard"]["passed"])
        self.assertTrue(criteria["location_fallback_guard"]["passed"])
        self.assertTrue(criteria["unclear_offer_fallback_guard"]["passed"])
        self.assertTrue(criteria["recording_playback_quality_guard"]["passed"])

    def test_demo_runtime_qa_endpoint_and_monitor_ui_are_rollout_gated(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        monitor_source = (
            FRONTEND_ROOT / "src" / "app" / "monitor" / "page.js"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/demo/qa/readiness", main_source)
        self.assertIn('feature_flags.is_enabled("demo.runtime_qa_readiness")', main_source)
        self.assertIn("_require_global_monitor_admin", main_source)
        self.assertIn("_build_demo_call_qa_readiness", main_source)
        self.assertIn("NEXT_PUBLIC_DEMO_RUNTIME_QA_READINESS_ENABLED", monitor_source)
        self.assertIn("/api/demo/qa/readiness", monitor_source)
        self.assertIn("Demo Call QA", monitor_source)
        self.assertIn("Audio contract unchanged", monitor_source)
        self.assertIn("Websocket contract unchanged", monitor_source)


if __name__ == "__main__":
    unittest.main()
