"""
WebSocket Broadcast Hub — Real-time event distribution to dashboard clients.

Replaces JSON file polling with push-based WebSocket events.
All call state changes (ringing, talking, completed, transcripts) are
instantly broadcast to every connected dashboard browser.

Usage:
    from ws_hub import ws_manager
    await ws_manager.broadcast_all({"type": "call_status", ...})
    await ws_manager.broadcast_to_client("client_123", {...})
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import WebSocket

from platform_migration import feature_flags
from platform_migration.auth_context import build_ws_event_scope_shadow_manifest

logger = logging.getLogger("ws_hub")


class WebSocketManager:
    """
    Central hub for WebSocket connections.

    Supports two broadcast scopes:
    - broadcast_all()       : sends to every connected browser (admin view)
    - broadcast_to_client() : sends only to browsers logged-in as a specific client
    """

    def __init__(self):
        # client_id -> set of connected websockets
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str = "global") -> None:
        await websocket.accept()
        async with self._lock:
            if client_id not in self._connections:
                self._connections[client_id] = set()
            self._connections[client_id].add(websocket)
        logger.info("WS connected: client=%s  total=%d", client_id, self._total_connections())

    async def disconnect(self, websocket: WebSocket, client_id: str = "global") -> None:
        async with self._lock:
            bucket = self._connections.get(client_id, set())
            bucket.discard(websocket)
            if not bucket:
                self._connections.pop(client_id, None)
        logger.info("WS disconnected: client=%s  total=%d", client_id, self._total_connections())

    def _total_connections(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def _shadow_event_scope(self, *, broadcast_mode: str, target_client_id: str | None, message: dict) -> None:
        if not feature_flags.is_enabled("ws.scoped_events_shadow"):
            return
        manifest = build_ws_event_scope_shadow_manifest(
            event_type=str(message.get("type") or "unknown"),
            broadcast_mode=broadcast_mode,
            target_client_id=target_client_id,
        )
        logger.info(
            "ws_scoped_event_shadow event_type=%s mode=%s target_tenant_present=%s global_broadcast=%s blockers=%s",
            manifest["event"]["type"],
            manifest["delivery"]["broadcast_mode"],
            manifest["delivery"]["target_tenant_present"],
            manifest["delivery"]["global_broadcast"],
            ",".join(manifest["decision"]["blockers"]),
        )

    async def broadcast_to_client(self, client_id: str, message: dict) -> None:
        """Send a message to all browsers connected under a specific client_id."""
        self._shadow_event_scope(broadcast_mode="client", target_client_id=client_id, message=message)
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []

        bucket = self._connections.get(client_id, set())
        for ws in list(bucket):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws, client_id)

    async def broadcast_all(self, message: dict) -> None:
        """Send a message to the global/admin channel.

        Legacy behavior fans this out to every bucket. When ws.scoped_events is
        enabled, tenant buckets only receive explicitly targeted messages.
        """
        self._shadow_event_scope(broadcast_mode="all", target_client_id=None, message=message)
        payload = json.dumps(message, default=str)
        dead: list[tuple[WebSocket, str]] = []

        async with self._lock:
            snapshot = {cid: set(sockets) for cid, sockets in self._connections.items()}
        if feature_flags.is_enabled("ws.scoped_events"):
            snapshot = {"global": snapshot.get("global", set())}

        for client_id, sockets in snapshot.items():
            for ws in sockets:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append((ws, client_id))

        for ws, client_id in dead:
            await self.disconnect(ws, client_id)

    async def send_call_event(
        self,
        event_type: str,
        *,
        campaign_id: str = "",
        lead_id: str = "",
        lead_name: str = "",
        status: str = "",
        snippet: str = "",
        transcripts: list | None = None,
        result: dict | None = None,
        provider: str = "",
        client_id: str = "global",
    ) -> None:
        """
        Structured helper — builds a typed event and broadcasts it.

        event_type values:
          call_ringing   — call just initiated
          call_connected — call picked up
          call_talking   — transcript snippet available
          call_completed — call ended, result attached
          call_error     — something went wrong
          demo_started   — demo mode initiated
        """
        message = {
            "type": event_type,
            "campaignId": campaign_id,
            "leadId": lead_id,
            "leadName": lead_name,
            "status": status,
            "snippet": snippet,
            "transcripts": transcripts or [],
            "result": result or {},
            "provider": provider,
        }
        # Always broadcast globally for admin + to specific client
        await self.broadcast_all(message)
        if client_id and client_id != "global":
            await self.broadcast_to_client(client_id, message)


# Singleton instance shared across the entire app
ws_manager = WebSocketManager()


class CallSessionRegistry:
    """
    In-process registry: call_id → Twilio session metadata.

    Populated by agent_runner.run_campaign() BEFORE provider.initiate_call()
    so that the /telephony/stream/{call_id} WebSocket handler can look up
    the campaign context and persist the call result when the stream ends.

    Thread-safe: plain dict, all mutations happen in the asyncio event loop.
    """

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def register(self, call_id: str, meta: dict) -> None:
        """Store metadata for an in-flight call."""
        self._sessions[call_id] = meta
        logger.info("CallRegistry: registered call_id=%s campaign=%s", call_id, meta.get("campaign_id"))

    def get(self, call_id: str) -> dict | None:
        """Return metadata or None if the call_id is unknown (e.g. inbound call)."""
        return self._sessions.get(call_id)

    def clear(self, call_id: str) -> None:
        """Remove the entry once the stream handler has finished."""
        self._sessions.pop(call_id, None)
        logger.info("CallRegistry: cleared call_id=%s", call_id)


# Singleton instance shared across the entire app
call_registry = CallSessionRegistry()
