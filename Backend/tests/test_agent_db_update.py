import asyncio
import tempfile
import unittest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager


class AgentDatabaseUpdateTest(unittest.TestCase):
    def test_update_agent_edits_provider_assignment_and_fields(self):
        original_db_path = db_manager.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db_manager.DB_PATH = Path(tmpdir) / "platform.db"
                db_manager._init_schema()
                manager = db_manager.DatabaseManager()
                asyncio.run(manager.create_client("client-old", {
                    "name": "Old Client",
                    "email": "old@example.com",
                    "plan": "assigned",
                }))
                asyncio.run(manager.create_client("client-new", {
                    "name": "New Client",
                    "email": "new@example.com",
                    "plan": "assigned",
                }))

                asyncio.run(manager.create_agent("agent-1", {
                    "name": "Old Agent",
                    "voice": "old-voice",
                    "language": "English",
                    "max_duration": 300,
                    "provider": "twilio",
                    "stt_provider": "groq",
                    "tts_provider": "edge",
                    "cartesia_voice_id": None,
                    "assigned_email": "old@example.com",
                    "agent_type": "real_estate_sales",
                    "script": "Old prompt",
                    "data_fields": ["interested"],
                    "schema_path": "db/agents/agent-1.json",
                    "client_id": "client-old",
                }))

                updated = asyncio.run(manager.update_agent("agent-1", {
                    "name": "New Agent",
                    "voice": "new-voice",
                    "language": "Hindi",
                    "max_duration": 420,
                    "provider": "demo",
                    "stt_provider": "deepgram",
                    "tts_provider": "cartesia",
                    "cartesia_voice_id": "95d51f79-c397-46f9-b49a-23763d3eaa2d",
                    "assigned_email": "new@example.com",
                    "agent_type": "finance",
                    "script": "New prompt",
                    "data_fields": ["interested", "callback"],
                    "schema_path": "db/agents/agent-1.json",
                    "client_id": "client-new",
                }))

                self.assertEqual(updated["name"], "New Agent")
                self.assertEqual(updated["stt_provider"], "deepgram")
                self.assertEqual(updated["tts_provider"], "cartesia")
                self.assertEqual(updated["assigned_email"], "new@example.com")
                self.assertEqual(updated["data_fields"], ["interested", "callback"])
        finally:
            db_manager.DB_PATH = original_db_path

    def test_clear_assignment_only_removes_matching_agent(self):
        original_db_path = db_manager.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                db_manager.DB_PATH = Path(tmpdir) / "platform.db"
                db_manager._init_schema()
                manager = db_manager.DatabaseManager()
                asyncio.run(manager.create_client("client-1", {
                    "name": "Client One",
                    "email": "client1@example.com",
                    "plan": "assigned",
                }))
                asyncio.run(manager.create_agent("agent-1", {
                    "name": "Agent One",
                    "voice": "voice",
                    "language": "English",
                    "max_duration": 300,
                    "provider": "twilio",
                    "stt_provider": "groq",
                    "tts_provider": "edge",
                    "cartesia_voice_id": None,
                    "assigned_email": "client1@example.com",
                    "agent_type": "real_estate_sales",
                    "script": "Prompt",
                    "data_fields": ["interested"],
                    "schema_path": "db/agents/agent-1.json",
                    "client_id": "client-1",
                }))

                asyncio.run(manager.set_assignment("client-1", "agent-1"))
                asyncio.run(manager.clear_assignment("client-1", "agent-2"))
                self.assertEqual(asyncio.run(manager.get_assignment("client-1")), "agent-1")

                asyncio.run(manager.clear_assignment("client-1", "agent-1"))
                self.assertIsNone(asyncio.run(manager.get_assignment("client-1")))
        finally:
            db_manager.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
