"""Draft-only website intelligence to FlowSpec generation."""

from __future__ import annotations

from typing import Any

from flows.v2 import build_flow_spec_from_agent, validate_flow_spec
from .extraction import assess_website_knowledge


def build_structured_knowledge_stub(*, url: str, domain: str, industry_hint: str | None = None) -> dict[str, Any]:
    """Create a conservative extraction placeholder without hallucinating."""
    knowledge = {
        "source_url": url,
        "domain": domain,
        "industry": industry_hint or "unknown",
        "company": {"name": domain, "evidence": [url]},
        "products_or_services": [],
        "value_propositions": [],
        "qualification_questions": [],
        "objections": [],
        "faqs": [],
        "limitations": [
            "No website pages have been fetched in this scaffold phase.",
            "Human review is required before publishing any generated workflow.",
        ],
        "complete_knowledge_summary_markdown": (
            f"# {domain} - Knowledge Base (Placeholder)\n\n"
            f"This is a fallback placeholder knowledge summary for {domain}.\n"
            "The live crawl has not completed or returned text content yet.\n\n"
            "## Company Overview\nDetails not fetched yet.\n\n"
            "## Mission & Vision\nMission statement pending.\n\n"
            "## Products\nProduct list pending.\n\n"
            "## Services\nService list pending.\n\n"
            "## Features\nFeatures list pending.\n\n"
            "## Industries Served\nTarget sectors pending.\n\n"
            "## Customer Segments\nCustomer segments pending.\n\n"
            "## Pricing\nPricing information is not publicly available.\n\n"
            "## FAQs\nFrequently asked questions list pending.\n\n"
            "## Technologies\nTechnology stack list pending.\n\n"
            "## Integrations\nIntegrations list pending.\n\n"
            "## Security & Compliance\nSecurity credentials pending.\n\n"
            "## Contact Information\nContact information pending.\n\n"
            "## Careers\nCareers description pending.\n\n"
            "## Blogs & News\nBlogs list pending.\n\n"
            "## Policies & Compliance\nPolicies list pending.\n\n"
            "## Support\nSupport contact channels pending.\n\n"
            "## Company Highlights & Key Facts\nHighlights pending.\n\n"
            "## Competitive Advantages\nCompetitive advantages pending.\n\n"
            "## Frequently Discussed Topics\nKey discussion areas pending.\n\n"
            "## Semantic Keywords & Synonyms\nsupport, pricing, details\n\n"
            f"## Relationships & Use Cases\nProvides automation solutions for {domain}."
        ),
    }
    knowledge["quality"] = assess_website_knowledge(knowledge)
    return knowledge


def generate_draft_flow_from_knowledge(
    *,
    agent_id: str,
    agent_name: str,
    agent_type: str,
    script: str,
    data_fields: list[str],
    knowledge: dict[str, Any],
) -> dict[str, Any]:
    """Generate a validated FlowSpec v2 draft from structured knowledge."""
    website_facts = _format_knowledge_for_script(knowledge)
    enriched_script = (
        f"{script.strip()}\n\n"
        f"Website intelligence source: {knowledge.get('source_url')}.\n"
        f"{website_facts}\n"
        "Use only verified website facts and keep unsupported claims out of the call."
    ).strip()
    flow = build_flow_spec_from_agent(
        agent_id=agent_id,
        agent_name=agent_name,
        agent_type=agent_type,
        script=enriched_script,
        data_fields=data_fields,
    )
    flow["metadata"]["source"] = "website_intelligence"
    flow["metadata"]["source_url"] = knowledge.get("source_url")
    flow["metadata"]["industry"] = knowledge.get("industry", "unknown")
    flow["metadata"]["review_required"] = True
    flow["metadata"]["website_intelligence"] = _build_generation_audit_metadata(knowledge)
    flow["status"] = "draft"
    flow["runtime_mode"] = "shadow"
    return validate_flow_spec(flow)


def _format_knowledge_for_script(knowledge: dict[str, Any]) -> str:
    summary_md = knowledge.get("complete_knowledge_summary_markdown")
    if summary_md:
        return f"Complete Company Knowledge Summary:\n{summary_md}"

    company = (knowledge.get("company") or {}).get("name") or knowledge.get("domain") or "the business"
    products = [
        item.get("name")
        for item in (knowledge.get("products_or_services") or [])
        if item.get("name")
    ][:5]
    value_props = [
        item.get("text")
        for item in (knowledge.get("value_propositions") or [])
        if item.get("text")
    ][:3]
    questions = [str(item) for item in (knowledge.get("qualification_questions") or [])][:5]
    objections = [
        f"{item.get('intent')}: {item.get('guidance')}"
        for item in (knowledge.get("objections") or [])
        if item.get("intent") and item.get("guidance")
    ][:5]
    inventory = knowledge.get("content_inventory") or {}
    page_types = [
        page_type.replace("_", " ")
        for page_type, count in (inventory.get("page_types") or {}).items()
        if count and page_type not in {"legal", "careers"}
    ][:6]

    lines = [f"Verified business name: {company}."]
    if page_types:
        lines.append("Relevant website sections found: " + "; ".join(page_types) + ".")
    if products:
        lines.append("Verified products/services: " + "; ".join(products) + ".")
    if value_props:
        lines.append("Website-backed value points: " + "; ".join(value_props) + ".")
    if questions:
        lines.append("Qualification questions to collect: " + "; ".join(questions) + ".")
    if objections:
        lines.append("Objection guidance: " + "; ".join(objections) + ".")
    return "\n".join(lines)


def _build_generation_audit_metadata(knowledge: dict[str, Any]) -> dict[str, Any]:
    quality = knowledge.get("quality") or assess_website_knowledge(knowledge)
    inventory = knowledge.get("content_inventory") or {}
    return {
        "domain": knowledge.get("domain"),
        "source_url": knowledge.get("source_url"),
        "industry": knowledge.get("industry", "unknown"),
        "advisory_only": True,
        "auto_publish": False,
        "quality": {
            "score": quality.get("score", 0),
            "level": quality.get("level", "insufficient"),
            "ready_for_review": bool(quality.get("ready_for_review")),
            "warnings": list(quality.get("warnings") or [])[:6],
        },
        "evidence_urls": _source_evidence_from_knowledge(knowledge),
        "content_inventory": {
            "page_types": inventory.get("page_types") or {},
            "has_services": bool(inventory.get("has_services")),
            "has_faq": bool(inventory.get("has_faq")),
            "has_contact": bool(inventory.get("has_contact")),
            "noise_filtered": bool(inventory.get("noise_filtered")),
        },
        "review_checklist": _build_review_checklist(knowledge, quality),
        "limitations": list(knowledge.get("limitations") or [])[:6],
    }


def _source_evidence_from_knowledge(knowledge: dict[str, Any]) -> list[str]:
    values: list[str] = []

    def add(items: Any) -> None:
        for item in items or []:
            if isinstance(item, str) and item.strip():
                values.append(item.strip())

    if knowledge.get("source_url"):
        values.append(str(knowledge["source_url"]).strip())
    add((knowledge.get("company") or {}).get("evidence"))
    for item in knowledge.get("products_or_services") or []:
        add(item.get("evidence") if isinstance(item, dict) else [])
    for item in knowledge.get("value_propositions") or []:
        add(item.get("evidence") if isinstance(item, dict) else [])
    for item in knowledge.get("faqs") or []:
        add(item.get("evidence") if isinstance(item, dict) else [])
    for page in knowledge.get("pages_crawled") or []:
        if isinstance(page, dict) and page.get("url"):
            values.append(str(page["url"]).strip())
    return list(dict.fromkeys(value for value in values if value))[:12]


def _build_review_checklist(knowledge: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_urls = _source_evidence_from_knowledge(knowledge)
    inventory = knowledge.get("content_inventory") or {}
    checks = [
        {
            "key": "human_review_required",
            "label": "Human review required before publishing",
            "passed": True,
        },
        {
            "key": "quality_ready_for_review",
            "label": "Extraction quality is ready for review",
            "passed": bool(quality.get("ready_for_review")),
        },
        {
            "key": "source_evidence_present",
            "label": "Website evidence URLs are attached",
            "passed": bool(evidence_urls),
        },
        {
            "key": "services_detected",
            "label": "Products or services were detected",
            "passed": bool(knowledge.get("products_or_services")),
        },
        {
            "key": "conversation_guidance_present",
            "label": "Qualification or objection guidance is present",
            "passed": bool(knowledge.get("qualification_questions") or knowledge.get("objections")),
        },
        {
            "key": "noise_filtered",
            "label": "Crawler noise filtering was applied",
            "passed": bool(inventory.get("noise_filtered")),
        },
    ]
    return checks
