import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend-next"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager
from platform_migration import feature_flags


def _enabled_env(*flags: str) -> dict[str, str]:
    return {feature_flags.env_name(flag): "true" for flag in flags}


class FinalCanaryRollbackReadinessTest(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_manager.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        db_manager.DB_PATH = Path(self._tmp.name) / "platform.db"
        db_manager._init_schema()
        self.manager = db_manager.DatabaseManager()
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.create_agent("agent-1", {
            "name": "Rani",
            "voice": "voice",
            "language": "English",
            "max_duration": 300,
            "provider": "demo",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "cartesia_voice_id": None,
            "assigned_email": "client1@example.com",
            "agent_type": "real_estate_sales",
            "script": "Qualify callers safely.",
            "data_fields": ["interested"],
            "schema_path": "db/agents/agent-1.json",
            "client_id": "client-1",
        }))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def _seed_campaign_evidence(self):
        asyncio.run(self.manager.upsert_campaign("campaign-final", {
            "name": "Final Campaign",
            "status": "Done",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-final", [
            {"name": "Asha", "phone": "+10000000001"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-final", {
            "name": "Asha",
            "phone": "+10000000001",
            "calledAt": "2026-05-20T10:00:00",
            "duration": "20s",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "assistant", "content": "Hello"}],
            "provider": "demo",
            "processed": True,
            "recording_url": "/recordings/campaign-final.wav",
        }))
        asyncio.run(self.manager.update_live_state(
            "campaign-final_+10000000001",
            "campaign-final",
            "Asha",
            "Completed",
            "Call ended",
            [{"role": "assistant", "content": "Hello"}],
            "demo",
        ))

    def _go_live_env(self) -> dict[str, str]:
        return _enabled_env(
            "auth.enforce_backend",
            "tenant.scoped_reads",
            "ws.scoped_events",
            "telephony.tenant_numbers",
            "tenant.security_leak_audit_readiness",
            "tenant.final_canary_rollback_readiness",
        )

    def test_final_canary_rollback_gate_ready_with_clean_evidence(self):
        import main

        self._seed_campaign_evidence()
        with patch.dict("os.environ", self._go_live_env(), clear=False):
            readiness = asyncio.run(main._build_final_canary_rollback_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_manual_canary"])
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["canary_started"])
        self.assertFalse(readiness["traffic_shifted"])
        self.assertFalse(readiness["production_activation_started"])
        self.assertFalse(readiness["rollback_action_performed"])
        self.assertFalse(readiness["feature_flags_modified"])
        self.assertFalse(readiness["audio_runtime_changed"])
        self.assertFalse(readiness["websocket_contract_changed"])
        self.assertFalse(readiness["campaign_runtime_changed"])
        self.assertFalse(readiness["db_write_performed"])
        self.assertFalse(readiness["resource_payload_returned"])

    def test_final_canary_rollback_gate_blocks_missing_campaign_evidence(self):
        import main

        with patch.dict("os.environ", self._go_live_env(), clear=False):
            readiness = asyncio.run(main._build_final_canary_rollback_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "not_ready")
        self.assertIn("campaign_e2e_ready", readiness["blockers"])
        self.assertFalse(readiness["canary_started"])
        self.assertFalse(readiness["traffic_shifted"])
        self.assertFalse(readiness["feature_flags_modified"])

    def test_final_canary_rollback_endpoint_and_ui_are_flagged(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        monitor_source = (
            FRONTEND_ROOT / "src" / "app" / "monitor" / "page.js"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/tenant/final-canary-rollback/readiness", main_source)
        self.assertIn('feature_flags.is_enabled("tenant.final_canary_rollback_readiness")', main_source)
        self.assertIn("_build_final_canary_rollback_readiness", main_source)
        self.assertIn("tenant final canary rollback readiness requires admin context", main_source)
        self.assertIn('"canary_started": False', main_source)
        self.assertIn('"traffic_shifted": False', main_source)
        self.assertIn('"production_activation_started": False', main_source)
        self.assertIn('"rollback_action_performed": False', main_source)
        self.assertIn('"feature_flags_modified": False', main_source)
        self.assertIn("NEXT_PUBLIC_FINAL_CANARY_ROLLBACK_ENABLED", monitor_source)
        self.assertIn("Final Canary Gate", monitor_source)
        self.assertIn("No canary started", monitor_source)
        self.assertIn("No traffic shifted", monitor_source)
        self.assertIn("No rollback executed", monitor_source)
        self.assertIn("No flag changes", monitor_source)


if __name__ == "__main__":
    unittest.main()
