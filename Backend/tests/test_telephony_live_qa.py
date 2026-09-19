import asyncio
import os
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


class TelephonyLiveQATest(unittest.TestCase):
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
            "agent_type": "finance",
            "script": "Qualify callers safely.",
            "data_fields": ["interested"],
            "schema_path": "db/agents/agent-1.json",
            "client_id": "client-1",
        }))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def _add_routed_number(self, provider: str = "demo"):
        number = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551230001",
            "sid": f"{provider.upper()}123",
            "region": "US",
            "provider": provider,
            "client_id": "client-1",
        }))
        asyncio.run(self.manager.upsert_phone_number_route(
            number_id=number["id"],
            client_id="client-1",
            agent_id="agent-1",
            metadata={"source": "telephony_live_qa_test"},
        ))
        return number

    def test_demo_provider_readiness_is_read_only_and_ready(self):
        import main

        self._add_routed_number("demo")
        with patch.dict(os.environ, {
            "FEATURE_TELEPHONY_TENANT_NUMBERS": "true",
            "WEBHOOK_BASE_URL": "https://voice.example.com",
        }, clear=False):
            readiness = asyncio.run(main._build_telephony_live_qa_readiness(
                provider_slug="demo",
                client_id="client-1",
                include_provider_probe=False,
            ))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["outbound_calls_started"])
        self.assertFalse(readiness["numbers_purchased"])
        self.assertFalse(readiness["tenant_routes_modified"])
        self.assertFalse(readiness["webhook_contract_changed"])

        criteria = {item["key"]: item for item in readiness["criteria"]}
        self.assertTrue(criteria["tenant_numbers_flag_enabled"]["passed"])
        self.assertTrue(criteria["tenant_routes_present"]["passed"])
        self.assertTrue(criteria["route_resolution_ok"]["passed"])
        self.assertTrue(criteria["route_scope_ok"]["passed"])

    def test_unconfigured_live_provider_reports_blockers_without_mutation(self):
        import main

        self._add_routed_number("twilio")
        with patch.dict(os.environ, {
            "FEATURE_TELEPHONY_TENANT_NUMBERS": "true",
            "WEBHOOK_BASE_URL": "https://voice.example.com",
            "TWILIO_ACCOUNT_SID": "",
            "TWILIO_AUTH_TOKEN": "",
        }, clear=False):
            readiness = asyncio.run(main._build_telephony_live_qa_readiness(
                provider_slug="twilio",
                client_id="client-1",
                include_provider_probe=False,
            ))

        self.assertEqual(readiness["status"], "not_ready")
        self.assertIn("provider_configured", readiness["blockers"])
        self.assertIn("provider_number_probe", readiness["blockers"])
        self.assertFalse(readiness["outbound_calls_started"])
        self.assertFalse(readiness["numbers_purchased"])
        self.assertFalse(readiness["tenant_routes_modified"])

    def test_endpoint_and_numbers_ui_are_flagged(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        numbers_source = (
            FRONTEND_ROOT / "src" / "app" / "numbers" / "page.js"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/telephony/live-qa/readiness", main_source)
        self.assertIn('feature_flags.is_enabled("telephony.live_qa_readiness")', main_source)
        self.assertIn("_build_telephony_live_qa_readiness", main_source)
        self.assertIn("NEXT_PUBLIC_TELEPHONY_LIVE_QA_READINESS_ENABLED", numbers_source)
        self.assertIn("Telephony Live QA", numbers_source)
        self.assertIn("Live provider probe", numbers_source)
        self.assertIn("No calls started", numbers_source)
        self.assertIn("No numbers purchased", numbers_source)


if __name__ == "__main__":
    unittest.main()
