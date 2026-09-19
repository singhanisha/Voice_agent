import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("GROQ_API_KEY", "runtime-guard-test-key")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from flows.runtime import _is_likely_agent_echo
from llm.llm import _classify_local_intent
from llm.llm_response_generator import generate_response_for_turn_sync
from llm.state_manager import StateManager


SCHEMA_PATH = BACKEND_ROOT / "Updated_Real_Estate_Agent.json"


class RuntimeConversationGuardTest(unittest.TestCase):
    def _manager_at_availability(self) -> StateManager:
        manager = StateManager(str(SCHEMA_PATH))
        first = manager.execute_transition(
            "yes",
            {"intent": "confirm", "entities": {"confirmation": "yes"}},
        )
        self.assertEqual(first.node_id, "node-1735264873079")
        return manager

    def test_availability_i_have_two_minutes_advances_to_intent(self):
        manager = self._manager_at_availability()

        turn = manager.execute_transition(
            "I have two minutes",
            {"intent": "unclear", "entities": {}},
        )

        self.assertEqual(turn.node_id, "node-1735264921453")
        self.assertTrue(turn.node_changed)

    def test_availability_tell_me_advances_to_intent(self):
        manager = self._manager_at_availability()

        turn = manager.execute_transition(
            "tell me",
            {"intent": "unclear", "entities": {}},
        )

        self.assertEqual(turn.node_id, "node-1735264921453")
        self.assertTrue(turn.node_changed)

    def test_confirm_availability_reports_real_node_change(self):
        manager = self._manager_at_availability()

        turn = manager.execute_transition(
            "I am free now",
            {"intent": "confirm_availability", "entities": {}},
        )

        self.assertEqual(turn.node_id, "node-1735264921453")
        self.assertTrue(turn.node_changed)

    def test_yes_what_is_it_answers_purpose_without_repeating_availability(self):
        manager = self._manager_at_availability()

        turn = manager.execute_transition(
            "Yes, what is it?",
            {"intent": "user_question", "entities": {}},
        )
        response = generate_response_for_turn_sync(turn)

        self.assertEqual(turn.node_id, "node-1735264921453")
        self.assertTrue(turn.node_changed)
        self.assertEqual(turn.user_question, "purpose")
        self.assertIn("property interest", response.lower())
        self.assertIn("buy", response.lower())
        self.assertNotIn("two minutes", response.lower())

    def test_location_suggestion_routes_to_useful_city_options(self):
        manager = StateManager(str(SCHEMA_PATH))
        manager.current_node_id = "node-1735267546732"
        manager.conversation_data["budget"] = "60 lakhs"

        turn = manager.execute_transition(
            "Suggest me the cities",
            {"intent": "unclear", "entities": {}},
        )
        response = generate_response_for_turn_sync(turn)

        self.assertEqual(turn.node_id, "fallback_location")
        self.assertIn("wakad", response.lower())
        self.assertNotIn("didn't catch", response.lower())

    def test_unclear_offer_request_repeats_city_options_not_generic_fallback(self):
        manager = StateManager(str(SCHEMA_PATH))
        manager.current_node_id = "fallback_location"
        manager.conversation_data["budget"] = "60 lakhs"

        turn = manager.execute_transition(
            "Buy, ask, can you offer me?",
            {"intent": "provide_intent", "entities": {"intent_value": "buy"}},
        )
        response = generate_response_for_turn_sync(turn)

        self.assertEqual(turn.node_id, "fallback_location")
        self.assertIn("wakad", response.lower())
        self.assertNotIn("didn't catch", response.lower())

    def test_local_intent_detects_location_suggestion_without_llm(self):
        intent = _classify_local_intent("Suggest me the cities.")

        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent"], "ask_location_suggestion")

    def test_agent_echo_guard_blocks_prompt_echo_not_user_answer(self):
        prompt = "Do you have two minutes right now? I came across something relevant for you."

        self.assertTrue(_is_likely_agent_echo("Do you have two minutes right now", prompt))
        self.assertFalse(_is_likely_agent_echo("I have two minutes", prompt))


if __name__ == "__main__":
    unittest.main()
