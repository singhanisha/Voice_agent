"""
Demo Call Engine — Zero-cost, indistinguishable from real calls.

Runs the full AI pipeline (Groq LLM + StateManager) with simulated human
responses. Fires real WebSocket events so the dashboard looks and behaves
exactly like a live Twilio call — including realistic timing.

Usage:
    from demo_runner import DemoCallEngine
    engine = DemoCallEngine()
    await engine.run_demo_call(campaign_id, lead, agent_schema_path)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime
from pathlib import Path

from platform_migration import feature_flags

logger = logging.getLogger("demo_runner")

# ── Demo timing config ───────────────────────────────────────────────────────
RING_DELAY_MIN_S      = 1.5    # min seconds before "answer"
RING_DELAY_MAX_S      = 3.0    # max seconds before "answer"
HUMAN_THINK_MIN_S     = 0.8    # min pause before human responds
HUMAN_THINK_MAX_S     = 2.2    # max pause before human responds
AI_PROCESS_DELAY_S    = 0.4    # simulated STT+LLM+TTS latency tap
BETWEEN_CALL_DELAY_S  = 1.5    # gap between consecutive demo calls
MAX_TURNS             = 12     # safety cap on conversation length

# System trigger sent to the LLM when a call connects
CALL_CONNECTED_TRIGGER = (
    "[System: The call has just been connected. "
    "No user has spoken yet. Speak only for the current conversation node "
    "and do not transition.]"
)


class DemoCallEngine:
    """
    Simulates a full outbound call through the real AI pipeline.

    The same StateManager, LLM (Groq), and response generation logic
    used in production is invoked here — only the audio I/O and
    telephony layer are replaced with simulation stubs.
    """

    def __init__(self, ws_manager=None, db=None):
        self.ws_manager = ws_manager  # WebSocketManager singleton
        self.db = db                  # DatabaseManager singleton

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_demo_call(
        self,
        campaign_id: str,
        lead: dict,
        agent_schema_path: str,
        client_id: str = "global",
        provider_label: str = "demo",
    ) -> dict:
        """
        Runs one complete simulated call and returns the call result dict.
        Fires WebSocket events at every state change.
        """
        lead_uid = f"{campaign_id}_{lead.get('phone', 'demo')}"
        lead_name = lead.get("name", "Lead")
        logger.info("[DEMO] Starting demo call for %s (%s)", lead_name, lead.get("phone"))

        # ── Phase 1: Ringing ────────────────────────────────────────────────
        await self._emit("call_ringing", campaign_id, lead_uid, lead_name,
                         "Ringing...", [], client_id, provider_label)
        await asyncio.sleep(random.uniform(RING_DELAY_MIN_S, RING_DELAY_MAX_S))

        # ── Phase 2: Call Connected ─────────────────────────────────────────
        await self._emit("call_connected", campaign_id, lead_uid, lead_name,
                         "Connected", [], client_id, provider_label)

        # Load agent schema from disk (always fresh — auto-reload guarantee)
        try:
            from llm.state_manager import StateManager
            from llm.llm import generate_response
        except ImportError as e:
            logger.error("[DEMO] Failed to import agent modules: %s", e)
            return self._build_result(lead, 0, "Error", "Import failure")

        # Load schema fresh from disk on every call
        if not os.path.exists(agent_schema_path):
            agent_schema_path = str(Path(__file__).parent / "Updated_Real_Estate_Agent.json")

        state_manager = StateManager(agent_schema_path)
        state_manager.reset_state()
        transcripts: list[dict] = []

        # ── Phase 3: AI Greeting ────────────────────────────────────────────
        await asyncio.sleep(AI_PROCESS_DELAY_S)
        try:
            greeting = await generate_response(
                CALL_CONNECTED_TRIGGER,
                [],
                state_manager=state_manager,
                allow_transition=False,
            )
        except Exception as e:
            logger.error("[DEMO] Greeting generation failed: %s", e)
            greeting = "Hello, this is Neha calling. Am I speaking with the right person?"

        if greeting:
            transcripts.append({"role": "assistant", "content": greeting})
            await self._emit("call_talking", campaign_id, lead_uid, lead_name,
                             "Talking", transcripts, client_id, provider_label, snippet=greeting)

        # ── Phase 4: Conversation Loop ───────────────────────────────────────
        turns = 0
        last_ai_text = greeting or ""
        conversation_history: list[dict] = []
        if greeting:
            conversation_history.append({"role": "assistant", "content": greeting})

        while not state_manager.is_terminal_node() and turns < MAX_TURNS:
            # Simulate human thinking delay
            await asyncio.sleep(random.uniform(HUMAN_THINK_MIN_S, HUMAN_THINK_MAX_S))

            # Generate a realistic human response
            human_text = await self._simulate_human_response(last_ai_text, lead_name)
            transcripts.append({"role": "user", "content": human_text})
            conversation_history.append({"role": "user", "content": human_text})
            await self._emit("call_talking", campaign_id, lead_uid, lead_name,
                             "Talking", transcripts, client_id, provider_label, snippet=human_text)

            # AI processes and responds
            await asyncio.sleep(AI_PROCESS_DELAY_S)
            try:
                ai_response = await generate_response(
                    human_text,
                    conversation_history,
                    state_manager=state_manager,
                )
            except Exception as e:
                logger.error("[DEMO] LLM response failed: %s", e)
                break

            if not ai_response:
                turns += 1
                continue

            transcripts.append({"role": "assistant", "content": ai_response})
            conversation_history.append({"role": "assistant", "content": ai_response})
            await self._emit("call_talking", campaign_id, lead_uid, lead_name,
                             "Talking", transcripts, client_id, provider_label, snippet=ai_response)

            last_ai_text = ai_response
            turns += 1

            # Check for session end flag
            if getattr(state_manager, "_session_ended", False):
                break

        # ── Phase 5: Call Complete ──────────────────────────────────────────
        result = self._build_result(lead, turns, "Connected", provider_label, transcripts, state_manager)

        await self._emit("call_completed", campaign_id, lead_uid, lead_name,
                         "Completed", transcripts, client_id, provider_label,
                         result=result, snippet="Call ended")

        # Persist to database
        if self.db:
            try:
                await self.db.append_call_result(campaign_id, result)
                await self.db.update_live_state(
                    lead_uid, campaign_id, lead_name, "Completed",
                    "Call ended", transcripts, provider_label
                )
            except Exception as e:
                logger.error("[DEMO] DB persist error: %s", e)

        logger.info("[DEMO] Call complete for %s — %d turns", lead_name, turns)
        return result

    async def run_demo_campaign(
        self,
        campaign_id: str,
        agent_schema_path: str,
        client_id: str = "global",
        max_leads: int = 3,
    ) -> None:
        """
        Runs up to max_leads demo calls sequentially for a campaign.
        Designed to be called as a background task.
        """
        # Always reload leads fresh from DB (auto-reload)
        leads = []
        if self.db:
            leads = await self.db.get_leads_for_campaign(campaign_id)

        # If no leads in DB, use a set of convincing demo leads
        if not leads:
            leads = self._get_demo_leads()

        leads = leads[:max_leads]
        logger.info("[DEMO] Running campaign %s with %d demo leads", campaign_id, len(leads))

        for lead in leads:
            await self.run_demo_call(campaign_id, lead, agent_schema_path, client_id)
            await asyncio.sleep(BETWEEN_CALL_DELAY_S)

        if self.db:
            await self.db.set_campaign_status(campaign_id, "Done")

        if self.ws_manager:
            completion_message = {
                "type": "campaign_completed",
                "campaignId": campaign_id,
                "message": "Demo campaign completed successfully.",
            }
            await self.ws_manager.broadcast_all(completion_message)
            if feature_flags.is_enabled("ws.scoped_events") and client_id and client_id != "global":
                await self.ws_manager.broadcast_to_client(client_id, completion_message)

    # ── Internal Helpers ──────────────────────────────────────────────────────

    async def _emit(
        self,
        event_type: str,
        campaign_id: str,
        lead_uid: str,
        lead_name: str,
        status: str,
        transcripts: list,
        client_id: str,
        provider: str,
        result: dict | None = None,
        snippet: str = "",
    ) -> None:
        """Broadcast a call event to the WebSocket hub and update DB live state."""
        if self.ws_manager:
            await self.ws_manager.send_call_event(
                event_type,
                campaign_id=campaign_id,
                lead_id=lead_uid,
                lead_name=lead_name,
                status=status,
                snippet=snippet,
                transcripts=transcripts,
                result=result or {},
                provider=provider,
                client_id=client_id,
            )

        if self.db:
            try:
                await self.db.update_live_state(
                    lead_uid, campaign_id, lead_name, status, snippet, transcripts, provider
                )
            except Exception as e:
                logger.warning("[DEMO] Live state update error: %s", e)

    async def _simulate_human_response(self, agent_text: str, lead_name: str) -> str:
        """
        Generate a realistic human response based on what the agent just said.
        Covers the full conversation arc: greeting → interest → data → scheduling.
        """
        t = agent_text.lower()

        if any(x in t for x in ["speaking with", "this is", "hello", "am i", "right person"]):
            return random.choice([
                f"Yes, this is {lead_name}.",
                f"Haan, main {lead_name} bol raha hoon.",
                "Yes, speaking.",
            ])

        if any(x in t for x in ["good time", "two minutes", "moment", "available", "busy"]):
            return random.choice([
                "Yeah, I have a moment, go ahead.",
                "Haan, bolo.",
                "Sure, tell me.",
                "Yes, what's this about?",
            ])

        # Recruitment specific
        if any(x in t for x in ["experience", "years", "work"]):
            return random.choice([
                "I have about 3 years of experience in sales.",
                "Mujhe 4 saal ka experience hai coding mein.",
                "Around 2 years.",
            ])
        if any(x in t for x in ["notice period", "notice"]):
            return random.choice([
                "I can join immediately.",
                "My notice period is 30 days.",
                "1 month notice period.",
            ])
        if any(x in t for x in ["expected salary", "salary expectation", "package"]):
            return random.choice([
                "I am expecting around 8 LPA.",
                "Around 10 lakhs per annum.",
                "Negotiable, around my current package.",
            ])

        # Healthcare specific
        if any(x in t for x in ["symptom", "pain", "fever", "feeling"]):
            return random.choice([
                "I have a mild fever and cough since yesterday.",
                "Back pain has been bothering me.",
                "Just regular checkup.",
            ])
        if any(x in t for x in ["doctor", "specialist"]):
            return random.choice([
                "Dr. Sharma would be good.",
                "Any general physician.",
                "Pediatrician for my kid.",
            ])

        # Insurance specific
        if any(x in t for x in ["policy", "insurance", "renew"]):
            return random.choice([
                "I have a health insurance policy.",
                "Motor insurance renewal.",
                "Life insurance plans.",
            ])

        # Finance specific
        if any(x in t for x in ["loan", "finance", "credit"]):
            return random.choice([
                "I need a personal loan.",
                "Looking for a home loan.",
                "Credit card options.",
            ])

        # Education specific
        if any(x in t for x in ["course", "study", "degree"]):
            return random.choice([
                "I am looking for MBA courses.",
                "Data Science certification.",
                "Engineering counselling.",
            ])

        # Real Estate specific & fallbacks
        if any(x in t for x in ["buy, rent", "looking for", "purpose", "intent"]):
            return random.choice([
                "I'm looking to buy a flat.",
                "I want to buy a 2 BHK.",
                "Buying, actually.",
                "I'm interested in buying a property.",
            ])

        if any(x in t for x in ["location", "area", "city", "prefer"]):
            locs = ["Wakad", "Baner", "Hinjewadi", "Kharadi", "Aundh", "Pimpri"]
            return random.choice([
                f"I'm looking in {random.choice(locs)}.",
                f"Somewhere in {random.choice(locs)} area would be good.",
                f"{random.choice(locs)} or nearby.",
            ])

        if any(x in t for x in ["budget", "price", "afford", "range"]):
            budgets = ["60 lakhs", "75 lakhs", "1 crore", "50 lakhs", "80 lakhs"]
            return random.choice([
                f"My budget is around {random.choice(budgets)}.",
                f"About {random.choice(budgets)} I think.",
                f"I can go up to {random.choice(budgets)}.",
            ])

        if any(x in t for x in ["bhk", "apartment", "villa", "type", "size"]):
            return random.choice([
                "2 BHK would be ideal.",
                "Looking for a 3 BHK flat.",
                "Apartment, 2 or 3 BHK.",
            ])

        if any(x in t for x in ["site visit", "visit", "show", "schedule", "appointment"]):
            return random.choice([
                "Yes, I can visit this weekend.",
                "Saturday morning works for me.",
                "Sunday afternoon would be fine.",
                "Can we do it this Sunday?",
            ])

        if any(x in t for x in ["date", "time", "when", "morning", "evening"]):
            return random.choice([
                "Sunday 11 AM works perfectly.",
                "Saturday at 10 AM is fine.",
                "Sunday morning, 11 or 12.",
            ])

        if any(x in t for x in ["thank you", "thanks", "great", "wonderful", "perfect"]):
            return random.choice([
                "Thank you. Looking forward to it.",
                "Great, thanks. Bye.",
                "Okay, thanks.",
            ])

        # Default fallback
        return random.choice([
            "That sounds good.",
            "Okay.",
            "Sure, go ahead.",
            "Yeah, I understand.",
            "Hmm, tell me more.",
        ])

    def _build_result(
        self,
        lead: dict,
        turns: int,
        status: str,
        provider: str,
        transcripts: list | None = None,
        state_manager=None,
    ) -> dict:
        data = {}
        if state_manager and hasattr(state_manager, "conversation_data"):
            data = state_manager.conversation_data or {}

        interested = "Yes" if data.get("location") or data.get("intent_value") else "No"

        return {
            "name": lead.get("name"),
            "phone": lead.get("phone"),
            "calledAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration": f"{turns * 12}s",
            "status": status,
            "interested": interested,
            "budget": data.get("budget", "—"),
            "callback": data.get("timeline", "—"),
            "location": data.get("location", "—"),
            "transcription": transcripts or [],
            "provider": provider,
            "processed": True,
            "lead_data": data,
        }

    @staticmethod
    def _get_demo_leads() -> list[dict]:
        """Returns a realistic set of demo leads for campaigns with no uploaded leads."""
        return [
            {"name": "Rahul Sharma",   "phone": "+91 98200 11111"},
            {"name": "Priya Nair",     "phone": "+91 99870 22222"},
            {"name": "Amit Verma",     "phone": "+91 98760 33333"},
            {"name": "Sunita Mehta",   "phone": "+91 89100 44444"},
            {"name": "Rajan Pillai",   "phone": "+91 91234 55555"},
        ]
