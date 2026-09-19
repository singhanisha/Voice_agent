import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager


class TelephonyTenantNumbersTest(unittest.TestCase):
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
        asyncio.run(self.manager.create_client("client-2", {
            "name": "Client Two",
            "email": "client2@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.create_agent("agent-1", {
            "name": "Rani",
            "voice": "voice",
            "language": "English",
            "max_duration": 300,
            "provider": "twilio",
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
        self.number = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551230001",
            "sid": "PN123",
            "region": "US",
            "provider": "twilio",
            "client_id": None,
        }))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_route_assignment_is_tenant_scoped_and_resolvable(self):
        route = asyncio.run(self.manager.upsert_phone_number_route(
            number_id=self.number["id"],
            client_id="client-1",
            agent_id="agent-1",
            metadata={"source": "test"},
        ))
        resolved = asyncio.run(self.manager.resolve_phone_number_route("+15551230001", "twilio"))
        number = asyncio.run(self.manager.get_phone_number(self.number["id"]))

        self.assertEqual(route["client_id"], "client-1")
        self.assertEqual(route["agent_id"], "agent-1")
        self.assertEqual(route["metadata"]["source"], "test")
        self.assertEqual(resolved["id"], route["id"])
        self.assertEqual(number["client_id"], "client-1")

    def test_route_assignment_rejects_cross_tenant_agent(self):
        asyncio.run(self.manager.create_agent("agent-2", {
            "name": "Arjun",
            "voice": "voice",
            "language": "English",
            "max_duration": 300,
            "provider": "twilio",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "cartesia_voice_id": None,
            "assigned_email": "client2@example.com",
            "agent_type": "finance",
            "script": "Qualify callers safely.",
            "data_fields": ["interested"],
            "schema_path": "db/agents/agent-2.json",
            "client_id": "client-2",
        }))

        with self.assertRaises(ValueError):
            asyncio.run(self.manager.upsert_phone_number_route(
                number_id=self.number["id"],
                client_id="client-1",
                agent_id="agent-2",
            ))

    def test_duplicate_number_keeps_single_owner_and_blocks_cross_tenant_reassignment(self):
        claimed = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551230001",
            "sid": "PN123-DUP",
            "region": "US",
            "provider": "twilio",
            "client_id": "client-1",
        }))
        duplicate = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551230001",
            "sid": "PN123-DUP2",
            "region": "US",
            "provider": "twilio",
            "client_id": "client-1",
        }))

        self.assertEqual(claimed["id"], self.number["id"])
        self.assertEqual(duplicate["id"], self.number["id"])
        self.assertEqual(duplicate["client_id"], "client-1")

        with self.assertRaises(ValueError):
            asyncio.run(self.manager.add_phone_number({
                "phone": "+15551230001",
                "sid": "PN123-CROSS",
                "region": "US",
                "provider": "twilio",
                "client_id": "client-2",
            }))

    def test_route_upsert_updates_existing_route_for_ui_resaves(self):
        first = asyncio.run(self.manager.upsert_phone_number_route(
            number_id=self.number["id"],
            client_id="client-1",
            agent_id="agent-1",
            metadata={"source": "first"},
        ))
        second = asyncio.run(self.manager.upsert_phone_number_route(
            number_id=self.number["id"],
            client_id="client-1",
            agent_id=None,
            metadata={"source": "second"},
        ))

        self.assertEqual(second["id"], first["id"])
        self.assertIsNone(second["agent_id"])
        self.assertEqual(second["metadata"]["source"], "second")

    def test_campaign_number_selection_is_tenant_and_agent_scoped(self):
        tenant_default = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551230002",
            "sid": "PN124",
            "region": "US",
            "provider": "twilio",
            "client_id": "client-1",
        }))
        other_tenant = asyncio.run(self.manager.add_phone_number({
            "phone": "+15551239999",
            "sid": "PN999",
            "region": "US",
            "provider": "twilio",
            "client_id": "client-2",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Client One Campaign",
            "status": "Pending",
            "agent_id": "agent-1",
            "client_id": "client-1",
            "telephony_provider": "twilio",
        }))
        asyncio.run(self.manager.upsert_phone_number_route(
            number_id=tenant_default["id"],
            client_id="client-1",
            metadata={"source": "tenant_default"},
        ))
        asyncio.run(self.manager.upsert_phone_number_route(
            number_id=self.number["id"],
            client_id="client-1",
            agent_id="agent-1",
            metadata={"source": "agent_route"},
        ))

        selected = asyncio.run(self.manager.get_phone_number_for_campaign(
            client_id="client-1",
            agent_id="agent-1",
            campaign_id="campaign-1",
            provider="twilio",
        ))
        other_selected = asyncio.run(self.manager.get_phone_number_for_campaign(
            client_id="client-2",
            agent_id="agent-1",
            campaign_id="campaign-1",
            provider="twilio",
        ))

        self.assertEqual(selected["id"], self.number["id"])
        self.assertEqual(selected["route"]["metadata"]["source"], "agent_route")
        self.assertEqual(other_selected["id"], other_tenant["id"])

    def test_main_telephony_routes_are_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        runner_source = (BACKEND_ROOT / "agent_runner.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("telephony.tenant_numbers")', source)
        self.assertIn("/api/telephony/numbers/routes", source)
        self.assertIn("/api/telephony/routes/resolve", source)
        self.assertIn('campaign.get("client_id") or "global"', source)
        self.assertIn("get_phone_number_for_campaign", runner_source)


if __name__ == "__main__":
    unittest.main()
