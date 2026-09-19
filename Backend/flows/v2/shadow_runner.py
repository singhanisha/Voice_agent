"""Deterministic FlowSpec v2 shadow traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spec import validate_flow_spec


@dataclass
class ShadowTurnResult:
    previous_node_id: str
    node_id: str
    intent: str
    response: str
    is_terminal: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)


class FlowShadowRunner:
    """Run deterministic shadow transitions without affecting v1 runtime."""

    def __init__(self, flow: dict[str, Any], locale: str = "en"):
        self.flow = validate_flow_spec(flow)
        self.locale = locale or self.flow.get("default_locale", "en")
        self.nodes = {node["id"]: node for node in self.flow["nodes"]}
        self.current_node_id = self.flow["start_node_id"]
        self.trace: list[dict[str, Any]] = []
        self.fallback_counts: dict[str, int] = {}

    def current_node(self) -> dict[str, Any]:
        return self.nodes[self.current_node_id]

    def current_response(self) -> str:
        return _localized_response(self.current_node(), self.locale, self.flow.get("default_locale", "en"))

    def step(self, intent: str, slots: dict[str, Any] | None = None) -> ShadowTurnResult:
        intent = (intent or "unclear").strip()
        previous_id = self.current_node_id
        current = self.current_node()
        transition = _select_transition(current, intent) or _select_transition(current, "unclear")

        if transition:
            target_id = transition["target"]
        else:
            target_id = current.get("fallback", {}).get("escalation_target") or previous_id

        next_node = self.nodes[target_id]
        if next_node.get("type") == "fallback":
            self.fallback_counts[previous_id] = self.fallback_counts.get(previous_id, 0) + 1
            max_attempts = int(next_node.get("fallback", {}).get("max_attempts", 2))
            if self.fallback_counts[previous_id] > max_attempts:
                target_id = next_node.get("fallback", {}).get("escalation_target", target_id)
                next_node = self.nodes[target_id]
        else:
            self.fallback_counts[previous_id] = 0

        self.current_node_id = target_id
        trace_item = {
            "from": previous_id,
            "to": target_id,
            "intent": intent,
            "slots": slots or {},
        }
        self.trace.append(trace_item)
        return ShadowTurnResult(
            previous_node_id=previous_id,
            node_id=target_id,
            intent=intent,
            response=_localized_response(next_node, self.locale, self.flow.get("default_locale", "en")),
            is_terminal=next_node.get("type") == "end",
            trace=list(self.trace),
        )


def _select_transition(node: dict[str, Any], intent: str) -> dict[str, Any] | None:
    for transition in node.get("transitions") or []:
        if transition.get("intent") == intent:
            return transition
    return None


def _localized_response(node: dict[str, Any], locale: str, default_locale: str) -> str:
    responses = node.get("response") or {}
    return str(responses.get(locale) or responses.get(default_locale) or next(iter(responses.values()), ""))

