"""Loader and validator for split JSON-driven voice agent configs."""

from __future__ import annotations

import json
import os
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class AgentConfigValidationError(ValueError):
    """Raised when there is a validation issue with the agent configuration files."""
    pass


def validate_agent_directory(dir_path: str) -> None:
    """Validate that the agent configuration directory contains all required files and is well-formed.
    
    Raises AgentConfigValidationError if validation fails.
    """
    if not os.path.isdir(dir_path):
        raise AgentConfigValidationError(f"Path is not a directory: {dir_path}")

    required_files = ["conversation.json", "nodes.json", "intents.json", "transitions.json"]
    for file_name in required_files:
        file_path = os.path.join(dir_path, file_name)
        if not os.path.isfile(file_path):
            raise AgentConfigValidationError(f"Missing required configuration file: {file_path}")

    # Load and parse conversation.json
    try:
        with open(os.path.join(dir_path, "conversation.json"), "r", encoding="utf-8") as f:
            conversation = json.load(f)
    except Exception as e:
        raise AgentConfigValidationError(f"Invalid JSON in conversation.json: {e}")

    # Validate conversation.json fields
    for field in ["agent_name", "voice_id", "global_prompt", "start_node_id"]:
        if field not in conversation or not str(conversation[field]).strip():
            raise AgentConfigValidationError(f"conversation.json is missing required field '{field}'")

    # Load and parse nodes.json
    try:
        with open(os.path.join(dir_path, "nodes.json"), "r", encoding="utf-8") as f:
            nodes = json.load(f)
    except Exception as e:
        raise AgentConfigValidationError(f"Invalid JSON in nodes.json: {e}")

    if not isinstance(nodes, list):
        raise AgentConfigValidationError("nodes.json must contain a list of nodes")

    if not nodes:
        raise AgentConfigValidationError("nodes.json cannot be empty")

    node_ids = set()
    has_end_node = False
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise AgentConfigValidationError(f"Node at index {i} in nodes.json must be an object")
        node_id = node.get("id")
        if not node_id or not str(node_id).strip():
            raise AgentConfigValidationError(f"Node at index {i} is missing a valid 'id' field")
        if node_id in node_ids:
            raise AgentConfigValidationError(f"Duplicate node id '{node_id}' in nodes.json")
        node_ids.add(node_id)

        node_type = node.get("type")
        if node_type not in {"conversation", "slot_collection", "fallback", "fallback_guided", "function", "end", "message"}:
            raise AgentConfigValidationError(f"Unsupported type '{node_type}' in node '{node_id}'")

        if node_type == "end":
            has_end_node = True

        # Validation of localized responses or instruction prompt text
        response = node.get("response")
        instruction = node.get("instruction")
        
        # In combined schema, response could be a string or a dict. Let's support both.
        if not response and not instruction:
            raise AgentConfigValidationError(f"Node '{node_id}' must define either 'response' or 'instruction'")

    # Ensure start node exists
    start_node_id = conversation["start_node_id"]
    if start_node_id not in node_ids:
        raise AgentConfigValidationError(
            f"start_node_id '{start_node_id}' in conversation.json does not exist in nodes.json"
        )

    # Ensure at least one end node exists
    if not has_end_node:
        raise AgentConfigValidationError("nodes.json must contain at least one node of type 'end'")

    # Load and parse intents.json
    try:
        with open(os.path.join(dir_path, "intents.json"), "r", encoding="utf-8") as f:
            intents = json.load(f)
    except Exception as e:
        raise AgentConfigValidationError(f"Invalid JSON in intents.json: {e}")

    if not isinstance(intents, list):
        raise AgentConfigValidationError("intents.json must contain a list of intent mappings")

    # Load and parse transitions.json
    try:
        with open(os.path.join(dir_path, "transitions.json"), "r", encoding="utf-8") as f:
            transitions = json.load(f)
    except Exception as e:
        raise AgentConfigValidationError(f"Invalid JSON in transitions.json: {e}")

    if not isinstance(transitions, list):
        raise AgentConfigValidationError("transitions.json must contain a list of state transitions")

    # Validate transition targets
    for i, entry in enumerate(transitions):
        if not isinstance(entry, dict):
            raise AgentConfigValidationError(f"Transition entry at index {i} must be an object")
        from_node_id = entry.get("from_node_id")
        if not from_node_id:
            raise AgentConfigValidationError(f"Transition entry at index {i} is missing 'from_node_id'")
        if from_node_id not in node_ids:
            raise AgentConfigValidationError(
                f"Transition from_node_id '{from_node_id}' does not refer to a node in nodes.json"
            )

        node_transitions = entry.get("transitions")
        if not isinstance(node_transitions, list):
            raise AgentConfigValidationError(
                f"Transition entry for node '{from_node_id}' must have a list of 'transitions'"
            )

        for j, t in enumerate(node_transitions):
            if not isinstance(t, dict):
                raise AgentConfigValidationError(
                    f"Transition index {j} for node '{from_node_id}' must be an object"
                )
            target = t.get("target")
            if not target:
                raise AgentConfigValidationError(
                    f"Transition index {j} for node '{from_node_id}' is missing a 'target' field"
                )
            if target not in node_ids:
                raise AgentConfigValidationError(
                    f"Transition target '{target}' for node '{from_node_id}' does not exist in nodes.json"
                )


def load_agent_directory(dir_path: str) -> Dict[str, Any]:
    """Load and compile the split JSON files in a directory into a standard, unified agent schema.
    
    Performs full validation before loading.
    """
    validate_agent_directory(dir_path)

    # Load all raw configurations
    with open(os.path.join(dir_path, "conversation.json"), "r", encoding="utf-8") as f:
        conversation = json.load(f)
    with open(os.path.join(dir_path, "nodes.json"), "r", encoding="utf-8") as f:
        nodes = json.load(f)
    with open(os.path.join(dir_path, "intents.json"), "r", encoding="utf-8") as f:
        intents = json.load(f)
    with open(os.path.join(dir_path, "transitions.json"), "r", encoding="utf-8") as f:
        transitions_list = json.load(f)

    # Build transition map: from_node_id -> list of transitions
    transition_map = {entry["from_node_id"]: entry["transitions"] for entry in transitions_list}

    assembled_nodes = []
    for node in nodes:
        node_id = node["id"]
        compiled_node = dict(node)

        # Build legacy edges list from transitions.json if present
        edges = []
        if node_id in transition_map:
            for i, t in enumerate(transition_map[node_id]):
                intent = t.get("intent")
                target = t.get("target")
                condition = t.get("condition", intent)
                
                edge = {
                    "id": t.get("id") or f"edge_{node_id}_{target}_{intent}_{i}",
                    "condition": condition,
                    "transition_condition": {
                        "type": "prompt",
                        "prompt": condition
                    },
                    "destination_node_id": target
                }
                edges.append(edge)
        compiled_node["edges"] = edges

        # Auto-inject intent_triggers if this is the start node and none are present
        if node_id == conversation["start_node_id"] and not compiled_node.get("intent_triggers"):
            compiled_node["intent_triggers"] = ["call_connected"]

        # Ensure type is converted to legacy format where applicable
        # (v1 uses 'conversation' for message / input-handling steps)
        if compiled_node["type"] == "message":
            compiled_node["type"] = "conversation"

        # Localized response conversion for legacy state_manager compatibility
        # If response is a dict (e.g. {"en": "...", "hi": "..."}), extract default locale string
        response_data = compiled_node.get("response")
        if isinstance(response_data, dict):
            default_locale = conversation.get("default_locale", "en")
            # Select default or fall back to first key
            legacy_response = response_data.get(default_locale) or next(iter(response_data.values()), "")
            compiled_node["response"] = legacy_response
            # Keep full translations in localized_responses if needed
            compiled_node["localized_responses"] = response_data
        
        assembled_nodes.append(compiled_node)

    # Compile the final consolidated schema dictionary
    legacy_schema = {
        "agent_id": conversation.get("conversation_flow_id", "flow_default"),
        "agent_name": conversation["agent_name"],
        "voice_id": conversation["voice_id"],
        "language": conversation.get("default_locale", "English"),
        "global_prompt": conversation["global_prompt"],
        "conversationFlow": {
            "conversation_flow_id": conversation.get("conversation_flow_id", "flow_default"),
            "global_prompt": conversation["global_prompt"],
            "start_node_id": conversation["start_node_id"],
            "nodes": assembled_nodes,
            "tools": conversation.get("tools", []),
            "default_locale": conversation.get("default_locale", "en"),
            "supported_locales": conversation.get("supported_locales", ["en"]),
            "intents": intents
        }
    }

    # Pass along any extra properties defined at the root of conversation.json
    for key, val in conversation.items():
        if key not in legacy_schema and key != "conversationFlow":
            legacy_schema[key] = val

    return legacy_schema
