"""
Campaign Runner — Production call orchestration.

Upgraded from the original agent_runner.py:
  - Fully async (no blocking time.sleep)
  - Uses SQLite via db_manager (no JSON file corruption)
  - WebSocket broadcast on every state change
  - Multi-provider telephony support
  - Auto-reloads agent schemas from disk on every call
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime
from typing import Optional

from platform_migration import feature_flags

logger = logging.getLogger("agent_runner")

DB_DIR     = "db"
AGENTS_DIR = os.path.join(DB_DIR, "agents")
DEFAULT_SCHEMA = "Updated_Real_Estate_Agent.json"


def _resolve_schema(agent_id: str) -> str:
    """Always resolves fresh from disk — auto-reload for agent fine-tuning."""
    path = os.path.join(AGENTS_DIR, f"{agent_id}.json")
    if os.path.exists(path):
        return path
    return DEFAULT_SCHEMA


async def simulate_human_response(agent_text: str, lead_name: str) -> str:
    """
    Simulates a realistic human reply for non-demo production testing.
    In real telephony, this is replaced by actual STT output.
    """
    t = agent_text.lower()
    if any(x in t for x in ["speaking with", "this is", "hello", "am i"]):
        return f"Yes, this is {lead_name}."
    if any(x in t for x in ["good time", "quick", "moment"]):
        return random.choice(["Yeah, I have a moment.", "Go ahead.", "Sure."])
    if any(x in t for x in ["buy, rent", "looking for"]):
        return "I'm looking to buy a property."
    if any(x in t for x in ["location", "area"]):
        return random.choice(["I'm interested in Wakad.", "Baner area.", "Hinjewadi."])
    if any(x in t for x in ["budget", "price"]):
        return random.choice(["Around 60 lakhs.", "75 lakhs budget.", "About 1 crore."])
    if any(x in t for x in ["site visit", "visit"]):
        return "Yes, I can visit this Sunday."
    if any(x in t for x in ["date", "time"]):
        return "Sunday 11 AM works."
    return "Okay, that sounds good."


async def run_campaign(
    campaign_id: str,
    agent_id: str,
    telephony_provider: str = "demo",
    client_id: str = "global",
) -> None:
    """
    Executes a full campaign for every lead.
    For demo/test: simulates conversation with simulate_human_response().
    For real telephony: each lead gets a real outbound call via the provider.
    """
    # Late import to avoid circular dependencies
    from db.db_manager import db
    from ws_hub import ws_manager, call_registry
    from llm.state_manager import StateManager
    from llm.llm import generate_response

    leads = await db.get_leads_for_campaign(campaign_id)
    if not leads:
        logger.warning("No leads found for campaign %s", campaign_id)
        return

    agent_schema_path = _resolve_schema(agent_id)
    provider = None

    if telephony_provider not in ("demo", "simulation"):
        try:
            from telephony.provider_registry import get_provider
            provider = get_provider(telephony_provider)
        except Exception as e:
            logger.error("Failed to load provider '%s': %s — falling back to simulation", telephony_provider, e)

    logger.info("Campaign %s: %d leads, provider=%s", campaign_id, len(leads), telephony_provider)

    for lead in leads:
        lead_uid = f"{campaign_id}_{lead.get('phone', 'unknown')}"
        lead_name = lead.get("name", "Lead")
        phone = lead.get("phone", "")

        logger.info("Starting call: %s (%s)", lead_name, phone)

        # ── Emit: Ringing ──────────────────────────────────────────────────
        await ws_manager.send_call_event(
            "call_ringing",
            campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
            status="Ringing...", provider=telephony_provider, client_id=client_id,
        )
        await db.update_live_state(lead_uid, campaign_id, lead_name, "Ringing...", provider=telephony_provider)

        # ── Register call in registry so stream handler can resolve context ──
        call_registry.register(lead_uid, {
            "campaign_id":       campaign_id,
            "lead_name":         lead_name,
            "phone":             phone,
            "client_id":         client_id,
            "agent_schema_path": agent_schema_path,
        })

        # ── Initiate real call if provider configured ──────────────────────
        if provider and phone:
            assigned_number = await db.get_phone_number_for_campaign(
                client_id=client_id,
                agent_id=agent_id,
                campaign_id=campaign_id,
                provider=telephony_provider,
            )
            from_number = assigned_number.get("phone") if assigned_number else None
            if from_number:
                webhook_base = os.getenv("WEBHOOK_BASE_URL", "http://localhost:3000")
                call_result = await provider.initiate_call(phone, from_number, lead_uid, webhook_base)
                logger.info("Provider call initiated: %s", call_result)
                # For real calls, the Twilio/VoBiz webhook handles the rest.
                # The stream handler will persist the result via call_registry.
                await db.update_live_state(
                    lead_uid, campaign_id, lead_name,
                    "Connecting...", provider=telephony_provider
                )
                await asyncio.sleep(2)  # Non-blocking wait
                continue
            logger.warning(
                "Campaign %s: no tenant-scoped from-number for client=%s agent=%s provider=%s; using simulation path",
                campaign_id,
                client_id,
                agent_id,
                telephony_provider,
            )

        # ── Simulation path (demo / no provider configured) ────────────────
        await asyncio.sleep(random.uniform(1.0, 2.5))  # Realistic ring delay

        # Load schema fresh from disk (auto-reload)
        state_manager = StateManager(agent_schema_path)
        state_manager.reset_state()
        transcripts: list[dict] = []

        # ── Connect ────────────────────────────────────────────────────────
        await ws_manager.send_call_event(
            "call_connected",
            campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
            status="Connected", provider=telephony_provider, client_id=client_id,
        )

        # ── AI Greeting ────────────────────────────────────────────────────
        response = await generate_response(
            "[System: The call has just been connected.]",
            [],
            state_manager=state_manager,
            allow_transition=False,
        )
        transcripts.append({"role": "assistant", "content": response})
        await db.update_live_state(
            lead_uid, campaign_id, lead_name, "Talking", response, transcripts, telephony_provider
        )
        await ws_manager.send_call_event(
            "call_talking",
            campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
            status="Talking", snippet=response, transcripts=transcripts,
            provider=telephony_provider, client_id=client_id,
        )

        # ── Conversation Loop ──────────────────────────────────────────────
        turns = 0
        while not state_manager.is_terminal_node() and turns < 12:
            await asyncio.sleep(random.uniform(0.8, 2.0))  # Human think time

            user_text = await simulate_human_response(response, lead_name)
            transcripts.append({"role": "user", "content": user_text})
            await db.update_live_state(
                lead_uid, campaign_id, lead_name, "Talking", user_text, transcripts, telephony_provider
            )
            await ws_manager.send_call_event(
                "call_talking",
                campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
                status="Talking", snippet=user_text, transcripts=transcripts,
                provider=telephony_provider, client_id=client_id,
            )

            response = await generate_response(user_text, [], state_manager=state_manager)
            if not response:
                turns += 1
                continue

            transcripts.append({"role": "assistant", "content": response})
            await db.update_live_state(
                lead_uid, campaign_id, lead_name, "Talking", response, transcripts, telephony_provider
            )
            await ws_manager.send_call_event(
                "call_talking",
                campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
                status="Talking", snippet=response, transcripts=transcripts,
                provider=telephony_provider, client_id=client_id,
            )
            turns += 1

            if getattr(state_manager, "_session_ended", False):
                break

        # ── Record result ──────────────────────────────────────────────────
        data = getattr(state_manager, "conversation_data", {}) or {}
        result = {
            "name":         lead_name,
            "phone":        phone,
            "calledAt":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration":     f"{turns * 12}s",
            "status":       "Connected",
            "interested":   "Yes" if data.get("location") else "No",
            "budget":       data.get("budget", "—"),
            "callback":     data.get("timeline", "—"),
            "location":     data.get("location", "—"),
            "transcription": transcripts,
            "provider":     telephony_provider,
            "processed":    True,
            "lead_data":    data,
        }
        await db.append_call_result(campaign_id, result)
        await db.update_live_state(
            lead_uid, campaign_id, lead_name, "Completed", "Call ended", transcripts, telephony_provider
        )
        await ws_manager.send_call_event(
            "call_completed",
            campaign_id=campaign_id, lead_id=lead_uid, lead_name=lead_name,
            status="Completed", snippet="Call ended", transcripts=transcripts,
            result=result, provider=telephony_provider, client_id=client_id,
        )

        await asyncio.sleep(1.0)  # Gap between calls

    # ── Campaign complete ──────────────────────────────────────────────────────
    await db.set_campaign_status(campaign_id, "Done")
    completion_message = {
        "type": "campaign_completed",
        "campaignId": campaign_id,
        "message": f"Campaign {campaign_id} completed.",
    }
    await ws_manager.broadcast_all(completion_message)
    if feature_flags.is_enabled("ws.scoped_events") and client_id and client_id != "global":
        await ws_manager.broadcast_to_client(client_id, completion_message)
    logger.info("Campaign %s DONE.", campaign_id)
