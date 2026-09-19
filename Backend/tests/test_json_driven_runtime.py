import os
import shutil
import tempfile
import unittest
from pathlib import Path

from flows.loader import (
    AgentConfigValidationError,
    load_agent_directory,
    validate_agent_directory,
)
from llm.state_manager import StateManager
from main import _resolve_schema


class TestJSONDrivenRuntime(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for configuration validation tests
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)

    def _write_temp_config(self, filename, content):
        with open(os.path.join(self.test_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)

    def test_validation_missing_files(self):
        # Empty directory should raise validation error
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("Missing required configuration file", str(ctx.exception))

    def test_validation_invalid_json(self):
        self._write_temp_config("conversation.json", "{invalid-json}")
        self._write_temp_config("nodes.json", "[]")
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", "[]")
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("Invalid JSON in conversation.json", str(ctx.exception))

    def test_validation_missing_fields_conversation(self):
        self._write_temp_config("conversation.json", '{"agent_name": "Test"}')
        self._write_temp_config("nodes.json", "[]")
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", "[]")
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("conversation.json is missing required field", str(ctx.exception))

    def test_validation_invalid_nodes_format(self):
        conv_json = (
            '{"agent_name": "Test", "voice_id": "voice-1", '
            '"global_prompt": "Prompt", "start_node_id": "greeting"}'
        )
        self._write_temp_config("conversation.json", conv_json)
        self._write_temp_config("nodes.json", '{"id": "greeting"}')  # Not a list
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", "[]")
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("nodes.json must contain a list", str(ctx.exception))

    def test_validation_missing_end_node(self):
        conv_json = (
            '{"agent_name": "Test", "voice_id": "voice-1", '
            '"global_prompt": "Prompt", "start_node_id": "greeting"}'
        )
        nodes_json = (
            '[{"id": "greeting", "type": "conversation", "response": "Hello"}]'
        )
        self._write_temp_config("conversation.json", conv_json)
        self._write_temp_config("nodes.json", nodes_json)
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", "[]")
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("must contain at least one node of type 'end'", str(ctx.exception))

    def test_validation_invalid_transition_target(self):
        conv_json = (
            '{"agent_name": "Test", "voice_id": "voice-1", '
            '"global_prompt": "Prompt", "start_node_id": "greeting"}'
        )
        nodes_json = (
            '[{"id": "greeting", "type": "conversation", "response": "Hello"},'
            '{"id": "end", "type": "end", "response": "Bye"}]'
        )
        transitions_json = (
            '[{"from_node_id": "greeting", "transitions": [{"target": "non_existent", "intent": "confirm"}]}]'
        )
        self._write_temp_config("conversation.json", conv_json)
        self._write_temp_config("nodes.json", nodes_json)
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", transitions_json)
        with self.assertRaises(AgentConfigValidationError) as ctx:
            validate_agent_directory(self.test_dir)
        self.assertIn("does not exist in nodes.json", str(ctx.exception))

    def test_load_valid_directory(self):
        conv_json = (
            '{"agent_name": "Test Agent", "voice_id": "voice-1", '
            '"global_prompt": "Test Prompt", "start_node_id": "greeting", "default_locale": "en"}'
        )
        nodes_json = (
            '[{"id": "greeting", "type": "message", "response": {"en": "Hello", "hi": "Namaste"}},'
            '{"id": "end", "type": "end", "response": {"en": "Bye"}} ]'
        )
        transitions_json = (
            '[{"from_node_id": "greeting", "transitions": [{"target": "end", "intent": "confirm"}]}]'
        )
        self._write_temp_config("conversation.json", conv_json)
        self._write_temp_config("nodes.json", nodes_json)
        self._write_temp_config("intents.json", "[]")
        self._write_temp_config("transitions.json", transitions_json)

        schema = load_agent_directory(self.test_dir)
        self.assertEqual(schema["agent_name"], "Test Agent")
        self.assertEqual(schema["voice_id"], "voice-1")
        self.assertEqual(schema["global_prompt"], "Test Prompt")
        
        flow = schema["conversationFlow"]
        self.assertEqual(flow["start_node_id"], "greeting")
        self.assertEqual(len(flow["nodes"]), 2)
        
        greeting_node = next(n for n in flow["nodes"] if n["id"] == "greeting")
        self.assertEqual(greeting_node["response"], "Hello")
        self.assertEqual(len(greeting_node["edges"]), 1)
        self.assertEqual(greeting_node["edges"][0]["destination_node_id"], "end")
        self.assertIn("call_connected", greeting_node["intent_triggers"])

    def test_state_manager_loads_real_estate_directory(self):
        path = os.path.join("db", "agents", "real_estate")
        sm = StateManager(path)
        self.assertEqual(sm.schema["agent_name"], "Updated Real Estate Agent")
        self.assertEqual(sm.start_node_id, "node-1767592854176")
        self.assertGreater(len(sm.nodes), 0)

    def test_state_manager_loads_healthcare_directory(self):
        path = os.path.join("db", "agents", "healthcare")
        sm = StateManager(path)
        self.assertEqual(sm.schema["agent_name"], "Maya")
        self.assertEqual(sm.start_node_id, "root_greeting")
        self.assertGreater(len(sm.nodes), 0)

    def test_state_manager_loads_banking_directory(self):
        path = os.path.join("db", "agents", "banking")
        sm = StateManager(path)
        self.assertEqual(sm.schema["agent_name"], "Arjun")
        self.assertEqual(sm.start_node_id, "root_greeting")
        self.assertGreater(len(sm.nodes), 0)

    def test_resolve_schema_directory_priority(self):
        # Resolving "real_estate" or "real_estate_sales" should return directory path
        path = _resolve_schema("real_estate")
        self.assertTrue(os.path.isdir(path))
        self.assertIn("real_estate", path)

        path_sales = _resolve_schema("real_estate_sales")
        self.assertTrue(os.path.isdir(path_sales))
        self.assertIn("real_estate", path_sales)


if __name__ == "__main__":
    unittest.main()
