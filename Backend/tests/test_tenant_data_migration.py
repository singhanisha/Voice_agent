import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager


class TenantDataMigrationTest(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_manager.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        db_manager.DB_PATH = Path(self._tmp.name) / "platform.db"
        db_manager._init_schema()
        self.manager = db_manager.DatabaseManager()

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_schema_adds_nullable_tenant_columns_and_sidecar_tables(self):
        conn = sqlite3.connect(str(db_manager.DB_PATH))
        try:
            for table in ("leads", "call_results", "live_call_state"):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                self.assertIn("client_id", columns)

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("recording_assets", tables)
            self.assertIn("tenant_audit_events", tables)
        finally:
            conn.close()

    def test_tenant_scoped_resource_owner_lookup_reads_metadata_only(self):
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))

        owner = asyncio.run(
            self.manager.get_tenant_scoped_resource_owner("campaign", "campaign-1")
        )
        missing = asyncio.run(
            self.manager.get_tenant_scoped_resource_owner("campaign", "missing-campaign")
        )

        self.assertEqual(owner["resource_type"], "campaign")
        self.assertEqual(owner["resource_label"], "Campaign")
        self.assertTrue(owner["found"])
        self.assertEqual(owner["owner_client_id"], "client-1")
        self.assertTrue(owner["owner_tenant_present"])
        self.assertFalse(owner["resource_id_included"])
        self.assertFalse(owner["payload_included"])
        self.assertFalse(missing["found"])
        self.assertIsNone(missing["owner_client_id"])

        with self.assertRaises(ValueError):
            asyncio.run(
                self.manager.get_tenant_scoped_resource_owner("unsupported", "campaign-1")
            )

    def test_call_result_owner_lookup_for_transcript_reads_metadata_only(self):
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Lead One",
            "phone": "+10000000001",
            "transcription": [{"role": "assistant", "content": "private transcript"}],
            "recording_url": "/recordings/private.wav",
            "processed": True,
        }))

        owner = asyncio.run(
            self.manager.get_call_result_owner_for_transcript("campaign-1_+10000000001")
        )
        missing = asyncio.run(
            self.manager.get_call_result_owner_for_transcript("missing-call-result")
        )

        self.assertEqual(owner["resource_type"], "call_result")
        self.assertTrue(owner["found"])
        self.assertEqual(owner["owner_client_id"], "client-1")
        self.assertTrue(owner["owner_tenant_present"])
        self.assertTrue(owner["campaign_id_present"])
        self.assertFalse(owner["resource_id_included"])
        self.assertFalse(owner["payload_included"])
        self.assertFalse(owner["transcript_content_included"])
        self.assertFalse(owner["recording_url_included"])
        self.assertFalse(missing["found"])
        self.assertIsNone(missing["owner_client_id"])

    def test_recording_asset_owner_lookup_reads_metadata_only(self):
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Lead One",
            "phone": "+10000000001",
            "transcription": [{"role": "assistant", "content": "private transcript"}],
            "recording_url": "/recordings/private.wav",
            "processed": True,
        }))

        owner = asyncio.run(
            self.manager.get_recording_asset_owner("/recordings/private.wav")
        )
        missing = asyncio.run(
            self.manager.get_recording_asset_owner("/recordings/missing.wav")
        )

        self.assertEqual(owner["resource_type"], "recording_asset")
        self.assertTrue(owner["found"])
        self.assertEqual(owner["owner_client_id"], "client-1")
        self.assertTrue(owner["owner_tenant_present"])
        self.assertTrue(owner["campaign_id_present"])
        self.assertFalse(owner["resource_id_included"])
        self.assertFalse(owner["payload_included"])
        self.assertFalse(owner["recording_url_included"])
        self.assertFalse(owner["storage_path_included"])
        self.assertFalse(owner["recording_bytes_included"])
        self.assertNotIn("private.wav", str(owner))
        self.assertFalse(missing["found"])
        self.assertIsNone(missing["owner_client_id"])
        self.assertFalse(missing["recording_url_included"])

    def test_dual_writes_client_id_to_leads_results_live_state_and_recordings(self):
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-1", [
            {"name": "Lead One", "phone": "+10000000001"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Lead One",
            "phone": "+10000000001",
            "calledAt": "2026-05-14 21:00:00",
            "duration": "10s",
            "status": "Connected",
            "interested": "Yes",
            "budget": "50 lakh",
            "callback": "Sunday",
            "transcription": [{"role": "assistant", "content": "hello"}],
            "provider": "demo",
            "processed": True,
            "recording_url": "/recordings/rec.wav",
        }))
        asyncio.run(self.manager.update_live_state(
            "campaign-1_+10000000001",
            "campaign-1",
            "Lead One",
            "Completed",
            "Call ended",
            [{"role": "assistant", "content": "hello"}],
            "demo",
        ))

        leads = asyncio.run(self.manager.get_leads_for_campaign("campaign-1"))
        results = asyncio.run(self.manager.get_results_for_campaign("campaign-1", client_id="client-1"))
        wrong_results = asyncio.run(self.manager.get_results_for_campaign("campaign-1", client_id="client-2"))
        transcript = asyncio.run(self.manager.get_transcript_for_lead(
            "campaign-1_+10000000001",
            client_id="client-1",
        ))
        wrong_transcript = asyncio.run(self.manager.get_transcript_for_lead(
            "campaign-1_+10000000001",
            client_id="client-2",
        ))
        live_state = asyncio.run(self.manager.get_live_state("campaign-1", client_id="client-1"))
        wrong_live_state = asyncio.run(self.manager.get_live_state("campaign-1", client_id="client-2"))

        self.assertEqual(leads[0]["client_id"], "client-1")
        self.assertEqual(results[0]["client_id"], "client-1")
        self.assertEqual(wrong_results, [])
        self.assertEqual(transcript, [{"role": "assistant", "content": "hello"}])
        self.assertEqual(wrong_transcript, [])
        self.assertEqual(live_state[0]["client_id"], "client-1")
        self.assertEqual(wrong_live_state, [])

        conn = sqlite3.connect(str(db_manager.DB_PATH))
        try:
            row = conn.execute("SELECT client_id, recording_url FROM recording_assets").fetchone()
            self.assertEqual(row[0], "client-1")
            self.assertEqual(row[1], "/recordings/rec.wav")
        finally:
            conn.close()

    def test_backfill_populates_null_client_ids_from_campaigns(self):
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Campaign One",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-1", [
            {"name": "Lead One", "phone": "+10000000001"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Lead One",
            "phone": "+10000000001",
            "transcription": [],
            "processed": True,
        }))
        asyncio.run(self.manager.update_live_state("lead-1", "campaign-1", "Lead One", "Talking"))

        conn = sqlite3.connect(str(db_manager.DB_PATH))
        try:
            conn.execute("UPDATE leads SET client_id=NULL")
            conn.execute("UPDATE call_results SET client_id=NULL")
            conn.execute("UPDATE live_call_state SET client_id=NULL")
            conn.commit()
        finally:
            conn.close()

        db_manager._init_schema()

        leads = asyncio.run(self.manager.get_leads_for_campaign("campaign-1"))
        results = asyncio.run(self.manager.get_results_for_campaign("campaign-1"))
        live_state = asyncio.run(self.manager.get_live_state("campaign-1"))

        self.assertEqual(leads[0]["client_id"], "client-1")
        self.assertEqual(results[0]["client_id"], "client-1")
        self.assertEqual(live_state[0]["client_id"], "client-1")


if __name__ == "__main__":
    unittest.main()
