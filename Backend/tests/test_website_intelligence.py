import asyncio
import ipaddress
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend-next"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import db_manager
from intelligence.crawler import CrawlError, CrawledPage
from intelligence.extraction import assess_website_knowledge
from intelligence.pipeline import WebsiteIntelligencePipeline
from intelligence.url_guard import URLSafetyError, validate_public_http_url


class FakeCrawler:
    def __init__(self):
        self.calls = []

    async def crawl(self, url: str, *, max_pages: int, max_bytes: int, timeout_s: int):
        self.calls.append({
            "url": url,
            "max_pages": max_pages,
            "max_bytes": max_bytes,
            "timeout_s": timeout_s,
        })
        return [
            CrawledPage(
                url=url,
                content_type="text/html; charset=utf-8",
                content_hash="hash-1",
                body="""
                <html>
                  <head>
                    <title>Acme Realty - Trusted Property Advisors</title>
                    <meta name="description" content="Personalized property advisory for buyers.">
                  </head>
                  <body>
                    <h1>Property Advisory Services</h1>
                    <p>Trusted experts help buyers compare apartments, villas, and investment properties.</p>
                    <h2>Which city are you considering?</h2>
                  </body>
                </html>
                """,
            )
        ]


class WebsiteIntelligenceTest(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_manager.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        db_manager.DB_PATH = Path(self._tmp.name) / "platform.db"
        db_manager._init_schema()
        self.manager = db_manager.DatabaseManager()
        asyncio.run(self.manager.create_client("client-1", {
            "name": "Client One",
            "email": "client1@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.create_agent("agent-1", {
            "name": "Rani",
            "voice": "voice",
            "language": "English",
            "max_duration": 300,
            "provider": "demo",
            "stt_provider": "groq",
            "tts_provider": "edge",
            "cartesia_voice_id": None,
            "assigned_email": "client1@example.com",
            "agent_type": "finance",
            "script": "Qualify callers safely.",
            "data_fields": ["interested", "callback"],
            "schema_path": "db/agents/agent-1.json",
            "client_id": "client-1",
        }))

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_url_guard_rejects_unsafe_urls(self):
        with self.assertRaises(URLSafetyError):
            validate_public_http_url("file:///etc/passwd", resolve_dns=False)
        with self.assertRaises(URLSafetyError):
            validate_public_http_url("http://localhost", resolve_dns=False)
        with self.assertRaises(URLSafetyError):
            validate_public_http_url("http://127.0.0.1", resolve_dns=False)
        with self.assertRaises(URLSafetyError):
            validate_public_http_url("http://169.254.169.254/latest/meta-data", resolve_dns=False)

    def test_url_guard_accepts_public_https_url_without_dns_for_jobs(self):
        safe = validate_public_http_url("https://example.com/products#section", resolve_dns=False)

        self.assertEqual(safe.domain, "example.com")
        self.assertEqual(safe.normalized_url, "https://example.com/products")

    def test_dns_resolution_rejects_private_resolved_address(self):
        fake_info = [(None, None, None, None, ("10.0.0.5", 0))]
        with patch("socket.getaddrinfo", return_value=fake_info):
            with self.assertRaises(URLSafetyError):
                validate_public_http_url("https://example.com", resolve_dns=True)

    def test_pipeline_creates_tenant_scoped_job_and_draft_only_flow(self):
        pipeline = WebsiteIntelligencePipeline(self.manager)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))
        agent = asyncio.run(self.manager.get_agent("agent-1"))
        draft = asyncio.run(pipeline.create_draft_from_job(job_id=job["id"], agent=agent))
        refreshed_job = asyncio.run(self.manager.get_scrape_job(job["id"]))
        fetched_draft = asyncio.run(self.manager.get_generated_script_draft(draft["id"]))
        draft_history = asyncio.run(self.manager.list_generated_script_drafts(agent_id="agent-1", client_id="client-1"))
        draft_for_job = asyncio.run(self.manager.get_generated_script_draft_for_job(job_id=job["id"], agent_id="agent-1"))
        job_history = asyncio.run(self.manager.list_scrape_jobs(client_id="client-1"))
        job_diagnostics = asyncio.run(self.manager.get_scrape_job_diagnostics(job["id"]))
        reused_job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))

        self.assertEqual(job["client_id"], "client-1")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(refreshed_job["status"], "draft_ready")
        self.assertEqual(reused_job["id"], job["id"])
        self.assertTrue(reused_job["cache"]["reused"])
        self.assertEqual(reused_job["status"], "draft_ready")
        self.assertEqual(draft["status"], "draft")
        self.assertIsNone(draft["published_at"])
        self.assertEqual(fetched_draft["draft"]["schema_version"], "2.0")
        self.assertEqual(fetched_draft["draft"]["runtime_mode"], "shadow")
        self.assertTrue(fetched_draft["draft"]["metadata"]["review_required"])
        audit_metadata = fetched_draft["draft"]["metadata"]["website_intelligence"]
        self.assertFalse(audit_metadata["auto_publish"])
        self.assertTrue(audit_metadata["advisory_only"])
        self.assertEqual(audit_metadata["quality"]["level"], "insufficient")
        self.assertTrue(audit_metadata["review_checklist"][0]["passed"])
        self.assertEqual(fetched_draft["knowledge"]["quality"]["level"], "insufficient")
        self.assertTrue(fetched_draft["knowledge"]["quality"]["advisory_only"])
        self.assertEqual([item["id"] for item in draft_history], [draft["id"]])
        self.assertEqual(draft_for_job["id"], draft["id"])
        self.assertEqual([item["id"] for item in job_history], [job["id"]])
        self.assertEqual(job_history[0]["diagnostics"]["draft_count"], 1)
        self.assertEqual(job_diagnostics["diagnostics"]["draft_count"], 1)
        self.assertEqual(job_diagnostics["drafts"][0]["id"], draft["id"])
        from main import _build_generated_script_review_policy, _prepare_generated_script_flow_for_agent
        from flows.v2 import build_flow_preview, validate_flow_spec

        prepared_flow = _prepare_generated_script_flow_for_agent(fetched_draft, agent)
        preview = build_flow_preview(validate_flow_spec(prepared_flow))
        self.assertEqual(prepared_flow["runtime_mode"], "shadow")
        self.assertEqual(prepared_flow["status"], "draft")
        self.assertTrue(prepared_flow["metadata"]["live_runtime_unchanged"])
        self.assertEqual(preview["runtime_mode"], "shadow")
        self.assertTrue(preview["audit"]["review_required"])
        self.assertTrue(preview["audit"]["live_runtime_unchanged"])
        self.assertEqual(preview["audit"]["website_intelligence"]["quality"]["level"], "insufficient")
        self.assertFalse(preview["audit"]["website_intelligence"]["auto_publish"])
        self.assertTrue(preview["audit"]["website_intelligence"]["review_checklist"][0]["passed"])
        with patch.dict(os.environ, {"FEATURE_SCRAPE_REVIEW_GATE_SHADOW": "true", "FEATURE_FLOW_V2_LIVE": "false"}, clear=False):
            review_policy = _build_generated_script_review_policy(
                fetched_draft,
                prepared_flow,
                review_acknowledged=False,
            )
        self.assertTrue(review_policy["enabled"])
        self.assertTrue(review_policy["would_block_if_enforced"])
        self.assertTrue(review_policy["can_save_flow_draft"])
        self.assertIn("human_review_not_acknowledged", review_policy["blockers"])
        self.assertIn("quality_not_ready_for_review", review_policy["blockers"])
        reviewed = asyncio.run(self.manager.mark_generated_script_draft_reviewed(
            draft["id"],
            reviewed_by="admin@example.com",
            review_notes="approved for flow draft",
        ))
        self.assertEqual(reviewed["status"], "flow_draft_saved")
        self.assertIsNotNone(reviewed["reviewed_at"])
        self.assertEqual(reviewed["reviewed_by"], "admin@example.com")
        self.assertEqual(reviewed["review_notes"], "approved for flow draft")

    def test_worker_v1_runs_crawler_and_reuses_extraction_for_draft(self):
        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))
        run_result = asyncio.run(pipeline.run_job(job_id=job["id"], industry_hint="real_estate"))
        refreshed_job = asyncio.run(self.manager.get_scrape_job(job["id"]))
        snapshots = asyncio.run(self.manager.list_page_snapshots(job["id"]))
        latest = asyncio.run(self.manager.get_latest_scrape_extraction(job["id"]))

        self.assertEqual(refreshed_job["status"], "completed")
        self.assertEqual(run_result["pages_crawled"], 1)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(latest["extraction"]["industry"], "real_estate")
        self.assertEqual(latest["extraction"]["company"]["name"], "Acme Realty")
        self.assertEqual(latest["extraction"]["products_or_services"][0]["name"], "Property Advisory Services")
        self.assertEqual(latest["extraction"]["pages_crawled"][0]["page_type"], "services")
        self.assertIn("value_proposition", latest["extraction"]["pages_crawled"][0]["signals"])
        self.assertTrue(latest["extraction"]["content_inventory"]["has_services"])
        self.assertTrue(latest["extraction"]["content_inventory"]["noise_filtered"])
        self.assertEqual(latest["extraction"]["quality"]["level"], "high")
        self.assertTrue(latest["extraction"]["quality"]["ready_for_review"])

        agent = asyncio.run(self.manager.get_agent("agent-1"))
        draft = asyncio.run(pipeline.create_draft_from_job(
            job_id=job["id"],
            agent=agent,
            use_live_extraction=True,
        ))
        fetched_draft = asyncio.run(self.manager.get_generated_script_draft(draft["id"]))

        self.assertEqual(len(crawler.calls), 1)
        self.assertEqual(fetched_draft["knowledge"]["company"]["name"], "Acme Realty")
        self.assertTrue(fetched_draft["knowledge"]["content_inventory"]["has_services"])
        self.assertEqual(fetched_draft["draft"]["runtime_mode"], "shadow")
        self.assertTrue(fetched_draft["draft"]["metadata"]["review_required"])
        audit_metadata = fetched_draft["draft"]["metadata"]["website_intelligence"]
        self.assertEqual(audit_metadata["quality"]["level"], "high")
        self.assertIn("https://example.com/services", audit_metadata["evidence_urls"])
        self.assertTrue(audit_metadata["content_inventory"]["has_services"])
        self.assertTrue(any(item["key"] == "source_evidence_present" and item["passed"] for item in audit_metadata["review_checklist"]))
        self.assertEqual(fetched_draft["knowledge"]["quality"]["level"], "high")
        from main import _build_generated_script_review_policy

        with patch.dict(os.environ, {"FEATURE_SCRAPE_REVIEW_GATE_SHADOW": "true"}, clear=False):
            review_policy = _build_generated_script_review_policy(
                fetched_draft,
                fetched_draft["draft"],
                review_acknowledged=True,
            )
        self.assertTrue(review_policy["enabled"])
        self.assertFalse(review_policy["would_block_if_enforced"])
        self.assertEqual(review_policy["blockers"], [])

    def test_live_qa_readiness_uses_real_domain_evidence_without_running_runtime(self):
        from main import _build_website_live_qa_readiness

        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        agent = asyncio.run(self.manager.get_agent("agent-1"))
        for url in (
            "https://acmeproperties.com/services",
            "https://bright-homes.com/services",
            "https://clear-title.com/services",
        ):
            job = asyncio.run(pipeline.create_job(
                client_id="client-1",
                agent_id="agent-1",
                url=url,
                requested_by="admin@example.com",
            ))
            asyncio.run(pipeline.run_job(job_id=job["id"], industry_hint="real_estate"))
            asyncio.run(pipeline.create_draft_from_job(
                job_id=job["id"],
                agent=agent,
                use_live_extraction=True,
            ))

        asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))

        with patch.dict(os.environ, {"FEATURE_SCRAPE_WORKER_V1": "true"}, clear=False):
            readiness = asyncio.run(_build_website_live_qa_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["summary"]["production_domains"], 3)
        self.assertEqual(readiness["summary"]["placeholder_jobs_ignored"], 1)
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["runtime_live_changed"])

    def test_generated_draft_qa_readiness_validates_reviewed_flow_artifact(self):
        import main
        from flows.v2 import validate_flow_spec

        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        agent = asyncio.run(self.manager.get_agent("agent-1"))
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://acmeproperties.com/services",
            requested_by="admin@example.com",
        ))
        asyncio.run(pipeline.run_job(job_id=job["id"], industry_hint="real_estate"))
        draft = asyncio.run(pipeline.create_draft_from_job(
            job_id=job["id"],
            agent=agent,
            use_live_extraction=True,
        ))
        fetched_draft = asyncio.run(self.manager.get_generated_script_draft(draft["id"]))
        flow = validate_flow_spec(main._prepare_generated_script_flow_for_agent(fetched_draft, agent))
        agents_dir = Path(self._tmp.name) / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = agents_dir / "agent-1.saved.flow.v2.json"
        artifact_path.write_text(json.dumps(flow), encoding="utf-8")
        with patch.object(main, "AGENTS_DIR", str(agents_dir)):
            flow_version = asyncio.run(self.manager.create_agent_flow_version(
                agent["id"],
                client_id="client-1",
                schema_version="2.0",
                status="draft",
                runtime_mode="shadow",
                artifact_path=str(artifact_path),
                validation=flow.get("validation", {}),
            ))
            asyncio.run(self.manager.mark_generated_script_draft_reviewed(
                draft["id"],
                reviewed_by="admin@example.com",
                review_notes="qa reviewed",
                flow_version_id=flow_version["id"],
            ))
            with patch.dict(os.environ, {
                "FEATURE_FLOW_VISUALIZATION": "true",
                "FEATURE_FLOW_V2_SHADOW": "true",
            }, clear=False):
                readiness = asyncio.run(main._build_generated_draft_qa_readiness(client_id="client-1"))

        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["ready_for_production_push"])
        self.assertEqual(readiness["summary"]["generated_drafts"], 1)
        self.assertEqual(readiness["summary"]["reviewed_saved_drafts"], 1)
        self.assertEqual(readiness["summary"]["valid_flow_artifacts"], 1)
        self.assertEqual(readiness["blockers"], [])
        self.assertFalse(readiness["runtime_live_changed"])

    def test_cancelled_scrape_job_does_not_run_worker(self):
        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))

        cancelled = asyncio.run(self.manager.cancel_scrape_job(job["id"], reason="admin_cancelled"))
        run_result = asyncio.run(pipeline.run_job(job_id=job["id"], industry_hint="real_estate"))
        snapshots = asyncio.run(self.manager.list_page_snapshots(job["id"]))
        refreshed_job = asyncio.run(self.manager.get_scrape_job(job["id"]))

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(run_result["status"], "cancelled")
        self.assertTrue(run_result["cancelled"])
        self.assertEqual(refreshed_job["status"], "cancelled")
        self.assertEqual(len(crawler.calls), 0)
        self.assertEqual(snapshots, [])

    def test_duplicate_dispatch_does_not_run_duplicate_worker(self):
        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))

        first_dispatch = asyncio.run(self.manager.queue_scrape_job_for_dispatch(job["id"]))
        second_dispatch = asyncio.run(self.manager.queue_scrape_job_for_dispatch(job["id"]))
        started = asyncio.run(self.manager.mark_scrape_job_running(job["id"]))
        duplicate_result = asyncio.run(pipeline.run_job(job_id=job["id"], industry_hint="real_estate"))

        self.assertTrue(first_dispatch["_dispatch_enqueued"])
        self.assertEqual(first_dispatch["status"], "dispatching")
        self.assertFalse(second_dispatch["_dispatch_enqueued"])
        self.assertEqual(second_dispatch["status"], "dispatching")
        self.assertTrue(started["_started"])
        self.assertEqual(started["status"], "running")
        self.assertEqual(duplicate_result["status"], "already_running")
        self.assertTrue(duplicate_result["skipped"])
        self.assertEqual(len(crawler.calls), 0)

    def test_live_draft_creation_blocks_while_worker_is_running(self):
        crawler = FakeCrawler()
        pipeline = WebsiteIntelligencePipeline(self.manager, crawler=crawler)
        agent = asyncio.run(self.manager.get_agent("agent-1"))
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))
        asyncio.run(self.manager.mark_scrape_job_running(job["id"]))

        with self.assertRaisesRegex(CrawlError, "still running"):
            asyncio.run(pipeline.create_draft_from_job(
                job_id=job["id"],
                agent=agent,
                use_live_extraction=True,
            ))

        self.assertEqual(crawler.calls, [])

    def test_stale_scrape_job_can_be_recovered_without_running_worker(self):
        pipeline = WebsiteIntelligencePipeline(self.manager)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))
        dispatched = asyncio.run(self.manager.queue_scrape_job_for_dispatch(job["id"]))
        conn = sqlite3.connect(db_manager.DB_PATH)
        try:
            conn.execute(
                "UPDATE website_scrape_jobs SET updated_at=? WHERE id=?",
                ("2000-01-01T00:00:00", job["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        recovered = asyncio.run(self.manager.recover_stale_scrape_job(
            job["id"],
            stale_after_minutes=15,
            reason="stale_worker_recovered",
        ))

        self.assertEqual(dispatched["status"], "dispatching")
        self.assertTrue(recovered["_stale_recovered"])
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error"], "stale_worker_recovered")
        self.assertFalse(recovered["health"]["is_stale"])

    def test_scrape_job_events_are_stored_in_diagnostics(self):
        pipeline = WebsiteIntelligencePipeline(self.manager)
        job = asyncio.run(pipeline.create_job(
            client_id="client-1",
            agent_id="agent-1",
            url="https://example.com/services",
            requested_by="admin@example.com",
        ))

        event = asyncio.run(self.manager.append_scrape_job_event(
            job["id"],
            "job_created",
            status="queued",
            actor="admin@example.com",
            metadata={"domain": "example.com"},
        ))
        events = asyncio.run(self.manager.list_scrape_job_events(job["id"]))
        diagnostics = asyncio.run(self.manager.get_scrape_job_diagnostics(job["id"]))

        self.assertEqual(event["event_type"], "job_created")
        self.assertEqual(event["metadata"]["domain"], "example.com")
        self.assertEqual(events[0]["id"], event["id"])
        self.assertEqual(diagnostics["events"][0]["id"], event["id"])
        self.assertEqual(diagnostics["events"][0]["actor"], "admin@example.com")

    def test_readiness_snapshot_is_advisory_and_non_runtime(self):
        from main import _build_website_intelligence_readiness

        snapshot = _build_website_intelligence_readiness()

        self.assertTrue(snapshot["advisory_only"])
        self.assertEqual(snapshot["crawler_provider"], "bounded_http")
        self.assertFalse(snapshot["safety"]["auto_publish"])
        self.assertTrue(snapshot["safety"]["ssrf_guard"])
        self.assertTrue(snapshot["safety"]["draft_review_audit"])
        self.assertTrue(snapshot["safety"]["review_gate_shadow"])
        self.assertTrue(snapshot["safety"]["generated_draft_qa_readiness"])
        self.assertIn("scrape.worker_v1", snapshot["flags"])
        self.assertIn("scrape.review_gate_shadow", snapshot["flags"])
        self.assertIn("scrape.live_qa_readiness", snapshot["flags"])
        self.assertIn("scrape.generated_draft_qa_readiness", snapshot["flags"])
        self.assertIn("FEATURE_SCRAPE_WORKER_V1", snapshot["rollback"]["disable_live_worker"])
        self.assertEqual(snapshot["limits"]["max_pages"], 20)

    def test_quality_assessment_is_advisory_only(self):
        quality = assess_website_knowledge({
            "domain": "example.com",
            "company": {"name": "example.com", "evidence": ["https://example.com"]},
            "industry": "unknown",
            "products_or_services": [],
            "value_propositions": [],
            "qualification_questions": [],
            "pages_crawled": [],
            "faqs": [],
        })

        self.assertEqual(quality["level"], "insufficient")
        self.assertFalse(quality["ready_for_review"])
        self.assertTrue(quality["advisory_only"])
        self.assertGreater(len(quality["warnings"]), 0)

    def test_main_endpoints_are_flag_gated(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("scrape.generate_script")', source)
        self.assertIn('feature_flags.is_enabled("scrape.worker_v1")', source)
        self.assertIn('feature_flags.is_enabled("scrape.job_cancel")', source)
        self.assertIn('feature_flags.is_enabled("scrape.stale_recovery")', source)
        self.assertIn('feature_flags.is_enabled("scrape.job_events")', source)
        self.assertIn("/api/intelligence/scrape-jobs", source)
        self.assertIn("list_scrape_jobs", source)
        self.assertIn("queue_scrape_job_for_dispatch", source)
        self.assertIn("recover_stale_scrape_job", source)
        self.assertIn("_append_scrape_job_event_if_enabled", source)
        self.assertIn("append_scrape_job_event", source)
        self.assertIn("_build_website_intelligence_readiness", source)
        self.assertIn("/api/intelligence/readiness", source)
        self.assertIn("reuseExisting", source)
        self.assertIn("reuse_existing=data.reuseExisting", source)
        self.assertIn("/api/intelligence/scrape-jobs/{job_id}/diagnostics", source)
        self.assertIn("get_scrape_job_diagnostics", source)
        self.assertIn("/api/intelligence/scrape-jobs/{job_id}/dispatch", source)
        self.assertIn("already_queued", source)
        self.assertIn("background_tasks.add_task(_run_scrape_job_background", source)
        self.assertIn("/api/intelligence/scrape-jobs/{job_id}/cancel", source)
        self.assertIn("cancel_scrape_job", source)
        self.assertIn("/api/intelligence/scrape-jobs/{job_id}/recover-stale", source)
        self.assertIn("/api/intelligence/scrape-jobs/{job_id}/run", source)
        self.assertIn("/api/intelligence/script-drafts", source)
        self.assertIn("list_generated_script_drafts", source)
        self.assertIn("get_generated_script_draft_for_job", source)
        self.assertIn("_prepare_generated_script_flow_for_agent", source)
        self.assertIn("website_intelligence", (BACKEND_ROOT / "intelligence" / "script_generation.py").read_text(encoding="utf-8"))
        self.assertIn("review_checklist", (BACKEND_ROOT / "intelligence" / "script_generation.py").read_text(encoding="utf-8"))
        self.assertIn("evidence_urls", (BACKEND_ROOT / "intelligence" / "script_generation.py").read_text(encoding="utf-8"))
        self.assertIn("/api/intelligence/script-drafts/{draft_id}/preflight-flow-draft", source)
        self.assertIn("preflight_script_draft_to_agent_flow", source)
        self.assertIn("draft_preflight_valid", source)
        self.assertIn("_build_generated_script_review_policy", source)
        self.assertIn("_build_website_live_qa_readiness", source)
        self.assertIn("_build_generated_draft_qa_readiness", source)
        self.assertIn("review_policy", source)
        self.assertIn("would_block_if_enforced", source)
        self.assertIn("/api/intelligence/live-qa/readiness", source)
        self.assertIn("/api/intelligence/generated-draft-qa/readiness", source)
        self.assertIn("/api/intelligence/script-drafts/{draft_id}/apply-flow-draft", source)
        self.assertIn("generated_script_draft_applied", source)
        self.assertIn("mark_generated_script_draft_reviewed", source)
        self.assertIn("generated_script_review", source)
        self.assertIn("review_acknowledged", source)
        self.assertIn("assess_website_knowledge", (BACKEND_ROOT / "intelligence" / "pipeline.py").read_text(encoding="utf-8"))

    def test_agents_page_has_flagged_website_draft_ui(self):
        source = (FRONTEND_ROOT / "src" / "app" / "agents" / "page.js").read_text(encoding="utf-8")

        self.assertIn("NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED", source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_WORKER_V1_ENABLED", source)
        self.assertIn("Generate Script", source)
        self.assertIn("/api/intelligence/scrape-jobs", source)
        self.assertIn("/api/intelligence/scrape-jobs/${job.id}/dispatch", source)
        self.assertIn("reuseExisting: true", source)
        self.assertIn("SCRAPE_REUSE_FINAL_STATUSES", source)
        self.assertIn("Using cached", source)
        self.assertIn("/api/intelligence/script-drafts", source)
        self.assertIn("Previous Drafts", source)
        self.assertIn("loadScrapeDraftHistory", source)
        self.assertIn("/api/intelligence/script-drafts/${draftToPreflight.id}/preflight-flow-draft", source)
        self.assertIn("handlePreflightGeneratedDraft", source)
        self.assertIn("flowPreviewReadOnly", source)
        self.assertIn("Preflight passed. No flow draft was saved.", source)
        self.assertIn("Preflight", source)
        self.assertIn("/api/intelligence/script-drafts/${draftToApply.id}/apply-flow-draft", source)
        self.assertIn("Review", source)
        self.assertIn("Save to Flow Draft", source)
        self.assertIn("qualityClass", source)
        self.assertIn("draftQuality", source)
        self.assertIn("advisory", source)
        self.assertIn("contentInventoryItems", source)
        self.assertIn("Content", source)
        self.assertIn("Noise filtered", source)
        self.assertIn("ConversationGuidance", source)
        self.assertIn("Qualification", source)
        self.assertIn("Objections", source)
        self.assertIn("FAQs", source)
        self.assertIn("Review warnings", source)
        self.assertIn("Source Evidence", source)
        self.assertIn("sourceEvidenceFromKnowledge", source)
        self.assertIn("Live calls and published runtime stay unchanged", source)
        self.assertIn("reviewAcknowledged", source)
        self.assertIn("Draft saved to agent flow", source)

    def test_intelligence_admin_page_has_diagnostics_ui(self):
        layout_source = (FRONTEND_ROOT / "src" / "components" / "DashboardLayout.js").read_text(encoding="utf-8")
        page_source = (FRONTEND_ROOT / "src" / "app" / "intelligence" / "page.js").read_text(encoding="utf-8")
        flow_modal_source = (FRONTEND_ROOT / "src" / "components" / "FlowPreviewModal.js").read_text(encoding="utf-8")

        self.assertIn("NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED", layout_source)
        self.assertIn("path: '/intelligence'", layout_source)
        self.assertIn("NEXT_PUBLIC_FLOW_VISUALIZATION_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_WORKER_V1_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_JOB_CANCEL_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_STALE_RECOVERY_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_JOB_EVENTS_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_LIVE_QA_READINESS_ENABLED", page_source)
        self.assertIn("NEXT_PUBLIC_SCRAPE_GENERATED_DRAFT_QA_ENABLED", page_source)
        self.assertIn("/api/intelligence/readiness", page_source)
        self.assertIn("/api/intelligence/live-qa/readiness", page_source)
        self.assertIn("/api/intelligence/generated-draft-qa/readiness", page_source)
        self.assertIn("Production Readiness", page_source)
        self.assertIn("Live Scrape QA", page_source)
        self.assertIn("Real-URL evidence before production push", page_source)
        self.assertIn("loadLiveQa", page_source)
        self.assertIn("Generated Draft QA", page_source)
        self.assertIn("Review, edit, and save evidence before production push", page_source)
        self.assertIn("loadGeneratedDraftQa", page_source)
        self.assertIn("loadReadiness", page_source)
        self.assertIn("/api/intelligence/scrape-jobs?", page_source)
        self.assertIn("/api/intelligence/scrape-jobs/${job.id}/diagnostics", page_source)
        self.assertIn("/api/intelligence/scrape-jobs/${job.id}/dispatch", page_source)
        self.assertIn("dispatching", page_source)
        self.assertIn("/api/intelligence/scrape-jobs/${job.id}/cancel", page_source)
        self.assertIn("/api/intelligence/scrape-jobs/${job.id}/recover-stale", page_source)
        self.assertIn("Retry Worker", page_source)
        self.assertIn("Cancel Job", page_source)
        self.assertIn("canCancelJob", page_source)
        self.assertIn("canRecoverStaleJob", page_source)
        self.assertIn("Recover stale job", page_source)
        self.assertIn("/api/intelligence/script-drafts", page_source)
        self.assertIn("/api/intelligence/script-drafts/${draft.id}/preflight-flow-draft", page_source)
        self.assertIn("preflightGeneratedDraft", page_source)
        self.assertIn("flowPreviewReadOnly", page_source)
        self.assertIn("Generated draft preflight passed", page_source)
        self.assertIn("Preflight", page_source)
        self.assertIn("/api/intelligence/script-drafts/${draft.id}/apply-flow-draft", page_source)
        self.assertIn("/api/agents/${flowPreviewAgent.id}/flow-v2-draft", page_source)
        self.assertIn("FlowPreviewModal", page_source)
        self.assertIn("Create Draft", page_source)
        self.assertIn("Draft already exists", page_source)
        self.assertIn("hasGeneratedDraft", page_source)
        self.assertIn("Save to Flow Draft", page_source)
        self.assertIn("Live agent runtime is unchanged", page_source)
        self.assertIn("Live calls and published runtime are unchanged", page_source)
        self.assertIn("qualityClass", page_source)
        self.assertIn("readiness", page_source)
        self.assertIn("contentInventoryItems", page_source)
        self.assertIn("Content Inventory", page_source)
        self.assertIn("Noise filtered", page_source)
        self.assertIn("ConversationGuidance", page_source)
        self.assertIn("Qualification", page_source)
        self.assertIn("Objections", page_source)
        self.assertIn("FAQs", page_source)
        self.assertIn("Review warnings", page_source)
        self.assertIn("Source Evidence", page_source)
        self.assertIn("sourceEvidenceFromKnowledge", page_source)
        self.assertIn("Scrape Diagnostics", page_source)
        self.assertIn("Page Snapshots", page_source)
        self.assertIn("Job Events", page_source)
        self.assertIn("formatEventType", page_source)
        self.assertIn("reviewAcknowledged", page_source)
        self.assertIn("flow_draft_saved", page_source)
        self.assertIn("Saved to Flow Draft", page_source)
        self.assertIn("Website Intelligence Audit", flow_modal_source)
        self.assertIn("Generated Draft Review", flow_modal_source)
        self.assertIn("Review Checklist", flow_modal_source)
        self.assertIn("Evidence URLs", flow_modal_source)
        self.assertIn("Auto-publish off", flow_modal_source)
        self.assertIn("Live runtime unchanged", flow_modal_source)
        self.assertIn("Review Gate Shadow", flow_modal_source)
        self.assertIn("Shadow blockers", flow_modal_source)
        self.assertIn("Future enforcement preview only", flow_modal_source)

    def test_intelligence_scope_helpers_reject_cross_tenant_access(self):
        from fastapi import HTTPException
        from main import _assert_intelligence_scope, _resolve_intelligence_client_id
        from platform_migration.auth_context import TenantContext

        client_request = SimpleNamespace(
            state=SimpleNamespace(
                tenant_context=TenantContext(auth_state="api_key", role="client", tenant_id="client-1")
            )
        )
        admin_request = SimpleNamespace(
            state=SimpleNamespace(
                tenant_context=TenantContext(auth_state="api_key", role="admin", tenant_id="admin")
            )
        )

        self.assertEqual(_resolve_intelligence_client_id(client_request, None), "client-1")
        self.assertEqual(_resolve_intelligence_client_id(admin_request, "client-2"), "client-2")
        _assert_intelligence_scope(client_request, "client-1", "Scrape job")
        _assert_intelligence_scope(admin_request, "client-2", "Scrape job")

        with self.assertRaises(HTTPException) as mismatch:
            _resolve_intelligence_client_id(client_request, "client-2")
        self.assertEqual(mismatch.exception.status_code, 403)

        with self.assertRaises(HTTPException) as cross_tenant:
            _assert_intelligence_scope(client_request, "client-2", "Scrape job")
        self.assertEqual(cross_tenant.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
