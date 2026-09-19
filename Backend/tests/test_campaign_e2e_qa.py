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


class CampaignE2EQATest(unittest.TestCase):
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
        asyncio.run(self.manager.upsert_campaign("campaign-ready", {
            "name": "Ready Campaign",
            "status": "Pending",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-ready", [
            {"name": "Asha", "phone": "+10000000001"},
            {"name": "Dev", "phone": "+10000000002"},
        ]))
        asyncio.run(self.manager.upsert_campaign("campaign-done", {
            "name": "Done Campaign",
            "status": "Done",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-done", [
            {"name": "Riya", "phone": "+10000000003"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-done", {
            "name": "Riya",
            "phone": "+10000000003",
            "calledAt": "2026-05-20T10:00:00",
            "duration": "20s",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "assistant", "content": "Hello"}],
            "provider": "demo",
            "processed": True,
            "recording_url": "/recordings/campaign-done.wav",
        }))
        asyncio.run(self.manager.update_live_state(
            "campaign-done_+10000000003",
            "campaign-done",
            "Riya",
            "Completed",
            "Call ended",
            [{"role": "assistant", "content": "Hello"}],
            "demo",
        ))

    def test_campaign_e2e_readiness_is_read_only_and_ready_with_evidence(self):
        import main

        self._seed_campaign_evidence()
        with patch.dict("os.environ", {"FEATURE_CAMPAIGN_WORKER_V2": "false"}, clear=False):
            readiness = asyncio.run(main._build_campaign_e2e_qa_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["campaigns_started"])
        self.assertFalse(readiness["outbound_calls_started"])
        self.assertFalse(readiness["results_written"])
        self.assertFalse(readiness["live_state_written"])
        self.assertFalse(readiness["queue_dispatch_started"])

        criteria = {item["key"]: item for item in readiness["criteria"]}
        self.assertTrue(criteria["lead_ingestion_contract"]["passed"])
        self.assertTrue(criteria["launch_prerequisites_present"]["passed"])
        self.assertTrue(criteria["result_persistence_evidence"]["passed"])
        self.assertTrue(criteria["transcript_persistence_evidence"]["passed"])
        self.assertTrue(criteria["recording_persistence_evidence"]["passed"])

    def test_campaign_e2e_readiness_reports_missing_evidence_as_blockers(self):
        import main

        readiness = asyncio.run(main._build_campaign_e2e_qa_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "not_ready")
        self.assertIn("launch_prerequisites_present", readiness["blockers"])
        self.assertIn("result_persistence_evidence", readiness["blockers"])
        self.assertIn("transcript_persistence_evidence", readiness["blockers"])
        self.assertIn("recording_persistence_evidence", readiness["blockers"])
        self.assertFalse(readiness["campaigns_started"])
        self.assertFalse(readiness["results_written"])

    def test_campaign_e2e_endpoint_and_ui_are_flagged(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        campaign_source = (
            FRONTEND_ROOT / "src" / "app" / "campaigns" / "page.js"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/campaigns/e2e-qa/readiness", main_source)
        self.assertIn('feature_flags.is_enabled("campaign.e2e_qa_readiness")', main_source)
        self.assertIn("_build_campaign_e2e_qa_readiness", main_source)
        self.assertIn("NEXT_PUBLIC_CAMPAIGN_E2E_QA_READINESS_ENABLED", campaign_source)
        self.assertIn("Campaign E2E QA", campaign_source)
        self.assertIn("No campaigns started", campaign_source)
        self.assertIn("No calls started", campaign_source)
        self.assertIn("No results written", campaign_source)


if __name__ == "__main__":
    unittest.main()
