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


class TenantSecurityAuditReadinessTest(unittest.TestCase):
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

    def _seed_tenant_owned_evidence(self):
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Tenant Campaign",
            "status": "Done",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_leads("campaign-1", [
            {"name": "Asha", "phone": "+10000000001"},
        ]))
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Asha",
            "phone": "+10000000001",
            "calledAt": "2026-05-20T10:00:00",
            "duration": "20s",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "assistant", "content": "Hello"}],
            "provider": "demo",
            "processed": True,
            "recording_url": "/recordings/campaign-1.wav",
        }))
        asyncio.run(self.manager.update_live_state(
            "campaign-1_+10000000001",
            "campaign-1",
            "Asha",
            "Completed",
            "Call ended",
            [{"role": "assistant", "content": "Hello"}],
            "demo",
        ))
        number = asyncio.run(self.manager.add_phone_number({
            "phone": "+15550000001",
            "provider": "twilio",
            "client_id": "client-1",
            "assigned_at": "2026-05-20T10:00:00",
        }))
        asyncio.run(self.manager.upsert_phone_number_route(
            number_id=number["id"],
            client_id="client-1",
            agent_id="agent-1",
            campaign_id="campaign-1",
            routing_mode="tenant_default",
            metadata={"source": "tenant_security_audit_test"},
        ))
        collection = asyncio.run(self.manager.create_agent_memory_collection(
            client_id="client-1",
            agent_id="agent-1",
            source_type="script",
            source_id="campaign-1",
        ))
        asyncio.run(self.manager.add_agent_memory_item(
            collection_id=collection["id"],
            client_id="client-1",
            agent_id="agent-1",
            content="Tenant-only sales guidance.",
        ))
        job = asyncio.run(self.manager.create_scrape_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com",
            domain="example.com",
            requested_by="admin@example.com",
        ))
        asyncio.run(self.manager.create_generated_script_draft(
            job_id=job["id"],
            client_id="client-1",
            agent_id="agent-1",
            status="draft",
            draft_json={"nodes": []},
            knowledge_json={"facts": []},
        ))

    def test_tenant_security_audit_is_ready_with_clean_tenant_evidence(self):
        import main

        self._seed_tenant_owned_evidence()
        env = _enabled_env(
            "auth.enforce_backend",
            "tenant.scoped_reads",
            "ws.scoped_events",
            "telephony.tenant_numbers",
            "tenant.security_leak_audit_readiness",
        )
        with patch.dict("os.environ", env, clear=False):
            readiness = asyncio.run(main._build_tenant_security_audit_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["db_write_performed"])
        self.assertFalse(readiness["resource_payload_returned"])
        self.assertFalse(readiness["tenant_data_returned"])
        self.assertFalse(readiness["cross_tenant_data_returned"])
        self.assertEqual(readiness["summary"]["missing_owner_total"], 0)
        self.assertEqual(readiness["summary"]["relationship_mismatch_total"], 0)

    def test_tenant_security_audit_blocks_cross_tenant_relationships(self):
        import main

        self._seed_tenant_owned_evidence()
        asyncio.run(self.manager.create_client("client-2", {
            "name": "Client Two",
            "email": "client2@example.com",
            "plan": "assigned",
        }))
        conn = db_manager._get_connection()
        try:
            conn.execute("UPDATE leads SET client_id=? WHERE campaign_id=?", ("client-2", "campaign-1"))
            conn.commit()
        finally:
            conn.close()

        env = _enabled_env(
            "auth.enforce_backend",
            "tenant.scoped_reads",
            "ws.scoped_events",
            "telephony.tenant_numbers",
            "tenant.security_leak_audit_readiness",
        )
        with patch.dict("os.environ", env, clear=False):
            readiness = asyncio.run(main._build_tenant_security_audit_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "not_ready")
        self.assertIn("relationship_scope_consistent", readiness["blockers"])
        self.assertGreater(readiness["summary"]["relationship_mismatch_total"], 0)
        self.assertFalse(readiness["db_write_performed"])
        self.assertFalse(readiness["resource_payload_returned"])

    def test_tenant_security_audit_endpoint_and_ui_are_flagged(self):
        main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        monitor_source = (
            FRONTEND_ROOT / "src" / "app" / "monitor" / "page.js"
        ).read_text(encoding="utf-8")

        self.assertIn("/api/tenant/security-leak-audit/readiness", main_source)
        self.assertIn('feature_flags.is_enabled("tenant.security_leak_audit_readiness")', main_source)
        self.assertIn("_build_tenant_security_audit_readiness", main_source)
        self.assertIn("tenant security leak audit requires admin context", main_source)
        self.assertIn('"db_write_performed": False', main_source)
        self.assertIn('"resource_payload_returned": False', main_source)
        self.assertIn('"tenant_data_returned": False', main_source)
        self.assertIn("NEXT_PUBLIC_TENANT_SECURITY_AUDIT_ENABLED", monitor_source)
        self.assertIn("Tenant Security Audit", monitor_source)
        self.assertIn("No DB writes", monitor_source)
        self.assertIn("No payloads returned", monitor_source)


if __name__ == "__main__":
    unittest.main()
