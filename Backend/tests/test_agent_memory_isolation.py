import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager
from memory import AgentMemoryService


class AgentMemoryIsolationTest(unittest.TestCase):
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
            "provider": "demo",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "cartesia_voice_id": None,
            "assigned_email": "client1@example.com",
            "agent_type": "finance",
            "script": "Only discuss verified finance products.",
            "data_fields": ["product_interest", "callback"],
            "schema_path": "db/agents/agent-1.json",
            "client_id": "client-1",
        }))
        self.service = AgentMemoryService(self.manager)

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_memory_collection_and_items_are_tenant_scoped(self):
        collection = asyncio.run(self.service.create_collection(
            client_id="client-1",
            agent_id="agent-1",
            source_type="manual",
            metadata={"purpose": "qa"},
        ))
        item = asyncio.run(self.service.add_item(
            collection_id=collection["id"],
            client_id="client-1",
            agent_id="agent-1",
            content="The advisor must not guarantee approval.",
            metadata={"source": "policy"},
        ))
        visible = asyncio.run(self.service.list_items(client_id="client-1", agent_id="agent-1"))
        other_tenant = asyncio.run(self.service.list_items(client_id="client-2", agent_id="agent-1"))

        self.assertEqual(collection["client_id"], "client-1")
        self.assertEqual(item["client_id"], "client-1")
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["content_hash"], item["content_hash"])
        self.assertEqual(other_tenant, [])

    def test_seed_from_agent_stores_prompt_without_runtime_injection(self):
        agent = asyncio.run(self.manager.get_agent("agent-1"))
        result = asyncio.run(self.service.seed_from_agent(client_id="client-1", agent=agent))

        self.assertEqual(result["collection"]["metadata"]["rag_runtime_enabled"], False)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(any("verified finance" in item["content"] for item in result["items"]))

    def test_reset_soft_deletes_memory_items(self):
        collection = asyncio.run(self.service.create_collection(client_id="client-1", agent_id="agent-1"))
        asyncio.run(self.service.add_item(
            collection_id=collection["id"],
            client_id="client-1",
            agent_id="agent-1",
            content="Reset me later.",
        ))
        reset = asyncio.run(self.service.reset(client_id="client-1", agent_id="agent-1", reason="test reset"))
        active = asyncio.run(self.service.list_items(client_id="client-1", agent_id="agent-1"))
        all_items = asyncio.run(self.service.list_items(client_id="client-1", agent_id="agent-1", include_deleted=True))

        self.assertEqual(reset["collections_reset"], 1)
        self.assertEqual(active, [])
        self.assertIsNotNone(all_items[0]["deleted_at"])

    def test_main_memory_surface_is_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("memory.rag_enabled")', source)
        self.assertIn("/api/memory/agents/{agent_id}/seed", source)
        self.assertIn("runtime_injection", source)


if __name__ == "__main__":
    unittest.main()
