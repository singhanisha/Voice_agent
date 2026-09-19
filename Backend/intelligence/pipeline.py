"""Async-safe scaffolding for website intelligence jobs.

Live crawling remains opt-in. With scrape.worker_v1 disabled, the pipeline keeps
the original placeholder behavior. With the flag enabled by the API layer, the
pipeline runs a bounded crawler, stores page snapshot metadata, and generates
review-only FlowSpec drafts from extracted public website facts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .crawler import CrawlError, WebsiteCrawler
from .extraction import assess_website_knowledge, extract_website_knowledge
from .script_generation import build_structured_knowledge_stub, generate_draft_flow_from_knowledge
from .url_guard import SafeURL, validate_public_http_url


logger = logging.getLogger("intelligence.pipeline")


@dataclass(frozen=True)
class ScrapeLimits:
    max_pages: int = 20
    max_bytes: int = 10_000_000
    timeout_s: int = 30


class WebsiteIntelligencePipeline:
    def __init__(self, db, crawler: WebsiteCrawler | None = None):
        self.db = db
        self.crawler = crawler or WebsiteCrawler()

    async def create_job(
        self,
        *,
        client_id: str | None,
        agent_id: str | None,
        url: str,
        requested_by: str | None = None,
        limits: ScrapeLimits | None = None,
        reuse_existing: bool = True,
    ) -> dict:
        safe = validate_public_http_url(url, resolve_dns=False)
        if reuse_existing:
            existing = await self.db.get_reusable_scrape_job(
                client_id=client_id,
                agent_id=agent_id,
                url=safe.normalized_url,
            )
            if existing:
                existing["cache"] = {"reused": True, "reason": "same_agent_url"}
                logger.info("[INTEL] reused scrape job=%s domain=%s", existing.get("id"), safe.domain)
                return existing
        cfg = limits or ScrapeLimits()
        job = await self.db.create_scrape_job(
            client_id=client_id,
            agent_id=agent_id,
            url=safe.normalized_url,
            domain=safe.domain,
            requested_by=requested_by,
            limits={
                "max_pages": cfg.max_pages,
                "max_bytes": cfg.max_bytes,
                "timeout_s": cfg.timeout_s,
            },
        )
        job["cache"] = {"reused": False}
        logger.info("[INTEL] created scrape job=%s domain=%s", job.get("id"), safe.domain)
        return job

    async def create_draft_from_job(
        self,
        *,
        job_id: str,
        agent: dict,
        industry_hint: str | None = None,
        use_live_extraction: bool = False,
    ) -> dict:
        job = await self.db.get_scrape_job(job_id)
        if not job:
            raise ValueError(f"scrape job not found: {job_id}")
        
        await self.db.update_scrape_job_progress(job_id, "Creating Agent Knowledge Base")
        
        try:
            knowledge = None
            if use_live_extraction:
                latest = await self.db.get_latest_scrape_extraction(job_id)
                knowledge = latest["extraction"] if latest else None
                if not knowledge:
                    run_result = await self.run_job(job_id=job_id, industry_hint=industry_hint or agent.get("agent_type"))
                    knowledge = run_result.get("knowledge")
                    if not knowledge:
                        status = run_result.get("status") or job.get("status") or "unknown"
                        if status in {"already_running", "running", "dispatching"}:
                            raise CrawlError("Scrape job is still running. Please wait for completion before creating a draft.")
                        raise CrawlError(f"Scrape extraction is not ready yet. Current status: {status}.")
            if not knowledge:
                safe = SafeURL(
                    url=job["url"],
                    normalized_url=job["url"],
                    domain=job["domain"],
                    scheme=job["url"].split(":", 1)[0],
                )
                knowledge = build_structured_knowledge_stub(
                    url=safe.normalized_url,
                    domain=safe.domain,
                    industry_hint=industry_hint or agent.get("agent_type"),
                )
                await self.db.save_scrape_extraction(job_id, knowledge)
            if "quality" not in knowledge:
                knowledge["quality"] = assess_website_knowledge(knowledge)
            flow = generate_draft_flow_from_knowledge(
                agent_id=agent["id"],
                agent_name=agent.get("name") or "Voice Agent",
                agent_type=agent.get("agent_type") or "real_estate_sales",
                script=agent.get("script") or "",
                data_fields=agent.get("data_fields") or [],
                knowledge=knowledge,
            )
            draft = await self.db.create_generated_script_draft(
                job_id=job_id,
                client_id=job.get("client_id"),
                agent_id=agent["id"],
                status="draft",
                draft_json=flow,
                knowledge_json=knowledge,
            )
            await self.db.update_scrape_job_status(job_id, "draft_ready")
            return draft
        finally:
            await self.db.update_scrape_job_progress(job_id, None)

    async def run_job(self, *, job_id: str, industry_hint: str | None = None) -> dict:
        job = await self.db.get_scrape_job(job_id)
        if not job:
            raise ValueError(f"scrape job not found: {job_id}")
        if job.get("status") == "cancelled":
            return {"job_id": job_id, "status": "cancelled", "cancelled": True}
        if job.get("status") in {"completed", "draft_ready"}:
            extraction = await self.db.get_latest_scrape_extraction(job_id)
            if extraction and extraction.get("extraction"):
                return {
                    "job_id": job_id,
                    "status": job.get("status"),
                    "skipped": True,
                    "knowledge": extraction["extraction"],
                }
        limits = job.get("limits") or {}
        started_job = await self.db.mark_scrape_job_running(job_id)
        if started_job and started_job.get("status") == "cancelled":
            return {"job_id": job_id, "status": "cancelled", "cancelled": True}
        if not started_job:
            raise ValueError(f"scrape job not found: {job_id}")
        if not started_job.get("_started"):
            status = started_job.get("status") or "unknown"
            return {
                "job_id": job_id,
                "status": "already_running" if status == "running" else status,
                "skipped": True,
            }

        try:
            await self.db.update_scrape_job_progress(job_id, "Validating URL")
            validate_public_http_url(job["url"], resolve_dns=False)

            await self.db.update_scrape_job_progress(job_id, "Crawling Website")
            pages = await self.crawler.crawl(
                job["url"],
                max_pages=int(limits.get("max_pages") or ScrapeLimits.max_pages),
                max_bytes=int(limits.get("max_bytes") or ScrapeLimits.max_bytes),
                timeout_s=int(limits.get("timeout_s") or ScrapeLimits.timeout_s),
            )
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            
            await self.db.update_scrape_job_progress(job_id, "Extracting Content")
            for page in pages:
                if await self._is_cancelled(job_id):
                    return {"job_id": job_id, "status": "cancelled", "cancelled": True}
                await self.db.save_page_snapshot(
                    job_id=job_id,
                    url=page.url,
                    content_hash=page.content_hash,
                    content_type=page.content_type,
                    storage_path=None,
                )
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            
            knowledge = await extract_website_knowledge(
                pages,
                source_url=job["url"],
                domain=job["domain"],
                industry_hint=industry_hint,
                job_id=job_id,
                db=self.db,
            )
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            extraction = await self.db.save_scrape_extraction(job_id, knowledge)
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            
            await self.db.update_scrape_job_progress(job_id, None)
            await self.db.update_scrape_job_status(job_id, "completed")
            return {
                "job_id": job_id,
                "status": "completed",
                "pages_crawled": len(pages),
                "extraction_id": extraction["id"],
                "knowledge": knowledge,
            }
        except CrawlError as exc:
            await self.db.update_scrape_job_progress(job_id, None)
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            await self.db.update_scrape_job_status(job_id, "failed", error=str(exc))
            raise
        except Exception as exc:
            await self.db.update_scrape_job_progress(job_id, None)
            if await self._is_cancelled(job_id):
                return {"job_id": job_id, "status": "cancelled", "cancelled": True}
            await self.db.update_scrape_job_status(job_id, "failed", error=str(exc))
            raise

    async def _is_cancelled(self, job_id: str) -> bool:
        current = await self.db.get_scrape_job(job_id)
        return bool(current and current.get("status") == "cancelled")


# ── LANGGRAPH VOICE AGENT WORKFLOW ────────────────────────────────────────────

import logging
from typing import Any, Dict, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END

# Import crawler & managers
from intelligence.crawler import SuncityCrawler

class ConversationState(TypedDict):
    messages: List[BaseMessage]
    extracted_slots: Dict[str, Any]  # intent, budget, bhk, timeline
    current_node: str               # GREETING, DISCOVERY, QUALIFICATION, LIVE_SEARCH, OBJECTION_HANDLING, SCHEDULING, CLOSING, OPEN_DOMAIN
    pending_filler_action: Optional[str]
    rag_context: Optional[str]
    retry_count: int
    user_input: str
    language: str                   # Active session language: "en", "hi", "hinglish", "mr"
    pending_return_node: Optional[str]   # Node to resume after OPEN_DOMAIN detour
    last_intent_confidence: Optional[float]  # Confidence from last intent extraction

# Crawler instance shared across pipeline
crawler_instance = SuncityCrawler()

def route_next_node(state: ConversationState) -> str:
    """
    Evaluates where to route the dialogue based on the current state.
    Provides strict exit fencing and deepening triggers.
    """
    curr = state.get("current_node", "GREETING")
    slots = state.get("extracted_slots", {})
    
    # Strict Exit Fencing: Cannot go to CLOSING unless slots are collected
    if curr == "CLOSING":
        has_critical_slots = (
            slots.get("intent") and 
            slots.get("budget") and 
            slots.get("bhk")
        )
        if not has_critical_slots:
            logger.info("Fencing CLOSING node: missing critical slots. Re-routing to QUALIFICATION.")
            return "QUALIFICATION"
        return "CLOSING"
        
    return curr

# Node handler placeholders (delegated to state_manager implementation)
async def node_greeting_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_greeting
    return await handle_greeting(state)

async def node_discovery_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_discovery
    return await handle_discovery(state)

async def node_qualification_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_qualification
    return await handle_qualification(state)

async def node_live_search_wrapper(state: ConversationState) -> ConversationState:
    """
    Handles live web search with connection-pooled crawler and latency-masking.
    Step A: Yields a verbal filler immediately.
    Step B: Fetches live data and populates rag_context.
    """
    from llm.state_manager import handle_live_search
    return await handle_live_search(state, crawler_instance)

async def node_objection_handling_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_objection_handling
    return await handle_objection_handling(state)

async def node_open_domain_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_open_domain_query
    return await handle_open_domain_query(state)

async def node_scheduling_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_scheduling
    return await handle_scheduling(state)

async def node_closing_wrapper(state: ConversationState) -> ConversationState:
    from llm.state_manager import handle_closing
    return await handle_closing(state)

# Compile LangGraph
workflow = StateGraph(ConversationState)

# Add all conversational nodes
workflow.add_node("GREETING", node_greeting_wrapper)
workflow.add_node("DISCOVERY", node_discovery_wrapper)
workflow.add_node("QUALIFICATION", node_qualification_wrapper)
workflow.add_node("LIVE_SEARCH", node_live_search_wrapper)
workflow.add_node("OBJECTION_HANDLING", node_objection_handling_wrapper)
workflow.add_node("SCHEDULING", node_scheduling_wrapper)
workflow.add_node("CLOSING", node_closing_wrapper)
workflow.add_node("OPEN_DOMAIN", node_open_domain_wrapper)

# Routing Logic
workflow.add_conditional_edges(
    START,
    route_next_node,
    {
        "GREETING": "GREETING",
        "DISCOVERY": "DISCOVERY",
        "QUALIFICATION": "QUALIFICATION",
        "LIVE_SEARCH": "LIVE_SEARCH",
        "OBJECTION_HANDLING": "OBJECTION_HANDLING",
        "SCHEDULING": "SCHEDULING",
        "CLOSING": "CLOSING",
        "OPEN_DOMAIN": "OPEN_DOMAIN",
    }
)

# Connect everything back to END
for node_name in ["GREETING", "DISCOVERY", "QUALIFICATION", "LIVE_SEARCH", "OBJECTION_HANDLING", "SCHEDULING", "CLOSING", "OPEN_DOMAIN"]:
    workflow.add_edge(node_name, END)

langgraph_engine = workflow.compile()
