import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager


class CampaignLifecycleTest(unittest.TestCase):
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
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Done",
            "agent_id": None,
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-1", [
            {"name": "Riya", "phone": "+10000000001"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Riya",
            "phone": "+10000000001",
            "calledAt": "2026-05-14T10:00:00",
            "duration": "20s",
            "status": "Connected",
            "transcription": [{"role": "assistant", "content": "Hello"}],
            "provider": "demo",
            "recording_url": "/recordings/test.wav",
        }))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_archive_hides_campaign_without_removing_results(self):
        archived = asyncio.run(self.manager.set_campaign_archived(
            "campaign-1",
            archived=True,
            actor_email="admin@example.com",
        ))

        visible = asyncio.run(self.manager.list_campaigns())
        with_archived = asyncio.run(self.manager.list_campaigns_with_lifecycle(include_archived=True))
        results = asyncio.run(self.manager.get_results_for_campaign("campaign-1"))

        self.assertIsNotNone(archived["archived_at"])
        self.assertEqual(visible, [])
        self.assertEqual(len(with_archived), 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["has_transcript"])
        self.assertTrue(results[0]["has_recording"])

    def test_soft_delete_creates_manifest_and_preserves_child_data(self):
        deleted = asyncio.run(self.manager.soft_delete_campaign(
            "campaign-1",
            reason="cleanup old campaign",
            actor_email="admin@example.com",
        ))
        summary = asyncio.run(self.manager.get_campaign_lifecycle_summary("campaign-1"))
        visible = asyncio.run(self.manager.list_campaigns())
        with_deleted = asyncio.run(self.manager.list_campaigns_with_lifecycle(include_archived=True, include_deleted=True))
        transcript = asyncio.run(self.manager.get_transcript_for_lead("campaign-1_+10000000001"))

        self.assertIsNotNone(deleted["campaign"]["deleted_at"])
        self.assertEqual(deleted["cleanup_manifest"]["status"], "planned")
        self.assertFalse(deleted["cleanup_manifest"]["retention"]["physical_delete"])
        self.assertEqual(deleted["cleanup_manifest"]["counts"]["leads"], 1)
        self.assertEqual(deleted["cleanup_manifest"]["counts"]["call_results"], 1)
        self.assertEqual(deleted["cleanup_manifest"]["counts"]["recording_assets"], 1)
        self.assertEqual(visible, [])
        self.assertEqual(len(with_deleted), 1)
        self.assertEqual(summary["related_counts"]["call_results"], 1)
        self.assertEqual(summary["audit_events"][0]["action"], "campaign.soft_deleted")
        self.assertEqual(transcript[0]["content"], "Hello")

    def test_restore_clears_archive_and_delete_markers(self):
        asyncio.run(self.manager.soft_delete_campaign("campaign-1", reason="test"))
        restored = asyncio.run(self.manager.restore_campaign_lifecycle(
            "campaign-1",
            actor_email="admin@example.com",
        ))
        visible = asyncio.run(self.manager.list_campaigns())

        self.assertIsNone(restored["archived_at"])
        self.assertIsNone(restored["deleted_at"])
        self.assertIsNone(restored["delete_reason"])
        self.assertEqual(len(visible), 1)

    def test_launch_campaign_metadata_keeps_leads_tenant_scoped(self):
        asyncio.run(self.manager.upsert_campaign("campaign-2", {
            "name": "May Launch",
            "status": "Pending",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-2", [
            {"name": "Asha", "phone": "+10000000002"},
            {"name": "Dev", "phone": "+10000000003"},
        ]))

        campaign = asyncio.run(self.manager.get_campaign("campaign-2"))
        leads = asyncio.run(self.manager.get_leads_for_campaign("campaign-2"))
        campaigns = asyncio.run(self.manager.list_campaigns_with_lifecycle(client_id="client-1"))
        campaign_row = next(row for row in campaigns if row["id"] == "campaign-2")

        self.assertEqual(campaign["name"], "May Launch")
        self.assertEqual(campaign["client_id"], "client-1")
        self.assertEqual(campaign["agent_id"], "agent-1")
        self.assertEqual([lead["client_id"] for lead in leads], ["client-1", "client-1"])
        self.assertEqual(campaign_row["lead_count"], 2)
        self.assertEqual(campaign_row["result_count"], 0)

    def test_main_campaign_launch_accepts_name_client_and_agent_metadata(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        upload_source = source.split("async def upload_leads", 1)[1].split("@app.get(\"/api/campaigns\")", 1)[0]

        self.assertIn("campaignName", source)
        self.assertIn("clientId", source)
        self.assertIn("agentId", source)
        self.assertIn("telephonyProvider", source)
        self.assertIn("_normalize_campaign_leads", source)
        self.assertIn("MAX_CAMPAIGN_LEADS", source)
        self.assertLess(upload_source.index("db.upsert_campaign"), upload_source.index("db.upsert_leads"))

    def test_main_lifecycle_surface_is_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("campaign.lifecycle_management")', source)
        self.assertIn("/api/campaigns/{campaign_id}/archive", source)
        self.assertIn("/api/campaigns/{campaign_id}/lifecycle", source)

    def test_results_and_transcripts_accept_optional_client_scope(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("async def get_results(campaign_id: str, request: Request, clientId: Optional[str] = None)", source)
        self.assertIn("return await db.get_results_for_campaign(campaign_id, client_id)", source)
        self.assertIn("async def get_transcript(lead_id: str, request: Request, clientId: Optional[str] = None)", source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)

    def test_live_state_accepts_optional_client_scope(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertLess(
            source.index('@app.get("/api/campaigns/all/live")'),
            source.index('@app.get("/api/campaigns/{campaign_id}/live")'),
        )
        self.assertIn("async def get_all_live_state(request: Request, clientId: Optional[str] = None)", source)
        self.assertIn("return await db.get_all_live_state(client_id)", source)
        self.assertIn("async def get_live_state(campaign_id: str, request: Request, clientId: Optional[str] = None)", source)
        self.assertIn("return await db.get_live_state(campaign_id, client_id)", source)

    def test_protected_recording_route_is_additive_and_tenant_scoped(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('app.mount("/recordings"', source)
        self.assertIn('@app.get("/api/recordings/protected")', source)
        self.assertIn("async def get_protected_recording(request: Request, recordingUrl: str, clientId: Optional[str] = None)", source)
        self.assertIn("owner = await db.get_recording_asset_owner(recordingUrl)", source)
        self.assertIn("Recording is outside tenant scope", source)
        self.assertIn("return FileResponse(", source)


if __name__ == "__main__":
    unittest.main()
