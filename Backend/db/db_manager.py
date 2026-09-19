"""
Async SQLite Database Manager.

Provides a clean async interface over SQLite for all platform data:
  - clients, campaigns, leads, call_results, phone_numbers, live_call_state

Design principles:
  1. Drop-in compatible with existing JSON file helpers (read_json / write_json)
  2. Agent JSON schemas are NEVER stored in SQLite — they stay as .json files in
     db/agents/ so the agent can be fine-tuned without any DB migration.
  3. All writes are wrapped in immediate transactions to prevent corruption.
  4. JSON file fallback is provided for any table that hasn't been migrated yet.

Auto-reload guarantee:
  - Agent schemas are loaded fresh on each call from disk — no caching.
  - The StateManager already does this (`load_schema()` on __init__).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("db_manager")

# Path to the SQLite database file
_DB_DIR = Path(__file__).parent
DB_PATH = _DB_DIR / "platform.db"

# Legacy JSON paths — kept for migration and fallback
_AGENTS_LIST_FILE = _DB_DIR / "agents.json"
_CAMPAIGNS_FILE   = _DB_DIR / "campaigns.json"
_LEADS_FILE       = _DB_DIR / "leads.json"
_ASSIGNMENTS_FILE = _DB_DIR / "assignments.json"
_LIVE_STATE_FILE  = _DB_DIR / "live_state.json"

# Agents schemas stay as files — never in SQLite
AGENTS_SCHEMA_DIR = _DB_DIR / "agents"


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _client_id_for_email(email: str) -> str:
    normalized = _normalize_email(email)
    return f"user-{uuid.uuid5(uuid.NAMESPACE_DNS, normalized).hex[:12]}"


# ---------------------------------------------------------------------------
# Low-level synchronous SQLite helpers (run inside thread executor)
# ---------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # enable WAL for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_schema() -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    conn = _get_connection()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT UNIQUE,
            plan        TEXT DEFAULT 'free',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS phone_numbers (
            id              TEXT PRIMARY KEY,
            phone           TEXT UNIQUE NOT NULL,
            sid             TEXT,
            region          TEXT,
            provider        TEXT NOT NULL DEFAULT 'twilio',
            client_id       TEXT REFERENCES clients(id),
            assigned_at     TIMESTAMP,
            purchased_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS phone_number_routes (
            id              TEXT PRIMARY KEY,
            number_id       TEXT NOT NULL REFERENCES phone_numbers(id),
            phone           TEXT NOT NULL,
            provider        TEXT NOT NULL DEFAULT 'twilio',
            client_id       TEXT NOT NULL REFERENCES clients(id),
            agent_id        TEXT REFERENCES agents(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            routing_mode    TEXT DEFAULT 'tenant_default',
            status          TEXT DEFAULT 'active',
            metadata_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deactivated_at  TIMESTAMP,
            UNIQUE(number_id, routing_mode)
        );

        CREATE TABLE IF NOT EXISTS agents (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            voice           TEXT,
            language        TEXT DEFAULT 'en',
            max_duration    INTEGER DEFAULT 300,
            provider        TEXT,
            stt_provider    TEXT DEFAULT 'groq',
            tts_provider    TEXT DEFAULT 'edge',
            cartesia_voice_id TEXT,
            assigned_email  TEXT,
            agent_type      TEXT DEFAULT 'real_estate_sales',
            script          TEXT,
            data_fields     TEXT,          -- JSON array as text
            schema_path     TEXT,          -- path to the .json agent schema file
            client_id       TEXT REFERENCES clients(id),
            certification_status TEXT DEFAULT 'Draft',
            qa_score        INTEGER,
            last_qa_report  TEXT,          -- JSON report
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id              TEXT PRIMARY KEY,
            name            TEXT,
            status          TEXT DEFAULT 'Pending',
            agent_id        TEXT REFERENCES agents(id),
            client_id       TEXT REFERENCES clients(id),
            telephony_provider TEXT DEFAULT 'demo',
            phone_number_id TEXT REFERENCES phone_numbers(id),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at      TIMESTAMP,
            completed_at    TIMESTAMP,
            archived_at     TIMESTAMP,
            deleted_at      TIMESTAMP,
            delete_reason   TEXT,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS leads (
            id              TEXT PRIMARY KEY,
            campaign_id     TEXT NOT NULL REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            name            TEXT NOT NULL,
            phone           TEXT NOT NULL,
            email           TEXT,
            extra_data      TEXT,          -- JSON blob for custom fields
            status          TEXT DEFAULT 'pending',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS call_results (
            id              TEXT PRIMARY KEY,
            campaign_id     TEXT REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            lead_id         TEXT REFERENCES leads(id),
            lead_name       TEXT,
            phone           TEXT,
            called_at       TIMESTAMP,
            duration        TEXT,
            status          TEXT DEFAULT 'pending',
            outcome         TEXT,          -- interested / not_interested / follow_up / no_answer
            interested      TEXT,
            budget          TEXT,
            callback_time   TEXT,
            transcription   TEXT,          -- JSON array of {role, content} turns
            provider        TEXT,
            processed       INTEGER DEFAULT 0,
            recording_url   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS live_call_state (
            lead_uid        TEXT PRIMARY KEY,
            campaign_id     TEXT,
            client_id       TEXT REFERENCES clients(id),
            lead_name       TEXT,
            status          TEXT,
            snippet         TEXT,
            transcripts     TEXT,          -- JSON text
            provider        TEXT,
            last_update     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_assignments (
            client_id   TEXT PRIMARY KEY,
            agent_id    TEXT REFERENCES agents(id)
        );

        CREATE TABLE IF NOT EXISTS recording_assets (
            id              TEXT PRIMARY KEY,
            client_id       TEXT REFERENCES clients(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            call_result_id  TEXT,
            lead_id         TEXT,
            recording_url   TEXT NOT NULL,
            storage_path    TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at      TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tenant_audit_events (
            id              TEXT PRIMARY KEY,
            client_id       TEXT REFERENCES clients(id),
            actor_email     TEXT,
            action          TEXT NOT NULL,
            resource_type   TEXT,
            resource_id     TEXT,
            metadata        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_cleanup_manifests (
            id              TEXT PRIMARY KEY,
            campaign_id     TEXT NOT NULL REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            action          TEXT NOT NULL,
            status          TEXT DEFAULT 'planned',
            counts_json     TEXT,
            retention_json  TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_executions (
            id              TEXT PRIMARY KEY,
            campaign_id     TEXT NOT NULL REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            agent_id        TEXT,
            telephony_provider TEXT DEFAULT 'demo',
            status          TEXT DEFAULT 'planned',
            mode            TEXT DEFAULT 'shadow',
            max_concurrency INTEGER DEFAULT 1,
            max_attempts    INTEGER DEFAULT 1,
            idempotency_key TEXT UNIQUE,
            requested_by    TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at      TIMESTAMP,
            completed_at    TIMESTAMP,
            paused_at       TIMESTAMP,
            cancelled_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_lead_attempts (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL REFERENCES campaign_executions(id),
            campaign_id     TEXT NOT NULL REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            lead_id         TEXT,
            lead_name       TEXT,
            phone           TEXT,
            attempt_number  INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'queued',
            idempotency_key TEXT UNIQUE,
            scheduled_at    TIMESTAMP,
            started_at      TIMESTAMP,
            completed_at    TIMESTAMP,
            next_retry_at   TIMESTAMP,
            last_error      TEXT,
            call_result_id  TEXT,
            recording_url   TEXT,
            transcript_hash TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_worker_events (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT REFERENCES campaign_executions(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            client_id       TEXT REFERENCES clients(id),
            event_type      TEXT NOT NULL,
            payload         TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_flow_versions (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL REFERENCES agents(id),
            client_id       TEXT REFERENCES clients(id),
            schema_version  TEXT NOT NULL,
            status          TEXT DEFAULT 'draft',
            runtime_mode    TEXT DEFAULT 'shadow',
            artifact_path   TEXT NOT NULL,
            validation_json TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_memory_collections (
            id              TEXT PRIMARY KEY,
            client_id       TEXT NOT NULL REFERENCES clients(id),
            agent_id        TEXT NOT NULL REFERENCES agents(id),
            status          TEXT DEFAULT 'active',
            source_type     TEXT DEFAULT 'manual',
            source_id       TEXT,
            metadata_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reset_at        TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_memory_items (
            id              TEXT PRIMARY KEY,
            collection_id   TEXT NOT NULL REFERENCES agent_memory_collections(id),
            client_id       TEXT NOT NULL REFERENCES clients(id),
            agent_id        TEXT NOT NULL REFERENCES agents(id),
            content         TEXT NOT NULL,
            content_hash    TEXT NOT NULL,
            metadata_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at      TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_memory_events (
            id              TEXT PRIMARY KEY,
            collection_id   TEXT REFERENCES agent_memory_collections(id),
            client_id       TEXT NOT NULL REFERENCES clients(id),
            agent_id        TEXT NOT NULL REFERENCES agents(id),
            event_type      TEXT NOT NULL,
            metadata_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS website_scrape_jobs (
            id              TEXT PRIMARY KEY,
            client_id       TEXT REFERENCES clients(id),
            agent_id        TEXT REFERENCES agents(id),
            url             TEXT NOT NULL,
            domain          TEXT NOT NULL,
            status          TEXT DEFAULT 'queued',
            requested_by    TEXT,
            limits_json     TEXT,
            error           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS website_scrape_job_events (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL REFERENCES website_scrape_jobs(id),
            event_type      TEXT NOT NULL,
            status          TEXT,
            actor           TEXT,
            metadata_json   TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS website_page_snapshots (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL REFERENCES website_scrape_jobs(id),
            url             TEXT NOT NULL,
            content_hash    TEXT,
            content_type    TEXT,
            storage_path    TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS website_extractions (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL REFERENCES website_scrape_jobs(id),
            extraction_json TEXT NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS generated_script_drafts (
            id              TEXT PRIMARY KEY,
            job_id          TEXT REFERENCES website_scrape_jobs(id),
            client_id       TEXT REFERENCES clients(id),
            agent_id        TEXT REFERENCES agents(id),
            status          TEXT DEFAULT 'draft',
            draft_json      TEXT NOT NULL,
            knowledge_json  TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at     TIMESTAMP,
            reviewed_by     TEXT,
            review_notes    TEXT,
            flow_version_id TEXT REFERENCES agent_flow_versions(id),
            published_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crm_connections (
            id                  TEXT PRIMARY KEY,
            client_id           TEXT NOT NULL REFERENCES clients(id),
            provider            TEXT NOT NULL,
            display_name        TEXT NOT NULL,
            external_account_id TEXT,
            status              TEXT DEFAULT 'draft',
            config_json         TEXT,
            secrets_configured  INTEGER DEFAULT 0,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            disabled_at         TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crm_sync_jobs (
            id              TEXT PRIMARY KEY,
            client_id       TEXT NOT NULL REFERENCES clients(id),
            connection_id   TEXT NOT NULL REFERENCES crm_connections(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            status          TEXT DEFAULT 'planned',
            mode            TEXT DEFAULT 'dry_run',
            direction       TEXT DEFAULT 'outbound',
            idempotency_key TEXT UNIQUE,
            payload_json    TEXT,
            error           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crm_sync_events (
            id              TEXT PRIMARY KEY,
            job_id          TEXT REFERENCES crm_sync_jobs(id),
            client_id       TEXT NOT NULL REFERENCES clients(id),
            connection_id   TEXT REFERENCES crm_connections(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            event_type      TEXT NOT NULL,
            payload_json    TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crm_sync_outbox (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL REFERENCES crm_sync_jobs(id),
            client_id       TEXT NOT NULL REFERENCES clients(id),
            connection_id   TEXT NOT NULL REFERENCES crm_connections(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            status          TEXT DEFAULT 'queued_shadow',
            mode            TEXT DEFAULT 'shadow',
            idempotency_key TEXT UNIQUE,
            payload_json    TEXT,
            attempt_count   INTEGER DEFAULT 0,
            last_error      TEXT,
            next_retry_at   TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            locked_at       TIMESTAMP,
            completed_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crm_delivery_approvals (
            id              TEXT PRIMARY KEY,
            outbox_id       TEXT NOT NULL REFERENCES crm_sync_outbox(id),
            job_id          TEXT NOT NULL REFERENCES crm_sync_jobs(id),
            client_id       TEXT NOT NULL REFERENCES clients(id),
            connection_id   TEXT NOT NULL REFERENCES crm_connections(id),
            campaign_id     TEXT REFERENCES campaigns(id),
            status          TEXT DEFAULT 'approved_shadow',
            approval_mode   TEXT DEFAULT 'shadow',
            plan_hash       TEXT NOT NULL,
            plan_summary_json TEXT,
            approved_by     TEXT,
            requested_by    TEXT,
            idempotency_key TEXT UNIQUE,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at      TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS demo_requests (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            company           TEXT,
            email             TEXT NOT NULL,
            phone             TEXT,
            industry          TEXT,
            use_case          TEXT,
            monthly_volume    TEXT,
            additional_notes  TEXT,
            status            TEXT DEFAULT 'New',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        for table_name in ("leads", "call_results", "live_call_state"):
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN client_id TEXT REFERENCES clients(id)")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE call_results ADD COLUMN recording_url TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE call_results ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE call_results ADD COLUMN lead_data TEXT")
        except sqlite3.OperationalError:
            pass
        for column_sql in (
            "ALTER TABLE crm_sync_outbox ADD COLUMN last_error TEXT",
            "ALTER TABLE crm_sync_outbox ADD COLUMN next_retry_at TIMESTAMP",
        ):
            try:
                conn.execute(column_sql)
            except sqlite3.OperationalError:
                pass
        # call_key: non-FK text identifier used for transcript lookups.
        # Replaces using lead_id (which is a real FK to leads.id) for custom IDs.
        try:
            conn.execute("ALTER TABLE call_results ADD COLUMN call_key TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN stt_provider TEXT DEFAULT 'groq'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN tts_provider TEXT DEFAULT 'edge'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN cartesia_voice_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN parler_description TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN assigned_email TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN agent_type TEXT DEFAULT 'real_estate_sales'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN certification_status TEXT DEFAULT 'Draft'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN qa_score INTEGER")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN last_qa_report TEXT")
        except Exception:
            pass
        for column_sql in (
            "ALTER TABLE campaigns ADD COLUMN archived_at TIMESTAMP",
            "ALTER TABLE campaigns ADD COLUMN deleted_at TIMESTAMP",
            "ALTER TABLE campaigns ADD COLUMN delete_reason TEXT",
            "ALTER TABLE campaigns ADD COLUMN updated_at TIMESTAMP",
        ):
            try:
                conn.execute(column_sql)
            except sqlite3.OperationalError:
                pass
        for column_sql in (
            "ALTER TABLE generated_script_drafts ADD COLUMN reviewed_by TEXT",
            "ALTER TABLE generated_script_drafts ADD COLUMN review_notes TEXT",
            "ALTER TABLE generated_script_drafts ADD COLUMN flow_version_id TEXT REFERENCES agent_flow_versions(id)",
        ):
            try:
                conn.execute(column_sql)
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE website_scrape_jobs ADD COLUMN progress TEXT")
        except sqlite3.OperationalError:
            pass
        _ensure_tenant_indexes(conn)
        _backfill_client_ids(conn)
        conn.commit()
        logger.info("DB schema initialized at %s", DB_PATH)
    finally:
        conn.close()


def _ensure_tenant_indexes(conn: sqlite3.Connection) -> None:
    """Create additive indexes used by future tenant-scoped reads."""
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_leads_client_campaign ON leads(client_id, campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_call_results_client_campaign ON call_results(client_id, campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_live_state_client_campaign ON live_call_state(client_id, campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_recording_assets_client_campaign ON recording_assets(client_id, campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_tenant_audit_client_created ON tenant_audit_events(client_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_lifecycle ON campaigns(client_id, archived_at, deleted_at, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_cleanup_campaign ON campaign_cleanup_manifests(campaign_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_executions_campaign ON campaign_executions(campaign_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_executions_client ON campaign_executions(client_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_lead_attempts_execution ON campaign_lead_attempts(execution_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_worker_events_execution ON campaign_worker_events(execution_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_agent_flow_versions_agent ON agent_flow_versions(agent_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_website_scrape_jobs_client ON website_scrape_jobs(client_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_website_scrape_job_events_job ON website_scrape_job_events(job_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_generated_script_drafts_agent ON generated_script_drafts(agent_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_phone_number_routes_client ON phone_number_routes(client_id, status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_phone_number_routes_phone ON phone_number_routes(phone, provider, status)",
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_collections_agent ON agent_memory_collections(client_id, agent_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_items_agent ON agent_memory_items(client_id, agent_id, deleted_at, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_events_agent ON agent_memory_events(client_id, agent_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_connections_client ON crm_connections(client_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_sync_jobs_client ON crm_sync_jobs(client_id, campaign_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_sync_events_job ON crm_sync_events(job_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_sync_outbox_client ON crm_sync_outbox(client_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_sync_outbox_job ON crm_sync_outbox(job_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_delivery_approvals_client ON crm_delivery_approvals(client_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_crm_delivery_approvals_outbox ON crm_delivery_approvals(outbox_id, created_at)",
    ):
        conn.execute(sql)


def _backfill_client_ids(conn: sqlite3.Connection) -> None:
    """Backfill nullable client_id columns from existing campaign ownership."""
    for table_name in ("leads", "call_results", "live_call_state"):
        conn.execute(
            f"""
            UPDATE {table_name}
               SET client_id = (
                   SELECT campaigns.client_id
                     FROM campaigns
                    WHERE campaigns.id = {table_name}.campaign_id
               )
             WHERE client_id IS NULL
               AND campaign_id IS NOT NULL
               AND EXISTS (
                   SELECT 1
                     FROM campaigns
                    WHERE campaigns.id = {table_name}.campaign_id
                      AND campaigns.client_id IS NOT NULL
               )
            """
        )


def _client_id_for_campaign_sync(conn: sqlite3.Connection, campaign_id: str | None) -> str | None:
    if not campaign_id:
        return None
    row = conn.execute("SELECT client_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    return row["client_id"] if row and row["client_id"] else None


_TENANT_SCOPED_RESOURCE_OWNER_LOOKUPS = {
    "agent": ("agents", "id", "Agent"),
    "campaign": ("campaigns", "id", "Campaign"),
    "crm_connection": ("crm_connections", "id", "CRM connection"),
    "crm_sync_job": ("crm_sync_jobs", "id", "CRM sync job"),
    "crm_outbox": ("crm_sync_outbox", "id", "CRM outbox item"),
    "crm_sync_outbox": ("crm_sync_outbox", "id", "CRM outbox item"),
    "crm_delivery_approval": ("crm_delivery_approvals", "id", "CRM delivery approval"),
    "phone_number": ("phone_numbers", "id", "Phone number"),
    "scrape_job": ("website_scrape_jobs", "id", "Scrape job"),
    "website_scrape_job": ("website_scrape_jobs", "id", "Scrape job"),
}


def _normalize_tenant_scoped_resource_type(resource_type: str) -> str:
    return (resource_type or "").strip().lower().replace("-", "_")


def _decode_scrape_job(row: dict) -> dict:
    try:
        row["limits"] = json.loads(row.pop("limits_json") or "{}")
    except Exception:
        row["limits"] = {}
    return row


def _decode_scrape_job_event(row: dict) -> dict:
    try:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _scrape_job_health(job: dict, *, stale_after_minutes: int = 15) -> dict:
    status = job.get("status") or "queued"
    threshold = max(1, int(stale_after_minutes or 15))
    timestamp = _parse_iso_datetime(job.get("updated_at") or job.get("created_at"))
    age_minutes = None
    if timestamp:
        now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
        age_minutes = max(0.0, (now - timestamp).total_seconds() / 60)
    recoverable = status in {"dispatching", "running"}
    is_stale = bool(recoverable and age_minutes is not None and age_minutes >= threshold)
    return {
        "recoverable": recoverable,
        "is_stale": is_stale,
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "stale_after_minutes": threshold,
    }


def _decode_script_draft(row: dict) -> dict:
    for source_key, target_key in (
        ("draft_json", "draft"),
        ("knowledge_json", "knowledge"),
    ):
        try:
            row[target_key] = json.loads(row.pop(source_key) or "{}")
        except Exception:
            row[target_key] = {}
    return row


def _decode_audit_event(row: dict) -> dict:
    try:
        row["metadata"] = json.loads(row.get("metadata") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _decode_cleanup_manifest(row: dict) -> dict:
    for source_key, target_key in (
        ("counts_json", "counts"),
        ("retention_json", "retention"),
    ):
        try:
            row[target_key] = json.loads(row.pop(source_key) or "{}")
        except Exception:
            row[target_key] = {}
    return row


def _decode_phone_route(row: dict) -> dict:
    try:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _decode_memory_collection(row: dict) -> dict:
    try:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _decode_memory_item(row: dict) -> dict:
    try:
        row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    except Exception:
        row["metadata"] = {}
    return row


def _decode_crm_connection(row: dict) -> dict:
    try:
        row["config"] = json.loads(row.pop("config_json") or "{}")
    except Exception:
        row["config"] = {}
    row["secrets_configured"] = bool(row.get("secrets_configured"))
    return row


def _decode_crm_sync_job(row: dict) -> dict:
    try:
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
    except Exception:
        row["payload"] = {}
    return row


def _decode_crm_sync_event(row: dict) -> dict:
    try:
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
    except Exception:
        row["payload"] = {}
    return row


def _decode_crm_sync_outbox(row: dict) -> dict:
    try:
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
    except Exception:
        row["payload"] = {}
    return row


def _decode_crm_delivery_approval(row: dict) -> dict:
    try:
        row["plan_summary"] = json.loads(row.pop("plan_summary_json") or "{}")
    except Exception:
        row["plan_summary"] = {}
    return row


def _campaign_retention_policy() -> dict:
    return {
        "mode": "soft_delete_only",
        "physical_delete": False,
        "transcripts": "preserved",
        "recordings": "preserved",
        "leads": "preserved",
        "cleanup_requires_future_phase": True,
    }


def _campaign_related_counts_sync(conn: sqlite3.Connection, campaign_id: str) -> dict:
    counts = {}
    for key, sql, params in (
        ("leads", "SELECT COUNT(*) FROM leads WHERE campaign_id=?", (campaign_id,)),
        ("call_results", "SELECT COUNT(*) FROM call_results WHERE campaign_id=?", (campaign_id,)),
        ("live_call_state", "SELECT COUNT(*) FROM live_call_state WHERE campaign_id=?", (campaign_id,)),
        ("recording_assets", "SELECT COUNT(*) FROM recording_assets WHERE campaign_id=?", (campaign_id,)),
        ("campaign_executions", "SELECT COUNT(*) FROM campaign_executions WHERE campaign_id=?", (campaign_id,)),
        ("campaign_lead_attempts", "SELECT COUNT(*) FROM campaign_lead_attempts WHERE campaign_id=?", (campaign_id,)),
    ):
        counts[key] = conn.execute(sql, params).fetchone()[0]
    return counts


def _create_campaign_cleanup_manifest_sync(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    client_id: Optional[str],
    action: str,
) -> dict:
    manifest_id = str(uuid.uuid4())
    counts = _campaign_related_counts_sync(conn, campaign_id)
    retention = _campaign_retention_policy()
    conn.execute(
        """INSERT INTO campaign_cleanup_manifests
           (id, campaign_id, client_id, action, status, counts_json, retention_json, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            manifest_id, campaign_id, client_id, action, "planned",
            json.dumps(counts), json.dumps(retention), datetime.now().isoformat(),
        ),
    )
    row = conn.execute("SELECT * FROM campaign_cleanup_manifests WHERE id=?", (manifest_id,)).fetchone()
    return _decode_cleanup_manifest(dict(row))


# ---------------------------------------------------------------------------
# Thread-safe executor wrapper
# ---------------------------------------------------------------------------

_executor = None  # will be set on first use


def _run_sync(fn, *args, **kwargs):
    """Run a synchronous DB function in the default thread pool."""
    return fn(*args, **kwargs)


async def run_in_executor(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

class DatabaseManager:
    """Async interface to the SQLite platform database."""

    # ── Initialization ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Initialize DB schema. Call once at server startup."""
        await run_in_executor(_init_schema)
        await self._migrate_json_files()

    async def _migrate_json_files(self) -> None:
        """
        One-time migration from legacy JSON flat files to SQLite.
        Reads existing JSON data and inserts it if tables are empty.
        Leaves JSON files in place as backup.
        """
        await run_in_executor(self._migrate_sync)

    def _migrate_sync(self) -> None:
        conn = _get_connection()
        try:
            # Migrate agents list
            row = conn.execute("SELECT COUNT(*) FROM agents").fetchone()
            if row[0] == 0 and _AGENTS_LIST_FILE.exists():
                try:
                    agents = json.loads(_AGENTS_LIST_FILE.read_text(encoding="utf-8"))
                    for a in agents:
                        conn.execute(
                            """INSERT OR IGNORE INTO agents
                               (id, name, voice, language, max_duration, provider, script, data_fields, schema_path, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (
                                a.get("id"), a.get("name"), a.get("voice"),
                                a.get("language", "en"), a.get("max_duration", 300),
                                a.get("provider"), a.get("script"),
                                json.dumps(a.get("data_fields", [])),
                                a.get("schema_path"), a.get("createdAt"),
                            )
                        )
                    conn.commit()
                    logger.info("Migrated %d agents from JSON", len(agents))
                except Exception as e:
                    logger.warning("Agent JSON migration skipped: %s", e)

            # Migrate campaigns
            row = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()
            if row[0] == 0 and _CAMPAIGNS_FILE.exists():
                try:
                    campaigns = json.loads(_CAMPAIGNS_FILE.read_text(encoding="utf-8"))
                    for c in campaigns:
                        conn.execute(
                            """INSERT OR IGNORE INTO campaigns (id, status, created_at)
                               VALUES (?,?,?)""",
                            (c.get("id"), c.get("status", "Pending"), c.get("createdAt"))
                        )
                        # Migrate results
                        for r in c.get("results", []):
                            rid = f"{c['id']}_{r.get('phone', '')}_{int(time.time())}"
                            conn.execute(
                                """INSERT OR IGNORE INTO call_results
                                   (id, campaign_id, lead_name, phone, called_at, duration,
                                    status, interested, budget, callback_time, transcription, processed)
                                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    rid, c.get("id"), r.get("name"), r.get("phone"),
                                    r.get("calledAt"), r.get("duration"), r.get("status", "Connected"),
                                    r.get("interested"), r.get("budget"), r.get("callback"),
                                    json.dumps(r.get("transcription", [])), 1 if r.get("processed") else 0,
                                )
                            )
                    conn.commit()
                    logger.info("Migrated %d campaigns from JSON", len(campaigns))
                except Exception as e:
                    logger.warning("Campaign JSON migration skipped: %s", e)

            # Migrate leads
            row = conn.execute("SELECT COUNT(*) FROM leads").fetchone()
            if row[0] == 0 and _LEADS_FILE.exists():
                try:
                    leads_db = json.loads(_LEADS_FILE.read_text(encoding="utf-8"))
                    for entry in leads_db:
                        campaign_id = entry.get("campaignId")
                        for lead in entry.get("leads", []):
                            lid = f"{campaign_id}_{lead.get('phone', '')}"
                            conn.execute(
                                """INSERT OR IGNORE INTO leads (id, campaign_id, name, phone, extra_data)
                                   VALUES (?,?,?,?,?)""",
                                (lid, campaign_id, lead.get("name"), lead.get("phone"),
                                 json.dumps({k: v for k, v in lead.items() if k not in ("name", "phone")}))
                            )
                    conn.commit()
                    logger.info("Migrated leads from JSON")
                except Exception as e:
                    logger.warning("Leads JSON migration skipped: %s", e)

        except Exception as e:
            logger.error("Migration error: %s", e)
        finally:
            conn.close()

    # ── Agents ──────────────────────────────────────────────────────────────

    async def get_tenant_scoped_resource_owner(self, resource_type: str, resource_id: str) -> dict:
        """Return owner metadata only for scoped-read shadow diagnostics."""
        normalized_type = _normalize_tenant_scoped_resource_type(resource_type)
        lookup = _TENANT_SCOPED_RESOURCE_OWNER_LOOKUPS.get(normalized_type)
        if not lookup:
            raise ValueError(f"unsupported tenant scoped resource type: {resource_type}")
        if not str(resource_id or "").strip():
            raise ValueError("tenant scoped resource id is required")

        table_name, id_column, resource_label = lookup

        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    f"SELECT client_id FROM {table_name} WHERE {id_column}=?",
                    (resource_id,),
                ).fetchone()
                owner_client_id = row["client_id"] if row and row["client_id"] else None
                return {
                    "resource_type": normalized_type,
                    "resource_label": resource_label,
                    "found": bool(row),
                    "owner_client_id": owner_client_id,
                    "owner_tenant_present": bool(owner_client_id),
                    "resource_id_included": False,
                    "payload_included": False,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_tenant_security_audit_counts(self, client_id: Optional[str] = None) -> dict:
        """Return aggregate tenant ownership/relationship counts without payload data."""
        tenant_tables = [
            "agents",
            "campaigns",
            "leads",
            "call_results",
            "live_call_state",
            "recording_assets",
            "phone_numbers",
            "phone_number_routes",
            "campaign_cleanup_manifests",
            "campaign_executions",
            "campaign_lead_attempts",
            "campaign_worker_events",
            "agent_flow_versions",
            "agent_memory_collections",
            "agent_memory_items",
            "agent_memory_events",
            "website_scrape_jobs",
            "generated_script_drafts",
            "crm_connections",
            "crm_sync_jobs",
            "crm_sync_events",
            "crm_sync_outbox",
            "crm_delivery_approvals",
        ]

        relationship_queries = {
            "campaign_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaigns c
                  JOIN agents a ON a.id=c.agent_id
                 WHERE COALESCE(c.client_id, '') != COALESCE(a.client_id, '')
            """,
            "client_assignment_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM client_assignments ca
                  JOIN agents a ON a.id=ca.agent_id
                 WHERE COALESCE(ca.client_id, '') != COALESCE(a.client_id, '')
            """,
            "campaign_phone_number_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaigns c
                  JOIN phone_numbers p ON p.id=c.phone_number_id
                 WHERE COALESCE(c.client_id, '') != COALESCE(p.client_id, '')
            """,
            "lead_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM leads l
                  JOIN campaigns c ON c.id=l.campaign_id
                 WHERE COALESCE(l.client_id, '') != COALESCE(c.client_id, '')
            """,
            "call_result_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM call_results r
                  JOIN campaigns c ON c.id=r.campaign_id
                 WHERE COALESCE(r.client_id, '') != COALESCE(c.client_id, '')
            """,
            "live_state_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM live_call_state s
                  JOIN campaigns c ON c.id=s.campaign_id
                 WHERE COALESCE(s.client_id, '') != COALESCE(c.client_id, '')
            """,
            "recording_asset_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM recording_assets a
                  JOIN campaigns c ON c.id=a.campaign_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(c.client_id, '')
            """,
            "phone_route_number_scope_mismatch": """
                SELECT COUNT(*)
                  FROM phone_number_routes r
                  JOIN phone_numbers p ON p.id=r.number_id
                 WHERE r.status='active'
                   AND COALESCE(r.client_id, '') != COALESCE(p.client_id, '')
            """,
            "phone_route_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM phone_number_routes r
                  JOIN agents a ON a.id=r.agent_id
                 WHERE r.status='active'
                   AND COALESCE(r.client_id, '') != COALESCE(a.client_id, '')
            """,
            "phone_route_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM phone_number_routes r
                  JOIN campaigns c ON c.id=r.campaign_id
                 WHERE r.status='active'
                   AND COALESCE(r.client_id, '') != COALESCE(c.client_id, '')
            """,
            "campaign_execution_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaign_executions e
                  JOIN campaigns c ON c.id=e.campaign_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(c.client_id, '')
            """,
            "campaign_lead_attempt_execution_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaign_lead_attempts a
                  JOIN campaign_executions e ON e.id=a.execution_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(e.client_id, '')
            """,
            "campaign_lead_attempt_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaign_lead_attempts a
                  JOIN campaigns c ON c.id=a.campaign_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(c.client_id, '')
            """,
            "campaign_worker_event_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM campaign_worker_events e
                  JOIN campaigns c ON c.id=e.campaign_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(c.client_id, '')
            """,
            "agent_flow_version_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_flow_versions f
                  JOIN agents a ON a.id=f.agent_id
                 WHERE COALESCE(f.client_id, '') != COALESCE(a.client_id, '')
            """,
            "memory_collection_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_memory_collections m
                  JOIN agents a ON a.id=m.agent_id
                 WHERE COALESCE(m.client_id, '') != COALESCE(a.client_id, '')
            """,
            "memory_item_collection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_memory_items i
                  JOIN agent_memory_collections c ON c.id=i.collection_id
                 WHERE COALESCE(i.client_id, '') != COALESCE(c.client_id, '')
            """,
            "memory_item_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_memory_items i
                  JOIN agents a ON a.id=i.agent_id
                 WHERE COALESCE(i.client_id, '') != COALESCE(a.client_id, '')
            """,
            "memory_event_collection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_memory_events e
                  JOIN agent_memory_collections c ON c.id=e.collection_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(c.client_id, '')
            """,
            "memory_event_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM agent_memory_events e
                  JOIN agents a ON a.id=e.agent_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(a.client_id, '')
            """,
            "scrape_job_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM website_scrape_jobs j
                  JOIN agents a ON a.id=j.agent_id
                 WHERE COALESCE(j.client_id, '') != COALESCE(a.client_id, '')
            """,
            "generated_draft_job_scope_mismatch": """
                SELECT COUNT(*)
                  FROM generated_script_drafts d
                  JOIN website_scrape_jobs j ON j.id=d.job_id
                 WHERE COALESCE(d.client_id, '') != COALESCE(j.client_id, '')
            """,
            "generated_draft_agent_scope_mismatch": """
                SELECT COUNT(*)
                  FROM generated_script_drafts d
                  JOIN agents a ON a.id=d.agent_id
                 WHERE COALESCE(d.client_id, '') != COALESCE(a.client_id, '')
            """,
            "crm_job_connection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_jobs j
                  JOIN crm_connections c ON c.id=j.connection_id
                 WHERE COALESCE(j.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_job_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_jobs j
                  JOIN campaigns c ON c.id=j.campaign_id
                 WHERE COALESCE(j.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_event_job_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_events e
                  JOIN crm_sync_jobs j ON j.id=e.job_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(j.client_id, '')
            """,
            "crm_event_connection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_events e
                  JOIN crm_connections c ON c.id=e.connection_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_event_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_events e
                  JOIN campaigns c ON c.id=e.campaign_id
                 WHERE COALESCE(e.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_outbox_job_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_outbox o
                  JOIN crm_sync_jobs j ON j.id=o.job_id
                 WHERE COALESCE(o.client_id, '') != COALESCE(j.client_id, '')
            """,
            "crm_outbox_connection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_outbox o
                  JOIN crm_connections c ON c.id=o.connection_id
                 WHERE COALESCE(o.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_outbox_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_sync_outbox o
                  JOIN campaigns c ON c.id=o.campaign_id
                 WHERE COALESCE(o.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_delivery_approval_outbox_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_delivery_approvals a
                  JOIN crm_sync_outbox o ON o.id=a.outbox_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(o.client_id, '')
            """,
            "crm_delivery_approval_job_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_delivery_approvals a
                  JOIN crm_sync_jobs j ON j.id=a.job_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(j.client_id, '')
            """,
            "crm_delivery_approval_connection_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_delivery_approvals a
                  JOIN crm_connections c ON c.id=a.connection_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(c.client_id, '')
            """,
            "crm_delivery_approval_campaign_scope_mismatch": """
                SELECT COUNT(*)
                  FROM crm_delivery_approvals a
                  JOIN campaigns c ON c.id=a.campaign_id
                 WHERE COALESCE(a.client_id, '') != COALESCE(c.client_id, '')
            """,
        }

        def _sync():
            conn = _get_connection()
            try:
                def count(sql: str, params: tuple[Any, ...] = ()) -> int:
                    row = conn.execute(sql, params).fetchone()
                    return int(row[0] or 0)

                totals_by_table = {
                    table: count(f"SELECT COUNT(*) FROM {table}")
                    for table in tenant_tables
                }
                selected_client_totals = (
                    {
                        table: count(f"SELECT COUNT(*) FROM {table} WHERE client_id=?", (client_id,))
                        for table in tenant_tables
                    }
                    if client_id
                    else {}
                )
                missing_owner_by_table = {
                    table: count(
                        f"""SELECT COUNT(*) FROM {table}
                            WHERE TRIM(COALESCE(client_id, ''))=''"""
                    )
                    for table in tenant_tables
                }
                relationship_mismatches = {
                    name: count(sql)
                    for name, sql in relationship_queries.items()
                }
                return {
                    "client_scope_requested": bool(client_id),
                    "total_clients": count("SELECT COUNT(*) FROM clients"),
                    "totals_by_table": totals_by_table,
                    "selected_client_totals": selected_client_totals,
                    "missing_owner_by_table": missing_owner_by_table,
                    "relationship_mismatches": relationship_mismatches,
                    "missing_owner_total": sum(missing_owner_by_table.values()),
                    "relationship_mismatch_total": sum(relationship_mismatches.values()),
                    "payloads_returned": False,
                    "ids_returned": False,
                    "tenant_values_returned": False,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_agents(self, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if client_id:
                    rows = conn.execute(
                        """SELECT a.*, c.name as client_name 
                           FROM agents a 
                           LEFT JOIN clients c ON a.client_id = c.id 
                           WHERE a.client_id=? ORDER BY a.created_at DESC""",
                        (client_id,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT a.*, c.name as client_name 
                           FROM agents a 
                           LEFT JOIN clients c ON a.client_id = c.id 
                           ORDER BY a.created_at DESC"""
                    ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d["data_fields"] = json.loads(d.get("data_fields") or "[]")
                    result.append(d)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_agent(self, agent_id: str, data: dict) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    """INSERT INTO agents (id, name, voice, language, max_duration, provider, stt_provider, tts_provider, cartesia_voice_id, parler_description, assigned_email, agent_type, script, data_fields, schema_path, client_id, certification_status, qa_score, last_qa_report, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        agent_id, data.get("name"), data.get("voice"),
                        data.get("language", "en"), data.get("max_duration", 300),
                        data.get("provider"), data.get("stt_provider", "groq"),
                        data.get("tts_provider", "edge"), data.get("cartesia_voice_id"),
                        data.get("parler_description"), data.get("assigned_email"),
                        data.get("agent_type", "real_estate_sales"), data.get("script"),
                        json.dumps(data.get("data_fields", [])),
                        data.get("schema_path"), data.get("client_id"),
                        data.get("certification_status", "Draft"), data.get("qa_score"),
                        json.dumps(data.get("last_qa_report")) if data.get("last_qa_report") else None,
                        datetime.now().isoformat(),
                    )
                )
                conn.commit()
                return {**data, "id": agent_id}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_agent(self, agent_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
                if not row:
                    return None
                data = dict(row)
                data["data_fields"] = json.loads(data.get("data_fields") or "[]")
                return data
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def update_agent(self, agent_id: str, data: dict) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                existing = conn.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
                if not existing:
                    return None

                conn.execute(
                    """UPDATE agents
                       SET name=?, voice=?, language=?, max_duration=?, provider=?,
                           stt_provider=?, tts_provider=?, cartesia_voice_id=?,
                           parler_description=?, assigned_email=?, agent_type=?, script=?,
                           data_fields=?, schema_path=?, client_id=?, certification_status=?,
                           qa_score=?, last_qa_report=?
                       WHERE id=?""",
                    (
                        data.get("name"),
                        data.get("voice"),
                        data.get("language", "en"),
                        data.get("max_duration", 300),
                        data.get("provider"),
                        data.get("stt_provider", "groq"),
                        data.get("tts_provider", "edge"),
                        data.get("cartesia_voice_id"),
                        data.get("parler_description"),
                        data.get("assigned_email"),
                        data.get("agent_type", "real_estate_sales"),
                        data.get("script"),
                        json.dumps(data.get("data_fields", [])),
                        data.get("schema_path"),
                        data.get("client_id"),
                        data.get("certification_status", "Draft"),
                        data.get("qa_score"),
                        json.dumps(data.get("last_qa_report")) if data.get("last_qa_report") else None,
                        agent_id,
                    ),
                )
                conn.commit()

                row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
                updated = dict(row)
                updated["data_fields"] = json.loads(updated.get("data_fields") or "[]")
                return updated
            finally:
                conn.close()
        return await run_in_executor(_sync)

    # ── Campaigns ────────────────────────────────────────────────────────────

    async def create_agent_flow_version(
        self,
        agent_id: str,
        *,
        client_id: Optional[str],
        schema_version: str,
        status: str,
        runtime_mode: str,
        artifact_path: str,
        validation: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                version_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO agent_flow_versions
                       (id, agent_id, client_id, schema_version, status, runtime_mode,
                        artifact_path, validation_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        version_id, agent_id, client_id, schema_version, status,
                        runtime_mode, artifact_path, json.dumps(validation or {}),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM agent_flow_versions WHERE id=?",
                    (version_id,),
                ).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_agent_flow_versions(self, agent_id: str) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                rows = conn.execute(
                    """SELECT * FROM agent_flow_versions
                       WHERE agent_id=?
                       ORDER BY created_at DESC""",
                    (agent_id,),
                ).fetchall()
                result = []
                for row in rows:
                    data = dict(row)
                    try:
                        data["validation"] = json.loads(data.pop("validation_json") or "{}")
                    except Exception:
                        data["validation"] = {}
                    result.append(data)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_agent_memory_collection(
        self,
        *,
        client_id: str,
        agent_id: str,
        source_type: str = "manual",
        source_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                agent = conn.execute("SELECT client_id FROM agents WHERE id=?", (agent_id,)).fetchone()
                if not agent:
                    raise ValueError(f"agent not found: {agent_id}")
                if agent["client_id"] and agent["client_id"] != client_id:
                    raise ValueError("agent is outside memory tenant scope")
                collection_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO agent_memory_collections
                       (id, client_id, agent_id, status, source_type, source_id, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        collection_id, client_id, agent_id, "active", source_type,
                        source_id, json.dumps(metadata or {}), now,
                    ),
                )
                conn.execute(
                    """INSERT INTO agent_memory_events
                       (id, collection_id, client_id, agent_id, event_type, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), collection_id, client_id, agent_id,
                        "collection_created", json.dumps({"source_type": source_type, "source_id": source_id}), now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM agent_memory_collections WHERE id=?", (collection_id,)).fetchone()
                return _decode_memory_collection(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def add_agent_memory_item(
        self,
        *,
        collection_id: str,
        client_id: str,
        agent_id: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> dict:
        text = (content or "").strip()
        if not text:
            raise ValueError("memory content is required")

        def _sync():
            conn = _get_connection()
            try:
                collection = conn.execute(
                    """SELECT * FROM agent_memory_collections
                       WHERE id=? AND client_id=? AND agent_id=? AND status='active'""",
                    (collection_id, client_id, agent_id),
                ).fetchone()
                if not collection:
                    raise ValueError("active memory collection not found for tenant and agent")
                item_id = str(uuid.uuid4())
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO agent_memory_items
                       (id, collection_id, client_id, agent_id, content, content_hash, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        item_id, collection_id, client_id, agent_id, text,
                        content_hash, json.dumps(metadata or {}), now,
                    ),
                )
                conn.execute(
                    """INSERT INTO agent_memory_events
                       (id, collection_id, client_id, agent_id, event_type, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), collection_id, client_id, agent_id,
                        "item_added", json.dumps({"content_hash": content_hash}), now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM agent_memory_items WHERE id=?", (item_id,)).fetchone()
                return _decode_memory_item(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_agent_memory_items(
        self,
        *,
        client_id: str,
        agent_id: str,
        include_deleted: bool = False,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = "client_id=? AND agent_id=?"
                params: list[Any] = [client_id, agent_id]
                if not include_deleted:
                    where += " AND deleted_at IS NULL"
                rows = conn.execute(
                    f"""SELECT * FROM agent_memory_items
                        WHERE {where}
                        ORDER BY created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [_decode_memory_item(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def reset_agent_memory(
        self,
        *,
        client_id: str,
        agent_id: str,
        reason: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                now = datetime.now().isoformat()
                collections = conn.execute(
                    """SELECT id FROM agent_memory_collections
                       WHERE client_id=? AND agent_id=? AND status='active'""",
                    (client_id, agent_id),
                ).fetchall()
                collection_ids = [row["id"] for row in collections]
                conn.execute(
                    """UPDATE agent_memory_items
                       SET deleted_at=COALESCE(deleted_at, ?)
                       WHERE client_id=? AND agent_id=? AND deleted_at IS NULL""",
                    (now, client_id, agent_id),
                )
                conn.execute(
                    """UPDATE agent_memory_collections
                       SET status='reset', reset_at=?
                       WHERE client_id=? AND agent_id=? AND status='active'""",
                    (now, client_id, agent_id),
                )
                for collection_id in collection_ids:
                    conn.execute(
                        """INSERT INTO agent_memory_events
                           (id, collection_id, client_id, agent_id, event_type, metadata_json, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            str(uuid.uuid4()), collection_id, client_id, agent_id,
                            "memory_reset", json.dumps({"reason": reason}), now,
                        ),
                    )
                conn.commit()
                return {
                    "client_id": client_id,
                    "agent_id": agent_id,
                    "reset_at": now,
                    "collections_reset": len(collection_ids),
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_scrape_job(
        self,
        *,
        client_id: Optional[str],
        agent_id: Optional[str],
        url: str,
        domain: str,
        requested_by: Optional[str] = None,
        limits: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                job_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO website_scrape_jobs
                       (id, client_id, agent_id, url, domain, status, requested_by,
                        limits_json, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id, client_id, agent_id, url, domain, "queued",
                        requested_by, json.dumps(limits or {}), now, now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_scrape_job(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_scrape_job(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_scrape_job(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_reusable_scrape_job(
        self,
        *,
        client_id: Optional[str],
        agent_id: Optional[str],
        url: str,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = ["url=?", "status IN ('queued','dispatching','running','completed','draft_ready')"]
                params: list[Any] = [url]
                if client_id:
                    where.append("client_id=?")
                    params.append(client_id)
                else:
                    where.append("client_id IS NULL")
                if agent_id:
                    where.append("agent_id=?")
                    params.append(agent_id)
                else:
                    where.append("agent_id IS NULL")
                row = conn.execute(
                    f"""SELECT * FROM website_scrape_jobs
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC
                        LIMIT 1""",
                    params,
                ).fetchone()
                return _decode_scrape_job(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_scrape_jobs(
        self,
        *,
        client_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                safe_limit = max(1, min(int(limit or 50), 200))
                where: list[str] = []
                params: list[Any] = []
                if client_id:
                    where.append("j.client_id=?")
                    params.append(client_id)
                if agent_id:
                    where.append("j.agent_id=?")
                    params.append(agent_id)
                if status:
                    where.append("j.status=?")
                    params.append(status)
                where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                params.append(safe_limit)
                rows = conn.execute(
                    f"""SELECT j.*,
                              (SELECT COUNT(*) FROM website_page_snapshots s WHERE s.job_id=j.id) AS page_count,
                              (SELECT COUNT(*) FROM generated_script_drafts d WHERE d.job_id=j.id) AS draft_count,
                              (SELECT created_at FROM website_extractions e WHERE e.job_id=j.id ORDER BY created_at DESC LIMIT 1) AS latest_extraction_at
                       FROM website_scrape_jobs j
                       {where_sql}
                       ORDER BY j.created_at DESC
                       LIMIT ?""",
                    params,
                ).fetchall()
                jobs: list[dict] = []
                for row in rows:
                    data = dict(row)
                    page_count = data.pop("page_count", 0)
                    draft_count = data.pop("draft_count", 0)
                    latest_extraction_at = data.pop("latest_extraction_at", None)
                    job = _decode_scrape_job(data)
                    job["health"] = _scrape_job_health(job)
                    job["diagnostics"] = {
                        "page_count": page_count,
                        "draft_count": draft_count,
                        "latest_extraction_at": latest_extraction_at,
                    }
                    jobs.append(job)
                return jobs
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_scrape_job_diagnostics(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                job = _decode_scrape_job(dict(row))
                job["health"] = _scrape_job_health(job)
                snapshots = [
                    dict(item)
                    for item in conn.execute(
                        """SELECT * FROM website_page_snapshots
                           WHERE job_id=?
                           ORDER BY created_at ASC""",
                        (job_id,),
                    ).fetchall()
                ]
                extraction_row = conn.execute(
                    """SELECT * FROM website_extractions
                       WHERE job_id=?
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    (job_id,),
                ).fetchone()
                latest_extraction = None
                if extraction_row:
                    latest_extraction = dict(extraction_row)
                    try:
                        latest_extraction["extraction"] = json.loads(latest_extraction.pop("extraction_json") or "{}")
                    except Exception:
                        latest_extraction["extraction"] = {}
                drafts = [
                    _decode_script_draft(dict(item))
                    for item in conn.execute(
                        """SELECT * FROM generated_script_drafts
                           WHERE job_id=?
                           ORDER BY created_at DESC""",
                        (job_id,),
                    ).fetchall()
                ]
                events = [
                    _decode_scrape_job_event(dict(item))
                    for item in conn.execute(
                        """SELECT * FROM website_scrape_job_events
                           WHERE job_id=?
                           ORDER BY created_at DESC
                           LIMIT 50""",
                        (job_id,),
                    ).fetchall()
                ]
                job["snapshots"] = snapshots
                job["latest_extraction"] = latest_extraction
                job["drafts"] = drafts
                job["events"] = events
                job["health"] = _scrape_job_health(job)
                job["diagnostics"] = {
                    "page_count": len(snapshots),
                    "draft_count": len(drafts),
                    "latest_extraction_at": latest_extraction.get("created_at") if latest_extraction else None,
                    "has_error": bool(job.get("error")),
                }
                return job
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def queue_scrape_job_for_dispatch(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                now = datetime.now().isoformat()
                cursor = conn.execute(
                    """UPDATE website_scrape_jobs
                       SET status='dispatching', error=NULL, updated_at=?
                       WHERE id=? AND status IN ('queued','failed')""",
                    (now, job_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                job = _decode_scrape_job(dict(row))
                job["_dispatch_enqueued"] = cursor.rowcount > 0
                return job
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def mark_scrape_job_running(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                now = datetime.now().isoformat()
                cursor = conn.execute(
                    """UPDATE website_scrape_jobs
                       SET status='running', error=NULL, updated_at=?
                       WHERE id=? AND status IN ('queued','failed','dispatching')""",
                    (now, job_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                job = _decode_scrape_job(dict(row))
                job["_started"] = cursor.rowcount > 0
                return job
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def cancel_scrape_job(self, job_id: str, reason: Optional[str] = None) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE website_scrape_jobs
                       SET status='cancelled', error=?, updated_at=?, completed_at=COALESCE(completed_at, ?)
                       WHERE id=? AND status IN ('queued','dispatching','running')""",
                    (reason or "cancelled_by_admin", now, now, job_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_scrape_job(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def recover_stale_scrape_job(
        self,
        job_id: str,
        *,
        stale_after_minutes: int = 15,
        reason: Optional[str] = None,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                job = _decode_scrape_job(dict(row))
                health = _scrape_job_health(job, stale_after_minutes=stale_after_minutes)
                if not health["is_stale"]:
                    job["health"] = health
                    job["_stale_recovered"] = False
                    return job

                now = datetime.now().isoformat()
                cursor = conn.execute(
                    """UPDATE website_scrape_jobs
                       SET status='failed', error=?, updated_at=?, completed_at=COALESCE(completed_at, ?)
                       WHERE id=? AND status IN ('dispatching','running')""",
                    (reason or "stale_worker_recovered", now, now, job_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM website_scrape_jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return None
                recovered = _decode_scrape_job(dict(row))
                recovered["health"] = _scrape_job_health(recovered, stale_after_minutes=stale_after_minutes)
                recovered["_stale_recovered"] = cursor.rowcount > 0
                return recovered
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def append_scrape_job_event(
        self,
        job_id: str,
        event_type: str,
        *,
        status: Optional[str] = None,
        actor: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                event_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO website_scrape_job_events
                       (id, job_id, event_type, status, actor, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        job_id,
                        event_type,
                        status,
                        actor,
                        json.dumps(metadata or {}),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM website_scrape_job_events WHERE id=?",
                    (event_id,),
                ).fetchone()
                return _decode_scrape_job_event(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_scrape_job_events(self, job_id: str, limit: int = 50) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                safe_limit = max(1, min(int(limit or 50), 200))
                rows = conn.execute(
                    """SELECT * FROM website_scrape_job_events
                       WHERE job_id=?
                       ORDER BY created_at DESC
                       LIMIT ?""",
                    (job_id, safe_limit),
                ).fetchall()
                return [_decode_scrape_job_event(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def update_scrape_job_status(self, job_id: str, status: str, error: Optional[str] = None) -> None:
        def _sync():
            conn = _get_connection()
            try:
                completed_at = datetime.now().isoformat() if status in {"completed", "failed", "draft_ready", "cancelled"} else None
                conn.execute(
                    """UPDATE website_scrape_jobs
                       SET status=?, error=?, updated_at=?, completed_at=COALESCE(?, completed_at)
                       WHERE id=?""",
                    (status, error, datetime.now().isoformat(), completed_at, job_id),
                )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def update_scrape_job_progress(self, job_id: str, progress: Optional[str]) -> None:
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    """UPDATE website_scrape_jobs
                       SET progress=?, updated_at=?
                       WHERE id=?""",
                    (progress, datetime.now().isoformat(), job_id),
                )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def save_scrape_extraction(self, job_id: str, extraction: dict) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                extraction_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO website_extractions
                       (id, job_id, extraction_json, created_at)
                       VALUES (?,?,?,?)""",
                    (extraction_id, job_id, json.dumps(extraction), datetime.now().isoformat()),
                )
                conn.commit()
                return {"id": extraction_id, "job_id": job_id, "extraction": extraction}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_latest_scrape_extraction(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT * FROM website_extractions
                       WHERE job_id=?
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    (job_id,),
                ).fetchone()
                if not row:
                    return None
                data = dict(row)
                try:
                    data["extraction"] = json.loads(data.pop("extraction_json") or "{}")
                except Exception:
                    data["extraction"] = {}
                return data
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def save_page_snapshot(
        self,
        *,
        job_id: str,
        url: str,
        content_hash: Optional[str],
        content_type: Optional[str],
        storage_path: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                snapshot_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO website_page_snapshots
                       (id, job_id, url, content_hash, content_type, storage_path, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        snapshot_id,
                        job_id,
                        url,
                        content_hash,
                        content_type,
                        storage_path,
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                return {
                    "id": snapshot_id,
                    "job_id": job_id,
                    "url": url,
                    "content_hash": content_hash,
                    "content_type": content_type,
                    "storage_path": storage_path,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_page_snapshots(self, job_id: str) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                rows = conn.execute(
                    """SELECT * FROM website_page_snapshots
                       WHERE job_id=?
                       ORDER BY created_at ASC""",
                    (job_id,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_generated_script_draft(
        self,
        *,
        job_id: str,
        client_id: Optional[str],
        agent_id: str,
        status: str,
        draft_json: dict,
        knowledge_json: dict,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                draft_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO generated_script_drafts
                       (id, job_id, client_id, agent_id, status, draft_json, knowledge_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        draft_id, job_id, client_id, agent_id, status,
                        json.dumps(draft_json), json.dumps(knowledge_json),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM generated_script_drafts WHERE id=?", (draft_id,)).fetchone()
                return _decode_script_draft(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_generated_script_draft(self, draft_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM generated_script_drafts WHERE id=?", (draft_id,)).fetchone()
                return _decode_script_draft(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def delete_generated_script_draft(self, draft_id: str) -> bool:
        def _sync():
            conn = _get_connection()
            try:
                cursor = conn.execute("DELETE FROM generated_script_drafts WHERE id=?", (draft_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_generated_script_draft_for_job(self, *, job_id: str, agent_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT * FROM generated_script_drafts
                       WHERE job_id=? AND agent_id=?
                       ORDER BY created_at DESC
                       LIMIT 1""",
                    (job_id, agent_id),
                ).fetchone()
                return _decode_script_draft(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_generated_script_drafts(
        self,
        *,
        agent_id: str,
        client_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                safe_limit = max(1, min(int(limit or 20), 100))
                params: list[Any] = [agent_id]
                where = "agent_id=?"
                if client_id:
                    where += " AND client_id=?"
                    params.append(client_id)
                params.append(safe_limit)
                rows = conn.execute(
                    f"""SELECT * FROM generated_script_drafts
                        WHERE {where}
                        ORDER BY created_at DESC
                        LIMIT ?""",
                    params,
                ).fetchall()
                return [_decode_script_draft(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def mark_generated_script_draft_reviewed(
        self,
        draft_id: str,
        *,
        status: str = "flow_draft_saved",
        reviewed_by: Optional[str] = None,
        review_notes: Optional[str] = None,
        flow_version_id: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM generated_script_drafts WHERE id=?", (draft_id,)).fetchone()
                if not row:
                    raise ValueError(f"generated script draft not found: {draft_id}")
                reviewed_at = datetime.now().isoformat()
                conn.execute(
                    """UPDATE generated_script_drafts
                       SET status=?,
                           reviewed_at=?,
                           reviewed_by=?,
                           review_notes=?,
                           flow_version_id=COALESCE(?, flow_version_id)
                       WHERE id=?""",
                    (
                        status,
                        reviewed_at,
                        reviewed_by,
                        review_notes,
                        flow_version_id,
                        draft_id,
                    ),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM generated_script_drafts WHERE id=?", (draft_id,)).fetchone()
                return _decode_script_draft(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_crm_connection(
        self,
        *,
        client_id: str,
        provider: str,
        display_name: str,
        external_account_id: Optional[str] = None,
        config: Optional[dict] = None,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                client = conn.execute("SELECT id FROM clients WHERE id=?", (client_id,)).fetchone()
                if not client:
                    raise ValueError(f"client not found: {client_id}")

                connection_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO crm_connections
                       (id, client_id, provider, display_name, external_account_id, status,
                        config_json, secrets_configured, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        connection_id, client_id, provider, display_name,
                        external_account_id, "draft", json.dumps(config or {}),
                        0, now, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.connection_created", "crm_connection", connection_id,
                        json.dumps({"provider": provider, "runtime_sync": False}),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM crm_connections WHERE id=?", (connection_id,)).fetchone()
                return _decode_crm_connection(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_crm_connection(self, connection_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM crm_connections WHERE id=?", (connection_id,)).fetchone()
                return _decode_crm_connection(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_crm_connections(
        self,
        *,
        client_id: Optional[str] = None,
        include_disabled: bool = False,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = []
                params: list[Any] = []
                if client_id:
                    where.append("client_id=?")
                    params.append(client_id)
                if not include_disabled:
                    where.append("disabled_at IS NULL")
                where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                rows = conn.execute(
                    f"SELECT * FROM crm_connections {where_sql} ORDER BY created_at DESC",
                    tuple(params),
                ).fetchall()
                return [_decode_crm_connection(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def configure_crm_connection_secret_reference(
        self,
        *,
        connection_id: str,
        client_id: str,
        credential_reference: dict,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_connections WHERE id=?",
                    (connection_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm connection not found: {connection_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm connection is outside tenant scope")
                if row["disabled_at"]:
                    raise ValueError("crm connection is disabled")

                try:
                    config = json.loads(row["config_json"] or "{}")
                except Exception:
                    config = {}
                config["credential_reference"] = credential_reference
                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_connections
                       SET config_json=?, secrets_configured=1, status='configured', updated_at=?
                       WHERE id=?""",
                    (json.dumps(config), now, connection_id),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.connection_secret_reference_configured",
                        "crm_connection", connection_id,
                        json.dumps({
                            "vault_provider": credential_reference.get("vault_provider"),
                            "reference_hash": credential_reference.get("reference_hash"),
                            "runtime_sync": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM crm_connections WHERE id=?", (connection_id,)).fetchone()
                return _decode_crm_connection(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_crm_sync_job(
        self,
        *,
        client_id: str,
        connection_id: str,
        campaign_id: Optional[str] = None,
        mode: str = "dry_run",
        direction: str = "outbound",
        payload: Optional[dict] = None,
        requested_by: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                connection = conn.execute(
                    "SELECT * FROM crm_connections WHERE id=?",
                    (connection_id,),
                ).fetchone()
                if not connection:
                    raise ValueError(f"crm connection not found: {connection_id}")
                if connection["client_id"] != client_id:
                    raise ValueError("crm connection is outside tenant scope")
                if connection["disabled_at"]:
                    raise ValueError("crm connection is disabled")

                if campaign_id:
                    campaign = conn.execute(
                        "SELECT * FROM campaigns WHERE id=?",
                        (campaign_id,),
                    ).fetchone()
                    if not campaign:
                        raise ValueError(f"campaign not found: {campaign_id}")
                    if not campaign["client_id"]:
                        raise ValueError("campaign must be tenant-owned before CRM sync")
                    if campaign["client_id"] != client_id:
                        raise ValueError("campaign is outside CRM tenant scope")

                now = datetime.now().isoformat()
                idem = idempotency_key or f"{client_id}:{connection_id}:{campaign_id or 'none'}:{mode}:{direction}"
                existing = conn.execute(
                    "SELECT * FROM crm_sync_jobs WHERE idempotency_key=?",
                    (idem,),
                ).fetchone()
                if existing:
                    return _decode_crm_sync_job(dict(existing))

                job_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO crm_sync_jobs
                       (id, client_id, connection_id, campaign_id, status, mode, direction,
                        idempotency_key, payload_json, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id, client_id, connection_id, campaign_id, "planned",
                        mode, direction, idem, json.dumps(payload or {}), now, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), job_id, client_id, connection_id,
                        campaign_id, "sync_planned",
                        json.dumps({"mode": mode, "direction": direction, "runtime_sync": False}),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.sync_job_planned", "crm_sync_job", job_id,
                        json.dumps({"connection_id": connection_id, "campaign_id": campaign_id, "mode": mode}),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM crm_sync_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_crm_sync_job(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_crm_sync_jobs(
        self,
        *,
        client_id: str,
        connection_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = ["client_id=?"]
                params: list[Any] = [client_id]
                if connection_id:
                    where.append("connection_id=?")
                    params.append(connection_id)
                if campaign_id:
                    where.append("campaign_id=?")
                    params.append(campaign_id)
                rows = conn.execute(
                    f"""SELECT * FROM crm_sync_jobs
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [_decode_crm_sync_job(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_crm_sync_job(self, job_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM crm_sync_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_crm_sync_job(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def record_crm_sync_event(
        self,
        *,
        job_id: str,
        client_id: str,
        connection_id: Optional[str],
        campaign_id: Optional[str],
        event_type: str,
        payload: Optional[dict] = None,
        status: Optional[str] = None,
        completed: bool = False,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                job = conn.execute("SELECT * FROM crm_sync_jobs WHERE id=?", (job_id,)).fetchone()
                if not job:
                    return None
                if job["client_id"] != client_id:
                    raise ValueError("crm sync job is outside tenant scope")

                now = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), job_id, client_id, connection_id,
                        campaign_id, event_type, json.dumps(payload or {}), now,
                    ),
                )
                if status:
                    conn.execute(
                        """UPDATE crm_sync_jobs
                           SET status=?, payload_json=?, updated_at=?, completed_at=COALESCE(?, completed_at)
                           WHERE id=?""",
                        (
                            status,
                            json.dumps(payload or job["payload_json"] or {}),
                            now,
                            now if completed else None,
                            job_id,
                        ),
                    )
                conn.commit()
                row = conn.execute("SELECT * FROM crm_sync_jobs WHERE id=?", (job_id,)).fetchone()
                return _decode_crm_sync_job(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_crm_sync_events(
        self,
        *,
        client_id: str,
        job_id: Optional[str] = None,
        connection_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = ["client_id=?"]
                params: list[Any] = [client_id]
                if job_id:
                    where.append("job_id=?")
                    params.append(job_id)
                if connection_id:
                    where.append("connection_id=?")
                    params.append(connection_id)
                if campaign_id:
                    where.append("campaign_id=?")
                    params.append(campaign_id)
                rows = conn.execute(
                    f"""SELECT * FROM crm_sync_events
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [_decode_crm_sync_event(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_crm_sync_outbox_item(
        self,
        *,
        client_id: str,
        job_id: str,
        payload: Optional[dict] = None,
        requested_by: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                job = conn.execute(
                    "SELECT * FROM crm_sync_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if not job:
                    raise ValueError(f"crm sync job not found: {job_id}")
                if job["client_id"] != client_id:
                    raise ValueError("crm sync job is outside tenant scope")
                if job["status"] != "preflight_validated":
                    raise ValueError("crm sync job must pass preflight before outbox queue")

                idem = idempotency_key or f"{client_id}:{job_id}:crm_outbox_shadow"
                existing = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE idempotency_key=?",
                    (idem,),
                ).fetchone()
                if existing:
                    return _decode_crm_sync_outbox(dict(existing))

                now = datetime.now().isoformat()
                outbox_id = str(uuid.uuid4())
                safe_payload = payload or {}
                conn.execute(
                    """INSERT INTO crm_sync_outbox
                       (id, job_id, client_id, connection_id, campaign_id, status, mode,
                        idempotency_key, payload_json, attempt_count, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        outbox_id, job_id, client_id, job["connection_id"], job["campaign_id"],
                        "queued_shadow", "shadow", idem, json.dumps(safe_payload),
                        0, now, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), job_id, client_id, job["connection_id"],
                        job["campaign_id"], "outbox_queued_shadow",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "runtime_campaign_hook": False,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_item_queued_shadow", "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": job_id,
                            "connection_id": job["connection_id"],
                            "campaign_id": job["campaign_id"],
                            "external_execution": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_crm_sync_outbox_items(
        self,
        *,
        client_id: str,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = ["client_id=?"]
                params: list[Any] = [client_id]
                if job_id:
                    where.append("job_id=?")
                    params.append(job_id)
                if status:
                    where.append("status=?")
                    params.append(status)
                rows = conn.execute(
                    f"""SELECT * FROM crm_sync_outbox
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [_decode_crm_sync_outbox(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_crm_sync_outbox_summary(
        self,
        *,
        client_id: str,
        job_id: Optional[str] = None,
        connection_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                where = ["client_id=?"]
                params: list[Any] = [client_id]
                if job_id:
                    where.append("job_id=?")
                    params.append(job_id)
                if connection_id:
                    where.append("connection_id=?")
                    params.append(connection_id)
                if campaign_id:
                    where.append("campaign_id=?")
                    params.append(campaign_id)
                where_sql = " AND ".join(where)
                rows = conn.execute(
                    f"""SELECT status, COUNT(*) AS count
                        FROM crm_sync_outbox
                        WHERE {where_sql}
                        GROUP BY status""",
                    tuple(params),
                ).fetchall()
                totals = conn.execute(
                    f"""SELECT COUNT(*) AS total_items,
                               COALESCE(MAX(attempt_count), 0) AS max_attempt_count,
                               MIN(CASE WHEN status='queued_shadow' THEN created_at END) AS oldest_queued_at,
                               MIN(CASE WHEN status='retry_scheduled_shadow' THEN next_retry_at END) AS oldest_retry_at,
                               MAX(updated_at) AS latest_update_at,
                               MIN(created_at) AS first_created_at
                        FROM crm_sync_outbox
                        WHERE {where_sql}""",
                    tuple(params),
                ).fetchone()
                status_counts = {
                    str(row["status"] or "unknown"): int(row["count"] or 0)
                    for row in rows
                }
                total_count = int(totals["total_items"] or 0) if totals else 0
                return {
                    "client_id": client_id,
                    "filters": {
                        "job_id": job_id,
                        "connection_id": connection_id,
                        "campaign_id": campaign_id,
                    },
                    "total_items": total_count,
                    "status_counts": status_counts,
                    "queued_shadow_count": status_counts.get("queued_shadow", 0),
                    "processing_shadow_count": status_counts.get("processing_shadow", 0),
                    "completed_shadow_count": status_counts.get("completed_shadow", 0),
                    "retry_scheduled_shadow_count": status_counts.get("retry_scheduled_shadow", 0),
                    "dead_letter_shadow_count": status_counts.get("dead_letter_shadow", 0),
                    "max_attempt_count": int(totals["max_attempt_count"] or 0) if totals else 0,
                    "oldest_queued_at": totals["oldest_queued_at"] if totals else None,
                    "oldest_retry_at": totals["oldest_retry_at"] if totals else None,
                    "latest_update_at": totals["latest_update_at"] if totals else None,
                    "first_created_at": totals["first_created_at"] if totals else None,
                    "payloads_included": False,
                    "provider_records_included": False,
                    "last_error_included": False,
                    "external_execution": False,
                    "worker_dispatch_enabled": False,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_crm_sync_outbox_item(self, outbox_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def start_crm_sync_outbox_shadow_processing(
        self,
        *,
        client_id: str,
        outbox_id: str,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if row["status"] != "queued_shadow":
                    raise ValueError("crm outbox item must be queued_shadow before shadow processing")

                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_sync_outbox
                       SET status='processing_shadow',
                           attempt_count=attempt_count + 1,
                           locked_at=?,
                           updated_at=?
                       WHERE id=?""",
                    (now, now, outbox_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), row["job_id"], client_id,
                        row["connection_id"], row["campaign_id"],
                        "outbox_processing_shadow_started",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_shadow_processing_started",
                        "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": row["job_id"],
                            "connection_id": row["connection_id"],
                            "external_execution": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def complete_crm_sync_outbox_shadow_processing(
        self,
        *,
        client_id: str,
        outbox_id: str,
        result: dict,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if row["status"] != "processing_shadow":
                    raise ValueError("crm outbox item must be processing_shadow before completion")

                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                payload["shadow_worker_result"] = result
                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_sync_outbox
                       SET status='completed_shadow',
                           payload_json=?,
                           updated_at=?,
                           completed_at=?
                       WHERE id=?""",
                    (json.dumps(payload), now, now, outbox_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), row["job_id"], client_id,
                        row["connection_id"], row["campaign_id"],
                        "outbox_completed_shadow",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                            "result": result,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_shadow_processing_completed",
                        "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": row["job_id"],
                            "connection_id": row["connection_id"],
                            "external_execution": False,
                            "sent_to_provider": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def mark_crm_sync_outbox_shadow_retry(
        self,
        *,
        client_id: str,
        outbox_id: str,
        error: str,
        next_retry_at: Optional[str],
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if row["status"] not in {"queued_shadow", "processing_shadow"}:
                    raise ValueError("crm outbox item cannot be marked retry from its current status")

                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_sync_outbox
                       SET status='retry_scheduled_shadow',
                           last_error=?,
                           next_retry_at=?,
                           updated_at=?,
                           locked_at=NULL
                       WHERE id=?""",
                    (error, next_retry_at, now, outbox_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), row["job_id"], client_id,
                        row["connection_id"], row["campaign_id"],
                        "outbox_retry_scheduled_shadow",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                            "next_retry_at": next_retry_at,
                            "error": error,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_shadow_retry_scheduled",
                        "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": row["job_id"],
                            "connection_id": row["connection_id"],
                            "external_execution": False,
                            "next_retry_at": next_retry_at,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def requeue_crm_sync_outbox_shadow_retry(
        self,
        *,
        client_id: str,
        outbox_id: str,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if row["status"] != "retry_scheduled_shadow":
                    raise ValueError("crm outbox item must be retry_scheduled_shadow before requeue")

                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_sync_outbox
                       SET status='queued_shadow',
                           next_retry_at=NULL,
                           updated_at=?
                       WHERE id=?""",
                    (now, outbox_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), row["job_id"], client_id,
                        row["connection_id"], row["campaign_id"],
                        "outbox_requeued_shadow",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_shadow_requeued",
                        "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": row["job_id"],
                            "connection_id": row["connection_id"],
                            "external_execution": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def mark_crm_sync_outbox_shadow_dead_letter(
        self,
        *,
        client_id: str,
        outbox_id: str,
        error: str,
        requested_by: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if row["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if row["status"] == "completed_shadow":
                    raise ValueError("completed crm outbox item cannot be dead-lettered")

                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_sync_outbox
                       SET status='dead_letter_shadow',
                           last_error=?,
                           next_retry_at=NULL,
                           updated_at=?,
                           completed_at=COALESCE(completed_at, ?)
                       WHERE id=?""",
                    (error, now, now, outbox_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), row["job_id"], client_id,
                        row["connection_id"], row["campaign_id"],
                        "outbox_dead_letter_shadow",
                        json.dumps({
                            "outbox_id": outbox_id,
                            "mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                            "error": error,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, requested_by,
                        "crm.outbox_shadow_dead_lettered",
                        "crm_sync_outbox", outbox_id,
                        json.dumps({
                            "job_id": row["job_id"],
                            "connection_id": row["connection_id"],
                            "external_execution": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                return _decode_crm_sync_outbox(dict(updated))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_crm_delivery_approval(
        self,
        *,
        client_id: str,
        outbox_id: str,
        plan_hash: str,
        plan_summary: dict,
        approved_by: Optional[str] = None,
        requested_by: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                outbox = conn.execute(
                    "SELECT * FROM crm_sync_outbox WHERE id=?",
                    (outbox_id,),
                ).fetchone()
                if not outbox:
                    raise ValueError(f"crm outbox item not found: {outbox_id}")
                if outbox["client_id"] != client_id:
                    raise ValueError("crm outbox item is outside tenant scope")
                if outbox["status"] not in {"queued_shadow", "retry_scheduled_shadow"}:
                    raise ValueError("crm delivery approval requires a queued or retryable outbox item")

                idem = idempotency_key or f"{client_id}:{outbox_id}:{plan_hash}:delivery_approval_shadow"
                existing = conn.execute(
                    "SELECT * FROM crm_delivery_approvals WHERE idempotency_key=?",
                    (idem,),
                ).fetchone()
                if existing:
                    return _decode_crm_delivery_approval(dict(existing))

                now = datetime.now().isoformat()
                approval_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO crm_delivery_approvals
                       (id, outbox_id, job_id, client_id, connection_id, campaign_id, status,
                        approval_mode, plan_hash, plan_summary_json, approved_by, requested_by,
                        idempotency_key, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        approval_id, outbox_id, outbox["job_id"], client_id,
                        outbox["connection_id"], outbox["campaign_id"],
                        "approved_shadow", "shadow", plan_hash,
                        json.dumps(plan_summary), approved_by, requested_by,
                        idem, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), outbox["job_id"], client_id,
                        outbox["connection_id"], outbox["campaign_id"],
                        "delivery_approval_shadow_created",
                        json.dumps({
                            "approval_id": approval_id,
                            "outbox_id": outbox_id,
                            "plan_hash": plan_hash,
                            "approval_mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                            "live_sync_enabled": False,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, approved_by or requested_by,
                        "crm.delivery_approval_shadow_created",
                        "crm_delivery_approval", approval_id,
                        json.dumps({
                            "outbox_id": outbox_id,
                            "job_id": outbox["job_id"],
                            "connection_id": outbox["connection_id"],
                            "plan_hash": plan_hash,
                            "external_execution": False,
                            "live_sync_enabled": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM crm_delivery_approvals WHERE id=?",
                    (approval_id,),
                ).fetchone()
                return _decode_crm_delivery_approval(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_crm_delivery_approvals(
        self,
        *,
        client_id: str,
        outbox_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = ["client_id=?"]
                params: list[Any] = [client_id]
                if outbox_id:
                    where.append("outbox_id=?")
                    params.append(outbox_id)
                if status:
                    where.append("status=?")
                    params.append(status)
                rows = conn.execute(
                    f"""SELECT * FROM crm_delivery_approvals
                        WHERE {' AND '.join(where)}
                        ORDER BY created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [_decode_crm_delivery_approval(dict(row)) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_crm_delivery_approval(self, approval_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM crm_delivery_approvals WHERE id=?",
                    (approval_id,),
                ).fetchone()
                return _decode_crm_delivery_approval(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def revoke_crm_delivery_approval(
        self,
        *,
        client_id: str,
        approval_id: str,
        revoked_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                approval = conn.execute(
                    "SELECT * FROM crm_delivery_approvals WHERE id=?",
                    (approval_id,),
                ).fetchone()
                if not approval:
                    raise ValueError(f"crm delivery approval not found: {approval_id}")
                if approval["client_id"] != client_id:
                    raise ValueError("crm delivery approval is outside tenant scope")
                if approval["status"] == "revoked_shadow":
                    return _decode_crm_delivery_approval(dict(approval))

                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE crm_delivery_approvals
                       SET status='revoked_shadow', revoked_at=?
                       WHERE id=?""",
                    (now, approval_id),
                )
                conn.execute(
                    """INSERT INTO crm_sync_events
                       (id, job_id, client_id, connection_id, campaign_id, event_type, payload_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), approval["job_id"], client_id,
                        approval["connection_id"], approval["campaign_id"],
                        "delivery_approval_shadow_revoked",
                        json.dumps({
                            "approval_id": approval_id,
                            "outbox_id": approval["outbox_id"],
                            "plan_hash": approval["plan_hash"],
                            "approval_mode": "shadow",
                            "external_execution": False,
                            "worker_dispatch_enabled": False,
                            "live_sync_enabled": False,
                            "reason": reason,
                        }),
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, revoked_by,
                        "crm.delivery_approval_shadow_revoked",
                        "crm_delivery_approval", approval_id,
                        json.dumps({
                            "outbox_id": approval["outbox_id"],
                            "job_id": approval["job_id"],
                            "connection_id": approval["connection_id"],
                            "plan_hash": approval["plan_hash"],
                            "external_execution": False,
                            "live_sync_enabled": False,
                            "reason": reason,
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM crm_delivery_approvals WHERE id=?",
                    (approval_id,),
                ).fetchone()
                return _decode_crm_delivery_approval(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_campaigns(self, client_id: Optional[str] = None) -> list[dict]:
        return await self.list_campaigns_with_lifecycle(client_id)

    async def list_campaigns_with_lifecycle(
        self,
        client_id: Optional[str] = None,
        *,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                where = []
                params = []
                if client_id:
                    where.append("client_id=?")
                    params.append(client_id)
                if not include_archived:
                    where.append("archived_at IS NULL")
                if not include_deleted:
                    where.append("deleted_at IS NULL")
                where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                rows = conn.execute(
                    f"""SELECT campaigns.*,
                               (SELECT COUNT(*) FROM leads WHERE leads.campaign_id=campaigns.id) AS lead_count,
                               (SELECT COUNT(*) FROM call_results WHERE call_results.campaign_id=campaigns.id) AS result_count
                          FROM campaigns
                          {where_sql}
                         ORDER BY campaigns.created_at DESC""",
                    tuple(params),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_campaign(self, campaign_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def upsert_campaign(self, campaign_id: str, data: dict) -> None:
        def _sync():
            conn = _get_connection()
            try:
                existing = conn.execute("SELECT id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if existing:
                    # Bug 4 fix: also update name and client_id so they are not silently dropped
                    conn.execute(
                        """UPDATE campaigns
                           SET status=?, agent_id=?, client_id=COALESCE(?, client_id),
                               name=COALESCE(?, name), telephony_provider=?, started_at=?, completed_at=?,
                               updated_at=?
                           WHERE id=?""",
                        (
                            data.get("status"), data.get("agent_id"),
                            data.get("client_id"), data.get("name"),
                            data.get("telephony_provider"), data.get("started_at"),
                            data.get("completed_at"), datetime.now().isoformat(), campaign_id,
                        )
                    )
                else:
                    conn.execute(
                        """INSERT INTO campaigns (id, name, status, agent_id, client_id, telephony_provider, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            campaign_id, data.get("name", ""), data.get("status", "Pending"),
                            data.get("agent_id"), data.get("client_id"),
                            data.get("telephony_provider", "demo"),
                            data.get("created_at", datetime.now().isoformat()),
                            datetime.now().isoformat(),
                        )
                    )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def set_campaign_status(self, campaign_id: str, status: str) -> None:
        def _sync():
            conn = _get_connection()
            try:
                ts_col = "started_at" if status == "Active" else "completed_at" if status == "Done" else None
                if ts_col:
                    conn.execute(
                        f"UPDATE campaigns SET status=?, {ts_col}=?, updated_at=? WHERE id=?",
                        (status, datetime.now().isoformat(), datetime.now().isoformat(), campaign_id)
                    )
                else:
                    conn.execute(
                        "UPDATE campaigns SET status=?, updated_at=? WHERE id=?",
                        (status, datetime.now().isoformat(), campaign_id),
                    )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    # ── Leads ────────────────────────────────────────────────────────────────

    async def append_tenant_audit_event(
        self,
        *,
        client_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: str,
        actor_email: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                event_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        event_id, client_id, actor_email, action, resource_type,
                        resource_id, json.dumps(metadata or {}), datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM tenant_audit_events WHERE id=?", (event_id,)).fetchone()
                return _decode_audit_event(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def set_campaign_archived(
        self,
        campaign_id: str,
        *,
        archived: bool,
        actor_email: Optional[str] = None,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    return None
                now = datetime.now().isoformat()
                archived_at = now if archived else None
                conn.execute(
                    "UPDATE campaigns SET archived_at=?, updated_at=? WHERE id=?",
                    (archived_at, now, campaign_id),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), campaign["client_id"], actor_email,
                        "campaign.archived" if archived else "campaign.unarchived",
                        "campaign", campaign_id,
                        json.dumps({"archived": archived, "previous_archived_at": campaign["archived_at"]}),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def soft_delete_campaign(
        self,
        campaign_id: str,
        *,
        reason: Optional[str] = None,
        actor_email: Optional[str] = None,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    return None
                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE campaigns
                       SET deleted_at=COALESCE(deleted_at, ?), delete_reason=COALESCE(?, delete_reason),
                           archived_at=COALESCE(archived_at, ?), updated_at=?
                       WHERE id=?""",
                    (now, reason, now, now, campaign_id),
                )
                manifest = _create_campaign_cleanup_manifest_sync(
                    conn,
                    campaign_id=campaign_id,
                    client_id=campaign["client_id"],
                    action="soft_delete",
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), campaign["client_id"], actor_email,
                        "campaign.soft_deleted", "campaign", campaign_id,
                        json.dumps({
                            "reason": reason,
                            "cleanup_manifest_id": manifest["id"],
                            "physical_delete": False,
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                return {"campaign": dict(row), "cleanup_manifest": manifest}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def restore_campaign_lifecycle(
        self,
        campaign_id: str,
        *,
        actor_email: Optional[str] = None,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    return None
                now = datetime.now().isoformat()
                conn.execute(
                    """UPDATE campaigns
                       SET archived_at=NULL, deleted_at=NULL, delete_reason=NULL, updated_at=?
                       WHERE id=?""",
                    (now, campaign_id),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, actor_email, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), campaign["client_id"], actor_email,
                        "campaign.restored", "campaign", campaign_id,
                        json.dumps({
                            "previous_archived_at": campaign["archived_at"],
                            "previous_deleted_at": campaign["deleted_at"],
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_campaign_lifecycle_summary(self, campaign_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    return None
                manifests = conn.execute(
                    """SELECT * FROM campaign_cleanup_manifests
                       WHERE campaign_id=?
                       ORDER BY created_at DESC""",
                    (campaign_id,),
                ).fetchall()
                events = conn.execute(
                    """SELECT * FROM tenant_audit_events
                       WHERE resource_type='campaign' AND resource_id=?
                       ORDER BY created_at DESC""",
                    (campaign_id,),
                ).fetchall()
                return {
                    "campaign": dict(campaign),
                    "related_counts": _campaign_related_counts_sync(conn, campaign_id),
                    "cleanup_manifests": [_decode_cleanup_manifest(dict(row)) for row in manifests],
                    "audit_events": [_decode_audit_event(dict(row)) for row in events],
                    "retention": _campaign_retention_policy(),
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_leads_for_campaign(self, campaign_id: str) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM leads WHERE campaign_id=? ORDER BY created_at", (campaign_id,)
                ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try:
                        extra = json.loads(d.get("extra_data") or "{}")
                        d.update(extra)
                    except Exception:
                        pass
                    result.append(d)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def upsert_leads(self, campaign_id: str, leads: list[dict]) -> None:
        def _sync():
            conn = _get_connection()
            try:
                client_id = _client_id_for_campaign_sync(conn, campaign_id)
                # Clear existing leads for this campaign
                conn.execute("DELETE FROM leads WHERE campaign_id=?", (campaign_id,))
                for lead in leads:
                    lid = f"{campaign_id}_{lead.get('phone', '')}"
                    extra = {k: v for k, v in lead.items() if k not in ("name", "phone")}
                    conn.execute(
                        """INSERT OR REPLACE INTO leads
                           (id, campaign_id, client_id, name, phone, extra_data, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (lid, campaign_id, client_id, lead.get("name"), lead.get("phone"),
                         json.dumps(extra), datetime.now().isoformat())
                    )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    # ── Call Results ─────────────────────────────────────────────────────────

    async def append_call_result(self, campaign_id: str, result: dict) -> None:
        def _sync():
            conn = _get_connection()
            try:
                import uuid as _uuid
                rid = str(_uuid.uuid4())

                # call_key: a stable non-FK text identifier for this call.
                # We store it in the non-FK call_key column so transcript lookups
                # work without violating the FOREIGN KEY constraint on lead_id
                # (which references leads.id — a real row that may not exist for
                # demo/mic sessions or Twilio calls not tied to a lead row).
                phone = result.get("phone", "")
                call_key = f"{campaign_id}_{phone}" if phone else rid

                lead_data_json = json.dumps(result.get("lead_data", {}))
                callback_value = (
                    result.get("callback")
                    or result.get("timeline")
                    or result.get("callback_time")
                )
                client_id = result.get("client_id") or _client_id_for_campaign_sync(conn, campaign_id)

                # Ensure non-FK columns exist (backwards compat)
                for col_sql in [
                    "ALTER TABLE call_results ADD COLUMN lead_data TEXT",
                    "ALTER TABLE call_results ADD COLUMN call_key TEXT",
                    "ALTER TABLE call_results ADD COLUMN client_id TEXT REFERENCES clients(id)",
                ]:
                    try:
                        conn.execute(col_sql)
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                conn.execute(
                    """INSERT INTO call_results
                       (id, campaign_id, client_id, call_key, lead_name, phone, called_at, duration, status,
                        outcome, interested, budget, callback_time, transcription, provider, processed,
                        lead_data, recording_url)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rid, campaign_id, client_id, call_key,
                        result.get("name"), result.get("phone"),
                        result.get("calledAt"), result.get("duration"),
                        result.get("status", "Connected"), result.get("outcome"),
                        result.get("interested"), result.get("budget"),
                        callback_value, json.dumps(result.get("transcription", [])),
                        result.get("provider", "demo"), 1 if result.get("processed") else 0,
                        lead_data_json, result.get("recording_url"),
                    )
                )
                if result.get("recording_url"):
                    conn.execute(
                        """INSERT INTO recording_assets
                           (id, client_id, campaign_id, call_result_id, lead_id, recording_url, storage_path, created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            str(_uuid.uuid4()), client_id, campaign_id, rid, call_key,
                            result.get("recording_url"), result.get("recording_url"),
                            datetime.now().isoformat(),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def get_results_for_campaign(self, campaign_id: str, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if client_id:
                    rows = conn.execute(
                        """SELECT * FROM call_results
                           WHERE campaign_id=? AND client_id=?
                           ORDER BY called_at DESC""",
                        (campaign_id, client_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM call_results WHERE campaign_id=? ORDER BY called_at DESC",
                        (campaign_id,)
                    ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try:
                        transcript = json.loads(d.get("transcription") or "[]")
                        d["has_transcript"] = len(transcript) > 0
                    except Exception:
                        d["has_transcript"] = False
                    d.pop("transcription", None)
                    d["has_recording"] = bool(d.get("recording_url"))
                    lead_data = d.get("lead_data")
                    if isinstance(lead_data, str):
                        try:
                            lead_data = json.loads(lead_data)
                        except Exception:
                            lead_data = {}
                    elif not isinstance(lead_data, dict):
                        lead_data = {}
                    callback_value = (
                        d.get("callback")
                        or d.get("timeline")
                        or d.get("callback_time")
                        or lead_data.get("timeline")
                        or "—"
                    )
                    # Normalize DB snake_case row keys to frontend camelCase keys.
                    d["name"] = d.get("name") or d.get("lead_name") or "—"
                    d["calledAt"] = d.get("calledAt") or d.get("called_at") or "—"
                    d["callback"] = callback_value
                    d["timeline"] = callback_value
                    # Expose call_key as lead_id so the frontend's (l.lead_id || l.id)
                    # pattern resolves to the correct lookup key for transcripts.
                    d["lead_id"] = d.get("call_key") or d.get("lead_id") or d.get("id")
                    result.append(d)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_call_result_owner_for_transcript(self, lead_id: str) -> dict:
        """Return owner metadata only for transcript read shadow diagnostics."""
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT client_id, campaign_id
                       FROM call_results
                       WHERE call_key=? OR id=?
                       ORDER BY called_at DESC LIMIT 1""",
                    (lead_id, lead_id),
                ).fetchone()
                owner_client_id = row["client_id"] if row and row["client_id"] else None
                return {
                    "resource_type": "call_result",
                    "found": bool(row),
                    "owner_client_id": owner_client_id,
                    "owner_tenant_present": bool(owner_client_id),
                    "campaign_id_present": bool(row and row["campaign_id"]),
                    "resource_id_included": False,
                    "payload_included": False,
                    "transcript_content_included": False,
                    "recording_url_included": False,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_recording_asset_owner(self, recording_url: str) -> dict:
        """Return owner metadata only for recording access shadow diagnostics."""
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT client_id, campaign_id
                       FROM recording_assets
                       WHERE recording_url=? OR storage_path=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (recording_url, recording_url),
                ).fetchone()
                owner_client_id = row["client_id"] if row and row["client_id"] else None
                return {
                    "resource_type": "recording_asset",
                    "found": bool(row),
                    "owner_client_id": owner_client_id,
                    "owner_tenant_present": bool(owner_client_id),
                    "campaign_id_present": bool(row and row["campaign_id"]),
                    "resource_id_included": False,
                    "payload_included": False,
                    "recording_url_included": False,
                    "storage_path_included": False,
                    "recording_bytes_included": False,
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)


    async def get_transcript_for_lead(self, lead_id: str, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                # Search by call_key (our custom non-FK identifier), then fall
                # back to row id (UUID). lead_id FK column is NULL for most calls.
                if client_id:
                    row = conn.execute(
                        """SELECT transcription FROM call_results
                           WHERE (call_key=? OR id=?) AND client_id=?
                           ORDER BY called_at DESC LIMIT 1""",
                        (lead_id, lead_id, client_id)
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT transcription FROM call_results
                           WHERE call_key=? OR id=?
                           ORDER BY called_at DESC LIMIT 1""",
                        (lead_id, lead_id)
                    ).fetchone()
                if row and row["transcription"]:
                    try:
                        return json.loads(row["transcription"])
                    except Exception:
                        return []
                return []
            finally:
                conn.close()
        return await run_in_executor(_sync)

    # ── Live Call State ──────────────────────────────────────────────────────

    async def update_live_state(
        self, lead_uid: str, campaign_id: str, name: str,
        status: str, snippet: str = "", transcripts: list | None = None,
        provider: str = "demo"
    ) -> None:
        def _sync():
            conn = _get_connection()
            try:
                client_id = _client_id_for_campaign_sync(conn, campaign_id)
                conn.execute(
                    """INSERT OR REPLACE INTO live_call_state
                       (lead_uid, campaign_id, client_id, lead_name, status, snippet, transcripts, provider, last_update)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        lead_uid, campaign_id, client_id, name, status, snippet,
                        json.dumps(transcripts or []), provider, datetime.now().isoformat()
                    )
                )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def get_live_state(self, campaign_id: str, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if client_id:
                    rows = conn.execute(
                        """SELECT * FROM live_call_state
                           WHERE campaign_id=? AND client_id=?
                           ORDER BY last_update DESC""",
                        (campaign_id, client_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM live_call_state WHERE campaign_id=? ORDER BY last_update DESC",
                        (campaign_id,)
                    ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["transcripts"] = json.loads(d.get("transcripts") or "[]")
                    except Exception:
                        d["transcripts"] = []
                    result.append(d)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_all_live_state(self, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if client_id:
                    rows = conn.execute(
                        "SELECT * FROM live_call_state WHERE client_id=? ORDER BY last_update DESC",
                        (client_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM live_call_state ORDER BY last_update DESC"
                    ).fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["transcripts"] = json.loads(d.get("transcripts") or "[]")
                    except Exception:
                        d["transcripts"] = []
                    result.append(d)
                return result
            finally:
                conn.close()
        return await run_in_executor(_sync)

    # ── Phone Numbers ────────────────────────────────────────────────────────

    async def list_phone_numbers(self, client_id: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if client_id:
                    rows = conn.execute(
                        "SELECT * FROM phone_numbers WHERE client_id=? ORDER BY purchased_at DESC",
                        (client_id,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM phone_numbers ORDER BY purchased_at DESC"
                    ).fetchall()
                results: list[dict] = []
                for row in rows:
                    number = dict(row)
                    route = conn.execute(
                        """SELECT * FROM phone_number_routes
                           WHERE number_id=? AND status='active'
                           ORDER BY
                             CASE
                               WHEN campaign_id IS NOT NULL AND campaign_id != '' THEN 0
                               WHEN agent_id IS NOT NULL AND agent_id != '' THEN 1
                               ELSE 2
                             END,
                             updated_at DESC
                           LIMIT 1""",
                        (number["id"],),
                    ).fetchone()
                    if route:
                        decoded = _decode_phone_route(dict(route))
                        number["route"] = decoded
                        number["route_id"] = decoded.get("id")
                        number["route_agent_id"] = decoded.get("agent_id")
                        number["route_campaign_id"] = decoded.get("campaign_id")
                        number["routing_mode"] = decoded.get("routing_mode")
                    results.append(number)
                return results
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def add_phone_number(self, number_data: dict) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                import uuid as _uuid
                phone = (number_data.get("phone") or "").strip()
                if not phone:
                    raise ValueError("phone number is required")
                requested_client_id = number_data.get("client_id")
                existing = conn.execute(
                    "SELECT * FROM phone_numbers WHERE phone=?",
                    (phone,),
                ).fetchone()
                if existing:
                    existing_dict = dict(existing)
                    existing_client_id = existing_dict.get("client_id")
                    if requested_client_id and existing_client_id and existing_client_id != requested_client_id:
                        raise ValueError("phone number already belongs to another tenant")
                    if requested_client_id and not existing_client_id:
                        now = datetime.now().isoformat()
                        conn.execute(
                            """UPDATE phone_numbers
                               SET client_id=?, assigned_at=?, sid=COALESCE(?, sid),
                                   region=COALESCE(?, region), provider=COALESCE(?, provider)
                               WHERE id=?""",
                            (
                                requested_client_id,
                                now,
                                number_data.get("sid"),
                                number_data.get("region"),
                                number_data.get("provider"),
                                existing_dict["id"],
                            ),
                        )
                        conn.commit()
                        existing = conn.execute(
                            "SELECT * FROM phone_numbers WHERE id=?",
                            (existing_dict["id"],),
                        ).fetchone()
                        existing_dict = dict(existing)
                    return existing_dict
                nid = str(_uuid.uuid4())
                conn.execute(
                    """INSERT OR IGNORE INTO phone_numbers
                       (id, phone, sid, region, provider, client_id, assigned_at, purchased_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        nid, phone, number_data.get("sid"),
                        number_data.get("region"), number_data.get("provider", "twilio"),
                        number_data.get("client_id"), number_data.get("assigned_at"),
                        datetime.now().isoformat(),
                    )
                )
                conn.commit()
                row = conn.execute("SELECT * FROM phone_numbers WHERE id=?", (nid,)).fetchone()
                return dict(row) if row else {**number_data, "id": nid, "phone": phone}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def assign_number_to_client(self, number_id: str, client_id: str) -> None:
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    "UPDATE phone_numbers SET client_id=?, assigned_at=? WHERE id=?",
                    (client_id, datetime.now().isoformat(), number_id)
                )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    # ── Clients & Assignments ────────────────────────────────────────────────

    async def get_phone_number(self, number_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM phone_numbers WHERE id=?", (number_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def upsert_phone_number_route(
        self,
        *,
        number_id: str,
        client_id: str,
        agent_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        routing_mode: str = "tenant_default",
        metadata: Optional[dict] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                number = conn.execute("SELECT * FROM phone_numbers WHERE id=?", (number_id,)).fetchone()
                if not number:
                    raise ValueError(f"phone number not found: {number_id}")
                if number["client_id"] and number["client_id"] != client_id:
                    raise ValueError("phone number is outside tenant scope")
                client = conn.execute("SELECT id FROM clients WHERE id=?", (client_id,)).fetchone()
                if not client:
                    raise ValueError(f"client not found: {client_id}")
                if agent_id:
                    agent = conn.execute("SELECT client_id FROM agents WHERE id=?", (agent_id,)).fetchone()
                    if not agent:
                        raise ValueError(f"agent not found: {agent_id}")
                    if agent["client_id"] and agent["client_id"] != client_id:
                        raise ValueError("agent is outside number tenant scope")
                if campaign_id:
                    campaign = conn.execute("SELECT client_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                    if not campaign:
                        raise ValueError(f"campaign not found: {campaign_id}")
                    if campaign["client_id"] and campaign["client_id"] != client_id:
                        raise ValueError("campaign is outside number tenant scope")

                now = datetime.now().isoformat()
                route_id = str(uuid.uuid4())
                existing_route = conn.execute(
                    "SELECT id FROM phone_number_routes WHERE number_id=? AND routing_mode=?",
                    (number_id, routing_mode),
                ).fetchone()
                if existing_route:
                    route_id = existing_route["id"]
                    conn.execute(
                        """UPDATE phone_number_routes
                           SET phone=?, provider=?, client_id=?, agent_id=?, campaign_id=?,
                               status='active', metadata_json=?, updated_at=?, deactivated_at=NULL
                           WHERE id=?""",
                        (
                            number["phone"], number["provider"], client_id, agent_id,
                            campaign_id, json.dumps(metadata or {}), now, route_id,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO phone_number_routes
                           (id, number_id, phone, provider, client_id, agent_id, campaign_id,
                            routing_mode, status, metadata_json, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            route_id, number_id, number["phone"], number["provider"], client_id,
                            agent_id, campaign_id, routing_mode, "active",
                            json.dumps(metadata or {}), now, now,
                        ),
                    )
                conn.execute(
                    "UPDATE phone_numbers SET client_id=?, assigned_at=? WHERE id=?",
                    (client_id, now, number_id),
                )
                conn.execute(
                    """INSERT INTO tenant_audit_events
                       (id, client_id, action, resource_type, resource_id, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), client_id, "telephony.number_route_upserted",
                        "phone_number", number_id,
                        json.dumps({
                            "phone": number["phone"],
                            "provider": number["provider"],
                            "agent_id": agent_id,
                            "campaign_id": campaign_id,
                            "routing_mode": routing_mode,
                        }),
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM phone_number_routes WHERE id=?", (route_id,)).fetchone()
                return _decode_phone_route(dict(row))
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_phone_number_route(self, number_id: str, routing_mode: str = "tenant_default") -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT * FROM phone_number_routes
                       WHERE number_id=? AND routing_mode=? AND status='active'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (number_id, routing_mode),
                ).fetchone()
                return _decode_phone_route(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_phone_number_for_campaign(
        self,
        *,
        client_id: Optional[str],
        agent_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[dict]:
        if not client_id:
            return None

        def _sync():
            conn = _get_connection()
            try:
                def _from_route(row: sqlite3.Row) -> dict:
                    number = {
                        "id": row["id"],
                        "phone": row["phone"],
                        "sid": row["sid"],
                        "region": row["region"],
                        "provider": row["provider"],
                        "client_id": row["client_id"],
                        "assigned_at": row["assigned_at"],
                        "purchased_at": row["purchased_at"],
                    }
                    route = {
                        "id": row["route_id"],
                        "number_id": row["route_number_id"],
                        "phone": row["route_phone"],
                        "provider": row["route_provider"],
                        "client_id": row["route_client_id"],
                        "agent_id": row["route_agent_id"],
                        "campaign_id": row["route_campaign_id"],
                        "routing_mode": row["route_routing_mode"],
                        "status": row["route_status"],
                        "metadata_json": row["route_metadata_json"],
                        "created_at": row["route_created_at"],
                        "updated_at": row["route_updated_at"],
                        "deactivated_at": row["route_deactivated_at"],
                    }
                    number["route"] = _decode_phone_route(route)
                    number["route_id"] = number["route"].get("id")
                    number["route_agent_id"] = number["route"].get("agent_id")
                    number["route_campaign_id"] = number["route"].get("campaign_id")
                    number["routing_mode"] = number["route"].get("routing_mode")
                    return number

                route_select = """
                    SELECT
                        p.id, p.phone, p.sid, p.region, p.provider, p.client_id, p.assigned_at, p.purchased_at,
                        r.id AS route_id, r.number_id AS route_number_id, r.phone AS route_phone,
                        r.provider AS route_provider, r.client_id AS route_client_id,
                        r.agent_id AS route_agent_id, r.campaign_id AS route_campaign_id,
                        r.routing_mode AS route_routing_mode, r.status AS route_status,
                        r.metadata_json AS route_metadata_json, r.created_at AS route_created_at,
                        r.updated_at AS route_updated_at, r.deactivated_at AS route_deactivated_at
                      FROM phone_number_routes r
                      JOIN phone_numbers p ON p.id = r.number_id
                     WHERE r.client_id=? AND r.status='active'
                """

                def _provider_clause(params: list[Any]) -> str:
                    if provider:
                        params.append(provider)
                        return " AND r.provider=?"
                    return ""

                if campaign_id:
                    params: list[Any] = [client_id, campaign_id]
                    row = conn.execute(
                        route_select
                        + " AND r.campaign_id=?"
                        + _provider_clause(params)
                        + " ORDER BY r.updated_at DESC LIMIT 1",
                        tuple(params),
                    ).fetchone()
                    if row:
                        return _from_route(row)

                if agent_id:
                    params = [client_id, agent_id]
                    row = conn.execute(
                        route_select
                        + " AND r.agent_id=? AND (r.campaign_id IS NULL OR r.campaign_id='')"
                        + _provider_clause(params)
                        + " ORDER BY r.updated_at DESC LIMIT 1",
                        tuple(params),
                    ).fetchone()
                    if row:
                        return _from_route(row)

                params = [client_id]
                row = conn.execute(
                    route_select
                    + """ AND r.routing_mode='tenant_default'
                           AND (r.agent_id IS NULL OR r.agent_id='')
                           AND (r.campaign_id IS NULL OR r.campaign_id='')"""
                    + _provider_clause(params)
                    + " ORDER BY r.updated_at DESC LIMIT 1",
                    tuple(params),
                ).fetchone()
                if row:
                    return _from_route(row)

                params = [client_id]
                fallback_sql = "SELECT * FROM phone_numbers WHERE client_id=?"
                if provider:
                    fallback_sql += " AND provider=?"
                    params.append(provider)
                fallback_sql += " ORDER BY assigned_at DESC, purchased_at DESC LIMIT 1"
                fallback = conn.execute(fallback_sql, tuple(params)).fetchone()
                return dict(fallback) if fallback else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def resolve_phone_number_route(self, phone: str, provider: str = "twilio") -> Optional[dict]:
        normalized_phone = (phone or "").strip()

        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    """SELECT * FROM phone_number_routes
                       WHERE phone=? AND provider=? AND status='active'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (normalized_phone, provider),
                ).fetchone()
                return _decode_phone_route(dict(row)) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_clients(self) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                rows = conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_client(self, client_id: str) -> Optional[dict]:
        if not client_id:
            return None

        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_client_by_email(self, email: str) -> Optional[dict]:
        normalized = _normalize_email(email)
        if not normalized:
            return None

        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM clients WHERE lower(email)=?",
                    (normalized,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    # ── Campaign Worker V2 Control Plane ─────────────────────────────────────

    async def create_campaign_execution(
        self,
        campaign_id: str,
        *,
        agent_id: Optional[str] = None,
        telephony_provider: str = "demo",
        client_id: Optional[str] = None,
        mode: str = "shadow",
        max_concurrency: int = 1,
        max_attempts: int = 1,
        requested_by: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                import uuid as _uuid

                campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
                if not campaign:
                    raise ValueError(f"campaign not found: {campaign_id}")

                resolved_client_id = client_id or campaign["client_id"]
                execution_id = str(_uuid.uuid4())
                now = datetime.now().isoformat()
                idem = idempotency_key or f"{campaign_id}:{agent_id or campaign['agent_id'] or 'default'}:{telephony_provider}:{mode}"

                existing = conn.execute(
                    "SELECT * FROM campaign_executions WHERE idempotency_key=?",
                    (idem,),
                ).fetchone()
                if existing:
                    return dict(existing)

                conn.execute(
                    """INSERT INTO campaign_executions
                       (id, campaign_id, client_id, agent_id, telephony_provider, status, mode,
                        max_concurrency, max_attempts, idempotency_key, requested_by, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        execution_id, campaign_id, resolved_client_id,
                        agent_id or campaign["agent_id"], telephony_provider,
                        "planned", mode, max(1, int(max_concurrency or 1)),
                        max(1, int(max_attempts or 1)), idem, requested_by, now, now,
                    ),
                )

                leads = conn.execute(
                    "SELECT * FROM leads WHERE campaign_id=? ORDER BY created_at",
                    (campaign_id,),
                ).fetchall()
                for lead in leads:
                    lead_id = lead["id"]
                    attempt_id = str(_uuid.uuid4())
                    attempt_key = f"{execution_id}:{lead_id}:0"
                    conn.execute(
                        """INSERT OR IGNORE INTO campaign_lead_attempts
                           (id, execution_id, campaign_id, client_id, lead_id, lead_name, phone,
                            attempt_number, status, idempotency_key, scheduled_at, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            attempt_id, execution_id, campaign_id, resolved_client_id,
                            lead_id, lead["name"], lead["phone"], 0, "queued",
                            attempt_key, now, now, now,
                        ),
                    )

                conn.execute(
                    """INSERT INTO campaign_worker_events
                       (id, execution_id, campaign_id, client_id, event_type, payload, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        str(_uuid.uuid4()), execution_id, campaign_id, resolved_client_id,
                        "execution_prepared",
                        json.dumps({"mode": mode, "lead_count": len(leads)}),
                        now,
                    ),
                )
                conn.commit()

                row = conn.execute("SELECT * FROM campaign_executions WHERE id=?", (execution_id,)).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_campaign_executions(
        self,
        campaign_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if campaign_id and client_id:
                    rows = conn.execute(
                        """SELECT * FROM campaign_executions
                           WHERE campaign_id=? AND client_id=?
                           ORDER BY created_at DESC""",
                        (campaign_id, client_id),
                    ).fetchall()
                elif campaign_id:
                    rows = conn.execute(
                        "SELECT * FROM campaign_executions WHERE campaign_id=? ORDER BY created_at DESC",
                        (campaign_id,),
                    ).fetchall()
                elif client_id:
                    rows = conn.execute(
                        "SELECT * FROM campaign_executions WHERE client_id=? ORDER BY created_at DESC",
                        (client_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM campaign_executions ORDER BY created_at DESC"
                    ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_campaign_lead_attempts(self, execution_id: str) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                rows = conn.execute(
                    """SELECT * FROM campaign_lead_attempts
                       WHERE execution_id=?
                       ORDER BY created_at, lead_name""",
                    (execution_id,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_campaign_execution(self, execution_id: str) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT * FROM campaign_executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def set_campaign_execution_status(
        self,
        execution_id: str,
        status: str,
        *,
        event_type: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> Optional[dict]:
        def _sync():
            conn = _get_connection()
            try:
                existing = conn.execute(
                    "SELECT * FROM campaign_executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                if not existing:
                    return None

                now = datetime.now().isoformat()
                timestamp_column = {
                    "running": "started_at",
                    "completed": "completed_at",
                    "paused": "paused_at",
                    "cancelled": "cancelled_at",
                }.get(status)
                if timestamp_column:
                    conn.execute(
                        f"UPDATE campaign_executions SET status=?, updated_at=?, {timestamp_column}=? WHERE id=?",
                        (status, now, now, execution_id),
                    )
                else:
                    conn.execute(
                        "UPDATE campaign_executions SET status=?, updated_at=? WHERE id=?",
                        (status, now, execution_id),
                    )

                if event_type:
                    conn.execute(
                        """INSERT INTO campaign_worker_events
                           (id, execution_id, campaign_id, client_id, event_type, payload, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            str(uuid.uuid4()), execution_id, existing["campaign_id"],
                            existing["client_id"], event_type,
                            json.dumps(payload or {"status": status}), now,
                        ),
                    )

                conn.commit()
                row = conn.execute(
                    "SELECT * FROM campaign_executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def ensure_client_for_email(self, email: str, name: Optional[str] = None) -> dict:
        normalized = _normalize_email(email)
        if not normalized:
            raise ValueError("email is required")

        def _sync():
            conn = _get_connection()
            try:
                existing = conn.execute(
                    "SELECT * FROM clients WHERE lower(email)=?",
                    (normalized,),
                ).fetchone()
                if existing:
                    return dict(existing)

                client_id = _client_id_for_email(normalized)
                display_name = name or normalized.split("@")[0].replace(".", " ").title()
                conn.execute(
                    """INSERT OR IGNORE INTO clients (id, name, email, plan, created_at)
                       VALUES (?,?,?,?,?)""",
                    (client_id, display_name, normalized, "assigned", datetime.now().isoformat()),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
                return dict(row)
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def create_client(self, client_id: str, data: dict) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO clients (id, name, email, plan, created_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        client_id, data.get("name"), data.get("email"),
                        data.get("plan", "free"), datetime.now().isoformat()
                    )
                )
                conn.commit()
                return {**data, "id": client_id}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def get_assignment(self, client_id: str) -> Optional[str]:
        def _sync():
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT agent_id FROM client_assignments WHERE client_id=?", (client_id,)
                ).fetchone()
                return row["agent_id"] if row else None
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def set_assignment(self, client_id: str, agent_id: str) -> None:
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO client_assignments (client_id, agent_id)
                       VALUES (?,?)""",
                    (client_id, agent_id)
                )
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    async def clear_assignment(self, client_id: str, agent_id: Optional[str] = None) -> None:
        def _sync():
            conn = _get_connection()
            try:
                if agent_id:
                    conn.execute(
                        "DELETE FROM client_assignments WHERE client_id=? AND agent_id=?",
                        (client_id, agent_id),
                    )
                else:
                    conn.execute("DELETE FROM client_assignments WHERE client_id=?", (client_id,))
                conn.commit()
            finally:
                conn.close()
        await run_in_executor(_sync)

    # ── Dashboard Stats ──────────────────────────────────────────────────────

    async def get_dashboard_stats(self) -> dict:
        def _sync():
            conn = _get_connection()
            try:
                total_calls = conn.execute("SELECT COUNT(*) FROM call_results").fetchone()[0]
                active_agents = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
                total_clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]

                connected = conn.execute("SELECT COUNT(*) FROM call_results WHERE status='Connected'").fetchone()[0]
                connect_rate = (connected / total_calls * 100) if total_calls > 0 else 38.5

                return {
                    "totalClients": total_clients,
                    "activeAgents": active_agents,
                    "calls": total_calls,
                    "connectRate": round(connect_rate, 1),
                }
            finally:
                conn.close()
        return await run_in_executor(_sync)

    # ── Demo Requests ──────────────────────────────────────────────────────────

    async def create_demo_request(self, data: dict) -> dict:
        request_id = str(uuid.uuid4())
        def _sync():
            conn = _get_connection()
            try:
                conn.execute(
                    """INSERT INTO demo_requests (id, name, company, email, phone, industry, use_case, monthly_volume, additional_notes, status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        request_id,
                        data.get("name"),
                        data.get("company"),
                        data.get("email"),
                        data.get("phone"),
                        data.get("industry"),
                        data.get("use_case"),
                        data.get("monthly_volume"),
                        data.get("additional_notes"),
                        data.get("status", "New"),
                        datetime.now().isoformat()
                    )
                )
                conn.commit()
                return {**data, "id": request_id, "created_at": datetime.now().isoformat()}
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def list_demo_requests(self, status: Optional[str] = None) -> list[dict]:
        def _sync():
            conn = _get_connection()
            try:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM demo_requests WHERE status=? ORDER BY created_at DESC", (status,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM demo_requests ORDER BY created_at DESC").fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        return await run_in_executor(_sync)

    async def update_demo_request_status(self, request_id: str, status: str) -> bool:
        def _sync():
            conn = _get_connection()
            try:
                cursor = conn.execute(
                    "UPDATE demo_requests SET status=? WHERE id=?", (status, request_id)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()
        return await run_in_executor(_sync)



# Singleton instance
db = DatabaseManager()
