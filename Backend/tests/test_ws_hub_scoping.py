import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ws_hub import WebSocketManager


class DummyWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


class WebSocketHubScopingTest(unittest.TestCase):
    def test_scoped_events_keeps_global_broadcast_on_admin_bucket(self):
        async def run():
            manager = WebSocketManager()
            admin_ws = DummyWebSocket()
            client_ws = DummyWebSocket()
            await manager.connect(admin_ws, "global")
            await manager.connect(client_ws, "client-1")

            with patch.dict(os.environ, {"FEATURE_WS_SCOPED_EVENTS": "true"}, clear=True):
                await manager.broadcast_all({"type": "campaign_completed"})

            self.assertEqual([msg["type"] for msg in admin_ws.messages], ["campaign_completed"])
            self.assertEqual(client_ws.messages, [])

        asyncio.run(run())

    def test_call_event_targets_client_once_when_scoped_events_enabled(self):
        async def run():
            manager = WebSocketManager()
            admin_ws = DummyWebSocket()
            client_ws = DummyWebSocket()
            await manager.connect(admin_ws, "global")
            await manager.connect(client_ws, "client-1")

            with patch.dict(os.environ, {"FEATURE_WS_SCOPED_EVENTS": "true"}, clear=True):
                await manager.send_call_event(
                    "call_talking",
                    campaign_id="campaign-1",
                    lead_id="lead-1",
                    client_id="client-1",
                )

            self.assertEqual(len(admin_ws.messages), 1)
            self.assertEqual(len(client_ws.messages), 1)
            self.assertEqual(admin_ws.messages[0]["type"], "call_talking")
            self.assertEqual(client_ws.messages[0]["leadId"], "lead-1")

        asyncio.run(run())

    def test_legacy_global_fanout_remains_when_scoped_events_disabled(self):
        async def run():
            manager = WebSocketManager()
            admin_ws = DummyWebSocket()
            client_ws = DummyWebSocket()
            await manager.connect(admin_ws, "global")
            await manager.connect(client_ws, "client-1")

            with patch.dict(os.environ, {}, clear=True):
                await manager.broadcast_all({"type": "legacy_notice"})

            self.assertEqual([msg["type"] for msg in admin_ws.messages], ["legacy_notice"])
            self.assertEqual([msg["type"] for msg in client_ws.messages], ["legacy_notice"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
