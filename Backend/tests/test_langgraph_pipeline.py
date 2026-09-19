import asyncio
import os
import sys
import unittest
from pathlib import Path

# Fix python path to allow importing Backend modules directly
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain_core.messages import AIMessage, HumanMessage
from intelligence.pipeline import langgraph_engine, ConversationState
from llm.state_manager import is_low_signal, is_hard_out

# Inject mock api key for tests
os.environ.setdefault("GROQ_API_KEY", "mock-api-key")

class TestLangGraphPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.initial_state: ConversationState = {
            "messages": [],
            "extracted_slots": {
                "intent_value": None,
                "budget_range": None,
                "preferred_bhk": None,
                "timeline_weeks": None,
                "contact_validated": None
            },
            "current_node": "GREETING",
            "pending_filler_action": None,
            "rag_context": None,
            "retry_count": 0,
            "user_input": ""
        }

    def test_low_signal_detector(self):
        self.assertTrue(is_low_signal("Yes"))
        self.assertTrue(is_low_signal("okay"))
        self.assertTrue(is_low_signal("झाल"))
        self.assertTrue(is_low_signal("Share it"))
        self.assertFalse(is_low_signal("I want to buy a 3 BHK apartment in Wakad"))

    def test_hard_out_detector(self):
        self.assertTrue(is_hard_out("Hang up now"))
        self.assertTrue(is_hard_out("Stop calling me"))
        self.assertFalse(is_hard_out("Yes, I am looking for a 2 BHK"))

    async def test_greeting_transition(self):
        # Seed greeting tick
        state = dict(self.initial_state)
        state["user_input"] = ""
        state["current_node"] = "GREETING"
        
        # Invoke LangGraph GREETING node
        res = await langgraph_engine.ainvoke(state)
        
        self.assertEqual(res["current_node"], "DISCOVERY")
        self.assertTrue(len(res["messages"]) > 0)
        self.assertTrue(isinstance(res["messages"][-1], AIMessage))

    async def test_live_search_latency_masking_two_step_flow(self):
        state = dict(self.initial_state)
        state["current_node"] = "LIVE_SEARCH"
        state["user_input"] = "What are the rules and amenities at Suncity?"
        state["messages"].append(HumanMessage(content=state["user_input"]))
        
        # Step A tick (Immediate Return with Filler)
        res_step_a = await langgraph_engine.ainvoke(state)
        
        self.assertIsNotNone(res_step_a.get("pending_filler_action"))
        self.assertEqual(res_step_a.get("rag_context"), "PENDING")
        
        # Step B tick (Execute Scraper in background / clear filler)
        res_step_a["pending_filler_action"] = None
        res_step_b = await langgraph_engine.ainvoke(res_step_a)
        
        self.assertIsNone(res_step_b.get("pending_filler_action"))
        self.assertNotEqual(res_step_b.get("rag_context"), "PENDING")
        self.assertIn("Suncity", res_step_b.get("rag_context"))
        self.assertTrue(len(res_step_b["messages"]) > 1)

if __name__ == "__main__":
    unittest.main()
