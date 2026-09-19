import asyncio
import tempfile
import unittest
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from campaigns.worker_v2 import CampaignWorkerV2Config, CampaignWorkerV2ControlPlane
from db import db_manager


class CampaignWorkerV2Test(unittest.TestCase):
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
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "agent_id": None,
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-1", [
            {"name": "Lead One", "phone": "+10000000001"},
            {"name": "Lead Two", "phone": "+10000000002"},
        ]))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_prepare_execution_creates_idempotent_shadow_plan(self):
        control = CampaignWorkerV2ControlPlane(self.manager)
        config = CampaignWorkerV2Config(mode="shadow", max_concurrency=2, max_attempts=3)

        first = asyncio.run(control.prepare_execution(
            campaign_id="campaign-1",
            agent_id="agent-1",
            telephony_provider="demo",
            client_id="client-1",
            config=config,
        ))
        second = asyncio.run(control.prepare_execution(
            campaign_id="campaign-1",
            agent_id="agent-1",
            telephony_provider="demo",
            client_id="client-1",
            config=config,
        ))
        attempts = asyncio.run(self.manager.list_campaign_lead_attempts(first["id"]))

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "planned")
        self.assertEqual(first["mode"], "shadow")
        self.assertEqual(first["max_concurrency"], 2)
        self.assertEqual(first["max_attempts"], 3)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(attempt["status"] == "queued" for attempt in attempts))
        self.assertTrue(all(attempt["client_id"] == "client-1" for attempt in attempts))

    def test_pause_resume_cancel_update_execution_metadata_only(self):
        control = CampaignWorkerV2ControlPlane(self.manager)
        execution = asyncio.run(control.prepare_execution(
            campaign_id="campaign-1",
            agent_id="agent-1",
            telephony_provider="demo",
            client_id="client-1",
        ))

        paused = asyncio.run(control.pause(execution["id"], reason="test"))
        resumed = asyncio.run(control.resume(execution["id"], reason="test"))
        cancelled = asyncio.run(control.cancel(execution["id"], reason="test"))

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(resumed["status"], "planned")
        self.assertEqual(cancelled["status"], "cancelled")

        executions = asyncio.run(self.manager.list_campaign_executions(campaign_id="campaign-1"))
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]["status"], "cancelled")

    def test_main_start_campaign_keeps_v1_runner_with_v2_shadow_hook(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("campaign.worker_v2")', main_source)
        self.assertIn("prepare_execution", main_source)
        self.assertIn("background_tasks.add_task(engine.run_demo_campaign", main_source)
        self.assertIn("background_tasks.add_task(run_campaign", main_source)


if __name__ == "__main__":
    unittest.main()

