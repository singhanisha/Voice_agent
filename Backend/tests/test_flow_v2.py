import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from flows.v2 import (
    FlowShadowRunner,
    FlowSpecValidationError,
    build_flow_preview,
    build_flow_spec_from_agent,
    validate_flow_spec,
)


class FlowV2Test(unittest.TestCase):
    def test_build_and_validate_agent_flow_spec_draft_shadow_only(self):
        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested", "callback"],
            language="English",
        )

        self.assertEqual(flow["schema_version"], "2.0")
        self.assertEqual(flow["status"], "draft")
        self.assertEqual(flow["runtime_mode"], "shadow")
        self.assertEqual(flow["validation"]["status"], "valid")
        self.assertEqual(flow["slots"][0]["id"], "interested")

    def test_validator_allows_live_publish_and_rejects_broken_graph(self):
        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested"],
        )
        flow["status"] = "published"
        flow["runtime_mode"] = "live"
        self.assertEqual(validate_flow_spec(flow)["runtime_mode"], "live")

        flow["status"] = "draft"
        flow["runtime_mode"] = "shadow"
        flow["nodes"][0]["transitions"][0]["target"] = "missing-node"
        with self.assertRaises(FlowSpecValidationError):
            validate_flow_spec(flow)

    def test_shadow_runner_is_deterministic_and_never_calls_v1_runtime(self):
        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested"],
        )
        runner = FlowShadowRunner(flow)

        first = runner.step("confirm")
        second = runner.step("provide_info", slots={"interested": "yes"})
        third = runner.step("confirm")

        self.assertEqual(first.previous_node_id, "start")
        self.assertEqual(first.node_id, "discovery")
        self.assertEqual(second.node_id, "confirm_followup")
        self.assertEqual(third.node_id, "end")
        self.assertTrue(third.is_terminal)
        self.assertEqual(len(third.trace), 3)

    def test_flow_preview_exposes_safe_read_only_paths(self):
        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested", "callback"],
        )

        preview = build_flow_preview(flow)

        self.assertEqual(preview["runtime_mode"], "shadow")
        self.assertEqual(preview["status"], "draft")
        self.assertEqual(preview["stats"]["node_count"], 5)
        self.assertGreaterEqual(preview["stats"]["fallback_path_count"], 1)
        self.assertGreaterEqual(preview["stats"]["objection_path_count"], 1)
        self.assertEqual(preview["conversation_preview"][0]["node_id"], "start")
        self.assertIn("Confirm", preview["conversation_preview"][0]["expected_user_responses"])
        self.assertEqual(preview["conversation_preview"][0]["fallback_node_id"], "fallback")
        self.assertEqual(preview["conversation_preview"][-1]["node_id"], "end")
        self.assertFalse(preview["audit"]["review_required"])
        self.assertIsNone(preview["audit"]["website_intelligence"])

    def test_main_flow_v2_shadow_writer_is_flag_gated(self):
        import main

        agent_data = {
            "name": "Rani",
            "agent_type": "finance",
            "script": "Qualify the caller.",
            "data_fields": ["interested"],
            "language": "English",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = str(Path(tmpdir) / "agent.json")
            with patch.dict(os.environ, {"FEATURE_FLOW_V2_SHADOW": "false"}, clear=False):
                self.assertIsNone(
                    main._write_agent_flow_v2_shadow("agent-1", schema_path, agent_data, None)
                )

            with patch.dict(os.environ, {"FEATURE_FLOW_V2_SHADOW": "true"}, clear=False):
                result = main._write_agent_flow_v2_shadow("agent-1", schema_path, agent_data, None)

            self.assertIsNotNone(result)
            artifact_path = Path(result["artifact_path"])
            self.assertTrue(artifact_path.exists())
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["runtime_mode"], "shadow")
            self.assertEqual(artifact["status"], "draft")

    def test_main_flow_preview_endpoint_is_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("/api/agents/{agent_id}/flow-preview", source)
        self.assertIn('feature_flags.is_enabled("flow.visualization")', source)

    def test_flow_v2_draft_update_preserves_shadow_runtime(self):
        import main

        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested"],
        )
        update = main.FlowDraftUpdate(
            nodes=[
                main.FlowNodeDraftUpdate(
                    id="start",
                    label="Permission Check",
                    response_en="Hi, do you have a minute to talk?",
                    transitions=[
                        main.FlowTransitionDraftUpdate(intent="confirm", label="Has time", target="discovery"),
                        main.FlowTransitionDraftUpdate(intent="deny", label="No time", target="end"),
                        main.FlowTransitionDraftUpdate(intent="unclear", label="Unclear", target="fallback"),
                    ],
                )
            ]
        )

        draft = main._apply_flow_v2_draft_updates(flow, update)

        self.assertEqual(draft["runtime_mode"], "shadow")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["nodes"][0]["label"], "Permission Check")
        self.assertEqual(draft["nodes"][0]["response"]["en"], "Hi, do you have a minute to talk?")
        self.assertEqual(draft["nodes"][0]["transitions"][0]["label"], "Has time")
        self.assertEqual(draft["validation"]["status"], "valid")

    def test_flow_v2_draft_update_can_add_reachable_node(self):
        import main

        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested"],
        )
        update = main.FlowDraftUpdate(
            nodes=[
                main.FlowNodeDraftUpdate(
                    id="start",
                    transitions=[
                        main.FlowTransitionDraftUpdate(intent="confirm", label="Has time", target="custom_pitch"),
                        main.FlowTransitionDraftUpdate(intent="deny", label="No time", target="end"),
                        main.FlowTransitionDraftUpdate(intent="unclear", label="Unclear", target="fallback"),
                    ],
                ),
                main.FlowNodeDraftUpdate(
                    id="custom_pitch",
                    type="message",
                    label="Custom Pitch",
                    response_en="Here is the short value pitch.",
                    transitions=[
                        main.FlowTransitionDraftUpdate(intent="confirm", label="Interested", target="discovery"),
                        main.FlowTransitionDraftUpdate(intent="deny", label="Not now", target="end"),
                    ],
                ),
            ]
        )

        draft = main._apply_flow_v2_draft_updates(flow, update)

        node_ids = {node["id"] for node in draft["nodes"]}
        custom = next(node for node in draft["nodes"] if node["id"] == "custom_pitch")
        self.assertIn("custom_pitch", node_ids)
        self.assertEqual(custom["type"], "message")
        self.assertEqual(custom["response"]["en"], "Here is the short value pitch.")
        self.assertEqual(draft["nodes"][0]["transitions"][0]["target"], "custom_pitch")
        self.assertEqual(draft["runtime_mode"], "shadow")
        self.assertEqual(draft["validation"]["status"], "valid")

    def test_flow_v2_draft_update_route_can_publish_live_when_flag_enabled(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("/api/agents/{agent_id}/flow-v2-draft", source)
        self.assertIn('feature_flags.is_enabled("flow.visualization")', source)
        self.assertIn('feature_flags.is_enabled("flow.v2_shadow")', source)
        self.assertIn('feature_flags.is_enabled("flow.v2_live")', source)
        self.assertIn("_apply_flow_v2_draft_updates", source)
        self.assertIn("_write_flow_v2_draft_artifact", source)
        self.assertIn("_publish_flow_v2_to_runtime", source)
        self.assertIn('"runtime_mode"] = "shadow"', source)
        self.assertIn('"status"] = "draft"', source)
        self.assertIn('"runtime_live_changed": runtime_live_changed', source)

    def test_flow_v2_to_runtime_conversion_preserves_nodes_edges_and_slots(self):
        import main

        flow = build_flow_spec_from_agent(
            agent_id="agent-1",
            agent_name="Rani",
            agent_type="finance",
            script="Qualify the caller.",
            data_fields=["interested"],
        )

        runtime_flow = main._flow_v2_to_runtime_conversation_flow(flow)

        self.assertEqual(runtime_flow["schema_version"], "2.0")
        self.assertEqual(runtime_flow["start_node_id"], "start")
        start = runtime_flow["nodes"][0]
        discovery = next(node for node in runtime_flow["nodes"] if node["id"] == "discovery")
        fallback = next(node for node in runtime_flow["nodes"] if node["id"] == "fallback")
        self.assertEqual(start["intent_triggers"], ["call_connected"])
        self.assertEqual(start["edges"][0]["destination_node_id"], "discovery")
        self.assertEqual(discovery["collects"], ["interested"])
        self.assertEqual(fallback["type"], "fallback")


if __name__ == "__main__":
    unittest.main()
