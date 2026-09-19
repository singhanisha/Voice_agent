"""Read-only FlowSpec v2 preview helpers.

The preview contract is intentionally separate from live traversal. It exists
for audit/debug UI only and never mutates a conversation session.
"""

from __future__ import annotations

from typing import Any

from .spec import validate_flow_spec


FALLBACK_INTENTS = {"unclear", "fallback", "repeat", "unknown"}
OBJECTION_INTENTS = {"deny", "not_interested", "objection", "price_objection", "busy"}
PRIMARY_INTENT_ORDER = ("confirm", "provide_info")


def build_flow_preview(flow: dict[str, Any]) -> dict[str, Any]:
    validated = validate_flow_spec(flow)
    nodes = validated["nodes"]
    node_map = {node["id"]: node for node in nodes}
    default_locale = validated.get("default_locale") or "en"

    graph_nodes = []
    graph_edges = []
    fallback_paths = []
    objection_paths = []
    for index, node in enumerate(nodes):
        transitions = node.get("transitions") or []
        preview_node = {
            "id": node["id"],
            "label": node.get("label") or node["id"],
            "type": node.get("type"),
            "sequence": index,
            "agent_says": _response_text(node, default_locale),
            "collects": list(node.get("collects") or []),
            "expected_user_responses": [
                _transition_label(transition)
                for transition in transitions
                if transition.get("intent") not in FALLBACK_INTENTS
            ],
            "fallback_target_id": _first_target_for(transitions, FALLBACK_INTENTS),
            "objection_target_id": _first_target_for(transitions, OBJECTION_INTENTS),
            "is_terminal": node.get("type") == "end",
        }
        graph_nodes.append(preview_node)

        for transition in transitions:
            edge = {
                "from": node["id"],
                "to": transition.get("target"),
                "intent": transition.get("intent"),
                "label": _transition_label(transition),
            }
            graph_edges.append(edge)
            if edge["intent"] in FALLBACK_INTENTS:
                fallback_paths.append(_path_summary(edge, node_map))
            if edge["intent"] in OBJECTION_INTENTS:
                objection_paths.append(_path_summary(edge, node_map))

    primary_path = _primary_path(validated["start_node_id"], node_map)
    return {
        "schema_version": "2.0",
        "flow_id": validated.get("id"),
        "agent_id": validated.get("agent_id"),
        "status": validated.get("status"),
        "runtime_mode": validated.get("runtime_mode"),
        "start_node_id": validated.get("start_node_id"),
        "stats": {
            "node_count": len(graph_nodes),
            "edge_count": len(graph_edges),
            "fallback_path_count": len(fallback_paths),
            "objection_path_count": len(objection_paths),
            "slot_count": len(validated.get("slots") or []),
        },
        "graph": {
            "nodes": graph_nodes,
            "edges": graph_edges,
        },
        "conversation_preview": [
            _conversation_step(node_id, node_map, default_locale)
            for node_id in primary_path
        ],
        "fallback_paths": fallback_paths,
        "objection_paths": objection_paths,
        "validation": validated.get("validation", {}),
        "audit": _audit_summary(validated),
    }


def _conversation_step(node_id: str, node_map: dict[str, dict[str, Any]], default_locale: str) -> dict[str, Any]:
    node = node_map[node_id]
    transitions = node.get("transitions") or []
    return {
        "node_id": node_id,
        "label": node.get("label") or node_id,
        "type": node.get("type"),
        "agent_says": _response_text(node, default_locale),
        "collects": list(node.get("collects") or []),
        "expected_user_responses": [
            _transition_label(transition)
            for transition in transitions
            if transition.get("intent") not in FALLBACK_INTENTS
        ],
        "next_node_id": _choose_primary_target(transitions),
        "fallback_node_id": _first_target_for(transitions, FALLBACK_INTENTS),
        "objection_node_id": _first_target_for(transitions, OBJECTION_INTENTS),
        "is_terminal": node.get("type") == "end",
    }


def _primary_path(start_node_id: str, node_map: dict[str, dict[str, Any]]) -> list[str]:
    path = []
    seen = set()
    current = start_node_id
    limit = len(node_map) + 1
    while current and current in node_map and current not in seen and len(path) < limit:
        path.append(current)
        seen.add(current)
        if node_map[current].get("type") == "end":
            break
        current = _choose_primary_target(node_map[current].get("transitions") or [])
    return path


def _choose_primary_target(transitions: list[dict[str, Any]]) -> str | None:
    for intent in PRIMARY_INTENT_ORDER:
        target = _first_target_for(transitions, {intent})
        if target:
            return target
    for transition in transitions:
        if transition.get("intent") not in FALLBACK_INTENTS | OBJECTION_INTENTS:
            return transition.get("target")
    return transitions[0].get("target") if transitions else None


def _first_target_for(transitions: list[dict[str, Any]], intents: set[str]) -> str | None:
    for transition in transitions:
        if transition.get("intent") in intents:
            return transition.get("target")
    return None


def _path_summary(edge: dict[str, Any], node_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    target = node_map.get(edge["to"], {})
    source = node_map.get(edge["from"], {})
    return {
        "from_node_id": edge["from"],
        "from_label": source.get("label") or edge["from"],
        "intent": edge["intent"],
        "target_node_id": edge["to"],
        "target_label": target.get("label") or edge["to"],
    }


def _transition_label(transition: dict[str, Any]) -> str:
    label = transition.get("label") or transition.get("intent") or ""
    return str(label).replace("_", " ").strip().title()


def _response_text(node: dict[str, Any], default_locale: str) -> str:
    response = node.get("response") or {}
    if not isinstance(response, dict):
        return ""
    text = response.get(default_locale)
    if not text:
        text = next((value for value in response.values() if str(value).strip()), "")
    return str(text).strip()


def _audit_summary(flow: dict[str, Any]) -> dict[str, Any]:
    metadata = flow.get("metadata") if isinstance(flow.get("metadata"), dict) else {}
    website = metadata.get("website_intelligence") if isinstance(metadata.get("website_intelligence"), dict) else None
    return {
        "source": metadata.get("source"),
        "source_url": metadata.get("source_url"),
        "industry": metadata.get("industry"),
        "review_required": bool(metadata.get("review_required") or website),
        "live_runtime_unchanged": bool(metadata.get("live_runtime_unchanged")),
        "website_intelligence": _website_audit_summary(website) if website else None,
    }


def _website_audit_summary(website: dict[str, Any]) -> dict[str, Any]:
    quality = website.get("quality") if isinstance(website.get("quality"), dict) else {}
    inventory = website.get("content_inventory") if isinstance(website.get("content_inventory"), dict) else {}
    checklist = website.get("review_checklist") if isinstance(website.get("review_checklist"), list) else []
    return {
        "domain": website.get("domain"),
        "source_url": website.get("source_url"),
        "industry": website.get("industry"),
        "advisory_only": bool(website.get("advisory_only", True)),
        "auto_publish": bool(website.get("auto_publish", False)),
        "quality": {
            "score": quality.get("score", 0),
            "level": quality.get("level", "unknown"),
            "ready_for_review": bool(quality.get("ready_for_review")),
            "warnings": list(quality.get("warnings") or [])[:6],
        },
        "evidence_urls": list(website.get("evidence_urls") or [])[:8],
        "content_inventory": {
            "page_types": inventory.get("page_types") or {},
            "noise_filtered": bool(inventory.get("noise_filtered")),
        },
        "review_checklist": [
            {
                "key": item.get("key"),
                "label": item.get("label") or item.get("key"),
                "passed": bool(item.get("passed")),
            }
            for item in checklist[:8]
            if isinstance(item, dict)
        ],
    }
