"""FlowSpec v2 schema helpers and validation."""

from __future__ import annotations

import uuid
from collections import deque
from copy import deepcopy
from typing import Any


class FlowSpecValidationError(ValueError):
    """Raised when a FlowSpec v2 document is not publish-safe."""


def build_flow_spec_from_agent(
    *,
    agent_id: str,
    agent_name: str,
    agent_type: str,
    script: str,
    data_fields: list[str],
    language: str = "English",
) -> dict[str, Any]:
    """Create a deterministic draft FlowSpec v2 from current agent metadata."""
    fields = [field for field in data_fields if field]
    start_id = "start"
    discovery_id = "discovery"
    confirm_id = "confirm_followup"
    fallback_id = "fallback"
    end_id = "end"

    flow = {
        "schema_version": "2.0",
        "id": f"flowv2_{uuid.uuid4().hex[:12]}",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_type": agent_type,
        "status": "draft",
        "runtime_mode": "shadow",
        "default_locale": "en",
        "supported_locales": ["en", "hi", "mr"],
        "start_node_id": start_id,
        "global_prompt": script,
        "slots": [
            {"id": field, "required": True, "source": "conversation"}
            for field in fields
        ],
        "intents": [
            {"id": "confirm", "examples": ["yes", "okay", "sure"]},
            {"id": "deny", "examples": ["no", "not interested"]},
            {"id": "unclear", "examples": ["hmm", "repeat"]},
            {"id": "provide_info", "examples": ["my budget is 50 lakh"]},
        ],
        "nodes": [
            {
                "id": start_id,
                "type": "message",
                "label": "Greeting",
                "response": {
                    "en": f"Hello, this is {agent_name}. Am I speaking with {{{{name}}}}?"
                },
                "transitions": [
                    {"intent": "confirm", "target": discovery_id},
                    {"intent": "deny", "target": end_id},
                    {"intent": "unclear", "target": fallback_id},
                ],
            },
            {
                "id": discovery_id,
                "type": "slot_collection",
                "label": "Discovery",
                "collects": fields,
                "response": {
                    "en": "Could you share a little about what you are looking for?"
                },
                "transitions": [
                    {"intent": "provide_info", "target": confirm_id},
                    {"intent": "confirm", "target": confirm_id},
                    {"intent": "unclear", "target": fallback_id},
                ],
            },
            {
                "id": fallback_id,
                "type": "fallback",
                "label": "Clarify",
                "response": {
                    "en": "Sorry, I did not catch that clearly. Could you repeat it once?"
                },
                "fallback": {"max_attempts": 2, "escalation_target": end_id},
                "transitions": [
                    {"intent": "provide_info", "target": discovery_id},
                    {"intent": "confirm", "target": discovery_id},
                    {"intent": "deny", "target": end_id},
                ],
            },
            {
                "id": confirm_id,
                "type": "message",
                "label": "Confirm Follow-up",
                "response": {
                    "en": "Thanks. I will have the team follow up with the right details."
                },
                "transitions": [
                    {"intent": "confirm", "target": end_id},
                    {"intent": "deny", "target": end_id},
                    {"intent": "unclear", "target": fallback_id},
                ],
            },
            {
                "id": end_id,
                "type": "end",
                "label": "End",
                "response": {"en": "Thank you. Have a good day."},
                "transitions": [],
            },
        ],
        "metadata": {
            "source": "agent_metadata",
            "source_language": language,
            "validation_required": True,
        },
    }
    return validate_flow_spec(flow)


def validate_flow_spec(flow: dict[str, Any]) -> dict[str, Any]:
    """Validate FlowSpec v2 and return a defensive copy."""
    if not isinstance(flow, dict):
        raise FlowSpecValidationError("flow must be an object")

    flow = deepcopy(flow)
    if flow.get("schema_version") != "2.0":
        raise FlowSpecValidationError("schema_version must be 2.0")
    if flow.get("runtime_mode") not in {"shadow", "draft", "live"}:
        raise FlowSpecValidationError("FlowSpec runtime_mode must be shadow, draft, or live")
    if flow.get("status") not in {"draft", "validated", "published"}:
        raise FlowSpecValidationError("FlowSpec status must be draft, validated, or published")

    nodes = flow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise FlowSpecValidationError("flow requires at least one node")

    node_ids = []
    node_map = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise FlowSpecValidationError("every node must be an object")
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            raise FlowSpecValidationError("every node requires an id")
        if node_id in node_map:
            raise FlowSpecValidationError(f"duplicate node id: {node_id}")
        node["id"] = node_id
        node_map[node_id] = node
        node_ids.append(node_id)
        _validate_node(node)

    start_node_id = str(flow.get("start_node_id") or "").strip()
    if start_node_id not in node_map:
        raise FlowSpecValidationError("start_node_id must reference an existing node")

    for node in nodes:
        for transition in node.get("transitions") or []:
            target = transition.get("target")
            if target not in node_map:
                raise FlowSpecValidationError(f"transition target does not exist: {target}")

    reachable = _reachable_nodes(start_node_id, node_map)
    missing = sorted(set(node_ids) - reachable)
    if missing:
        raise FlowSpecValidationError(f"unreachable nodes: {', '.join(missing)}")

    terminal_nodes = [node for node in nodes if node.get("type") == "end"]
    if not terminal_nodes:
        raise FlowSpecValidationError("flow requires at least one end node")

    slot_ids = set()
    for slot in flow.get("slots") or []:
        if not isinstance(slot, dict) or not str(slot.get("id") or "").strip():
            raise FlowSpecValidationError("every slot requires an id")
        slot_ids.add(str(slot["id"]))
    for node in nodes:
        for slot_id in node.get("collects") or []:
            if slot_id not in slot_ids:
                raise FlowSpecValidationError(f"node collects unknown slot: {slot_id}")

    flow["validation"] = {
        "status": "valid",
        "node_count": len(nodes),
        "terminal_count": len(terminal_nodes),
    }
    return flow


def _validate_node(node: dict[str, Any]) -> None:
    node_type = node.get("type")
    if node_type not in {"message", "slot_collection", "fallback", "end"}:
        raise FlowSpecValidationError(f"unsupported node type: {node_type}")

    response = node.get("response")
    if not isinstance(response, dict) or not any(str(value).strip() for value in response.values()):
        raise FlowSpecValidationError(f"node {node['id']} requires localized response text")

    transitions = node.get("transitions", [])
    if not isinstance(transitions, list):
        raise FlowSpecValidationError(f"node {node['id']} transitions must be a list")

    if node_type != "end" and not transitions:
        raise FlowSpecValidationError(f"non-terminal node {node['id']} requires transitions")

    seen_intents: set[str] = set()
    for transition in transitions:
        if not isinstance(transition, dict):
            raise FlowSpecValidationError(f"node {node['id']} has invalid transition")
        intent = str(transition.get("intent") or "").strip()
        if not intent:
            raise FlowSpecValidationError(f"node {node['id']} transition requires intent")
        if intent in seen_intents:
            raise FlowSpecValidationError(f"node {node['id']} has duplicate transition intent: {intent}")
        seen_intents.add(intent)
        if not str(transition.get("target") or "").strip():
            raise FlowSpecValidationError(f"node {node['id']} transition requires target")


def _reachable_nodes(start_node_id: str, node_map: dict[str, dict[str, Any]]) -> set[str]:
    reachable: set[str] = set()
    queue: deque[str] = deque([start_node_id])
    while queue:
        node_id = queue.popleft()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        node = node_map[node_id]
        for transition in node.get("transitions") or []:
            target = transition.get("target")
            if target in node_map and target not in reachable:
                queue.append(target)
    return reachable
