"""
agent_flow_generator.py

Generates a full, rich conversation-flow JSON matching the schema
used by real_estate.json / StateManager from a plain-language
description of what the agent should do.

This replaces relying on StateManager.template_new_agent() (a fixed
2-node skeleton) for agents created "from a prompt".

The generated flow is structurally validated before being returned.
If validation fails, the LLM is given a fresh, compact retry request
containing only the original prompt and validation errors. This avoids
growing the request with previous full JSON responses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FlowGenerationError(Exception):
    """Raised when the LLM could not produce a structurally valid flow."""


# =============================================================================
# Schema documentation + few-shot example given to the LLM
# =============================================================================

_SCHEMA_INSTRUCTIONS = """
You are a conversation-flow architect. You design phone-call scripts for an
AI voice agent as a JSON state machine.

You will be told what kind of call the agent should make, and you must output
ONLY a single JSON object — no prose, no markdown fences — matching the
schema below EXACTLY.

## Top-level shape

{
  "agent_name": "<short human name for the agent>",
  "conversationFlow": {
    "start_node_id": "<id of the first node>",
    "global_prompt": "<one paragraph: who the agent is, their tone, and the overall goal of the call>",
    "nodes": [ ... ]
  }
}

## Node shape

Every node is an object with:

  - "id": a short unique slug, e.g. "node-greeting",
    "node-ask-experience"

  - "name": short human label, e.g. "Ask Experience"

  - "type": one of:
      - "conversation"
      - "fallback"
      - "end"

      "conversation":
        A normal turn where the agent speaks and listens.

      "fallback":
        Used when the user's answer was unclear on a previous node.
        It politely re-asks for the same information in different words.

      "end":
        Terminates the call.
        MUST have "edges": [].

  - "instruction":
      {
        "type": "prompt",
        "text": "<one sentence telling the LLM what THIS node's job is,
                 and reminding it not to re-ask anything the user already said>"
      }

  - "response":
      "<the exact line the agent says on this node, in English>"

  - "collects" (optional):
      A slot name (string) or list of slot names this node is trying
      to gather.

      Example:
        "current_ctc"

      or:
        ["callback_date", "callback_time"]

      Omit if the node collects nothing.

  - "edges":
      A list of possible ways the conversation can move on.

      Each edge MUST have:

      {
        "condition": "<short label>",
        "id": "edge-<slug>",
        "transition_condition": {
          "type": "prompt",
          "prompt": "<one clear sentence describing exactly when this edge should fire>"
        },
        "destination_node_id": "<id of another node in this same flow>"
      }

## CRITICAL NODE/EDGE RULES

1. EVERY destination_node_id MUST exactly match an id of a node that
   exists in the SAME nodes array.

2. NEVER reference a node that has not been defined.

3. Before outputting JSON, mentally create a list of ALL node ids and
   verify EVERY destination_node_id against that list.

4. start_node_id MUST exactly match an existing node id.

5. Every "end" node MUST have:
      "edges": []

6. Every non-end node MUST have at least one edge.

7. If you want to use a goodbye node, you MUST explicitly define it,
   for example:

      {
        "id": "node-goodbye",
        "name": "Goodbye",
        "type": "end",
        ...
        "edges": []
      }

8. If you want to use a fallback node, you MUST explicitly define it
   in the nodes array before referencing it from an edge.

9. Do not create edges pointing to imaginary nodes such as
   "node-goodbye", "node-fallback-followup", "node-next-step", etc.
   unless those exact nodes are included in the nodes array.

10. Every node id must be unique.

## Conversation rules

11. Always include a not-interested / rejection path from the first
    1-2 nodes that leads to a defined end node.

12. Always include at least one fallback node for the most important
    piece of information that may be difficult to capture correctly
    from speech.

13. The fallback node should loop back to the appropriate information
    collection node.

14. Build a realistic conversation, not just a form.

15. Ask one question at a time.

16. Keep every response short — one or two natural sentences.

17. Produce between 10 and 18 nodes total.

18. Summarize and confirm important collected information before ending.

19. Propose a reasonable next step when appropriate.

20. Output valid JSON only.

21. No comments.

22. No trailing commas.

23. No markdown fences.

24. No text before or after the JSON object.

## IMPORTANT SELF-CHECK BEFORE OUTPUT

Before returning the JSON:

- List all node ids mentally.
- Check start_node_id exists.
- Check every destination_node_id exists.
- Check every non-end node has at least one edge.
- Check every end node has edges: [].
- Check every fallback destination exists.
- Check there are no references to undefined nodes.
- Check node ids are unique.

## Worked example

{
  "agent_name": "Real Estate Sales - Neha",
  "conversationFlow": {
    "start_node_id": "node-greeting",
    "global_prompt": "You are Neha, a warm and efficient property consultant calling a lead who enquired online. Keep responses short and conversational and never re-ask something the caller already told you.",
    "nodes": [
      {
        "id": "node-greeting",
        "name": "Smart Greeting",
        "type": "conversation",
        "instruction": {
          "type": "prompt",
          "text": "Greet naturally, introduce yourself briefly, and confirm you're speaking with the right person without re-asking known information."
        },
        "response": "Hey, this is Neha from the Real Estate team. Can I speak with you?",
        "edges": [
          {
            "condition": "user confirms identity",
            "id": "edge-confirm-identity",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User confirms they are the correct person and can talk."
            },
            "destination_node_id": "node-ask-intent"
          },
          {
            "condition": "wrong person or busy",
            "id": "edge-wrong-busy",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User is the wrong person or says they are busy right now."
            },
            "destination_node_id": "node-goodbye"
          }
        ]
      },
      {
        "id": "node-ask-intent",
        "name": "Ask Intent",
        "type": "conversation",
        "instruction": {
          "type": "prompt",
          "text": "Find out whether the user is interested in the service without repeating information already provided."
        },
        "response": "Are you currently looking for a property?",
        "collects": "intent",
        "edges": [
          {
            "condition": "interested",
            "id": "edge-interested",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User confirms they are interested in a property."
            },
            "destination_node_id": "node-ask-budget"
          },
          {
            "condition": "not interested",
            "id": "edge-not-interested",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User says they are not interested or do not want to continue."
            },
            "destination_node_id": "node-goodbye"
          }
        ]
      },
      {
        "id": "node-ask-budget",
        "name": "Ask Budget",
        "type": "conversation",
        "instruction": {
          "type": "prompt",
          "text": "Collect the user's approximate property budget and accept natural spoken amounts."
        },
        "response": "What budget range should I keep in mind?",
        "collects": "budget",
        "edges": [
          {
            "condition": "budget provided",
            "id": "edge-budget-clear",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User gives a clear approximate property budget."
            },
            "destination_node_id": "node-confirm"
          },
          {
            "condition": "budget unclear",
            "id": "edge-budget-unclear",
            "transition_condition": {
              "type": "prompt",
              "prompt": "The user's budget answer is unclear or cannot be understood."
            },
            "destination_node_id": "node-fallback-budget"
          }
        ]
      },
      {
        "id": "node-fallback-budget",
        "name": "Fallback Budget",
        "type": "fallback",
        "instruction": {
          "type": "prompt",
          "text": "Politely ask the user to repeat their approximate budget in a clearer way."
        },
        "response": "Sorry, I didn't quite catch that. Could you give me the approximate budget again?",
        "edges": [
          {
            "condition": "budget clarified",
            "id": "edge-budget-fixed",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User now provides a clear approximate budget."
            },
            "destination_node_id": "node-confirm"
          }
        ]
      },
      {
        "id": "node-confirm",
        "name": "Confirm Details",
        "type": "conversation",
        "instruction": {
          "type": "prompt",
          "text": "Briefly summarize the information collected and ask whether it is correct."
        },
        "response": "Thanks. I have your requirements noted. Does everything sound correct?",
        "edges": [
          {
            "condition": "confirmed",
            "id": "edge-confirmed",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User confirms the collected information is correct."
            },
            "destination_node_id": "node-goodbye"
          },
          {
            "condition": "needs correction",
            "id": "edge-correction",
            "transition_condition": {
              "type": "prompt",
              "prompt": "User says one or more collected details need to be changed."
            },
            "destination_node_id": "node-ask-budget"
          }
        ]
      },
      {
        "id": "node-goodbye",
        "name": "Goodbye",
        "type": "end",
        "instruction": {
          "type": "prompt",
          "text": "End the conversation politely and warmly."
        },
        "response": "Thanks for your time. Have a great day!",
        "edges": []
      }
    ]
  }
}

Now generate a flow for the request that follows.

Output ONLY the JSON object, nothing else.
"""


# =============================================================================
# Build user prompt
# =============================================================================

def _build_user_prompt(
    prompt: str,
    agent_name: Optional[str],
    agent_type: Optional[str],
    data_fields: Optional[list[str]],
) -> str:
    parts = [
        f"Agent description:\n{prompt.strip()}"
    ]

    if agent_name:
        parts.append(
            f"\nUse this as the agent's name: {agent_name}"
        )

    if agent_type:
        parts.append(
            f"\nAgent category/domain: {agent_type}"
        )

    if data_fields:
        parts.append(
            "\nThe flow MUST end up collecting all of these pieces of "
            "information at some point. Collect one per node, in a sensible "
            "order, and never ask for two of these in the same node: "
            + ", ".join(data_fields)
        )

    return "\n".join(parts)


# =============================================================================
# Structural validation
# =============================================================================

def validate_flow(flow_json: dict[str, Any]) -> list[str]:
    """
    Returns a list of validation error strings.

    Empty list = structurally valid flow.

    Every error is written so it can be fed back to the LLM as a compact
    repair instruction.
    """

    errors: list[str] = []

    cf = flow_json.get("conversationFlow")

    if not isinstance(cf, dict):
        return ["Missing or invalid 'conversationFlow' object."]

    nodes = cf.get("nodes")

    if not isinstance(nodes, list) or not nodes:
        return [
            "'conversationFlow.nodes' must be a non-empty list."
        ]

    node_ids: set[str] = set()

    # -------------------------------------------------------------------------
    # Validate node definitions
    # -------------------------------------------------------------------------

    for i, node in enumerate(nodes):

        if not isinstance(node, dict):
            errors.append(
                f"nodes[{i}] is not an object."
            )
            continue

        nid = node.get("id")

        if not nid or not isinstance(nid, str):
            errors.append(
                f"nodes[{i}] is missing a valid string 'id'."
            )
            continue

        if nid in node_ids:
            errors.append(
                f"Duplicate node id '{nid}' — every node id must be unique."
            )

        node_ids.add(nid)

        if not node.get("name"):
            errors.append(
                f"Node '{nid}' is missing 'name'."
            )

        if not node.get("response"):
            errors.append(
                f"Node '{nid}' is missing 'response'."
            )

        if node.get("type") not in {
            "conversation",
            "fallback",
            "end",
        }:
            errors.append(
                f"Node '{nid}' has invalid 'type' "
                f"(must be conversation, fallback, or end)."
            )

        if (
            "edges" not in node
            or not isinstance(node.get("edges"), list)
        ):
            errors.append(
                f"Node '{nid}' is missing an 'edges' list."
            )

    # -------------------------------------------------------------------------
    # Validate start node
    # -------------------------------------------------------------------------

    start_id = cf.get("start_node_id")

    if not start_id:
        errors.append(
            "'conversationFlow.start_node_id' is missing."
        )

    elif start_id not in node_ids:
        errors.append(
            f"'start_node_id' ({start_id!r}) "
            f"does not match any node id."
        )

    # -------------------------------------------------------------------------
    # Validate edges
    # -------------------------------------------------------------------------

    for node in nodes:

        if not isinstance(node, dict):
            continue

        nid = node.get("id", "?")
        ntype = node.get("type")
        edges = node.get("edges") or []

        # End nodes must not have outgoing edges.
        if ntype == "end" and edges:
            errors.append(
                f"Node '{nid}' is type 'end' but has edges — "
                f"'end' nodes must have edges: []."
            )

        # Non-end nodes must have at least one edge.
        if ntype != "end" and not edges:
            errors.append(
                f"Node '{nid}' is type '{ntype}' but has no edges — "
                f"every non-end node needs at least one way out."
            )

        for j, edge in enumerate(edges):

            if not isinstance(edge, dict):
                errors.append(
                    f"Node '{nid}' edges[{j}] is not an object."
                )
                continue

            dest = edge.get("destination_node_id")

            if not dest:
                errors.append(
                    f"Node '{nid}' edges[{j}] is missing "
                    f"'destination_node_id'."
                )

            elif dest not in node_ids:
                errors.append(
                    f"Node '{nid}' edges[{j}] points to "
                    f"destination_node_id {dest!r}, which does not "
                    f"match any node id you defined."
                )

            if not edge.get("condition"):
                errors.append(
                    f"Node '{nid}' edges[{j}] is missing 'condition'."
                )

    # -------------------------------------------------------------------------
    # Reachability check
    # -------------------------------------------------------------------------

    if not errors and start_id:

        reachable = {start_id}
        frontier = [start_id]

        by_id = {
            n["id"]: n
            for n in nodes
            if isinstance(n, dict) and n.get("id")
        }

        while frontier:

            current = frontier.pop()

            for edge in by_id.get(current, {}).get("edges", []) or []:

                dest = edge.get("destination_node_id")

                if dest and dest not in reachable:
                    reachable.add(dest)
                    frontier.append(dest)

        unreachable = node_ids - reachable

        if unreachable:
            errors.append(
                "These nodes are never reachable from start_node_id "
                "and must be connected by an edge from somewhere, "
                "or removed: "
                + ", ".join(sorted(unreachable))
            )

    return errors


# =============================================================================
# Generation entry point
# =============================================================================

async def generate_agent_flow(
    prompt: str,
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
    data_fields: Optional[list[str]] = None,
    max_attempts: int = 3,
) -> tuple[dict[str, Any], list[str]]:
    """
    Generate a validated conversation flow from a natural-language prompt.

    Returns:
        (flow_json, warnings)

    Raises:
        FlowGenerationError:
            If no valid flow could be produced after max_attempts.
    """

    from .llm import _async_call_groq_api

    user_prompt = _build_user_prompt(
        prompt,
        agent_name,
        agent_type,
        data_fields,
    )

    # Base request used for every attempt.
    base_messages = [
        {
            "role": "system",
            "content": _SCHEMA_INSTRUCTIONS,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    last_errors: list[str] = []
    last_raw = ""

    # =========================================================================
    # Retry loop
    # =========================================================================

    for attempt in range(1, max_attempts + 1):

        # ---------------------------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT append previous full LLM responses to the next request.
        #
        # That caused the request to grow from a normal size to 11k+ tokens,
        # which triggered the Groq 413 TPM error seen in the logs.
        #
        # Each retry gets a fresh compact request.
        # ---------------------------------------------------------------------

        if attempt == 1:

            request_messages = base_messages

        else:

            repair_instruction = (
                "Your previous generated flow failed structural validation.\n\n"
                "Fix ALL of the following errors and generate the COMPLETE "
                "flow again.\n\n"
                + "\n".join(
                    f"- {error}"
                    for error in last_errors
                )
                + "\n\n"
                "CRITICAL REQUIREMENT:\n"
                "Every destination_node_id MUST exactly match the id of "
                "a node that exists in the nodes array.\n"
                "If you reference node-goodbye, node-fallback, or any "
                "other node, that node MUST be explicitly defined in "
                "the nodes array.\n"
                "Do not reference undefined nodes.\n\n"
                "Output ONLY the complete JSON object."
            )

            request_messages = [
                {
                    "role": "system",
                    "content": _SCHEMA_INSTRUCTIONS,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
                {
                    "role": "user",
                    "content": repair_instruction,
                },
            ]

        logger.info(
            "[AGENT FLOW GEN] Starting generation attempt %d/%d",
            attempt,
            max_attempts,
        )

        try:

            raw = await _async_call_groq_api(
                request_messages,
                max_tokens=3500,
                temperature=0.2,
                response_format={"type": "json_object"},
            )

        except Exception as exc:

            logger.exception(
                "[AGENT FLOW GEN] LLM request failed on attempt %d/%d: %s",
                attempt,
                max_attempts,
                exc,
            )

            last_errors = [
                f"LLM request failed: {str(exc)}"
            ]

            continue

        last_raw = raw or ""

        # ---------------------------------------------------------------------
        # Empty response
        # ---------------------------------------------------------------------

        if not raw:

            logger.warning(
                "[AGENT FLOW GEN] Empty response on attempt %d/%d",
                attempt,
                max_attempts,
            )

            last_errors = [
                "The LLM returned an empty response. "
                "Output the complete JSON object."
            ]

            continue

        # ---------------------------------------------------------------------
        # Parse JSON
        # ---------------------------------------------------------------------

        try:

            flow_json = json.loads(
                _strip_code_fences(raw)
            )

        except json.JSONDecodeError as exc:

            logger.warning(
                "[AGENT FLOW GEN] Invalid JSON on attempt %d/%d: %s",
                attempt,
                max_attempts,
                exc,
            )

            last_errors = [
                f"Invalid JSON: {exc}. "
                "Output only one complete valid JSON object."
            ]

            continue

        # ---------------------------------------------------------------------
        # Structural validation
        # ---------------------------------------------------------------------

        errors = validate_flow(flow_json)

        if not errors:

            logger.info(
                "[AGENT FLOW GEN] Valid flow produced on attempt %d/%d "
                "(%d nodes)",
                attempt,
                max_attempts,
                len(
                    flow_json
                    .get("conversationFlow", {})
                    .get("nodes", [])
                ),
            )

            return flow_json, []

        # ---------------------------------------------------------------------
        # Validation failed
        # ---------------------------------------------------------------------

        last_errors = errors

        logger.warning(
            "[AGENT FLOW GEN] Validation failed on attempt %d/%d: %s",
            attempt,
            max_attempts,
            "; ".join(errors),
        )

        # IMPORTANT:
        # We intentionally do NOT append the previous full raw JSON to the
        # next request. This keeps retries small enough for Groq TPM limits.

    # =========================================================================
    # All attempts failed
    # =========================================================================

    raise FlowGenerationError(
        f"Could not produce a valid flow after {max_attempts} attempts. "
        f"Last errors: "
        f"{last_errors or ['LLM returned empty/invalid output']}. "
        f"Last raw output (truncated): {last_raw[:500]!r}"
    )


# =============================================================================
# Helpers
# =============================================================================

def _strip_code_fences(text: str) -> str:
    """
    The model occasionally wraps JSON in:
        ```json
        {...}
        ```

    Remove those fences before json.loads().
    """

    text = text.strip()

    match = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        text,
        re.DOTALL,
    )

    return match.group(1) if match else text