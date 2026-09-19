import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("GROQ_API_KEY", "test-key")

from llm.language_utils import analyze_user_text
from llm.conversation_response import generate_spoken_response
from llm.llm import _extract_budget_entity, _enrich_intent_entities
from llm.state_manager import StateManager, _finalize_response_text


class MultilingualSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        schema_path = PROJECT_ROOT / "Updated_Real_Estate_Agent.json"
        self.state_manager = StateManager(str(schema_path))
        self.state_manager.reset_state()

    def test_detects_roman_hindi(self) -> None:
        analysis = analyze_user_text("main ghar dekh raha hoon", fallback="en")
        self.assertEqual(analysis.detected_language, "hi")

    def test_detects_hinglish(self) -> None:
        analysis = analyze_user_text("budget 50 lakh ke around hai", fallback="en")
        self.assertEqual(analysis.detected_language, "hinglish")

    def test_localizes_shared_flow_response(self) -> None:
        self.state_manager.set_active_language("hinglish")
        response = self.state_manager.next_step("", allow_transition=False)
        self.assertIn("Neha bol rahi hoon", response)

    def test_ask_intent_understands_hindi_phrase(self) -> None:
        self.state_manager.current_node_id = "node-1735264921453"
        self.state_manager.set_active_language("hinglish")
        response = self.state_manager.process_turn(
            "investment ke liye dekh raha hoon",
            {"intent": "unclear", "entities": {}},
        )
        self.assertEqual(self.state_manager.current_node_id, "node-1735267546732")
        self.assertEqual(self.state_manager.conversation_data.get("intent_value"), "invest")
        self.assertIn("area", response.lower())

    def test_budget_entity_is_extracted_from_hinglish(self) -> None:
        self.assertEqual(_extract_budget_entity("budget 50 lakh hai"), "50 lakh")
        intent, entities = _enrich_intent_entities("budget 50 lakh hai", "unclear", {})
        self.assertEqual(intent, "provide_budget")
        self.assertEqual(entities.get("budget"), "50 lakh")

    def test_response_shaping_removes_filler_and_extra_questions(self) -> None:
        response = _finalize_response_text(
            "Sure, are you looking in Baner? And what's your budget?"
        )
        self.assertFalse(response.lower().startswith("sure"))
        self.assertEqual(response.count("?"), 1)

    def test_response_shaping_caps_word_count(self) -> None:
        response = _finalize_response_text(
            "This is a very long response that keeps going beyond the configured spoken limit and should be trimmed cleanly for calls."
        )
        self.assertLessEqual(len(response.split()), 20)

    def test_composer_uses_memory_for_purpose_clarification(self) -> None:
        response = generate_spoken_response(
            current_node={"id": "fallback_intent", "name": "Fallback Intent"},
            memory="Last conversation: customer asked about returns and future appreciation.",
            user_input="haan dekh raha hoon",
            language="hinglish",
        )
        self.assertIn("investment", response.lower())
        self.assertLessEqual(len(response.split()), 20)
        self.assertEqual(response.count("?"), 1)

    def test_composer_asks_budget_after_location_is_known(self) -> None:
        response = generate_spoken_response(
            current_node={"id": "node-1735267546732", "name": "Ask Location and Budget"},
            location="Baner",
            language="en",
        )
        self.assertIn("budget", response.lower())
        self.assertNotIn("city or area", response.lower())
        self.assertEqual(response.count("?"), 1)

    def test_composer_suggests_locations_for_vague_input(self) -> None:
        response = generate_spoken_response(
            current_node={"id": "fallback_location", "name": "Fallback Location", "expected_input_type": "location"},
            user_input="not sure, suggest something",
            language="en",
        )
        self.assertIn("Wakad", response)
        self.assertIn("Baner", response)
        self.assertNotIn("didn't catch", response.lower())
        self.assertLessEqual(len(response.split()), 20)

    def test_composer_answers_identity_question_before_flow_question(self) -> None:
        response = generate_spoken_response(
            current_node={"id": "node-1735264921453", "name": "Ask Intent"},
            user_input="Who are you?",
            language="en",
        )
        self.assertTrue(response.startswith("Neha here"))
        self.assertIn("investment", response.lower())
        self.assertEqual(response.count("?"), 1)
        self.assertLessEqual(len(response.split()), 20)

    def test_composer_reintroduces_on_confusion(self) -> None:
        response = generate_spoken_response(
            current_node={"id": "node-1735267546732", "name": "Ask Location and Budget"},
            user_input="I don't understand what you are talking about",
            language="en",
        )
        self.assertIn("property interest", response.lower())
        self.assertIn("area", response.lower())
        self.assertEqual(response.count("?"), 1)
        self.assertLessEqual(len(response.split()), 20)

    def test_state_manager_answers_purpose_question_at_availability(self) -> None:
        self.state_manager.current_node_id = "node-1735264873079"
        self.state_manager.set_active_language("en")
        response = self.state_manager.process_turn(
            "Why are you calling?",
            {"intent": "unclear", "entities": {}},
        )
        self.assertEqual(self.state_manager.current_node_id, "node-1735264873079")
        self.assertIn("property interest", response.lower())
        self.assertIn("two minutes", response.lower())
        self.assertEqual(response.count("?"), 1)

    def test_state_manager_answers_identity_question_at_greeting(self) -> None:
        self.state_manager.current_node_id = "node-1767592854176"
        self.state_manager.set_active_language("en")
        response = self.state_manager.process_turn(
            "Who are you?",
            {"intent": "unclear", "entities": {}},
        )
        self.assertEqual(self.state_manager.current_node_id, "node-1767592854176")
        self.assertTrue(response.startswith("Neha here"))
        self.assertIn("prashant", response.lower())
        self.assertEqual(response.count("?"), 1)

    def test_state_manager_answers_question_without_leaving_ask_intent(self) -> None:
        self.state_manager.current_node_id = "node-1735264921453"
        self.state_manager.set_active_language("en")
        response = self.state_manager.process_turn(
            "What is this about?",
            {"intent": "unclear", "entities": {}},
        )
        self.assertEqual(self.state_manager.current_node_id, "node-1735264921453")
        self.assertIn("property interest", response.lower())
        self.assertIn("investment", response.lower())
        self.assertEqual(response.count("?"), 1)

    def test_state_manager_qualification_node_stays_single_slot_focused(self) -> None:
        self.state_manager.current_node_id = "node-1735267546732"
        self.state_manager.conversation_data["location"] = "Wakad"
        self.state_manager.set_active_language("en")
        response = self.state_manager.next_step("", allow_transition=False)
        self.assertIn("budget", response.lower())
        self.assertNotIn("which city", response.lower())


if __name__ == "__main__":
    unittest.main()
