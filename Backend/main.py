"""
Voice AI Calling SaaS Platform — Main Server  v2.1 (Production Hardened)
# Trigger reload: torch cuda installed
Changes from v2.0:
  - lifespan replaces deprecated on_event
  - websocket.accept() now called BEFORE pipeline starts (Issue 2)
  - API Key middleware — X-API-Key header guards all write endpoints (Issue 6)
  - VoiceLiveSink emits speaker labels in transcript events (Issue 9)
  - agentId resolved from DB assignment on /api/assignments (Issue 3)
  - /health endpoint for uptime monitoring (Issue 14)
  - /api/voice-demo  — mic session that also fires dashboard WS events (Issue 12)
  - Groq 429 retry with exponential backoff in generate_response (Issue 13)
"""

from __future__ import annotations

# ── Fix #4: Force UTF-8 on Windows stdout/stderr ─────────────────────────────
# Python on Windows uses the active console code page (usually cp1252) which
# cannot encode Devanagari, Unicode arrows (→), or emoji. This causes
# UnicodeEncodeError in the logging StreamHandler, silently swallowing log lines.
# Reconfigure BEFORE any import that might trigger logging.
import sys
import io as _io
import time

def _force_utf8_streams() -> None:
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream and hasattr(_stream, "buffer"):
            setattr(
                sys,
                _stream_name,
                _io.TextIOWrapper(_stream.buffer, encoding="utf-8", errors="replace", line_buffering=True),
            )

_force_utf8_streams()
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
if sys.platform == "win32":
    def _noop_add_signal_handler(*args, **kwargs):
        return None
    asyncio.AbstractEventLoop.add_signal_handler = _noop_add_signal_handler
    if hasattr(asyncio, "BaseEventLoop"):
        asyncio.BaseEventLoop.add_signal_handler = _noop_add_signal_handler
import array
import json
import logging
import os

os.environ.setdefault("PLATFORM_FEATURE_PROFILE", "live")

import secrets
import tempfile
import uuid
import wave
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode
from starlette.websockets import WebSocketState

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# ── Internal modules ──────────────────────────────────────────────────────────
from ws_hub import ws_manager
from db.db_manager import db
from demo_runner import DemoCallEngine
from qa_simulator import QASimulator
from agent_runner import run_campaign
from telephony.provider_registry import get_provider, list_providers
from metrics.provider_metrics import snapshot_provider_metrics
from call_recording import SessionRecorder as TimelineSessionRecorder
from campaigns.worker_v2 import CampaignWorkerV2Config, CampaignWorkerV2ControlPlane
from crm import CRMIntegrationService
from flows.v2 import FlowSpecValidationError, build_flow_preview, build_flow_spec_from_agent, validate_flow_spec
from intelligence.crawler import CrawlError
from intelligence.pipeline import ScrapeLimits, WebsiteIntelligencePipeline
from intelligence.url_guard import URLSafetyError
from memory import AgentMemoryService
from platform_migration import feature_flags
from platform_migration.auth_context import (
    audit_context,
    build_http_tenant_context,
    build_ws_tenant_context,
    build_tenant_enforcement_readiness,
    build_tenant_scoped_read_policy_manifest,
    build_tenant_scoped_read_guard_decision,
    build_tenant_scoped_read_canary,
    build_tenant_leak_regression_matrix_manifest,
    build_result_asset_readiness_manifest,
    build_final_rollout_report_readiness_manifest,
    build_rollout_approval_packet_manifest,
    build_rollout_canary_plan_manifest,
    build_rollback_drill_readiness_manifest,
    build_rollout_evidence_bundle_manifest,
    build_canary_observation_checklist_manifest,
    build_production_go_no_go_gate_manifest,
    build_production_activation_contract_stub_manifest,
    build_production_activation_permission_shadow_manifest,
    build_production_activation_payload_dry_run_manifest,
    build_production_activation_readiness_manifest,
    build_production_activation_rollback_confirmation_manifest,
    build_controlled_handoff_readiness_manifest,
    build_recording_access_shadow_manifest,
    build_recording_owner_lookup_shadow_manifest,
    build_recording_access_enforcement_readiness_manifest,
    build_recording_access_gate_dry_run_manifest,
    build_transcript_access_shadow_manifest,
    build_transcript_access_canary_manifest,
    build_transcript_access_enforcement_readiness_manifest,
    build_transcript_access_gate_dry_run_manifest,
    build_transcript_protected_route_stub_manifest,
    build_transcript_protected_route_permission_shadow_manifest,
    build_transcript_protected_response_shape_canary_manifest,
    build_transcript_protected_payload_dry_run_manifest,
    build_transcript_protected_enforcement_readiness_manifest,
    build_transcript_protected_live_activation_plan_manifest,
    build_transcript_protected_rollback_readiness_manifest,
    build_transcript_frontend_migration_readiness_manifest,
    should_reject_http_request,
)

""" _PIPECAT_AVAILABLE = False
try:
    from pipecat.frames.frames import AudioRawFrame, CancelFrame, EndFrame, Frame, StartFrame, TextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from flows.runtime import AgentTextFrame, RealEstateSTTProcessor, RealEstateLLMProcessor, RealEstateTTSProcessor, VoiceTurnState
    _PIPECAT_AVAILABLE = True
except (ImportError, Exception) as e:
    import logging
    logging.getLogger("server").warning(f"Skipping pipecat/flows imports: {e}")
    class FrameProcessor: pass  # type: ignore[no-redef] """

_PIPECAT_AVAILABLE = False

try:
    from pipecat.frames.frames import (
        AudioRawFrame,
        CancelFrame,
        EndFrame,
        Frame,
        StartFrame,
        TextFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.processors.frame_processor import (
        FrameDirection,
        FrameProcessor,
    )

    _PIPECAT_AVAILABLE = True
    print("[PIPECAT] Pipecat imports OK")

except Exception as e:
    import traceback

    print("[PIPECAT] Pipecat import FAILED")
    print(f"[PIPECAT] Error: {e}")
    traceback.print_exc()

    class FrameProcessor:
        pass


# Import our runtime processors separately.
try:
    from flows.runtime import (
        AgentTextFrame,
        RealEstateSTTProcessor,
        RealEstateLLMProcessor,
        RealEstateTTSProcessor,
        VoiceTurnState,
    )

    print("[PIPECAT] flows.runtime imports OK")

except Exception as e:
    import traceback

    print("[PIPECAT] flows.runtime import FAILED")
    print(f"[PIPECAT] Error: {e}")
    traceback.print_exc()

    _PIPECAT_AVAILABLE = False

    class FrameProcessor:
        pass

from llm.state_manager import StateManager

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    handlers=[
        logging.FileHandler("voice_agent.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("server")

# ── Constants ─────────────────────────────────────────────────────────────────
DB_DIR           = "db"
AGENTS_DIR       = os.path.join(DB_DIR, "agents")
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(AGENTS_DIR, exist_ok=True)
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:3000")
DEFAULT_CARTESIA_VOICE_ID = "95d51f79-c397-46f9-b49a-23763d3eaa2d"
VALID_STT_PROVIDERS = {"groq", "deepgram", "indic_seamless", "smallest"}
VALID_TTS_PROVIDERS = {"edge", "cartesia", "parler", "indic_parler", "sarvam", "smallest", "magpie"}
VALID_AGENT_TYPES = {"real_estate_sales", "finance", "insurance", "education","hr"}
AGENT_TYPE_LABELS = {
    "real_estate_sales": "Real Estate team",
    "finance": "Finance advisory team",
    "insurance": "Insurance advisory team",
    "education": "Education counselling team",
    "hr":"HR team",
}
VOICE_MAP = {"ElevenLabs - Priya (Female)": "11labs-06nek6zjTCD1vCbtc8bc"}

# API Key Auth — set PLATFORM_API_KEY in .env; if empty, auth is DISABLED (dev mode)
_PLATFORM_API_KEY = os.getenv("PLATFORM_API_KEY", "")
_api_key_header   = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_auth(key: Optional[str] = Depends(_api_key_header)) -> None:
    """Dependency: validates X-API-Key header on write endpoints.
    If PLATFORM_API_KEY is not set in .env, auth is skipped (development mode).
    """
    if not _PLATFORM_API_KEY:
        return  # dev mode — no key required
    if key != _PLATFORM_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: invalid or missing API key")


# ── App lifespan (replaces deprecated on_event) ───────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB and migrate JSON files. Shutdown: nothing needed."""
    logger.info("Initializing database...")
    await db.initialize()
    logger.info("Database ready.")
    # Pre-load local STT engine in a background thread to prevent first-call latency
    try:
        from stt.stt import transcribe_audio
        logger.info("Pre-loading local STT engine...")
        asyncio.get_running_loop().run_in_executor(None, lambda: transcribe_audio(b""))
    except Exception as e:
        logger.warning(f"Failed to pre-load STT engine: {e}")
    yield
    logger.info("Server shutting down.")


# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Voice AI Calling SaaS Platform",
    version="2.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("recordings", exist_ok=True)
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")


def _shadow_recording_access(context, request_path: str) -> None:
    if not feature_flags.is_enabled("recordings.access_shadow"):
        return
    manifest = build_recording_access_shadow_manifest(context, request_path=request_path)
    if not manifest["recording"]["recording_path_requested"]:
        return
    logger.info(
        "recording_access_shadow requested=%s extension=%s tenant_present=%s blockers=%s",
        manifest["recording"]["recording_path_requested"],
        manifest["recording"]["file_extension"],
        manifest["requester"]["tenant_present"],
        ",".join(manifest["decision"]["blockers"]),
    )


async def _shadow_recording_owner_lookup(context, request_path: str) -> None:
    if not (
        feature_flags.is_enabled("recordings.access_shadow")
        and feature_flags.is_enabled("recordings.owner_lookup_shadow")
    ):
        return
    if not request_path.startswith("/recordings/"):
        return

    try:
        owner = await db.get_recording_asset_owner(request_path)
    except Exception as exc:
        logger.warning(
            "recording_owner_lookup_shadow failed error_type=%s",
            type(exc).__name__,
        )
        return
    manifest = build_recording_owner_lookup_shadow_manifest(
        context,
        recording_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "recording_owner_lookup_shadow found=%s owner_tenant_present=%s allowed=%s blockers=%s",
        manifest["recording"]["found"],
        manifest["recording"]["owner_tenant_present"],
        manifest["decision"]["current_requester_allowed_if_enforced"],
        ",".join(manifest["decision"]["blockers"]),
    )
    if feature_flags.is_enabled("recordings.access_enforcement_shadow"):
        readiness = build_recording_access_enforcement_readiness_manifest(
            context,
            recording_found=owner["found"],
            owner_tenant_id=owner.get("owner_client_id"),
            campaign_id_present=owner.get("campaign_id_present", False),
        )
        logger.info(
            "recording_access_enforcement_shadow ready=%s would_allow=%s blockers=%s",
            readiness["decision"]["ready_for_future_enforcement"],
            readiness["decision"]["would_allow_if_recording_access_enforced"],
            ",".join(readiness["decision"]["blockers"]),
        )
    if feature_flags.is_enabled("recordings.access_gate_dry_run"):
        dry_run = build_recording_access_gate_dry_run_manifest(
            context,
            recording_found=owner["found"],
            owner_tenant_id=owner.get("owner_client_id"),
            campaign_id_present=owner.get("campaign_id_present", False),
        )
        logger.info(
            "recording_access_gate_dry_run ready=%s would_allow=%s blockers=%s",
            dry_run["decision"]["ready_for_future_gate"],
            dry_run["decision"]["would_allow_if_gate_active"],
            ",".join(dry_run["decision"]["blockers"]),
        )


@app.middleware("http")
async def tenant_auth_audit_middleware(request: Request, call_next):
    context = build_http_tenant_context(request, api_key_secret=_PLATFORM_API_KEY)
    request.state.tenant_context = context
    _shadow_recording_access(context, request.url.path)
    await _shadow_recording_owner_lookup(context, request.url.path)
    audit_context(logger, context, surface="http", method=request.method, route=request.url.path)
    if should_reject_http_request(context, request.url.path):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


def _audit_ws_connection(websocket: WebSocket, route: str, client_id: str | None = None):
    context = build_ws_tenant_context(
        websocket,
        path_tenant_id=client_id,
        api_key_secret=_PLATFORM_API_KEY,
    )
    audit_context(logger, context, surface="websocket", route=route)
    return context


def _should_enforce_global_monitor_admin() -> bool:
    return (
        feature_flags.is_enabled("ws.scoped_events")
        or feature_flags.is_enabled("auth.enforce_backend")
    )


def _require_global_monitor_admin(context, surface: str) -> None:
    if not _should_enforce_global_monitor_admin():
        return
    if context and context.is_admin:
        return
    logger.warning("Global monitor access rejected: surface=%s", surface)
    raise HTTPException(status_code=403, detail=f"{surface} requires admin access")


# ── Pydantic Models ───────────────────────────────────────────────────────────
class DemoRequestCreate(BaseModel):
    name: str
    company: Optional[str] = None
    email: str
    phone: Optional[str] = None
    industry: Optional[str] = None
    use_case: Optional[str] = None
    monthly_volume: Optional[str] = None
    additional_notes: Optional[str] = None

class DemoRequestUpdateStatus(BaseModel):
    status: str

class AgentCreate(BaseModel):
    name: str
    voice: str
    language: str
    max_duration: int
    provider: str
    #stt_provider: str = "groq"
    stt_provider: str = "deepgram"
    tts_provider: str = "edge"
    cartesia_voice_id: Optional[str] = None
    parler_description: Optional[str] = None
    assigned_email: Optional[str] = None
    agent_type: str = "real_estate_sales"
    script: str
    data_fields: List[str]
    custom_json: Optional[dict] = None

class AgentUpdate(BaseModel):
    name: Optional[str] = None
    voice: Optional[str] = None
    language: Optional[str] = None
    max_duration: Optional[int] = None
    provider: Optional[str] = None
    stt_provider: Optional[str] = None
    tts_provider: Optional[str] = None
    cartesia_voice_id: Optional[str] = None
    parler_description: Optional[str] = None
    assigned_email: Optional[str] = None
    agent_type: Optional[str] = None
    script: Optional[str] = None
    data_fields: Optional[List[str]] = None
    custom_json: Optional[dict] = None

class AgentFlowGenerateRequest(BaseModel):
    prompt: str
    agent_name: Optional[str] = None
    agent_type: Optional[str] = "recruitment"
    data_fields: Optional[List[str]] = None

class LeadsUpload(BaseModel):
    campaignId: str
    campaignName: Optional[str] = None
    agentId: Optional[str] = None
    telephonyProvider: Optional[str] = "demo"
    clientId: Optional[str] = None
    leads: List[dict]

class CampaignCreate(BaseModel):
    campaignId: str
    agentId: Optional[str] = None
    telephonyProvider: Optional[str] = "demo"

class CampaignStart(BaseModel):
    campaignId: str
    agentId: Optional[str] = None
    telephonyProvider: Optional[str] = "demo"
    clientId: Optional[str] = None

class CampaignLifecycleRequest(BaseModel):
    reason: Optional[str] = None
    actorEmail: Optional[str] = None

class AssignmentUpdate(BaseModel):
    clientId: str
    agentId: str

class DemoStart(BaseModel):
    campaignId: str
    agentId: Optional[str] = None
    leadOverride: Optional[dict] = None
    clientId: Optional[str] = "global"

class PhoneNumberPurchase(BaseModel):
    phoneNumber: str
    provider: str = "twilio"
    clientId: Optional[str] = None

class PhoneNumberAssign(BaseModel):
    numberId: str
    clientId: str

class PhoneNumberRouteUpdate(BaseModel):
    numberId: str
    clientId: str
    agentId: Optional[str] = None
    campaignId: Optional[str] = None
    routingMode: str = "tenant_default"
    metadata: Optional[dict] = None

class ClientCreate(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    plan: Optional[str] = "free"
    agentId: Optional[str] = None

class WebsiteScrapeStart(BaseModel):
    url: str
    agentId: Optional[str] = None
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None
    reuseExisting: bool = True

class WebsiteScrapeDispatch(BaseModel):
    industryHint: Optional[str] = None
    requestedBy: Optional[str] = None

class WebsiteScrapeCancel(BaseModel):
    reason: Optional[str] = None
    requestedBy: Optional[str] = None

class WebsiteScrapeStaleRecovery(BaseModel):
    staleAfterMinutes: int = 15
    reason: Optional[str] = None
    requestedBy: Optional[str] = None

class WebsiteScriptDraftCreate(BaseModel):
    jobId: str
    agentId: str
    industryHint: Optional[str] = None
    agentName: Optional[str] = None

class FlowTransitionDraftUpdate(BaseModel):
    intent: str
    target: str
    label: Optional[str] = None

class FlowNodeDraftUpdate(BaseModel):
    id: str
    type: Optional[str] = None
    label: Optional[str] = None
    response_en: Optional[str] = None
    collects: Optional[List[str]] = None
    transitions: Optional[List[FlowTransitionDraftUpdate]] = None

class FlowDraftUpdate(BaseModel):
    nodes: List[FlowNodeDraftUpdate]


# ── Health Check ──────────────────────────────────────────────────────────────
class AgentMemoryCollectionCreate(BaseModel):
    clientId: Optional[str] = None
    sourceType: str = "manual"
    sourceId: Optional[str] = None
    metadata: Optional[dict] = None

class AgentMemoryItemCreate(BaseModel):
    clientId: Optional[str] = None
    collectionId: str
    content: str
    metadata: Optional[dict] = None

class AgentMemoryResetRequest(BaseModel):
    clientId: Optional[str] = None
    reason: Optional[str] = None

class CRMConnectionCreate(BaseModel):
    clientId: Optional[str] = None
    provider: str
    displayName: Optional[str] = None
    externalAccountId: Optional[str] = None
    config: Optional[dict] = None
    requestedBy: Optional[str] = None

class CRMSecretReferenceUpdate(BaseModel):
    clientId: Optional[str] = None
    vaultProvider: str = "external"
    referenceId: str
    rotationDueAt: Optional[str] = None
    metadata: Optional[dict] = None
    requestedBy: Optional[str] = None

class CRMSyncPlanCreate(BaseModel):
    clientId: Optional[str] = None
    connectionId: str
    campaignId: Optional[str] = None
    direction: str = "outbound"
    requestedBy: Optional[str] = None
    idempotencyKey: Optional[str] = None

class CRMSyncDryRunExecute(BaseModel):
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None

class CRMSyncPreflightExecute(BaseModel):
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None

class CRMSyncOutboxQueue(BaseModel):
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None
    idempotencyKey: Optional[str] = None

class CRMSyncOutboxShadowRun(BaseModel):
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None

class CRMSyncOutboxRetryUpdate(BaseModel):
    clientId: Optional[str] = None
    error: str
    nextRetryAt: Optional[str] = None
    requestedBy: Optional[str] = None

class CRMSyncOutboxRequeue(BaseModel):
    clientId: Optional[str] = None
    requestedBy: Optional[str] = None

class CRMDeliveryApprovalCreate(BaseModel):
    clientId: Optional[str] = None
    approvedBy: Optional[str] = None
    requestedBy: Optional[str] = None
    idempotencyKey: Optional[str] = None

class CRMDeliveryApprovalRevoke(BaseModel):
    clientId: Optional[str] = None
    revokedBy: Optional[str] = None
    reason: Optional[str] = None


@app.get("/health")
async def health():
    """
    Uptime monitoring endpoint. UptimeRobot / BetterUptime hits this every 5 min.
    Returns 200 if server is alive and DB is reachable.
    """
    try:
        await db.get_dashboard_stats()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status": "ok",
        "version": "2.1",
        "db": db_status,
        "auth": "enabled" if _PLATFORM_API_KEY else "disabled (dev mode)",
        "timestamp": datetime.now().isoformat(),
    }


# ── Frontend / Health ─────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok"}

@app.get("/audio-worklet-processor.js")
async def serve_audio_worklet():
    """Serve the AudioWorklet processor script (replaces deprecated ScriptProcessor)."""
    worklet_path = os.path.join(os.path.dirname(__file__), "..", "Frontend", "audio-worklet-processor.js")
    if os.path.exists(worklet_path):
        return FileResponse(worklet_path, media_type="application/javascript")
    # Inline fallback if file doesn't exist yet
    js = """
class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() { super(); this._buffer = []; }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch) { for (let i = 0; i < ch.length; i++) this._buffer.push(ch[i]); }
    if (this._buffer.length >= 2048) {
      this.port.postMessage(new Float32Array(this._buffer.splice(0, 2048)));
    }
    return true;
  }
}
registerProcessor('mic-capture-processor', MicCaptureProcessor);
"""
    return HTMLResponse(js, media_type="application/javascript")


# ── Dashboard Stats ───────────────────────────────────────────────────────────
@app.get("/api/dashboard")
async def get_dashboard():
    return await db.get_dashboard_stats()

@app.get("/api/provider-metrics")
async def get_provider_metrics(request: Request):
    _require_global_monitor_admin(_tenant_context_from_request(request), "Provider metrics")
    return snapshot_provider_metrics()

@app.get("/api/demo/qa/readiness", dependencies=[Depends(require_auth)])
async def get_demo_call_qa_readiness(request: Request):
    if not feature_flags.is_enabled("demo.runtime_qa_readiness"):
        raise HTTPException(status_code=403, detail="demo.runtime_qa_readiness is disabled")
    _require_global_monitor_admin(_tenant_context_from_request(request), "Demo call QA")
    return _build_demo_call_qa_readiness()

def _voice_id_for_agent(voice: str | None) -> str:
    raw_voice = (voice or "11labs-06nek6zjTCD1vCbtc8bc").strip()
    return VOICE_MAP.get(raw_voice, raw_voice)


def _clean_agent_data_fields(fields: Any) -> list[str]:
    if isinstance(fields, str):
        return [item.strip() for item in fields.split(",") if item.strip()]
    if isinstance(fields, list):
        return [str(item).strip() for item in fields if str(item).strip()]
    return []


def _normalize_agent_record(data: dict) -> dict:
    normalized = dict(data)
    normalized["name"] = str(normalized.get("name") or "Voice Agent").strip()
    normalized["voice"] = str(normalized.get("voice") or "11labs-06nek6zjTCD1vCbtc8bc").strip()
    normalized["language"] = str(normalized.get("language") or "English").strip()
    normalized["max_duration"] = int(normalized.get("max_duration") or 300)
    normalized["provider"] = str(normalized.get("provider") or "twilio").strip()
    stt_provider = str(normalized.get("stt_provider") or "groq").strip().lower()
    tts_provider = str(normalized.get("tts_provider") or "edge").strip().lower()
    normalized["stt_provider"] = stt_provider if stt_provider in VALID_STT_PROVIDERS else "groq"
    normalized["tts_provider"] = tts_provider if tts_provider in VALID_TTS_PROVIDERS else "edge"
    normalized["cartesia_voice_id"] = str(normalized.get("cartesia_voice_id") or DEFAULT_CARTESIA_VOICE_ID).strip()
    normalized["parler_description"] = str(normalized.get("parler_description") or "").strip()
    normalized["assigned_email"] = str(normalized.get("assigned_email") or "").strip().lower()
    agent_type = str(normalized.get("agent_type") or "real_estate_sales").strip()
    normalized["agent_type"] = agent_type if agent_type in VALID_AGENT_TYPES else "real_estate_sales"
    normalized["script"] = str(normalized.get("script") or "").strip()
    normalized["data_fields"] = _clean_agent_data_fields(normalized.get("data_fields"))
    return normalized


def _agent_schema_path(agent_id: str, existing_path: str | None = None) -> str:
    expected_path = os.path.join(AGENTS_DIR, f"{agent_id}.json")
    if not existing_path:
        return expected_path

    root = os.path.abspath(AGENTS_DIR)
    candidate = os.path.abspath(existing_path)
    if candidate == os.path.abspath(expected_path) or candidate.startswith(root + os.sep):
        return existing_path
    logger.warning("Ignoring unsafe schema_path for agent_id=%s: %s", agent_id, existing_path)
    return expected_path


async def _write_agent_runtime_schema(
    agent_id: str,
    schema_path: str,
    agent_data: dict,
    voice_id: str,
    assigned_client: Optional[dict],
) -> None:
    schema = None

    # 1. Caller already supplied a full generated flow (the "Generate from
    #    Prompt" path calls /api/agents/generate-flow first, then submits
    #    the result here as custom_json).
    if agent_data.get("custom_json") and isinstance(agent_data["custom_json"], dict):
        schema = agent_data["custom_json"]
    else:
        # 2. Editing an existing agent that already has a real schema on
        #    disk — keep its current node graph, just update metadata below.
        try:
            with open(schema_path, "r", encoding="utf-8") as existing_file:
                existing_schema = json.load(existing_file)
            if _is_usable_agent_schema(schema_path):
                schema = existing_schema
        except (OSError, json.JSONDecodeError):
            pass

    if schema is None:
        # 3. "Manual Setup" creation (or first save with no prior schema):
        #    generate a real, multi-node conversation from the agent's
        #    prompt/script the same way "Generate from Prompt" does, so a
        #    manually-created agent is just as functional as a prompt-
        #    generated one — not a bare 2-node skeleton.
        try:
            from llm.agent_flow_generator import generate_agent_flow, FlowGenerationError
            schema, _warnings = await generate_agent_flow(
                prompt=agent_data["script"],
                agent_name=agent_data["name"],
                agent_type=agent_data["agent_type"],
                data_fields=agent_data["data_fields"],
            )
        except Exception as exc:
            logger.warning(
                "[SCHEMA] Rich flow generation failed for agent_id=%s (%s) — "
                "falling back to minimal template.", agent_id, exc,
            )
            schema = StateManager.template_new_agent(
                name=agent_data["name"],
                script=agent_data["script"],
                voice_id=voice_id,
                data_fields=agent_data["data_fields"],
                agent_type=agent_data["agent_type"],
            )

    if not isinstance(schema, dict):
        schema = {}

    type_label = AGENT_TYPE_LABELS.get(agent_data["agent_type"], "Customer advisory team")
    schema["agent_name"] = agent_data["name"]
    schema["voice_id"] = voice_id
    schema["global_prompt"] = agent_data["script"]
    schema["tts_provider"] = agent_data["tts_provider"]
    schema["stt_provider"] = agent_data["stt_provider"]
    schema["provider_config"] = {
        "stt_provider": agent_data["stt_provider"],
        "tts_provider": agent_data["tts_provider"],
        "cartesia_voice_id": agent_data["cartesia_voice_id"],
        "parler_description": agent_data.get("parler_description"),
    }
    schema["agent_metadata"] = {
        "agent_type": agent_data["agent_type"],
        "assigned_email": agent_data["assigned_email"],
        "client_id": assigned_client.get("id") if assigned_client else None,
    }

    flow = schema.get("conversationFlow")
    if not isinstance(flow, dict):
        flow = {"start_node_id": "root_greeting", "nodes": []}
        schema["conversationFlow"] = flow
    flow["global_prompt"] = agent_data["script"]
    flow.setdefault("start_node_id", "root_greeting")

    nodes = flow.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        flow["nodes"] = nodes

    root = next((node for node in nodes if node.get("id") == "root_greeting"), None)
    if not root:
        root = {
            "id": "root_greeting",
            "name": "Initial Greeting",
            "type": "conversation",
            "intent_triggers": ["call_connected"],
            "edges": [{"id": "to_discovery", "condition": "user responds", "destination_node_id": "discovery"}],
        }
        nodes.insert(0, root)
    root["instruction"] = {
        "type": "prompt",
        "text": f"Greet the user warmly as {agent_data['name']} and confirm identity using the lead name.",
    }
    root["response"] = f"Hello, this is {agent_data['name']} from the {type_label}. Am I speaking with {{{{name}}}}?"

    for node in nodes:
        if node.get("id") == "discovery":
            node["collects"] = agent_data["data_fields"]

    os.makedirs(os.path.dirname(schema_path), exist_ok=True)
    with open(schema_path, "w", encoding="utf-8") as schema_file:
        json.dump(schema, schema_file, indent=4)


def _write_agent_flow_v2_shadow(
    agent_id: str,
    schema_path: str,
    agent_data: dict,
    assigned_client: Optional[dict],
) -> dict | None:
    if not feature_flags.is_enabled("flow.v2_shadow"):
        return None

    flow = build_flow_spec_from_agent(
        agent_id=agent_id,
        agent_name=agent_data["name"],
        agent_type=agent_data["agent_type"],
        script=agent_data["script"],
        data_fields=agent_data["data_fields"],
        language=agent_data["language"],
    )
    artifact_path = f"{os.path.splitext(schema_path)[0]}.flow.v2.json"
    with open(artifact_path, "w", encoding="utf-8") as flow_file:
        json.dump(flow, flow_file, indent=2)
    return {
        "artifact_path": artifact_path,
        "client_id": assigned_client.get("id") if assigned_client else None,
        "validation": flow.get("validation", {}),
    }


# ── Agents ────────────────────────────────────────────────────────────────────
def _read_agent_flow_v2_artifact(agent_id: str, artifact_path: Optional[str]) -> Optional[dict]:
    if not artifact_path:
        return None
    agents_root = os.path.abspath(AGENTS_DIR)
    candidate = os.path.abspath(artifact_path)
    try:
        if os.path.commonpath([agents_root, candidate]) != agents_root:
            logger.warning("Ignoring flow preview artifact outside agents dir: agent=%s path=%s", agent_id, artifact_path)
            return None
    except ValueError:
        return None
    if not candidate.endswith(".flow.v2.json"):
        logger.warning("Ignoring flow preview artifact with unexpected suffix: agent=%s path=%s", agent_id, artifact_path)
        return None
    if not os.path.exists(candidate):
        return None
    with open(candidate, "r", encoding="utf-8") as flow_file:
        data = json.load(flow_file)
    if data.get("agent_id") != agent_id:
        logger.warning("Ignoring flow preview artifact with agent mismatch: agent=%s path=%s", agent_id, artifact_path)
        return None
    return data


async def _load_agent_flow_v2_spec(agent: dict) -> tuple[dict, dict]:
    source = {"type": "generated_from_agent"}
    flow = None
    for version in await db.list_agent_flow_versions(agent["id"]):
        flow = _read_agent_flow_v2_artifact(agent["id"], version.get("artifact_path"))
        if flow:
            source = {
                "type": "flow_v2_artifact",
                "version_id": version.get("id"),
                "artifact_path": version.get("artifact_path"),
            }
            break
    if not flow:
        flow = build_flow_spec_from_agent(
            agent_id=agent["id"],
            agent_name=agent.get("name") or "Voice Agent",
            agent_type=agent.get("agent_type") or "real_estate_sales",
            script=agent.get("script") or "",
            data_fields=agent.get("data_fields") or [],
            language=agent.get("language") or "English",
        )
    return flow, source


def _build_editable_flow_payload(flow: dict) -> dict:
    nodes = []
    for node in flow.get("nodes") or []:
        response = node.get("response") if isinstance(node.get("response"), dict) else {}
        nodes.append(
            {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": node.get("label") or node.get("id"),
                "response_en": response.get("en") or next((value for value in response.values() if str(value).strip()), ""),
                "collects": list(node.get("collects") or []),
                "transitions": [
                    {
                        "intent": transition.get("intent"),
                        "label": transition.get("label") or str(transition.get("intent") or "").replace("_", " ").title(),
                        "target": transition.get("target"),
                    }
                    for transition in (node.get("transitions") or [])
                ],
            }
        )
    return {
        "nodes": nodes,
        "node_options": [
            {"id": node.get("id"), "label": node.get("label") or node.get("id")}
            for node in flow.get("nodes") or []
        ],
        "editable_fields": ("label", "response_en", "collects", "transitions"),
        "runtime_mode": flow.get("runtime_mode"),
        "status": flow.get("status"),
        "live_runtime_unchanged": flow.get("runtime_mode") != "live",
    }


async def _load_agent_flow_preview(agent: dict) -> dict:
    flow, source = await _load_agent_flow_v2_spec(agent)
    preview = build_flow_preview(flow)
    preview["agent"] = {
        "id": agent["id"],
        "name": agent.get("name"),
        "agent_type": agent.get("agent_type"),
        "client_id": agent.get("client_id"),
    }
    preview["source"] = source
    preview["editable_flow"] = _build_editable_flow_payload(flow)
    return preview


def _write_flow_v2_draft_artifact(agent: dict, flow: dict) -> str:
    schema_path = agent.get("schema_path") or os.path.join(AGENTS_DIR, f"{agent['id']}.json")
    base_path = os.path.splitext(schema_path)[0]
    artifact_path = f"{base_path}.{uuid.uuid4().hex[:8]}.flow.v2.json"
    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as flow_file:
        json.dump(flow, flow_file, indent=2)
    return artifact_path


def _write_flow_v2_live_artifact(agent: dict, flow: dict) -> str:
    live_flow = json.loads(json.dumps(flow))
    live_flow["status"] = "published"
    live_flow["runtime_mode"] = "live"
    metadata = live_flow.get("metadata") if isinstance(live_flow.get("metadata"), dict) else {}
    metadata.update({
        "live_runtime_unchanged": False,
        "published_at": datetime.now().isoformat(),
        "published_to": "v1_runtime_schema",
    })
    live_flow["metadata"] = metadata
    live_flow = validate_flow_spec(live_flow)

    schema_path = agent.get("schema_path") or os.path.join(AGENTS_DIR, f"{agent['id']}.json")
    base_path = os.path.splitext(schema_path)[0]
    artifact_path = f"{base_path}.{uuid.uuid4().hex[:8]}.live.flow.v2.json"
    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as flow_file:
        json.dump(live_flow, flow_file, indent=2)
    return artifact_path


def _flow_v2_response_text(node: dict, default_locale: str = "en") -> str:
    response = node.get("response") if isinstance(node.get("response"), dict) else {}
    text = response.get(default_locale)
    if not text:
        text = next((value for value in response.values() if str(value).strip()), "")
    return str(text or "Continue.").strip()


def _flow_v2_transition_text(transition: dict) -> str:
    label = transition.get("label") or transition.get("intent") or "user responds"
    return str(label).replace("_", " ").strip() or "user responds"


def _flow_v2_to_runtime_conversation_flow(flow: dict) -> dict:
    validated = validate_flow_spec(flow)
    default_locale = validated.get("default_locale") or "en"
    incoming_intents: dict[str, set[str]] = {}
    for node in validated.get("nodes") or []:
        for transition in node.get("transitions") or []:
            target = transition.get("target")
            intent = str(transition.get("intent") or "").strip()
            if target and intent:
                incoming_intents.setdefault(target, set()).add(intent)

    runtime_nodes = []
    for node in validated.get("nodes") or []:
        node_id = node["id"]
        node_type = node.get("type")
        response_text = _flow_v2_response_text(node, default_locale)
        runtime_type = "end" if node_type == "end" else "fallback" if node_type == "fallback" else "conversation"
        runtime_node = {
            "id": node_id,
            "name": node.get("label") or node_id,
            "type": runtime_type,
            "instruction": {
                "type": "prompt",
                "text": f"Follow the published Flow V2 node '{node.get('label') or node_id}' and keep the reply concise.",
            },
            "response": response_text,
            "edges": [
                {
                    "id": f"edge_{node_id}_{index}_{transition.get('target')}",
                    "condition": _flow_v2_transition_text(transition),
                    "transition_condition": {
                        "type": "prompt",
                        "prompt": _flow_v2_transition_text(transition),
                    },
                    "destination_node_id": transition.get("target"),
                }
                for index, transition in enumerate(node.get("transitions") or [], start=1)
            ],
        }
        if node_id == validated.get("start_node_id"):
            runtime_node["start_speaker"] = "agent"
            runtime_node["intent_triggers"] = ["call_connected"]
        elif incoming_intents.get(node_id):
            runtime_node["intent_triggers"] = sorted(incoming_intents[node_id])
        if node.get("collects"):
            runtime_node["collects"] = list(node.get("collects") or [])
        if node.get("fallback"):
            runtime_node["fallback"] = node.get("fallback")
        runtime_nodes.append(runtime_node)

    return {
        "conversation_flow_id": validated.get("id"),
        "version": 2,
        "schema_version": "2.0",
        "global_prompt": validated.get("global_prompt") or "",
        "start_node_id": validated.get("start_node_id"),
        "nodes": runtime_nodes,
    }


def _publish_flow_v2_to_runtime(agent: dict, flow: dict, *, actor: Optional[str] = None) -> dict:
    live_artifact_path = _write_flow_v2_live_artifact(agent, flow)
    schema_path = _agent_schema_path(agent["id"], agent.get("schema_path"))
    try:
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
    except (OSError, json.JSONDecodeError):
        schema = {}

    if not isinstance(schema, dict):
        schema = {}

    backup_path = ""
    if os.path.exists(schema_path):
        backup_path = f"{os.path.splitext(schema_path)[0]}.runtime.backup.{uuid.uuid4().hex[:8]}.json"
        with open(backup_path, "w", encoding="utf-8") as backup_file:
            json.dump(schema, backup_file, indent=2)

    live_flow = _read_agent_flow_v2_artifact(agent["id"], live_artifact_path)
    if not live_flow:
        raise FlowSpecValidationError("Published Flow V2 artifact could not be read back safely")

    schema["agent_name"] = agent.get("name") or live_flow.get("agent_name") or "Voice Agent"
    schema["global_prompt"] = live_flow.get("global_prompt") or agent.get("script") or schema.get("global_prompt") or ""
    schema["conversation_flow_id"] = live_flow.get("id") or schema.get("conversation_flow_id")
    schema["conversationFlow"] = _flow_v2_to_runtime_conversation_flow(live_flow)
    metadata = schema.get("agent_metadata") if isinstance(schema.get("agent_metadata"), dict) else {}
    metadata.update({
        "flow_v2_live": True,
        "flow_v2_artifact_path": live_artifact_path,
        "flow_v2_published_at": datetime.now().isoformat(),
        "flow_v2_published_by": actor,
        "flow_v2_runtime_backup_path": backup_path or None,
    })
    schema["agent_metadata"] = metadata

    os.makedirs(os.path.dirname(schema_path), exist_ok=True)
    with open(schema_path, "w", encoding="utf-8") as schema_file:
        json.dump(schema, schema_file, indent=4)

    return {
        "artifact_path": live_artifact_path,
        "runtime_schema_path": schema_path,
        "backup_path": backup_path,
        "status": "published",
        "runtime_mode": "live",
    }


def _apply_flow_v2_draft_updates(flow: dict, updates: FlowDraftUpdate) -> dict:
    draft = json.loads(json.dumps(flow))
    nodes = draft.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        draft["nodes"] = nodes
    node_map = {node.get("id"): node for node in nodes}
    for update in updates.nodes:
        node = node_map.get(update.id)
        if not node:
            node = {
                "id": update.id,
                "type": update.type or "message",
                "label": update.label or update.id,
                "response": {"en": update.response_en or "Continue."},
                "transitions": [],
            }
            nodes.append(node)
            node_map[update.id] = node
        if update.type is not None:
            node["type"] = str(update.type).strip() or "message"
        if update.label is not None:
            node["label"] = str(update.label).strip() or update.id
        if update.response_en is not None:
            response = node.get("response")
            if not isinstance(response, dict):
                response = {}
                node["response"] = response
            response["en"] = str(update.response_en).strip()
        if update.collects is not None:
            node["collects"] = [str(slot).strip() for slot in update.collects if str(slot).strip()]
        if update.transitions is not None:
            node["transitions"] = [
                {
                    "intent": str(transition.intent).strip(),
                    "target": str(transition.target).strip(),
                    **({"label": str(transition.label).strip()} if transition.label else {}),
                }
                for transition in update.transitions
            ]

    draft["status"] = "draft"
    draft["runtime_mode"] = "shadow"
    return validate_flow_spec(draft)


def _prepare_generated_script_flow_for_agent(script_draft: dict, agent: dict) -> dict:
    flow = json.loads(json.dumps(script_draft.get("draft") or {}))
    if flow.get("agent_id") and flow["agent_id"] != agent["id"]:
        raise HTTPException(status_code=400, detail="Generated draft does not belong to this agent")
    flow["agent_id"] = agent["id"]
    flow["agent_name"] = agent.get("name") or flow.get("agent_name") or "Voice Agent"
    flow["status"] = "draft"
    flow["runtime_mode"] = "shadow"
    metadata = flow.get("metadata") if isinstance(flow.get("metadata"), dict) else {}
    metadata["applied_from_script_draft_id"] = script_draft.get("id")
    metadata["review_required"] = True
    metadata["live_runtime_unchanged"] = True
    flow["metadata"] = metadata
    return flow


def _build_generated_script_review_policy(
    script_draft: dict,
    flow: dict,
    *,
    review_acknowledged: bool = False,
) -> dict:
    """Build a non-enforcing review gate manifest for generated scripts."""
    metadata = flow.get("metadata") if isinstance(flow.get("metadata"), dict) else {}
    website = metadata.get("website_intelligence") if isinstance(metadata.get("website_intelligence"), dict) else {}
    knowledge = script_draft.get("knowledge") if isinstance(script_draft.get("knowledge"), dict) else {}
    quality = (
        knowledge.get("quality")
        if isinstance(knowledge.get("quality"), dict)
        else website.get("quality") if isinstance(website.get("quality"), dict) else {}
    )
    checklist = website.get("review_checklist") if isinstance(website.get("review_checklist"), list) else []
    failed_checks = [
        item
        for item in checklist
        if isinstance(item, dict) and not bool(item.get("passed"))
    ]
    blockers: list[str] = []
    warnings: list[str] = []

    if not review_acknowledged:
        blockers.append("human_review_not_acknowledged")
    if not bool(quality.get("ready_for_review")):
        blockers.append("quality_not_ready_for_review")
    if not (website.get("evidence_urls") or knowledge.get("source_url")):
        blockers.append("source_evidence_missing")
    if not bool(knowledge.get("products_or_services")):
        blockers.append("services_not_detected")
    if bool(website.get("auto_publish")):
        blockers.append("auto_publish_must_remain_off")

    warnings.extend(str(item) for item in (quality.get("warnings") or []) if str(item).strip())
    warnings.extend(
        str(item.get("label") or item.get("key"))
        for item in failed_checks
        if str(item.get("label") or item.get("key") or "").strip()
    )

    would_allow = not blockers
    gate_enabled = feature_flags.is_enabled("scrape.review_gate_shadow")
    gate_enforced = gate_enabled and feature_flags.is_enabled("flow.v2_live")
    return {
        "enabled": gate_enabled,
        "mode": "live_enforced" if gate_enforced else "shadow",
        "enforced": gate_enforced,
        "can_save_flow_draft": not (gate_enforced and not would_allow),
        "would_allow_if_enforced": would_allow,
        "would_block_if_enforced": not would_allow,
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings))[:8],
        "review_acknowledged": review_acknowledged,
        "quality": {
            "score": quality.get("score", 0),
            "level": quality.get("level", "unknown"),
            "ready_for_review": bool(quality.get("ready_for_review")),
        },
        "checklist": {
            "total": len(checklist),
            "failed": len(failed_checks),
        },
        "auto_publish": False,
        "runtime_live_changed": False,
        "rollback": {
            "disable_shadow": feature_flags.env_name("scrape.review_gate_shadow"),
            "disable_live_publish": feature_flags.env_name("flow.v2_live"),
        },
    }


_LIVE_QA_PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
}


async def _build_website_live_qa_readiness(client_id: Optional[str] = None) -> dict:
    """Build a read-only production QA snapshot for real website scrape evidence."""
    jobs = await db.list_scrape_jobs(client_id=client_id, limit=50)
    production_samples: list[dict] = []
    recent_failures: list[dict] = []
    placeholder_count = 0

    for job in jobs:
        domain = str(job.get("domain") or "").strip().lower()
        if not domain:
            continue
        is_placeholder = domain in _LIVE_QA_PLACEHOLDER_DOMAINS or domain.endswith(".localhost")
        if is_placeholder:
            placeholder_count += 1
            continue
        if job.get("status") == "failed":
            recent_failures.append({
                "job_id": job.get("id"),
                "domain": domain,
                "error": job.get("error"),
            })
            continue
        if job.get("status") not in {"completed", "draft_ready"}:
            continue

        details = await db.get_scrape_job_diagnostics(job["id"])
        if not details:
            continue
        extraction = (
            details.get("latest_extraction", {}).get("extraction", {})
            if isinstance(details.get("latest_extraction"), dict)
            else {}
        )
        quality = extraction.get("quality") if isinstance(extraction.get("quality"), dict) else {}
        production_samples.append({
            "job_id": job.get("id"),
            "domain": domain,
            "status": job.get("status"),
            "pages": details.get("diagnostics", {}).get("page_count", 0),
            "drafts": details.get("diagnostics", {}).get("draft_count", 0),
            "quality_level": quality.get("level", "unknown"),
            "quality_score": quality.get("score", 0),
            "ready_for_review": bool(quality.get("ready_for_review")),
        })

    unique_domains = {item["domain"] for item in production_samples}
    ready_samples = [item for item in production_samples if item["ready_for_review"]]
    medium_or_high = [
        item
        for item in production_samples
        if item["quality_level"] in {"medium", "high"}
    ]
    draft_ready = [item for item in production_samples if int(item.get("drafts") or 0) > 0]

    criteria = [
        {
            "key": "live_worker_enabled",
            "label": "Live scrape worker is enabled for QA",
            "passed": feature_flags.is_enabled("scrape.worker_v1"),
        },
        {
            "key": "live_real_domain_coverage",
            "label": "At least 3 non-placeholder domains completed",
            "passed": len(unique_domains) >= 3,
            "value": len(unique_domains),
            "required": 3,
        },
        {
            "key": "pages_captured",
            "label": "Completed samples captured website pages",
            "passed": len(production_samples) >= 3 and all(int(item.get("pages") or 0) > 0 for item in production_samples[:3]),
        },
        {
            "key": "quality_ready",
            "label": "At least 2 samples are ready for human review",
            "passed": len(ready_samples) >= 2 and len(medium_or_high) >= 2,
            "value": len(ready_samples),
            "required": 2,
        },
        {
            "key": "drafts_generated",
            "label": "At least 2 QA samples generated review drafts",
            "passed": len(draft_ready) >= 2,
            "value": len(draft_ready),
            "required": 2,
        },
        {
            "key": "no_recent_failures",
            "label": "No recent non-placeholder scrape failures",
            "passed": len(recent_failures) == 0,
            "value": len(recent_failures),
        },
    ]
    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only",
        "client_id": client_id,
        "criteria": criteria,
        "blockers": blockers,
        "summary": {
            "jobs_considered": len(jobs),
            "placeholder_jobs_ignored": placeholder_count,
            "production_domains": len(unique_domains),
            "ready_samples": len(ready_samples),
            "draft_ready_samples": len(draft_ready),
            "recent_failures": len(recent_failures),
        },
        "samples": production_samples[:10],
        "recent_failures": recent_failures[:5],
        "runtime_live_changed": False,
        "rollback": {
            "disable_live_qa_readiness": feature_flags.env_name("scrape.live_qa_readiness"),
        },
    }


async def _build_generated_draft_qa_readiness(client_id: Optional[str] = None) -> dict:
    """Build a read-only QA snapshot for generated script review/save evidence."""
    jobs = await db.list_scrape_jobs(client_id=client_id, limit=50)
    generated_count = 0
    reviewed_saved_count = 0
    valid_flow_artifact_count = 0
    auto_published_count = 0
    invalid_saved: list[dict] = []
    samples: list[dict] = []
    flow_versions_by_agent: dict[str, list[dict]] = {}

    async def versions_for(agent_id: str) -> list[dict]:
        if agent_id not in flow_versions_by_agent:
            flow_versions_by_agent[agent_id] = await db.list_agent_flow_versions(agent_id)
        return flow_versions_by_agent[agent_id]

    for job in jobs:
        details = await db.get_scrape_job_diagnostics(job["id"])
        if not details:
            continue
        for draft in details.get("drafts") or []:
            generated_count += 1
            draft_metadata = draft.get("draft", {}).get("metadata", {}) if isinstance(draft.get("draft"), dict) else {}
            website_metadata = (
                draft_metadata.get("website_intelligence")
                if isinstance(draft_metadata.get("website_intelligence"), dict)
                else {}
            )
            if draft.get("published_at") or website_metadata.get("auto_publish"):
                auto_published_count += 1

            is_saved = bool(
                draft.get("status") == "flow_draft_saved"
                and draft.get("reviewed_at")
                and draft.get("flow_version_id")
            )
            sample = {
                "draft_id": draft.get("id"),
                "job_id": job.get("id"),
                "domain": job.get("domain"),
                "agent_id": draft.get("agent_id"),
                "status": draft.get("status"),
                "reviewed_at": draft.get("reviewed_at"),
                "flow_version_id": draft.get("flow_version_id"),
                "quality_level": (draft.get("knowledge", {}).get("quality", {}) or {}).get("level", "unknown")
                if isinstance(draft.get("knowledge"), dict)
                else "unknown",
                "runtime_live_changed": False,
            }
            if not is_saved:
                samples.append({**sample, "flow_artifact_valid": False})
                continue

            reviewed_saved_count += 1
            agent_id = draft.get("agent_id")
            if not agent_id:
                invalid_saved.append({**sample, "reason": "agent_id_missing"})
                samples.append({**sample, "flow_artifact_valid": False})
                continue
            matched_version = None
            for version in await versions_for(agent_id):
                if version.get("id") == draft.get("flow_version_id"):
                    matched_version = version
                    break
            if not matched_version:
                invalid_saved.append({**sample, "reason": "flow_version_missing"})
                samples.append({**sample, "flow_artifact_valid": False})
                continue

            try:
                artifact = _read_agent_flow_v2_artifact(agent_id, matched_version.get("artifact_path"))
                if not artifact:
                    raise ValueError("flow_artifact_missing")
                validated = validate_flow_spec(artifact)
                metadata = validated.get("metadata") if isinstance(validated.get("metadata"), dict) else {}
                if validated.get("runtime_mode") != "shadow" or validated.get("status") != "draft":
                    raise ValueError("flow_artifact_not_shadow_draft")
                if metadata.get("applied_from_script_draft_id") != draft.get("id"):
                    raise ValueError("flow_artifact_draft_mismatch")
                if metadata.get("live_runtime_unchanged") is not True:
                    raise ValueError("live_runtime_marker_missing")
                editable = _build_editable_flow_payload(validated)
                if not editable.get("nodes"):
                    raise ValueError("editable_flow_missing_nodes")
                valid_flow_artifact_count += 1
                samples.append({**sample, "flow_artifact_valid": True, "node_count": len(validated.get("nodes") or [])})
            except Exception as exc:
                invalid_saved.append({**sample, "reason": str(exc) or type(exc).__name__})
                samples.append({**sample, "flow_artifact_valid": False})

    criteria = [
        {
            "key": "flow_visualization_enabled",
            "label": "Flow visualization is enabled for review/edit",
            "passed": feature_flags.is_enabled("flow.visualization"),
        },
        {
            "key": "flow_v2_shadow_enabled",
            "label": "Flow V2 shadow draft saving is enabled",
            "passed": feature_flags.is_enabled("flow.v2_shadow"),
        },
        {
            "key": "generated_drafts_present",
            "label": "At least 1 generated website script draft exists",
            "passed": generated_count >= 1,
            "value": generated_count,
            "required": 1,
        },
        {
            "key": "review_saved_present",
            "label": "At least 1 generated draft was reviewed and saved",
            "passed": reviewed_saved_count >= 1,
            "value": reviewed_saved_count,
            "required": 1,
        },
        {
            "key": "saved_flow_artifact_valid",
            "label": "Saved generated draft has a valid Flow V2 shadow artifact",
            "passed": valid_flow_artifact_count >= 1 and not invalid_saved,
            "value": valid_flow_artifact_count,
            "required": 1,
        },
        {
            "key": "no_auto_publish",
            "label": "Generated drafts remain draft-only and not auto-published",
            "passed": auto_published_count == 0,
            "value": auto_published_count,
        },
    ]
    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only",
        "client_id": client_id,
        "criteria": criteria,
        "blockers": blockers,
        "summary": {
            "jobs_considered": len(jobs),
            "generated_drafts": generated_count,
            "reviewed_saved_drafts": reviewed_saved_count,
            "valid_flow_artifacts": valid_flow_artifact_count,
            "invalid_saved_artifacts": len(invalid_saved),
            "auto_published_drafts": auto_published_count,
        },
        "samples": samples[:10],
        "invalid_saved_artifacts": invalid_saved[:5],
        "runtime_live_changed": False,
        "rollback": {
            "disable_generated_draft_qa_readiness": feature_flags.env_name("scrape.generated_draft_qa_readiness"),
        },
    }


def _demo_qa_criterion(
    key: str,
    label: str,
    passed: bool,
    *,
    value: Any = None,
    detail: Optional[str] = None,
) -> dict:
    item = {
        "key": key,
        "label": label,
        "passed": bool(passed),
    }
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


def _pcm_constant(sample_rate: int, seconds: float, amplitude: int) -> bytes:
    count = max(1, int(sample_rate * seconds))
    return array.array("h", [int(amplitude)] * count).tobytes()


def _recording_dry_run_evidence() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "demo_qa.wav")
        recorder = TimelineSessionRecorder(sample_rate=24000)
        recorder.add_user_audio(_pcm_constant(24000, 0.2, 9000), sample_rate=24000)
        recorder.add_agent_audio(_pcm_constant(24000, 0.2, 9000), sample_rate=24000)
        duration = recorder.finalize(path)
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

    samples = array.array("h")
    samples.frombytes(frames)
    left = samples[0::2]
    right = samples[1::2]
    user_window = left[1000:3000] or left
    agent_window = right[1000:3000] or right
    user_mean = sum(abs(value) for value in user_window) / max(1, len(user_window))
    agent_mean = sum(abs(value) for value in agent_window) / max(1, len(agent_window))
    return {
        "duration_s": round(duration, 3),
        "channels": channels,
        "sample_width": sample_width,
        "sample_rate": sample_rate,
        "user_mean": round(user_mean, 2),
        "agent_mean": round(agent_mean, 2),
        "agent_audible": agent_mean > 10000,
        "user_ducked_during_agent": user_mean < 3000,
    }


def _build_demo_call_qa_readiness() -> dict:
    """Run deterministic demo-runtime QA without opening websockets or live audio."""
    from demo_runner import AI_PROCESS_DELAY_S, HUMAN_THINK_MAX_S, MAX_TURNS
    from llm.llm import _classify_local_intent
    from llm.llm_response_generator import generate_response_for_turn_sync

    criteria: list[dict] = []
    evidence: dict[str, Any] = {
        "mode": "deterministic_dry_run",
        "websocket_opened": False,
        "live_audio_changed": False,
        "recording_asset_written": False,
    }
    errors: list[str] = []

    criteria.append(_demo_qa_criterion(
        "demo_latency_constants",
        "Demo timing constants stay within QA thresholds",
        AI_PROCESS_DELAY_S <= 0.75 and HUMAN_THINK_MAX_S <= 2.5 and MAX_TURNS <= 12,
        value={
            "ai_process_delay_s": AI_PROCESS_DELAY_S,
            "human_think_max_s": HUMAN_THINK_MAX_S,
            "max_turns": MAX_TURNS,
        },
    ))

    """ schema_path = os.path.join(os.path.dirname(__file__), "Updated_Real_Estate_Agent.json") """
    schema_path = os.path.join(
        os.path.dirname(__file__),
        "db",
        "agents",
        "real_estate.json"
    )
    try:
        manager = StateManager(schema_path)
        first = manager.execute_transition("yes", {"intent": "confirm", "entities": {"confirmation": "yes"}})
        purpose_turn = manager.execute_transition("Yes, what is it?", {"intent": "user_question", "entities": {}})
        purpose_response = generate_response_for_turn_sync(purpose_turn, state_manager=manager)
        purpose_ok = (
            first.node_id == "node-1735264873079"
            and purpose_turn.node_id == "node-1735264921453"
            and purpose_turn.node_changed
            and "property interest" in purpose_response.lower()
            and "two minutes" not in purpose_response.lower()
        )
        evidence["purpose_response_sample"] = purpose_response
        criteria.append(_demo_qa_criterion(
            "availability_loop_guard",
            "Availability confirmation advances without repeating the two-minute prompt",
            purpose_ok,
            value={"node_id": purpose_turn.node_id, "user_question": purpose_turn.user_question},
        ))
    except Exception as exc:
        errors.append(f"availability_loop_guard:{type(exc).__name__}")
        criteria.append(_demo_qa_criterion(
            "availability_loop_guard",
            "Availability confirmation advances without repeating the two-minute prompt",
            False,
            detail=type(exc).__name__,
        ))

    try:
        local_intent = _classify_local_intent("Suggest me the cities.")
        intent_ok = bool(local_intent and local_intent.get("intent") == "ask_location_suggestion")
        evidence["location_intent"] = local_intent
        criteria.append(_demo_qa_criterion(
            "location_intent_guard",
            "Local intent detects location suggestion without LLM fallback",
            intent_ok,
            value=local_intent,
        ))

        manager = StateManager(schema_path)
        manager.current_node_id = "node-1735267546732"
        manager.conversation_data["budget"] = "60 lakhs"
        location_turn = manager.execute_transition("Suggest me the cities", {"intent": "unclear", "entities": {}})
        location_response = generate_response_for_turn_sync(location_turn, state_manager=manager)
        location_ok = (
            location_turn.node_id == "fallback_location"
            and "wakad" in location_response.lower()
            and "didn't catch" not in location_response.lower()
        )
        evidence["location_response_sample"] = location_response
        criteria.append(_demo_qa_criterion(
            "location_fallback_guard",
            "Unclear location requests receive useful city guidance",
            location_ok,
            value={"node_id": location_turn.node_id},
        ))

        offer_turn = manager.execute_transition(
            "Buy, ask, can you offer me?",
            {"intent": "provide_intent", "entities": {"intent_value": "buy"}},
        )
        offer_response = generate_response_for_turn_sync(offer_turn, state_manager=manager)
        offer_ok = (
            offer_turn.node_id == "fallback_location"
            and any(w in offer_response.lower() for w in ["wakad", "area", "property", "location"])
            and "didn't catch" not in offer_response.lower()
        )
        evidence["unclear_offer_response_sample"] = offer_response
        criteria.append(_demo_qa_criterion(
            "unclear_offer_fallback_guard",
            "Unclear offer requests avoid generic fallback loops",
            offer_ok,
            value={"node_id": offer_turn.node_id},
        ))
    except Exception as exc:
        errors.append(f"fallback_guard:{type(exc).__name__}")
        criteria.append(_demo_qa_criterion(
            "location_fallback_guard",
            "Unclear location requests receive useful city guidance",
            False,
            detail=type(exc).__name__,
        ))

    try:
        recording = _recording_dry_run_evidence()
        evidence["recording"] = recording
        recording_ok = (
            recording["duration_s"] > 0
            and recording["channels"] == 2
            and recording["sample_width"] == 2
            and recording["sample_rate"] == 24000
            and recording["agent_audible"]
            and recording["user_ducked_during_agent"]
        )
        criteria.append(_demo_qa_criterion(
            "recording_playback_quality_guard",
            "Recording dry-run keeps stereo audio, audible agent, and echo ducking",
            recording_ok,
            value=recording,
        ))
    except Exception as exc:
        errors.append(f"recording_guard:{type(exc).__name__}")
        criteria.append(_demo_qa_criterion(
            "recording_playback_quality_guard",
            "Recording dry-run keeps stereo audio, audible agent, and echo ducking",
            False,
            detail=type(exc).__name__,
        ))

    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only_dry_run",
        "criteria": criteria,
        "blockers": blockers,
        "errors": errors,
        "evidence": evidence,
        "runtime_live_changed": False,
        "audio_contract_changed": False,
        "websocket_contract_changed": False,
        "recording_assets_changed": False,
        "rollback": {
            "disable_demo_qa_readiness": feature_flags.env_name("demo.runtime_qa_readiness"),
        },
    }


_TELEPHONY_PROVIDER_ENV_VARS = {
    "twilio": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
    "vobiz": ("VOBIZ_API_KEY",),
    "exotel": ("EXOTEL_SID", "EXOTEL_TOKEN"),
    "knowlarity": ("KNOWLARITY_API_KEY", "KNOWLARITY_ACCOUNT_SID"),
    "demo": (),
}


def _telephony_qa_criterion(
    key: str,
    label: str,
    passed: bool,
    *,
    required: bool = True,
    value: Any = None,
    detail: Optional[str] = None,
) -> dict:
    item = {
        "key": key,
        "label": label,
        "passed": bool(passed),
        "required": bool(required),
    }
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


def _mask_phone_number(phone: str | None) -> str:
    raw = str(phone or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return f"****{digits[-4:]}"


def _public_webhook_ready(webhook_base: str) -> bool:
    normalized = (webhook_base or "").strip().lower()
    if not normalized.startswith("https://"):
        return False
    blocked_tokens = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    return not any(token in normalized for token in blocked_tokens)


async def _build_telephony_live_qa_readiness(
    *,
    provider_slug: str = "twilio",
    client_id: Optional[str] = None,
    country_code: str = "IN",
    include_provider_probe: bool = False,
) -> dict:
    """Build read-only telephony production readiness without buying or calling."""
    provider_slug = (provider_slug or "twilio").strip().lower()
    country_code = (country_code or "IN").strip().upper()
    provider_registry = {item["slug"]: item for item in list_providers()}
    provider_meta = provider_registry.get(provider_slug)
    provider_registered = provider_meta is not None
    provider_configured = bool(provider_meta and provider_meta.get("configured"))
    env_names = _TELEPHONY_PROVIDER_ENV_VARS.get(provider_slug, ())
    missing_env = [name for name in env_names if not os.getenv(name)]
    webhook_base = os.getenv("WEBHOOK_BASE_URL", WEBHOOK_BASE_URL)
    webhook_public = _public_webhook_ready(webhook_base)

    numbers = await db.list_phone_numbers(client_id)
    provider_numbers = [
        number for number in numbers
        if str(number.get("provider") or "twilio").lower() == provider_slug
    ]
    routed_numbers = [
        number for number in provider_numbers
        if number.get("client_id") and isinstance(number.get("route"), dict)
    ]

    unresolved_routes: list[dict] = []
    cross_scope_routes: list[dict] = []
    samples: list[dict] = []
    for number in routed_numbers[:10]:
        route = number.get("route") or {}
        resolved = await db.resolve_phone_number_route(number.get("phone") or "", provider_slug)
        if not resolved or resolved.get("id") != route.get("id"):
            unresolved_routes.append({
                "number_id": number.get("id"),
                "masked_phone": _mask_phone_number(number.get("phone")),
                "route_id": route.get("id"),
            })
        agent_id = route.get("agent_id")
        if agent_id:
            agent = await db.get_agent(agent_id)
            if not agent or (agent.get("client_id") and agent.get("client_id") != route.get("client_id")):
                cross_scope_routes.append({
                    "number_id": number.get("id"),
                    "route_id": route.get("id"),
                    "agent_id": agent_id,
                })
        samples.append({
            "number_id": number.get("id"),
            "masked_phone": _mask_phone_number(number.get("phone")),
            "provider": provider_slug,
            "client_id": number.get("client_id"),
            "routing_mode": route.get("routing_mode"),
            "agent_route_present": bool(route.get("agent_id")),
            "campaign_route_present": bool(route.get("campaign_id")),
        })

    provider_probe = {
        "attempted": bool(include_provider_probe),
        "required_for_live_provider": provider_slug != "demo",
        "sample_count": 0,
        "error": None,
    }
    if include_provider_probe:
        if not provider_registered:
            provider_probe["error"] = "provider_not_registered"
        elif not provider_configured and provider_slug != "demo":
            provider_probe["error"] = "provider_not_configured"
        else:
            try:
                provider = get_provider(provider_slug)
                results = await provider.list_available_numbers(country_code)
                normalized_results = [
                    _normalize_available_number(item, provider_slug, country_code)
                    for item in results
                ]
                provider_probe["sample_count"] = len(normalized_results)
            except Exception as exc:
                provider_probe["error"] = type(exc).__name__

    probe_required = provider_slug != "demo"
    probe_passed = (
        not probe_required
        or (
            include_provider_probe
            and provider_probe["sample_count"] > 0
            and not provider_probe["error"]
        )
    )

    criteria = [
        _telephony_qa_criterion(
            "provider_registered",
            "Provider is registered in the telephony registry",
            provider_registered,
            value={"provider": provider_slug},
        ),
        _telephony_qa_criterion(
            "provider_configured",
            "Provider credentials are configured without exposing secrets",
            provider_configured,
            value={
                "provider": provider_slug,
                "required_env": list(env_names),
                "missing_env": missing_env,
            },
        ),
        _telephony_qa_criterion(
            "public_webhook_base_url",
            "Webhook base URL is public HTTPS for provider callbacks",
            webhook_public,
            value={"configured": bool(webhook_base), "scheme": "https" if webhook_public else "not_public_https"},
        ),
        _telephony_qa_criterion(
            "tenant_numbers_flag_enabled",
            "Tenant phone-number isolation flag is enabled",
            feature_flags.is_enabled("telephony.tenant_numbers"),
        ),
        _telephony_qa_criterion(
            "tenant_routes_present",
            "At least one tenant-owned provider number has an active route",
            len(routed_numbers) > 0,
            value={"routed_numbers": len(routed_numbers), "provider_numbers": len(provider_numbers)},
        ),
        _telephony_qa_criterion(
            "route_resolution_ok",
            "Webhook route lookup resolves tenant-owned numbers",
            len(routed_numbers) > 0 and not unresolved_routes,
            value={"checked": min(len(routed_numbers), 10), "unresolved": len(unresolved_routes)},
        ),
        _telephony_qa_criterion(
            "route_scope_ok",
            "Agent/campaign number routes stay inside tenant scope",
            not cross_scope_routes,
            value={"checked": min(len(routed_numbers), 10), "cross_scope": len(cross_scope_routes)},
        ),
        _telephony_qa_criterion(
            "provider_number_probe",
            "Live provider number lookup was verified",
            probe_passed,
            required=probe_required,
            value=provider_probe,
        ),
    ]
    blockers = [item["key"] for item in criteria if item["required"] and not item["passed"]]
    warnings = [
        item["key"] for item in criteria
        if not item["required"] and not item["passed"]
    ]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only_provider_preflight",
        "provider": provider_slug,
        "client_id": client_id,
        "country_code": country_code,
        "criteria": criteria,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "provider_numbers": len(provider_numbers),
            "tenant_routed_numbers": len(routed_numbers),
            "unresolved_routes": len(unresolved_routes),
            "cross_scope_routes": len(cross_scope_routes),
            "provider_probe_attempted": bool(include_provider_probe),
        },
        "samples": samples,
        "runtime_live_changed": False,
        "outbound_calls_started": False,
        "numbers_purchased": False,
        "tenant_routes_modified": False,
        "webhook_contract_changed": False,
        "rollback": {
            "disable_live_qa_readiness": feature_flags.env_name("telephony.live_qa_readiness"),
            "disable_tenant_number_routing": feature_flags.env_name("telephony.tenant_numbers"),
        },
    }


def _tenant_context_from_request(request: Request):
    return getattr(request.state, "tenant_context", None)


def _resolve_intelligence_client_id(request: Request, requested_client_id: Optional[str]) -> Optional[str]:
    context = _tenant_context_from_request(request)
    tenant_id = getattr(context, "tenant_id", None)
    if context and context.is_admin:
        return requested_client_id or tenant_id
    if tenant_id:
        if requested_client_id and requested_client_id != tenant_id:
            raise HTTPException(status_code=403, detail="Tenant scope mismatch")
        return tenant_id
    if feature_flags.is_enabled("tenant.scoped_reads") or feature_flags.is_enabled("auth.enforce_backend"):
        raise HTTPException(status_code=403, detail="Tenant context required")
    return requested_client_id


def _assert_intelligence_scope(
    request: Request,
    resource_client_id: Optional[str],
    resource_name: str,
    *,
    allow_unassigned: bool = False,
) -> None:
    context = _tenant_context_from_request(request)
    if context and context.is_admin:
        return
    tenant_id = getattr(context, "tenant_id", None)
    if tenant_id:
        if resource_client_id == tenant_id:
            return
        if allow_unassigned and not resource_client_id:
            return
        raise HTTPException(status_code=403, detail=f"{resource_name} is outside tenant scope")
    if feature_flags.is_enabled("tenant.scoped_reads") or feature_flags.is_enabled("auth.enforce_backend"):
        raise HTTPException(status_code=403, detail="Tenant context required")


def _build_website_intelligence_readiness() -> dict:
    scrape_flags = [
        "scrape.generate_script",
        "scrape.worker_v1",
        "scrape.job_cancel",
        "scrape.stale_recovery",
        "scrape.job_events",
        "scrape.review_gate_shadow",
        "scrape.live_qa_readiness",
        "scrape.generated_draft_qa_readiness",
    ]
    support_flags = [
        "flow.visualization",
        "flow.v2_shadow",
        "tenant.scoped_reads",
        "auth.enforce_backend",
    ]
    flag_state = {
        flag: {
            "enabled": feature_flags.is_enabled(flag),
            "env": feature_flags.env_name(flag),
        }
        for flag in [*scrape_flags, *support_flags]
    }
    live_required = [
        "scrape.generate_script",
        "scrape.worker_v1",
        "scrape.job_cancel",
        "scrape.stale_recovery",
        "scrape.job_events",
    ]
    blockers = [
        f"{flag}.disabled"
        for flag in live_required
        if not flag_state[flag]["enabled"]
    ]
    flow_warnings = [
        f"{flag}.disabled"
        for flag in ("flow.visualization", "flow.v2_shadow")
        if not flag_state[flag]["enabled"]
    ]
    isolation_warnings = [
        f"{flag}.disabled"
        for flag in ("tenant.scoped_reads", "auth.enforce_backend")
        if not flag_state[flag]["enabled"]
    ]
    defaults = ScrapeLimits()
    return {
        "status": "ready" if not blockers else "not_ready",
        "advisory_only": True,
        "crawler_provider": "bounded_http",
        "draft_publication": "review_required",
        "flags": flag_state,
        "frontend_flags": {
            "NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED": "shows Generate Script and Intelligence UI",
            "NEXT_PUBLIC_SCRAPE_WORKER_V1_ENABLED": "allows live worker dispatch from UI",
            "NEXT_PUBLIC_SCRAPE_JOB_CANCEL_ENABLED": "shows cancel controls",
            "NEXT_PUBLIC_SCRAPE_STALE_RECOVERY_ENABLED": "shows stale recovery controls",
            "NEXT_PUBLIC_SCRAPE_JOB_EVENTS_ENABLED": "shows job event history",
            "NEXT_PUBLIC_SCRAPE_LIVE_QA_READINESS_ENABLED": "shows real-URL live QA readiness evidence",
            "NEXT_PUBLIC_SCRAPE_GENERATED_DRAFT_QA_ENABLED": "shows generated draft review/edit/save QA evidence",
        },
        "limits": {
            "max_pages": defaults.max_pages,
            "max_bytes": defaults.max_bytes,
            "timeout_s": defaults.timeout_s,
            "crawler_hard_caps": {
                "max_pages": 50,
                "max_bytes": 10_000_000,
                "timeout_s": 60,
            },
        },
        "safety": {
            "ssrf_guard": True,
            "dns_private_ip_rejection": True,
            "same_domain_crawl": True,
            "draft_only_generation": True,
            "draft_review_audit": True,
            "auto_publish": False,
            "tenant_scoped_jobs": True,
            "duplicate_dispatch_guard": True,
            "cancel_and_stale_recovery": True,
            "event_history_available": True,
            "review_gate_shadow": True,
            "live_qa_readiness": True,
            "generated_draft_qa_readiness": True,
        },
        "blockers": blockers,
        "warnings": [*flow_warnings, *isolation_warnings],
        "rollback": {
            "disable_live_worker": feature_flags.env_name("scrape.worker_v1"),
            "hide_ui": feature_flags.env_name("scrape.generate_script"),
            "disable_events": feature_flags.env_name("scrape.job_events"),
            "disable_review_gate_shadow": feature_flags.env_name("scrape.review_gate_shadow"),
            "disable_live_qa_readiness": feature_flags.env_name("scrape.live_qa_readiness"),
            "disable_generated_draft_qa_readiness": feature_flags.env_name("scrape.generated_draft_qa_readiness"),
        },
    }


def _shadow_tenant_scoped_read(
    request: Request,
    resource_type: str,
    resource_client_id: Optional[str],
    *,
    resource_found: bool = True,
) -> None:
    if not feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        return
    context = _tenant_context_from_request(request)
    if not context:
        return
    decision = build_tenant_scoped_read_guard_decision(
        context,
        resource_found=resource_found,
        owner_tenant_id=resource_client_id,
        requested_tenant_id=getattr(context, "requested_tenant_id", None) or getattr(context, "tenant_id", None),
    )
    logger.info(
        "tenant_scoped_read_endpoint_shadow resource_type=%s allowed=%s active_enforcement=%s blockers=%s",
        resource_type,
        decision["decision"]["current_requester_allowed_if_enforced"],
        decision["decision"]["active_enforcement"],
        ",".join(decision["decision"]["blockers"]),
    )


async def _shadow_transcript_access(request: Request, lead_id: str) -> None:
    if not feature_flags.is_enabled("transcripts.access_shadow"):
        return
    context = _tenant_context_from_request(request)
    if not context:
        return
    try:
        owner = await db.get_call_result_owner_for_transcript(lead_id)
    except Exception as exc:
        logger.warning(
            "transcript_access_shadow failed error_type=%s",
            type(exc).__name__,
        )
        return

    manifest = build_transcript_access_shadow_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "transcript_access_shadow found=%s owner_tenant_present=%s allowed=%s blockers=%s",
        manifest["transcript"]["found"],
        manifest["transcript"]["owner_tenant_present"],
        manifest["decision"]["current_requester_allowed_if_enforced"],
        ",".join(manifest["decision"]["blockers"]),
    )


def _require_campaign_lifecycle_enabled() -> None:
    if not feature_flags.is_enabled("campaign.lifecycle_management"):
        raise HTTPException(status_code=403, detail="campaign.lifecycle_management is disabled")


def _actor_email(request: Request, explicit_email: Optional[str] = None) -> Optional[str]:
    context = _tenant_context_from_request(request)
    return explicit_email or getattr(context, "user_email", None)


def _assert_campaign_startable(campaign: dict) -> None:
    if campaign.get("deleted_at"):
        raise HTTPException(status_code=409, detail="Campaign is soft-deleted")
    if campaign.get("archived_at"):
        raise HTTPException(status_code=409, detail="Campaign is archived")


def _campaign_lead_limit() -> int:
    try:
        return max(1, int(os.getenv("MAX_CAMPAIGN_LEADS", "5000")))
    except ValueError:
        return 5000


def _campaign_lead_key(phone: str) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit()) or str(phone or "").strip().lower()


def _normalize_campaign_leads(raw_leads: list[dict]) -> tuple[list[dict], dict[str, int]]:
    seen: set[str] = set()
    accepted: list[dict] = []
    duplicate_count = 0
    invalid_count = 0

    for raw in raw_leads:
        if not isinstance(raw, dict):
            invalid_count += 1
            continue
        name = str(raw.get("name") or "").strip()
        phone = str(raw.get("phone") or "").strip()
        key = _campaign_lead_key(phone)
        if not name or not phone or len(key) < 6:
            invalid_count += 1
            continue
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        extra = {k: v for k, v in raw.items() if k not in ("name", "phone")}
        accepted.append({"name": name, "phone": phone, **extra})

    return accepted, {
        "submitted": len(raw_leads),
        "accepted": len(accepted),
        "duplicates": duplicate_count,
        "invalid": invalid_count,
        "limit": _campaign_lead_limit(),
    }


async def _resolve_campaign_launch_client_id(request: Request, requested_client_id: Optional[str]) -> Optional[str]:
    client_id = _resolve_intelligence_client_id(request, requested_client_id)
    if not client_id:
        return None
    clients = await db.list_clients()
    if any(client.get("id") == client_id for client in clients):
        return client_id
    if feature_flags.is_enabled("tenant.scoped_reads") or feature_flags.is_enabled("auth.enforce_backend"):
        raise HTTPException(status_code=400, detail="Campaign clientId is not registered")
    logger.warning("Campaign launch requested unknown client_id=%s; preserving legacy unscoped campaign behavior", client_id)
    return None


def _campaign_qa_criterion(
    key: str,
    label: str,
    passed: bool,
    *,
    value: Any = None,
    detail: Optional[str] = None,
) -> dict:
    item = {
        "key": key,
        "label": label,
        "passed": bool(passed),
    }
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


async def _build_campaign_e2e_qa_readiness(
    *,
    client_id: Optional[str] = None,
    sample_limit: int = 10,
) -> dict:
    """Build read-only campaign launch/result readiness without starting calls."""
    sample_leads = [
        {"name": "Asha", "phone": "+919876543210", "budget": "60 lakhs"},
        {"name": "Asha Duplicate", "phone": "+91 98765 43210"},
        {"name": "Dev", "phone": "+919812345678"},
        {"name": "", "phone": "123"},
    ]
    normalized_leads, lead_summary = _normalize_campaign_leads(sample_leads)
    lead_contract_ok = (
        len(normalized_leads) == 2
        and lead_summary["duplicates"] == 1
        and lead_summary["invalid"] == 1
        and lead_summary["limit"] >= 1
    )

    campaigns = await db.list_campaigns_with_lifecycle(
        client_id=client_id,
        include_archived=True,
        include_deleted=False,
    )
    campaign_samples: list[dict] = []
    lead_scope_issues: list[str] = []
    result_scope_issues: list[str] = []
    missing_transcripts: list[str] = []
    missing_recording_assets: list[str] = []
    cross_scope_recordings: list[str] = []
    campaigns_with_leads_and_agent = 0
    campaigns_with_results = 0
    transcript_evidence_count = 0
    recording_evidence_count = 0
    live_rows_seen = 0

    for campaign in campaigns[:sample_limit]:
        campaign_id = campaign.get("id")
        leads = await db.get_leads_for_campaign(campaign_id)
        results = await db.get_results_for_campaign(campaign_id, client_id)
        live_rows = await db.get_live_state(campaign_id, client_id)
        live_rows_seen += len(live_rows)

        if client_id and campaign.get("client_id") and campaign.get("client_id") != client_id:
            result_scope_issues.append(str(campaign_id))
        if client_id:
            for lead in leads:
                if lead.get("client_id") and lead.get("client_id") != client_id:
                    lead_scope_issues.append(str(campaign_id))
                    break

        if leads and campaign.get("agent_id") and campaign.get("client_id"):
            campaigns_with_leads_and_agent += 1
        if results:
            campaigns_with_results += 1

        for result in results:
            result_client_id = result.get("client_id")
            if client_id and result_client_id and result_client_id != client_id:
                result_scope_issues.append(str(campaign_id))

            lead_id = result.get("lead_id") or result.get("id")
            if result.get("has_transcript"):
                transcript = await db.get_transcript_for_lead(lead_id, client_id)
                if transcript:
                    transcript_evidence_count += 1
                else:
                    missing_transcripts.append(str(lead_id))

            recording_url = result.get("recording_url")
            if result.get("has_recording") and recording_url:
                owner = await db.get_recording_asset_owner(recording_url)
                if owner.get("found"):
                    recording_evidence_count += 1
                    if client_id and owner.get("owner_client_id") and owner.get("owner_client_id") != client_id:
                        cross_scope_recordings.append(str(lead_id))
                else:
                    missing_recording_assets.append(str(recording_url))

        campaign_samples.append({
            "id": campaign_id,
            "client_id": campaign.get("client_id"),
            "status": campaign.get("status"),
            "provider": campaign.get("telephony_provider") or "demo",
            "lead_count": len(leads),
            "result_count": len(results),
            "live_rows": len(live_rows),
            "agent_assigned": bool(campaign.get("agent_id")),
            "archived": bool(campaign.get("archived_at")),
            "deleted": bool(campaign.get("deleted_at")),
        })

    worker_config = CampaignWorkerV2Config()
    criteria = [
        _campaign_qa_criterion(
            "lead_ingestion_contract",
            "Lead ingestion accepts valid rows and rejects duplicate/invalid rows",
            lead_contract_ok,
            value=lead_summary,
        ),
        _campaign_qa_criterion(
            "campaign_read_scope_ok",
            "Campaign reads are tenant-scoped and return no cross-tenant rows",
            not lead_scope_issues and not result_scope_issues,
            value={
                "campaigns_checked": min(len(campaigns), sample_limit),
                "lead_scope_issues": len(lead_scope_issues),
                "result_scope_issues": len(result_scope_issues),
            },
        ),
        _campaign_qa_criterion(
            "launch_prerequisites_present",
            "At least one campaign has tenant, agent, and leads ready for launch",
            campaigns_with_leads_and_agent > 0,
            value={"campaigns_with_leads_and_agent": campaigns_with_leads_and_agent},
        ),
        _campaign_qa_criterion(
            "v1_runner_fallback_preserved",
            "Existing campaign runner remains available as the live fallback",
            callable(run_campaign),
            value={"runner": "agent_runner.run_campaign"},
        ),
        _campaign_qa_criterion(
            "worker_v2_live_metadata_safe",
            "Worker V2 is live for durable metadata while v1 remains the dispatch/audio runner",
            worker_config.mode == "live_metadata",
            value={
                "campaign_worker_v2_enabled": feature_flags.is_enabled("campaign.worker_v2"),
                "default_mode": worker_config.mode,
                "dispatch_runner": "v1_compatible",
                "max_attempts": worker_config.max_attempts,
            },
        ),
        _campaign_qa_criterion(
            "result_persistence_evidence",
            "At least one campaign result is persisted for QA evidence",
            campaigns_with_results > 0,
            value={"campaigns_with_results": campaigns_with_results},
        ),
        _campaign_qa_criterion(
            "transcript_persistence_evidence",
            "Persisted campaign results expose retrievable transcripts",
            transcript_evidence_count > 0 and not missing_transcripts,
            value={
                "transcripts_verified": transcript_evidence_count,
                "missing_transcripts": len(missing_transcripts),
            },
        ),
        _campaign_qa_criterion(
            "recording_persistence_evidence",
            "Persisted campaign results have tenant-owned recording assets",
            recording_evidence_count > 0 and not missing_recording_assets and not cross_scope_recordings,
            value={
                "recordings_verified": recording_evidence_count,
                "missing_recording_assets": len(missing_recording_assets),
                "cross_scope_recordings": len(cross_scope_recordings),
            },
        ),
        _campaign_qa_criterion(
            "live_update_read_contract",
            "Live campaign update reads are available and scoped",
            isinstance(live_rows_seen, int),
            value={"live_rows_seen": live_rows_seen},
        ),
    ]
    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only_campaign_e2e_preflight",
        "client_id": client_id,
        "criteria": criteria,
        "blockers": blockers,
        "summary": {
            "campaigns_checked": min(len(campaigns), sample_limit),
            "campaigns_with_leads_and_agent": campaigns_with_leads_and_agent,
            "campaigns_with_results": campaigns_with_results,
            "transcripts_verified": transcript_evidence_count,
            "recordings_verified": recording_evidence_count,
            "live_rows_seen": live_rows_seen,
        },
        "samples": campaign_samples,
        "campaigns_started": False,
        "outbound_calls_started": False,
        "results_written": False,
        "live_state_written": False,
        "queue_dispatch_started": False,
        "runtime_live_changed": False,
        "websocket_contract_changed": False,
        "audio_contract_changed": False,
        "rollback": {
            "disable_campaign_e2e_qa_readiness": feature_flags.env_name("campaign.e2e_qa_readiness"),
            "disable_campaign_worker_v2": feature_flags.env_name("campaign.worker_v2"),
        },
    }


def _tenant_security_criterion(
    key: str,
    label: str,
    passed: bool,
    *,
    value: Any = None,
    detail: Optional[str] = None,
) -> dict:
    item = {
        "key": key,
        "label": label,
        "passed": bool(passed),
    }
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


async def _build_tenant_security_audit_readiness(
    *,
    client_id: Optional[str] = None,
) -> dict:
    """Build a read-only tenant/security audit from aggregate counts only."""
    counts = await db.get_tenant_security_audit_counts(client_id=client_id)
    missing_owner_by_table = counts["missing_owner_by_table"]
    relationship_mismatches = counts["relationship_mismatches"]
    blocking_missing_owner_by_table = {
        table: value
        for table, value in missing_owner_by_table.items()
        if value and table not in {"phone_numbers"}
    }
    warning_missing_owner_by_table = {
        table: value
        for table, value in missing_owner_by_table.items()
        if value and table in {"phone_numbers"}
    }

    required_live_flags = [
        "auth.enforce_backend",
        "tenant.scoped_reads",
        "ws.scoped_events",
        "telephony.tenant_numbers",
    ]
    guard_flags = [
        *required_live_flags,
        "tenant.scoped_read_endpoint_shadow",
        "tenant.leak_regression_matrix",
        "tenant.security_leak_audit_readiness",
        "recordings.access_gate_dry_run",
        "transcripts.protected_route_stub",
        "transcripts.frontend_migration_readiness",
    ]
    known_flag_names = feature_flags.known_flags()
    flag_state = {
        flag: {
            "enabled": feature_flags.is_enabled(flag),
            "env": feature_flags.env_name(flag),
            "registered": flag in known_flag_names,
        }
        for flag in guard_flags
    }
    live_flags_enabled = all(flag_state[flag]["enabled"] for flag in required_live_flags)
    flags_registered = all(config["registered"] for config in flag_state.values())

    phone_mismatch_total = sum(
        value
        for key, value in relationship_mismatches.items()
        if key.startswith("phone_route_") or key == "campaign_phone_number_scope_mismatch"
    )
    memory_mismatch_total = sum(
        value
        for key, value in relationship_mismatches.items()
        if key.startswith("memory_")
    )
    scrape_crm_mismatch_total = sum(
        value
        for key, value in relationship_mismatches.items()
        if key.startswith(("scrape_", "generated_", "crm_"))
    )
    criteria = [
        _tenant_security_criterion(
            "tenant_inventory_present",
            "Tenant inventory exists for audit",
            counts["total_clients"] > 0,
            value={"total_clients": counts["total_clients"]},
        ),
        _tenant_security_criterion(
            "guard_flags_registered",
            "Tenant guard and rollback flags are registered",
            flags_registered,
            value=flag_state,
        ),
        _tenant_security_criterion(
            "live_isolation_flags_enabled",
            "Production isolation flags are enabled before go-live",
            live_flags_enabled,
            value={
                flag: flag_state[flag]["enabled"]
                for flag in required_live_flags
            },
            detail="This audit does not enable flags; production must enable them deliberately.",
        ),
        _tenant_security_criterion(
            "ownership_metadata_complete",
            "Tenant-owned resources have client ownership metadata",
            not blocking_missing_owner_by_table,
            value={
                "blocking_missing_owner_by_table": blocking_missing_owner_by_table,
                "warning_missing_owner_by_table": warning_missing_owner_by_table,
            },
        ),
        _tenant_security_criterion(
            "relationship_scope_consistent",
            "Related resources stay inside the same tenant boundary",
            counts["relationship_mismatch_total"] == 0,
            value={
                "relationship_mismatch_total": counts["relationship_mismatch_total"],
                "relationship_mismatches": {
                    key: value
                    for key, value in relationship_mismatches.items()
                    if value
                },
            },
        ),
        _tenant_security_criterion(
            "phone_route_isolation_consistent",
            "Phone numbers, routes, agents, and campaigns stay tenant-aligned",
            phone_mismatch_total == 0 and not missing_owner_by_table.get("phone_number_routes"),
            value={
                "phone_mismatch_total": phone_mismatch_total,
                "unassigned_phone_numbers": missing_owner_by_table.get("phone_numbers", 0),
                "active_routes_missing_owner": missing_owner_by_table.get("phone_number_routes", 0),
            },
        ),
        _tenant_security_criterion(
            "memory_training_isolated",
            "Agent memory and training rows stay tenant/agent aligned",
            memory_mismatch_total == 0
            and not any(
                blocking_missing_owner_by_table.get(table, 0)
                for table in (
                    "agent_memory_collections",
                    "agent_memory_items",
                    "agent_memory_events",
                )
            ),
            value={"memory_mismatch_total": memory_mismatch_total},
        ),
        _tenant_security_criterion(
            "scrape_crm_scope_consistent",
            "Website intelligence and CRM rows stay tenant-aligned",
            scrape_crm_mismatch_total == 0,
            value={"scrape_crm_mismatch_total": scrape_crm_mismatch_total},
        ),
        _tenant_security_criterion(
            "payload_safe_audit_contract",
            "Audit returns counts only and no tenant payloads",
            not counts["payloads_returned"]
            and not counts["ids_returned"]
            and not counts["tenant_values_returned"],
            value={
                "payloads_returned": counts["payloads_returned"],
                "ids_returned": counts["ids_returned"],
                "tenant_values_returned": counts["tenant_values_returned"],
            },
        ),
    ]
    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_production_push": not blockers,
        "mode": "read_only_tenant_security_audit",
        "client_scope_requested": bool(client_id),
        "criteria": criteria,
        "blockers": blockers,
        "warnings": [
            "unassigned_phone_numbers_present"
            for _table, value in warning_missing_owner_by_table.items()
            if value
        ],
        "summary": {
            "total_clients": counts["total_clients"],
            "tenant_tables_checked": len(counts["totals_by_table"]),
            "selected_client_tables_checked": len(counts["selected_client_totals"]),
            "missing_owner_total": sum(blocking_missing_owner_by_table.values()),
            "relationship_mismatch_total": counts["relationship_mismatch_total"],
            "phone_mismatch_total": phone_mismatch_total,
            "memory_mismatch_total": memory_mismatch_total,
            "scrape_crm_mismatch_total": scrape_crm_mismatch_total,
        },
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "phone_numbers_returned": False,
        "transcript_content_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "rollback": {
            "disable_security_audit": feature_flags.env_name("tenant.security_leak_audit_readiness"),
            "disable_backend_auth_enforcement": feature_flags.env_name("auth.enforce_backend"),
            "disable_scoped_reads": feature_flags.env_name("tenant.scoped_reads"),
            "disable_scoped_events": feature_flags.env_name("ws.scoped_events"),
            "disable_tenant_number_routing": feature_flags.env_name("telephony.tenant_numbers"),
        },
    }


def _final_canary_criterion(
    key: str,
    label: str,
    passed: bool,
    *,
    value: Any = None,
    detail: Optional[str] = None,
) -> dict:
    item = {
        "key": key,
        "label": label,
        "passed": bool(passed),
    }
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


async def _build_final_canary_rollback_readiness(
    *,
    client_id: Optional[str] = None,
) -> dict:
    """Build a final no-action canary and rollback readiness gate."""
    campaign = await _build_campaign_e2e_qa_readiness(client_id=client_id)
    security = await _build_tenant_security_audit_readiness(client_id=client_id)

    live_required_flags = [
        "auth.enforce_backend",
        "tenant.scoped_reads",
        "ws.scoped_events",
        "telephony.tenant_numbers",
    ]
    rollback_switches = [
        "tenant.final_canary_rollback_readiness",
        "tenant.production_go_no_go_gate",
        "tenant.rollback_drill_readiness",
        "tenant.rollout_canary_plan",
        "tenant.rollout_approval_packet",
        "tenant.final_rollout_report",
        "tenant.result_asset_readiness",
        "tenant.leak_regression_matrix",
        "auth.enforce_backend",
        "tenant.scoped_reads",
        "ws.scoped_events",
        "telephony.tenant_numbers",
        "campaign.worker_v2",
        "flow.v2_live",
        "scrape.worker_v1",
        "crm.sync_enabled",
        "memory.rag_enabled",
    ]
    known_flags = feature_flags.known_flags()
    flag_state = {
        flag: {
            "enabled": feature_flags.is_enabled(flag),
            "env": feature_flags.env_name(flag),
            "registered": flag in known_flags,
        }
        for flag in rollback_switches
    }
    live_flags_enabled = all(flag_state[flag]["enabled"] for flag in live_required_flags)
    kill_switches_registered = all(flag_state[flag]["registered"] for flag in rollback_switches)
    canary_plan = {
        "plan_only": True,
        "single_tenant_canary_required": True,
        "single_campaign_canary_required": True,
        "demo_call_canary_required": True,
        "minimum_observation_minutes": 30,
        "traffic_shift_percent": 0,
        "automatic_activation_enabled": False,
        "sequence": [
            "confirm_backend_health",
            "confirm_demo_call_smoke",
            "confirm_single_tenant_campaign_readiness",
            "enable_flags_for_one tenant only",
            "observe_results_transcripts_recordings",
            "manual_go_or_rollback_decision",
        ],
        "abort_thresholds": {
            "tenant_leak_count": 0,
            "audio_runtime_errors": 0,
            "websocket_contract_errors": 0,
            "campaign_result_persistence_errors": 0,
            "recording_playback_errors": 0,
        },
    }
    rollback_plan = {
        "readiness_only": True,
        "rollback_action_performed": False,
        "kill_switch_order": [
            "tenant.final_canary_rollback_readiness",
            "tenant.production_go_no_go_gate",
            "tenant.rollback_drill_readiness",
            "tenant.rollout_canary_plan",
            "auth.enforce_backend",
            "tenant.scoped_reads",
            "ws.scoped_events",
            "telephony.tenant_numbers",
        ],
        "post_rollback_checks": [
            "legacy_results_endpoint_available",
            "legacy_transcript_endpoint_available",
            "static_recording_mount_available",
            "demo_voice_call_smoke",
            "dashboard_websocket_smoke",
            "tenant_security_audit_recheck",
        ],
    }

    runtime_neutral = all(
        value is False
        for value in (
            campaign.get("runtime_live_changed"),
            campaign.get("websocket_contract_changed"),
            campaign.get("audio_contract_changed"),
            security.get("runtime_enforcement_changed"),
            security.get("audio_runtime_changed"),
            security.get("websocket_contract_changed"),
            security.get("campaign_runtime_changed"),
            security.get("db_write_performed"),
        )
    )
    criteria = [
        _final_canary_criterion(
            "campaign_e2e_ready",
            "Campaign launch, results, transcripts, recordings, and live updates are ready",
            bool(campaign.get("ready_for_production_push")),
            value=campaign.get("summary"),
        ),
        _final_canary_criterion(
            "tenant_security_ready",
            "Tenant ownership, isolation, memory, phone, scraping, and CRM audit is clean",
            bool(security.get("ready_for_production_push")),
            value=security.get("summary"),
        ),
        _final_canary_criterion(
            "live_isolation_flags_enabled",
            "Production isolation flags are explicitly enabled for go-live",
            live_flags_enabled,
            value={
                flag: flag_state[flag]["enabled"]
                for flag in live_required_flags
            },
            detail="This gate reports flag state only; it never changes flags.",
        ),
        _final_canary_criterion(
            "kill_switches_registered",
            "Rollback kill switches are registered and can be disabled quickly",
            kill_switches_registered,
            value={
                flag: flag_state[flag]["env"]
                for flag in rollback_switches
            },
        ),
        _final_canary_criterion(
            "manual_canary_plan_defined",
            "Manual one-tenant canary plan is defined",
            True,
            value=canary_plan,
        ),
        _final_canary_criterion(
            "rollback_drill_defined",
            "Rollback drill and post-rollback checks are defined",
            True,
            value=rollback_plan,
        ),
        _final_canary_criterion(
            "runtime_contracts_unchanged",
            "Final gate does not change audio, websocket, campaign, or DB-write behavior",
            runtime_neutral,
            value={
                "campaign_runtime_live_changed": campaign.get("runtime_live_changed"),
                "campaign_websocket_contract_changed": campaign.get("websocket_contract_changed"),
                "campaign_audio_contract_changed": campaign.get("audio_contract_changed"),
                "security_runtime_enforcement_changed": security.get("runtime_enforcement_changed"),
                "security_db_write_performed": security.get("db_write_performed"),
            },
        ),
        _final_canary_criterion(
            "activation_not_started",
            "No canary, traffic shift, production activation, or rollback is executed by this gate",
            True,
            value={
                "canary_started": False,
                "traffic_shifted": False,
                "production_activation_started": False,
                "rollback_action_performed": False,
                "feature_flags_modified": False,
                "routes_modified": False,
            },
        ),
    ]
    blockers = [item["key"] for item in criteria if not item["passed"]]
    return {
        "status": "ready" if not blockers else "not_ready",
        "ready_for_manual_canary": not blockers,
        "ready_for_production_push": not blockers,
        "mode": "read_only_final_canary_rollback_gate",
        "client_scope_requested": bool(client_id),
        "criteria": criteria,
        "blockers": blockers,
        "summary": {
            "campaign_ready": bool(campaign.get("ready_for_production_push")),
            "tenant_security_ready": bool(security.get("ready_for_production_push")),
            "live_flags_enabled": live_flags_enabled,
            "kill_switches_registered": kill_switches_registered,
            "minimum_observation_minutes": canary_plan["minimum_observation_minutes"],
            "traffic_shift_percent": 0,
        },
        "child_blockers": {
            "campaign_e2e": campaign.get("blockers", []),
            "tenant_security": security.get("blockers", []),
        },
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "recording_playback_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "phone_numbers_returned": False,
        "transcript_content_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "canary_started": False,
        "traffic_shifted": False,
        "production_activation_started": False,
        "rollback_action_performed": False,
        "feature_flags_modified": False,
        "routes_modified": False,
        "rollback": {
            "disable_final_gate": feature_flags.env_name("tenant.final_canary_rollback_readiness"),
            "disable_backend_auth_enforcement": feature_flags.env_name("auth.enforce_backend"),
            "disable_scoped_reads": feature_flags.env_name("tenant.scoped_reads"),
            "disable_scoped_events": feature_flags.env_name("ws.scoped_events"),
            "disable_tenant_number_routing": feature_flags.env_name("telephony.tenant_numbers"),
            "disable_campaign_worker_v2": feature_flags.env_name("campaign.worker_v2"),
            "disable_flow_v2_live": feature_flags.env_name("flow.v2_live"),
        },
    }


def _require_tenant_numbers_enabled() -> None:
    if not feature_flags.is_enabled("telephony.tenant_numbers"):
        raise HTTPException(status_code=403, detail="telephony.tenant_numbers is disabled")


def _require_memory_enabled() -> None:
    if not feature_flags.is_enabled("memory.rag_enabled"):
        raise HTTPException(status_code=403, detail="memory.rag_enabled is disabled")


def _require_tenant_enforcement_readiness_enabled() -> None:
    if not feature_flags.is_enabled("tenant.enforcement_readiness"):
        raise HTTPException(status_code=403, detail="tenant.enforcement_readiness is disabled")


def _require_tenant_scoped_read_canary_enabled() -> None:
    _require_tenant_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("tenant.scoped_read_canary"):
        raise HTTPException(status_code=403, detail="tenant.scoped_read_canary is disabled")


def _require_tenant_scoped_read_policy_enabled() -> None:
    _require_tenant_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("tenant.scoped_read_policy_shadow"):
        raise HTTPException(status_code=403, detail="tenant.scoped_read_policy_shadow is disabled")


def _require_tenant_leak_regression_matrix_enabled() -> None:
    _require_tenant_scoped_read_canary_enabled()
    if not feature_flags.is_enabled("tenant.leak_regression_matrix"):
        raise HTTPException(status_code=403, detail="tenant.leak_regression_matrix is disabled")


def _require_tenant_security_leak_audit_readiness_enabled() -> None:
    if not feature_flags.is_enabled("tenant.security_leak_audit_readiness"):
        raise HTTPException(status_code=403, detail="tenant.security_leak_audit_readiness is disabled")


def _require_final_canary_rollback_readiness_enabled() -> None:
    if not feature_flags.is_enabled("tenant.final_canary_rollback_readiness"):
        raise HTTPException(status_code=403, detail="tenant.final_canary_rollback_readiness is disabled")


def _require_recording_owner_lookup_shadow_enabled() -> None:
    _require_tenant_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("recordings.access_shadow"):
        raise HTTPException(status_code=403, detail="recordings.access_shadow is disabled")
    if not feature_flags.is_enabled("recordings.owner_lookup_shadow"):
        raise HTTPException(status_code=403, detail="recordings.owner_lookup_shadow is disabled")


def _require_recording_access_enforcement_shadow_enabled() -> None:
    _require_recording_owner_lookup_shadow_enabled()
    if not feature_flags.is_enabled("recordings.access_enforcement_shadow"):
        raise HTTPException(status_code=403, detail="recordings.access_enforcement_shadow is disabled")


def _require_recording_access_gate_dry_run_enabled() -> None:
    _require_recording_access_enforcement_shadow_enabled()
    if not feature_flags.is_enabled("recordings.access_gate_dry_run"):
        raise HTTPException(status_code=403, detail="recordings.access_gate_dry_run is disabled")


def _require_transcript_access_canary_enabled() -> None:
    _require_tenant_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("transcripts.access_shadow"):
        raise HTTPException(status_code=403, detail="transcripts.access_shadow is disabled")
    if not feature_flags.is_enabled("transcripts.access_canary"):
        raise HTTPException(status_code=403, detail="transcripts.access_canary is disabled")


def _require_transcript_access_enforcement_shadow_enabled() -> None:
    _require_tenant_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("transcripts.access_shadow"):
        raise HTTPException(status_code=403, detail="transcripts.access_shadow is disabled")
    if not feature_flags.is_enabled("transcripts.access_enforcement_shadow"):
        raise HTTPException(status_code=403, detail="transcripts.access_enforcement_shadow is disabled")


def _require_transcript_access_gate_dry_run_enabled() -> None:
    _require_transcript_access_enforcement_shadow_enabled()
    if not feature_flags.is_enabled("transcripts.access_gate_dry_run"):
        raise HTTPException(status_code=403, detail="transcripts.access_gate_dry_run is disabled")


def _require_transcript_protected_route_stub_enabled() -> None:
    if not feature_flags.is_enabled("transcripts.protected_route_stub"):
        raise HTTPException(status_code=404, detail="Transcript route not found")
    _require_transcript_access_gate_dry_run_enabled()


def _require_transcript_protected_enforcement_readiness_enabled() -> None:
    _require_transcript_access_gate_dry_run_enabled()
    if not feature_flags.is_enabled("transcripts.protected_route_stub"):
        raise HTTPException(status_code=403, detail="transcripts.protected_route_stub is disabled")
    if not feature_flags.is_enabled("transcripts.protected_route_permission_shadow"):
        raise HTTPException(status_code=403, detail="transcripts.protected_route_permission_shadow is disabled")
    if not feature_flags.is_enabled("transcripts.protected_response_shape_canary"):
        raise HTTPException(status_code=403, detail="transcripts.protected_response_shape_canary is disabled")
    if not feature_flags.is_enabled("transcripts.protected_payload_dry_run"):
        raise HTTPException(status_code=403, detail="transcripts.protected_payload_dry_run is disabled")
    if not feature_flags.is_enabled("transcripts.protected_enforcement_readiness"):
        raise HTTPException(status_code=403, detail="transcripts.protected_enforcement_readiness is disabled")


def _require_transcript_protected_live_activation_plan_enabled() -> None:
    _require_transcript_protected_enforcement_readiness_enabled()
    if not feature_flags.is_enabled("transcripts.protected_live_activation_plan"):
        raise HTTPException(status_code=403, detail="transcripts.protected_live_activation_plan is disabled")


def _require_transcript_protected_rollback_readiness_enabled() -> None:
    _require_transcript_protected_live_activation_plan_enabled()
    if not feature_flags.is_enabled("transcripts.protected_rollback_readiness"):
        raise HTTPException(status_code=403, detail="transcripts.protected_rollback_readiness is disabled")


def _require_transcript_frontend_migration_readiness_enabled() -> None:
    _require_transcript_protected_rollback_readiness_enabled()
    if not feature_flags.is_enabled("transcripts.frontend_migration_readiness"):
        raise HTTPException(status_code=403, detail="transcripts.frontend_migration_readiness is disabled")


def _require_result_asset_readiness_enabled() -> None:
    _require_transcript_frontend_migration_readiness_enabled()
    _require_recording_access_gate_dry_run_enabled()
    _require_tenant_leak_regression_matrix_enabled()
    if not feature_flags.is_enabled("tenant.result_asset_readiness"):
        raise HTTPException(status_code=403, detail="tenant.result_asset_readiness is disabled")


def _require_final_rollout_report_enabled() -> None:
    _require_result_asset_readiness_enabled()
    if not feature_flags.is_enabled("tenant.final_rollout_report"):
        raise HTTPException(status_code=403, detail="tenant.final_rollout_report is disabled")


def _require_rollout_approval_packet_enabled() -> None:
    _require_final_rollout_report_enabled()
    if not feature_flags.is_enabled("tenant.rollout_approval_packet"):
        raise HTTPException(status_code=403, detail="tenant.rollout_approval_packet is disabled")


def _require_rollout_canary_plan_enabled() -> None:
    _require_rollout_approval_packet_enabled()
    if not feature_flags.is_enabled("tenant.rollout_canary_plan"):
        raise HTTPException(status_code=403, detail="tenant.rollout_canary_plan is disabled")


def _require_rollback_drill_readiness_enabled() -> None:
    _require_rollout_canary_plan_enabled()
    if not feature_flags.is_enabled("tenant.rollback_drill_readiness"):
        raise HTTPException(status_code=403, detail="tenant.rollback_drill_readiness is disabled")


def _require_rollout_evidence_bundle_enabled() -> None:
    _require_rollback_drill_readiness_enabled()
    if not feature_flags.is_enabled("tenant.rollout_evidence_bundle"):
        raise HTTPException(status_code=403, detail="tenant.rollout_evidence_bundle is disabled")


def _require_canary_observation_checklist_enabled() -> None:
    _require_rollout_evidence_bundle_enabled()
    if not feature_flags.is_enabled("tenant.canary_observation_checklist"):
        raise HTTPException(status_code=403, detail="tenant.canary_observation_checklist is disabled")


def _require_production_go_no_go_gate_enabled() -> None:
    _require_canary_observation_checklist_enabled()
    if not feature_flags.is_enabled("tenant.production_go_no_go_gate"):
        raise HTTPException(status_code=403, detail="tenant.production_go_no_go_gate is disabled")


def _require_production_activation_contract_stub_enabled() -> None:
    _require_production_go_no_go_gate_enabled()
    if not feature_flags.is_enabled("tenant.production_activation_contract_stub"):
        raise HTTPException(status_code=403, detail="tenant.production_activation_contract_stub is disabled")


def _require_production_activation_permission_shadow_enabled() -> None:
    _require_production_activation_contract_stub_enabled()
    if not feature_flags.is_enabled("tenant.production_activation_permission_shadow"):
        raise HTTPException(status_code=403, detail="tenant.production_activation_permission_shadow is disabled")


def _require_production_activation_payload_dry_run_enabled() -> None:
    _require_production_activation_permission_shadow_enabled()
    if not feature_flags.is_enabled("tenant.production_activation_payload_dry_run"):
        raise HTTPException(status_code=403, detail="tenant.production_activation_payload_dry_run is disabled")


def _require_production_activation_readiness_enabled() -> None:
    _require_production_activation_payload_dry_run_enabled()
    if not feature_flags.is_enabled("tenant.production_activation_readiness"):
        raise HTTPException(status_code=403, detail="tenant.production_activation_readiness is disabled")


def _require_production_activation_rollback_confirmation_enabled() -> None:
    _require_production_activation_readiness_enabled()
    if not feature_flags.is_enabled("tenant.production_activation_rollback_confirmation"):
        raise HTTPException(status_code=403, detail="tenant.production_activation_rollback_confirmation is disabled")


def _require_controlled_handoff_readiness_enabled() -> None:
    _require_production_activation_rollback_confirmation_enabled()
    if not feature_flags.is_enabled("tenant.controlled_handoff_readiness"):
        raise HTTPException(status_code=403, detail="tenant.controlled_handoff_readiness is disabled")


def _require_crm_enabled() -> None:
    if not feature_flags.is_enabled("crm.sync_enabled"):
        raise HTTPException(status_code=403, detail="crm.sync_enabled is disabled")


def _require_crm_preflight_enabled() -> None:
    _require_crm_enabled()
    if not feature_flags.is_enabled("crm.sync_preflight"):
        raise HTTPException(status_code=403, detail="crm.sync_preflight is disabled")


def _require_crm_outbox_enabled() -> None:
    _require_crm_preflight_enabled()
    if not feature_flags.is_enabled("crm.sync_outbox"):
        raise HTTPException(status_code=403, detail="crm.sync_outbox is disabled")


def _require_crm_worker_shadow_enabled() -> None:
    _require_crm_outbox_enabled()
    if not feature_flags.is_enabled("crm.sync_worker_shadow"):
        raise HTTPException(status_code=403, detail="crm.sync_worker_shadow is disabled")


def _require_crm_worker_retries_enabled() -> None:
    _require_crm_worker_shadow_enabled()
    if not feature_flags.is_enabled("crm.sync_worker_retries"):
        raise HTTPException(status_code=403, detail="crm.sync_worker_retries is disabled")


def _require_crm_observability_enabled() -> None:
    _require_crm_outbox_enabled()
    if not feature_flags.is_enabled("crm.sync_observability"):
        raise HTTPException(status_code=403, detail="crm.sync_observability is disabled")


def _require_crm_provider_contracts_enabled() -> None:
    _require_crm_enabled()
    if not feature_flags.is_enabled("crm.provider_contracts"):
        raise HTTPException(status_code=403, detail="crm.provider_contracts is disabled")


def _require_crm_delivery_plan_enabled() -> None:
    _require_crm_outbox_enabled()
    _require_crm_provider_contracts_enabled()
    if not feature_flags.is_enabled("crm.delivery_plan_shadow"):
        raise HTTPException(status_code=403, detail="crm.delivery_plan_shadow is disabled")


def _require_crm_delivery_approval_enabled() -> None:
    _require_crm_delivery_plan_enabled()
    if not feature_flags.is_enabled("crm.delivery_approval_shadow"):
        raise HTTPException(status_code=403, detail="crm.delivery_approval_shadow is disabled")


def _require_crm_delivery_approval_revoke_enabled() -> None:
    _require_crm_delivery_approval_enabled()
    if not feature_flags.is_enabled("crm.delivery_approval_revoke"):
        raise HTTPException(status_code=403, detail="crm.delivery_approval_revoke is disabled")


def _require_crm_live_readiness_enabled() -> None:
    _require_crm_delivery_approval_revoke_enabled()
    if not feature_flags.is_enabled("crm.live_readiness_shadow"):
        raise HTTPException(status_code=403, detail="crm.live_readiness_shadow is disabled")


def _require_crm_provider_sandbox_enabled() -> None:
    _require_crm_live_readiness_enabled()
    if not feature_flags.is_enabled("crm.provider_sandbox_shadow"):
        raise HTTPException(status_code=403, detail="crm.provider_sandbox_shadow is disabled")


def _require_crm_dispatch_canary_enabled() -> None:
    _require_crm_provider_sandbox_enabled()
    if not feature_flags.is_enabled("crm.dispatch_canary_shadow"):
        raise HTTPException(status_code=403, detail="crm.dispatch_canary_shadow is disabled")


def _resolve_memory_client_id(request: Request, agent: dict, requested_client_id: Optional[str]) -> str:
    agent_client_id = agent.get("client_id")
    client_id = requested_client_id or agent_client_id
    if not client_id:
        raise HTTPException(status_code=400, detail="Agent must be tenant-assigned before memory can be created")
    _assert_intelligence_scope(request, client_id, "Agent memory")
    if agent_client_id and agent_client_id != client_id:
        raise HTTPException(status_code=403, detail="Agent is outside memory tenant scope")
    return client_id


def _require_resolved_client_id(client_id: Optional[str], resource_name: str) -> str:
    if not client_id:
        raise HTTPException(status_code=400, detail=f"{resource_name} requires a tenant clientId")
    return client_id


def _crm_value_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    if "outside" in detail or "tenant scope" in detail:
        return HTTPException(status_code=403, detail=detail)
    if "not found" in detail:
        return HTTPException(status_code=404, detail=detail)
    return HTTPException(status_code=400, detail=detail)


async def _assert_phone_number_scope(request: Request, number_id: str, client_id: Optional[str] = None) -> dict:
    number = await db.get_phone_number(number_id)
    if not number:
        raise HTTPException(status_code=404, detail="Phone number not found")
    requested_client_id = client_id or number.get("client_id")
    if requested_client_id:
        _assert_intelligence_scope(request, requested_client_id, "Phone number")
    if number.get("client_id") and requested_client_id and number["client_id"] != requested_client_id:
        raise HTTPException(status_code=403, detail="Phone number is assigned to another tenant")
    return number


async def _resolve_tenant_phone_route_from_webhook(request: Request, provider: str = "twilio") -> Optional[dict]:
    if not feature_flags.is_enabled("telephony.tenant_numbers"):
        return None

    candidates = [
        request.query_params.get("To"),
        request.query_params.get("Called"),
        request.query_params.get("to"),
        request.query_params.get("phone"),
    ]
    if not any(candidates):
        body = await request.body()
        if body:
            form = parse_qs(body.decode("utf-8", errors="ignore"))
            candidates.extend([
                (form.get("To") or [None])[0],
                (form.get("Called") or [None])[0],
                (form.get("to") or [None])[0],
                (form.get("phone") or [None])[0],
            ])

    phone = next((str(value).strip() for value in candidates if value and str(value).strip()), None)
    if not phone:
        logger.warning("telephony tenant routing enabled but webhook did not include a destination number")
        return None

    route = await db.resolve_phone_number_route(phone, provider)
    if not route:
        raise HTTPException(status_code=403, detail="Phone number is not routed to a tenant")
    await db.append_tenant_audit_event(
        client_id=route.get("client_id"),
        action="telephony.webhook_route_resolved",
        resource_type="phone_number",
        resource_id=route.get("number_id") or phone,
        metadata={"phone": phone, "provider": provider, "routing_mode": route.get("routing_mode")},
    )
    return route


@app.get("/api/tenant/enforcement-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_enforcement_readiness(
    request: Request,
    path: str = "/api/campaigns",
):
    _require_tenant_enforcement_readiness_enabled()
    context = _tenant_context_from_request(request)
    manifest = build_tenant_enforcement_readiness(context, path=path)
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "db_query_executed": False,
        "tenant_data_returned": False,
        "readiness": manifest,
    }


@app.get("/api/tenant/scoped-read-policy", dependencies=[Depends(require_auth)])
async def get_tenant_scoped_read_policy(request: Request):
    _require_tenant_scoped_read_policy_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant scoped-read policy requires admin context")
    manifest = build_tenant_scoped_read_policy_manifest(context)
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "db_query_executed": False,
        "db_write_performed": False,
        "tenant_data_returned": False,
        "policy": manifest,
    }


@app.get("/api/tenant/scoped-read-canary", dependencies=[Depends(require_auth)])
async def get_tenant_scoped_read_canary(
    request: Request,
    resourceType: str,
    resourceId: str,
    clientId: Optional[str] = None,
):
    _require_tenant_scoped_read_canary_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant scoped-read canary requires admin context")
    try:
        owner = await db.get_tenant_scoped_resource_owner(resourceType, resourceId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    manifest = build_tenant_scoped_read_canary(
        context,
        resource_type=owner["resource_type"],
        resource_label=owner["resource_label"],
        resource_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "tenant_data_returned": False,
        "scoped_read_canary": manifest,
    }


@app.get("/api/tenant/leak-regression-matrix", dependencies=[Depends(require_auth)])
async def get_tenant_leak_regression_matrix(
    request: Request,
    leadId: Optional[str] = None,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_tenant_leak_regression_matrix_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant leak regression matrix requires admin context")

    scenarios: list[dict[str, Any]] = []
    if leadId:
        owner = await db.get_call_result_owner_for_transcript(leadId)
        scenarios.append({
            "resource_type": "transcript",
            "resource_label": "Transcript",
            "resource_found": owner["found"],
            "owner_tenant_id": owner.get("owner_client_id"),
            "requested_tenant_id": clientId or context.requested_tenant_id or context.tenant_id,
        })
        scenarios.append({
            "resource_type": "call_result",
            "resource_label": "Call result",
            "resource_found": owner["found"],
            "owner_tenant_id": owner.get("owner_client_id"),
            "requested_tenant_id": clientId or context.requested_tenant_id or context.tenant_id,
        })
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        owner = await db.get_recording_asset_owner(recordingUrl)
        scenarios.append({
            "resource_type": "recording_asset",
            "resource_label": "Recording asset",
            "resource_found": owner["found"],
            "owner_tenant_id": owner.get("owner_client_id"),
            "requested_tenant_id": clientId or context.requested_tenant_id or context.tenant_id,
        })
    if campaignId:
        owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)
        scenarios.append({
            "resource_type": owner["resource_type"],
            "resource_label": owner["resource_label"],
            "resource_found": owner["found"],
            "owner_tenant_id": owner.get("owner_client_id"),
            "requested_tenant_id": clientId or context.requested_tenant_id or context.tenant_id,
        })

    manifest = build_tenant_leak_regression_matrix_manifest(
        context,
        scenarios=scenarios,
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
    )
    logger.info(
        "tenant_leak_regression_matrix ready=%s scenarios=%s leak_detected=%s blockers=%s",
        manifest["decision"]["matrix_ready"],
        manifest["decision"]["scenario_count"],
        manifest["decision"]["cross_tenant_leak_detected"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "tenant_leak_regression_matrix": manifest,
    }


@app.get("/api/tenant/security-leak-audit/readiness", dependencies=[Depends(require_auth)])
async def get_tenant_security_leak_audit_readiness(
    request: Request,
    clientId: Optional[str] = None,
):
    _require_tenant_security_leak_audit_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant security leak audit requires admin context")
    client_id = _resolve_intelligence_client_id(request, clientId) if clientId else None
    readiness = await _build_tenant_security_audit_readiness(client_id=client_id)
    logger.info(
        "tenant_security_leak_audit ready=%s missing_owner_total=%s relationship_mismatch_total=%s blockers=%s",
        readiness["ready_for_production_push"],
        readiness["summary"]["missing_owner_total"],
        readiness["summary"]["relationship_mismatch_total"],
        ",".join(readiness["blockers"]),
    )
    return readiness


@app.get("/api/tenant/final-canary-rollback/readiness", dependencies=[Depends(require_auth)])
async def get_tenant_final_canary_rollback_readiness(
    request: Request,
    clientId: Optional[str] = None,
):
    _require_final_canary_rollback_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant final canary rollback readiness requires admin context")
    client_id = _resolve_intelligence_client_id(request, clientId) if clientId else None
    readiness = await _build_final_canary_rollback_readiness(client_id=client_id)
    logger.info(
        "final_canary_rollback ready=%s campaign_ready=%s tenant_security_ready=%s blockers=%s",
        readiness["ready_for_production_push"],
        readiness["summary"]["campaign_ready"],
        readiness["summary"]["tenant_security_ready"],
        ",".join(readiness["blockers"]),
    )
    return readiness


@app.get("/api/tenant/recording-access-canary", dependencies=[Depends(require_auth)])
async def get_tenant_recording_access_canary(
    request: Request,
    recordingUrl: str,
    clientId: Optional[str] = None,
):
    _require_recording_owner_lookup_shadow_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant recording-access canary requires admin context")
    if not str(recordingUrl or "").startswith("/recordings/"):
        raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")

    owner = await db.get_recording_asset_owner(recordingUrl)
    manifest = build_recording_owner_lookup_shadow_manifest(
        context,
        recording_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "tenant_data_returned": False,
        "recording_access_canary": manifest,
    }


@app.get("/api/tenant/recording-access-enforcement-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_recording_access_enforcement_readiness(
    request: Request,
    recordingUrl: str,
    clientId: Optional[str] = None,
):
    _require_recording_access_enforcement_shadow_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant recording-access enforcement readiness requires admin context")
    if not str(recordingUrl or "").startswith("/recordings/"):
        raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")

    owner = await db.get_recording_asset_owner(recordingUrl)
    manifest = build_recording_access_enforcement_readiness_manifest(
        context,
        recording_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "recording_response_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "tenant_data_returned": False,
        "recording_access_enforcement": manifest,
    }


@app.get("/api/tenant/recording-access-gate-dry-run", dependencies=[Depends(require_auth)])
async def get_tenant_recording_access_gate_dry_run(
    request: Request,
    recordingUrl: str,
    clientId: Optional[str] = None,
):
    _require_recording_access_gate_dry_run_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant recording-access gate dry run requires admin context")
    if not str(recordingUrl or "").startswith("/recordings/"):
        raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")

    owner = await db.get_recording_asset_owner(recordingUrl)
    manifest = build_recording_access_gate_dry_run_manifest(
        context,
        recording_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "recording_response_changed": False,
        "protected_recording_route_activated": False,
        "db_write_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "tenant_data_returned": False,
        "recording_access_gate_dry_run": manifest,
    }


@app.get("/api/tenant/transcript-access-canary", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_access_canary(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_access_canary_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript-access canary requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_access_canary_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_access_canary": manifest,
    }


@app.get("/api/tenant/transcript-access-enforcement-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_access_enforcement_readiness(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_access_enforcement_shadow_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript-access enforcement readiness requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_access_enforcement_readiness_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_access_enforcement": manifest,
    }


@app.get("/api/tenant/transcript-access-gate-dry-run", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_access_gate_dry_run(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_access_gate_dry_run_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript-access gate dry run requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_access_gate_dry_run_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "db_write_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_access_gate_dry_run": manifest,
    }


@app.get("/api/protected/transcripts/{lead_id}", dependencies=[Depends(require_auth)])
async def get_protected_transcript_contract_stub(
    request: Request,
    lead_id: str,
    clientId: Optional[str] = None,
):
    _require_transcript_protected_route_stub_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_verified:
        raise HTTPException(status_code=403, detail="protected transcript route stub requires verified backend identity")
    if not context.is_admin and not context.tenant_id:
        raise HTTPException(status_code=403, detail="protected transcript route stub requires tenant context")

    owner = await db.get_call_result_owner_for_transcript(lead_id)
    manifest = build_transcript_protected_route_stub_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    permission_shadow = None
    response_shape_canary = None
    payload_dry_run = None
    if feature_flags.is_enabled("transcripts.protected_route_permission_shadow"):
        permission_shadow = build_transcript_protected_route_permission_shadow_manifest(
            context,
            transcript_found=owner["found"],
            owner_tenant_id=owner.get("owner_client_id"),
            requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
            campaign_id_present=owner.get("campaign_id_present", False),
        )
        logger.info(
            "transcript_protected_route_permission_shadow would_allow_payload=%s blockers=%s",
            permission_shadow["permission"]["would_allow_payload_if_enforced"],
            ",".join(permission_shadow["permission"]["blockers"]),
        )
    if feature_flags.is_enabled("transcripts.protected_response_shape_canary"):
        response_shape_canary = build_transcript_protected_response_shape_canary_manifest(
            context,
            transcript_found=owner["found"],
            owner_tenant_id=owner.get("owner_client_id"),
            requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
            campaign_id_present=owner.get("campaign_id_present", False),
        )
        logger.info(
            "transcript_protected_response_shape_canary schema_ready=%s blockers=%s",
            response_shape_canary["decision"]["schema_ready_for_future_payload"],
            ",".join(response_shape_canary["decision"]["blockers"]),
        )
    if feature_flags.is_enabled("transcripts.protected_payload_dry_run"):
        payload_dry_run = build_transcript_protected_payload_dry_run_manifest(
            context,
            transcript_found=owner["found"],
            owner_tenant_id=owner.get("owner_client_id"),
            requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
            campaign_id_present=owner.get("campaign_id_present", False),
        )
        logger.info(
            "transcript_protected_payload_dry_run ready_for_payload_read=%s blockers=%s",
            payload_dry_run["decision"]["ready_for_future_payload_read"],
            ",".join(payload_dry_run["decision"]["blockers"]),
        )
    logger.info(
        "transcript_protected_route_stub ready=%s would_allow=%s blockers=%s",
        manifest["decision"]["contract_route_ready"],
        manifest["decision"]["would_allow_contract_route"],
        ",".join(manifest["decision"]["blockers"]),
    )
    response = {
        "status": "stub",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_protected_route_stub": manifest,
    }
    if permission_shadow is not None:
        response["transcript_protected_route_permission_shadow"] = permission_shadow
    if response_shape_canary is not None:
        response["transcript_protected_response_shape_canary"] = response_shape_canary
    if payload_dry_run is not None:
        response["transcript_protected_payload_dry_run"] = payload_dry_run
    return response


@app.get("/api/tenant/transcript-protected-enforcement-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_protected_enforcement_readiness(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_protected_enforcement_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript protected enforcement readiness requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_protected_enforcement_readiness_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "transcript_protected_enforcement_readiness ready=%s blockers=%s",
        manifest["decision"]["ready_for_future_enforcement_candidate"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "live_payload_route_enabled": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_protected_enforcement_readiness": manifest,
    }


@app.get("/api/tenant/transcript-protected-live-activation-plan", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_protected_live_activation_plan(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_protected_live_activation_plan_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript protected live activation plan requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_protected_live_activation_plan_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "transcript_protected_live_activation_plan ready=%s blockers=%s",
        manifest["decision"]["activation_plan_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "planned",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "live_payload_route_enabled": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_protected_live_activation_plan": manifest,
    }


@app.get("/api/tenant/transcript-protected-rollback-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_protected_rollback_readiness(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_protected_rollback_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript protected rollback readiness requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_protected_rollback_readiness_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "transcript_protected_rollback_readiness ready=%s blockers=%s",
        manifest["decision"]["rollback_ready_for_future_live_activation"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "live_payload_route_enabled": False,
        "rollback_action_performed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_protected_rollback_readiness": manifest,
    }


@app.get("/api/tenant/transcript-frontend-migration-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_transcript_frontend_migration_readiness(
    request: Request,
    leadId: str,
    clientId: Optional[str] = None,
):
    _require_transcript_frontend_migration_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant transcript frontend migration readiness requires admin context")

    owner = await db.get_call_result_owner_for_transcript(leadId)
    manifest = build_transcript_frontend_migration_readiness_manifest(
        context,
        transcript_found=owner["found"],
        owner_tenant_id=owner.get("owner_client_id"),
        requested_tenant_id=clientId or context.requested_tenant_id or context.tenant_id,
        campaign_id_present=owner.get("campaign_id_present", False),
    )
    logger.info(
        "transcript_frontend_migration_readiness ready=%s blockers=%s",
        manifest["decision"]["frontend_migration_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "transcript_response_changed": False,
        "protected_transcript_route_activated": False,
        "frontend_code_changed": False,
        "live_payload_route_enabled": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "transcript_frontend_migration_readiness": manifest,
    }


@app.get("/api/tenant/result-asset-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_result_asset_readiness(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_result_asset_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant result asset readiness requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_result_asset_readiness_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "result_asset_readiness ready=%s transcript_ready=%s recording_ready=%s leak_ready=%s blockers=%s",
        manifest["decision"]["result_asset_readiness_ready"],
        manifest["assets"]["transcript_ready"],
        manifest["assets"]["recording_ready"],
        manifest["assets"]["leak_matrix_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "result_asset_readiness": manifest,
    }


@app.get("/api/tenant/final-rollout-report", dependencies=[Depends(require_auth)])
async def get_tenant_final_rollout_report(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_final_rollout_report_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant final rollout report requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_final_rollout_report_readiness_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "final_rollout_report ready=%s result_assets_ready=%s blockers=%s",
        manifest["decision"]["final_rollout_report_ready"],
        manifest["components"]["result_asset_readiness_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "final_rollout_report": manifest,
    }


@app.get("/api/tenant/rollout-approval-packet", dependencies=[Depends(require_auth)])
async def get_tenant_rollout_approval_packet(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_rollout_approval_packet_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant rollout approval packet requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_rollout_approval_packet_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "rollout_approval_packet ready=%s final_report_ready=%s blockers=%s",
        manifest["decision"]["rollout_approval_packet_ready"],
        manifest["components"]["final_rollout_report_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "rollout_approval_packet": manifest,
    }


@app.get("/api/tenant/rollout-canary-plan", dependencies=[Depends(require_auth)])
async def get_tenant_rollout_canary_plan(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_rollout_canary_plan_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant rollout canary plan requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_rollout_canary_plan_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "rollout_canary_plan ready=%s approval_ready=%s blockers=%s",
        manifest["decision"]["rollout_canary_plan_ready"],
        manifest["components"]["approval_packet_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "rollout_canary_plan": manifest,
    }


@app.get("/api/tenant/rollback-drill-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_rollback_drill_readiness(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_rollback_drill_readiness_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant rollback drill readiness requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_rollback_drill_readiness_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "rollback_drill_readiness ready=%s canary_ready=%s blockers=%s",
        manifest["decision"]["rollback_drill_readiness_ready"],
        manifest["components"]["canary_plan_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "rollback_action_performed": False,
        "routes_modified": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "rollback_drill_readiness": manifest,
    }


@app.get("/api/tenant/rollout-evidence-bundle", dependencies=[Depends(require_auth)])
async def get_tenant_rollout_evidence_bundle(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_rollout_evidence_bundle_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant rollout evidence bundle requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_rollout_evidence_bundle_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "rollout_evidence_bundle ready=%s rollback_ready=%s blockers=%s",
        manifest["decision"]["rollout_evidence_bundle_ready"],
        manifest["components"]["rollback_drill_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "rollback_action_performed": False,
        "routes_modified": False,
        "evidence_record_created": False,
        "live_data_collected": False,
        "metrics_sampled": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "rollout_evidence_bundle": manifest,
    }


@app.get("/api/tenant/canary-observation-checklist", dependencies=[Depends(require_auth)])
async def get_tenant_canary_observation_checklist(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_canary_observation_checklist_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant canary observation checklist requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_canary_observation_checklist_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "canary_observation_checklist ready=%s evidence_ready=%s blockers=%s",
        manifest["decision"]["canary_observation_checklist_ready"],
        manifest["components"]["evidence_bundle_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "rollback_action_performed": False,
        "routes_modified": False,
        "evidence_record_created": False,
        "observation_record_created": False,
        "live_data_collected": False,
        "metrics_sampled": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "canary_observation_checklist": manifest,
    }


@app.get("/api/tenant/production-go-no-go-gate", dependencies=[Depends(require_auth)])
async def get_tenant_production_go_no_go_gate(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_go_no_go_gate_enabled()
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail="tenant production go/no-go gate requires admin context")

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = build_production_go_no_go_gate_manifest(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "production_go_no_go_gate ready=%s observation_ready=%s blockers=%s",
        manifest["decision"]["production_go_no_go_gate_ready"],
        manifest["components"]["canary_observation_checklist_ready"],
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "rollback_action_performed": False,
        "routes_modified": False,
        "evidence_record_created": False,
        "observation_record_created": False,
        "decision_record_created": False,
        "production_activation_started": False,
        "live_data_collected": False,
        "metrics_sampled": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        "production_go_no_go_gate": manifest,
    }


async def _build_production_activation_admin_report(
    request: Request,
    leadId: str,
    *,
    recordingUrl: Optional[str],
    campaignId: Optional[str],
    clientId: Optional[str],
    builder: Any,
    manifest_key: str,
    log_name: str,
    ready_decision_key: str,
    component_ready_key: str,
    admin_detail: str,
):
    context = _tenant_context_from_request(request)
    if not context or not context.is_admin:
        raise HTTPException(status_code=403, detail=admin_detail)

    requested_scope = clientId or context.requested_tenant_id or context.tenant_id
    transcript_owner = await db.get_call_result_owner_for_transcript(leadId)
    recording_owner: dict[str, Any] = {"found": False, "owner_client_id": None, "campaign_id_present": False}
    if recordingUrl:
        if not str(recordingUrl or "").startswith("/recordings/"):
            raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
        recording_owner = await db.get_recording_asset_owner(recordingUrl)
    campaign_owner: dict[str, Any] = {"found": False, "owner_client_id": None}
    if campaignId:
        campaign_owner = await db.get_tenant_scoped_resource_owner("campaign", campaignId)

    manifest = builder(
        context,
        transcript_found=transcript_owner["found"],
        transcript_owner_tenant_id=transcript_owner.get("owner_client_id"),
        recording_found=recording_owner["found"],
        recording_owner_tenant_id=recording_owner.get("owner_client_id"),
        campaign_found=campaign_owner["found"],
        campaign_owner_tenant_id=campaign_owner.get("owner_client_id"),
        requested_tenant_id=requested_scope,
        transcript_campaign_id_present=transcript_owner.get("campaign_id_present", False),
        recording_campaign_id_present=recording_owner.get("campaign_id_present", False),
        recording_required=bool(recordingUrl),
        campaign_required=bool(campaignId),
    )
    logger.info(
        "%s ready=%s upstream_ready=%s blockers=%s",
        log_name,
        manifest["decision"].get(ready_decision_key),
        manifest["components"].get(component_ready_key),
        ",".join(manifest["decision"]["blockers"]),
    )
    return {
        "status": "ready",
        "runtime_enforcement_changed": False,
        "audio_runtime_changed": False,
        "websocket_contract_changed": False,
        "campaign_runtime_changed": False,
        "results_endpoint_changed": False,
        "transcript_response_changed": False,
        "recording_response_changed": False,
        "static_file_serving_changed": False,
        "recording_playback_changed": False,
        "approval_state_changed": False,
        "feature_flags_modified": False,
        "canary_started": False,
        "traffic_shifted": False,
        "rollback_action_performed": False,
        "routes_modified": False,
        "evidence_record_created": False,
        "observation_record_created": False,
        "decision_record_created": False,
        "activation_request_recorded": False,
        "activation_executed": False,
        "rollback_token_issued": False,
        "handoff_record_created": False,
        "production_activation_started": False,
        "live_activation_performed": False,
        "live_data_collected": False,
        "metrics_sampled": False,
        "protected_transcript_route_activated": False,
        "protected_recording_route_activated": False,
        "live_payload_route_enabled": False,
        "frontend_code_changed": False,
        "db_write_performed": False,
        "db_payload_read_performed": False,
        "file_bytes_read": False,
        "resource_payload_returned": False,
        "lead_id_returned": False,
        "call_result_id_returned": False,
        "campaign_id_returned": False,
        "recording_url_returned": False,
        "recording_bytes_returned": False,
        "transcript_content_returned": False,
        "transcript_turn_count_returned": False,
        "tenant_data_returned": False,
        "cross_tenant_data_returned": False,
        manifest_key: manifest,
    }


@app.get("/api/tenant/production-activation-contract-stub", dependencies=[Depends(require_auth)])
async def get_tenant_production_activation_contract_stub(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_activation_contract_stub_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_production_activation_contract_stub_manifest,
        manifest_key="production_activation_contract_stub",
        log_name="production_activation_contract_stub",
        ready_decision_key="production_activation_contract_stub_ready",
        component_ready_key="production_go_no_go_gate_ready",
        admin_detail="tenant production activation contract stub requires admin context",
    )


@app.get("/api/tenant/production-activation-permission-shadow", dependencies=[Depends(require_auth)])
async def get_tenant_production_activation_permission_shadow(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_activation_permission_shadow_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_production_activation_permission_shadow_manifest,
        manifest_key="production_activation_permission_shadow",
        log_name="production_activation_permission_shadow",
        ready_decision_key="production_activation_permission_shadow_ready",
        component_ready_key="activation_contract_ready",
        admin_detail="tenant production activation permission shadow requires admin context",
    )


@app.get("/api/tenant/production-activation-payload-dry-run", dependencies=[Depends(require_auth)])
async def get_tenant_production_activation_payload_dry_run(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_activation_payload_dry_run_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_production_activation_payload_dry_run_manifest,
        manifest_key="production_activation_payload_dry_run",
        log_name="production_activation_payload_dry_run",
        ready_decision_key="production_activation_payload_dry_run_ready",
        component_ready_key="permission_shadow_ready",
        admin_detail="tenant production activation payload dry-run requires admin context",
    )


@app.get("/api/tenant/production-activation-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_production_activation_readiness(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_activation_readiness_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_production_activation_readiness_manifest,
        manifest_key="production_activation_readiness",
        log_name="production_activation_readiness",
        ready_decision_key="production_activation_readiness_ready",
        component_ready_key="payload_dry_run_ready",
        admin_detail="tenant production activation readiness requires admin context",
    )


@app.get("/api/tenant/production-activation-rollback-confirmation", dependencies=[Depends(require_auth)])
async def get_tenant_production_activation_rollback_confirmation(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_production_activation_rollback_confirmation_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_production_activation_rollback_confirmation_manifest,
        manifest_key="production_activation_rollback_confirmation",
        log_name="production_activation_rollback_confirmation",
        ready_decision_key="production_activation_rollback_confirmation_ready",
        component_ready_key="activation_readiness_ready",
        admin_detail="tenant production activation rollback confirmation requires admin context",
    )


@app.get("/api/tenant/controlled-handoff-readiness", dependencies=[Depends(require_auth)])
async def get_tenant_controlled_handoff_readiness(
    request: Request,
    leadId: str,
    recordingUrl: Optional[str] = None,
    campaignId: Optional[str] = None,
    clientId: Optional[str] = None,
):
    _require_controlled_handoff_readiness_enabled()
    return await _build_production_activation_admin_report(
        request,
        leadId,
        recordingUrl=recordingUrl,
        campaignId=campaignId,
        clientId=clientId,
        builder=build_controlled_handoff_readiness_manifest,
        manifest_key="controlled_handoff_readiness",
        log_name="controlled_handoff_readiness",
        ready_decision_key="controlled_handoff_readiness_ready",
        component_ready_key="rollback_confirmation_ready",
        admin_detail="tenant controlled handoff readiness requires admin context",
    )


@app.get("/api/agents")
async def list_agents(client_id: Optional[str] = None, user_email: Optional[str] = None):
    if user_email:
        client = await db.get_client_by_email(user_email)
        if not client:
            return []
        return await db.list_agents(client["id"])
    return await db.list_agents(client_id)

@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@app.get("/api/agents/{agent_id}/runtime-schema", dependencies=[Depends(require_auth)])
async def get_agent_runtime_schema(agent_id: str, request: Request):
    """Return the exact conversation JSON (nodes, global_prompt, voice...) the
    live agent runs on, so the client Agent Studio can view/edit it."""
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    schema_path = _agent_schema_path(agent_id, agent.get("schema_path"))
    if not os.path.isfile(schema_path):
        raise HTTPException(status_code=404, detail="Agent schema file not found")
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read agent schema: {exc}")
    return {"schema": schema, "agent": agent}
 


@app.get("/api/clients/resolve")
async def resolve_client_by_email(email: str):
    client = await db.get_client_by_email(email)
    if not client:
        return {"client": None, "assignment": None, "agents": []}

    agents = await db.list_agents(client["id"])
    assigned_agent_id = await db.get_assignment(client["id"])
    assigned_agent = next((agent for agent in agents if agent.get("id") == assigned_agent_id), None)
    return {
        "client": client,
        "assignment": {
            "agentId": assigned_agent_id,
            "agent": assigned_agent,
        } if assigned_agent_id else None,
        "agents": agents,
    }

@app.get("/api/registry/domains")
async def list_registry_domains():
    registry_path = os.path.join(DB_DIR, "domain_registry.json")
    if not os.path.exists(registry_path):
        raise HTTPException(status_code=404, detail="Domain registry not found")
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load domain registry: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load domain registry")

@app.post("/api/demo-requests")
async def create_demo_request(req: DemoRequestCreate):
    try:
        created = await db.create_demo_request(req.dict())
        return created
    except Exception as e:
        logger.error("Failed to create demo request: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save demo request")

@app.get("/api/demo-requests", dependencies=[Depends(require_auth)])
async def list_demo_requests(status: Optional[str] = None):
    try:
        requests = await db.list_demo_requests(status)
        return requests
    except Exception as e:
        logger.error("Failed to list demo requests: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve demo requests")

@app.put("/api/demo-requests/{request_id}", dependencies=[Depends(require_auth)])
async def update_demo_request_status(request_id: str, req: DemoRequestUpdateStatus):
    try:
        success = await db.update_demo_request_status(request_id, req.status)
        if not success:
            raise HTTPException(status_code=404, detail="Demo request not found")
        return {"status": "success", "message": f"Demo request status updated to {req.status}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update demo request: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update demo request status")


@app.post("/api/agents", dependencies=[Depends(require_auth)])
async def create_agent(agent: AgentCreate):
    agent_id = str(uuid.uuid4())
    data = _normalize_agent_record(agent.dict())
    voice_id = _voice_id_for_agent(data["voice"])
    assigned_email = data["assigned_email"]
    assigned_client = None
    if assigned_email:
        assigned_client = await db.ensure_client_for_email(assigned_email)

    schema_path = os.path.join(AGENTS_DIR, f"{agent_id}.json")
    await _write_agent_runtime_schema(agent_id, schema_path, data, voice_id, assigned_client)
    flow_v2_shadow = _write_agent_flow_v2_shadow(agent_id, schema_path, data, assigned_client)
    data.update({
        "client_id": assigned_client.get("id") if assigned_client else None,
        "schema_path": schema_path,
        "created_at": datetime.now().isoformat(),
    })
    created = await db.create_agent(agent_id, data)
    if flow_v2_shadow:
        await db.create_agent_flow_version(
            agent_id,
            client_id=flow_v2_shadow["client_id"],
            schema_version="2.0",
            status="draft",
            runtime_mode="shadow",
            artifact_path=flow_v2_shadow["artifact_path"],
            validation=flow_v2_shadow["validation"],
        )
    if assigned_client:
        await db.set_assignment(assigned_client["id"], agent_id)
    return created


@app.post("/api/agents/generate-flow", dependencies=[Depends(require_auth)])
async def generate_agent_flow_endpoint(request: AgentFlowGenerateRequest):
    """
    Generate a full conversation-flow JSON from a plain-language prompt —
    the "Create Agent from prompt" feature. This does NOT create an agent;
    it returns a draft flow for the caller to preview before submitting
    it to POST /api/agents as `custom_json`.
    """
    from llm.agent_flow_generator import generate_agent_flow, FlowGenerationError

    if not request.prompt or not request.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")

    try:
        flow_json, warnings = await generate_agent_flow(
            prompt=request.prompt,
            agent_name=request.agent_name,
            agent_type=request.agent_type,
            data_fields=request.data_fields,
        )
    except FlowGenerationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not generate a valid flow: {exc}",
        )

    return {
        "flow": flow_json,
        "warnings": warnings,
        "node_count": len(
            flow_json.get("conversationFlow", {}).get("nodes", [])
        ),
    }


@app.put("/api/agents/{agent_id}", dependencies=[Depends(require_auth)])
async def update_agent(agent_id: str, agent: AgentUpdate):
    existing = await db.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")

    patch = agent.dict(exclude_unset=True)
    merged = _normalize_agent_record({**existing, **patch})
    voice_id = _voice_id_for_agent(merged["voice"])
    previous_client_id = existing.get("client_id")

    assigned_client = None
    if merged["assigned_email"]:
        assigned_client = await db.ensure_client_for_email(merged["assigned_email"])

    schema_path = _agent_schema_path(agent_id, existing.get("schema_path"))
    await _write_agent_runtime_schema(agent_id, schema_path, merged, voice_id, assigned_client)
    flow_v2_shadow = _write_agent_flow_v2_shadow(agent_id, schema_path, merged, assigned_client)
    merged.update({
        "client_id": assigned_client.get("id") if assigned_client else None,
        "schema_path": schema_path,
    })

    updated = await db.update_agent(agent_id, merged)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    if flow_v2_shadow:
        await db.create_agent_flow_version(
            agent_id,
            client_id=flow_v2_shadow["client_id"],
            schema_version="2.0",
            status="draft",
            runtime_mode="shadow",
            artifact_path=flow_v2_shadow["artifact_path"],
            validation=flow_v2_shadow["validation"],
        )

    new_client_id = assigned_client.get("id") if assigned_client else None
    if previous_client_id and previous_client_id != new_client_id:
        await db.clear_assignment(previous_client_id, agent_id)
    if new_client_id:
        await db.set_assignment(new_client_id, agent_id)

    return updated

@app.post("/api/agents/{agent_id}/qa-test", dependencies=[Depends(require_auth)])
async def run_qa_test(agent_id: str):
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
        
    simulator = QASimulator(db)
    schema_path = agent.get("schema_path") or os.path.join(AGENTS_DIR, f"{agent_id}.json")
    report = await simulator.run_full_qa_suite(agent_id, schema_path)
    return report

@app.post("/api/agents/{agent_id}/stress-test", dependencies=[Depends(require_auth)])
async def run_stress_test(agent_id: str, background_tasks: BackgroundTasks):
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
        
    simulator = QASimulator(db)
    schema_path = agent.get("schema_path") or os.path.join(AGENTS_DIR, f"{agent_id}.json")
    
    background_tasks.add_task(simulator.run_stress_test, agent_id, schema_path, 100)
    return {"status": "accepted", "message": "Stress test queued for execution in background."}

@app.put("/api/agents/{agent_id}/certify", dependencies=[Depends(require_auth)])
async def certify_agent(agent_id: str):
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
        
    if agent.get("qa_score", 0) < 95:
        raise HTTPException(status_code=400, detail="Agent must have a QA score >= 95 to be Certified.")
        
    agent["certification_status"] = "Certified"
    updated = await db.update_agent(agent_id, agent)
    return updated

@app.get("/api/agents/{agent_id}/flow-preview", dependencies=[Depends(require_auth)])
async def get_agent_flow_preview(agent_id: str, request: Request):
    if not feature_flags.is_enabled("flow.visualization"):
        raise HTTPException(status_code=403, detail="flow.visualization is disabled")
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    _shadow_tenant_scoped_read(request, "agent", agent.get("client_id"))
    return await _load_agent_flow_preview(agent)

@app.put("/api/agents/{agent_id}/flow-v2-draft", dependencies=[Depends(require_auth)])
async def update_agent_flow_v2_draft(agent_id: str, data: FlowDraftUpdate, request: Request):
    if not feature_flags.is_enabled("flow.visualization"):
        raise HTTPException(status_code=403, detail="flow.visualization is disabled")
    if not feature_flags.is_enabled("flow.v2_shadow"):
        raise HTTPException(status_code=403, detail="flow.v2_shadow is disabled")
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    _shadow_tenant_scoped_read(request, "agent", agent.get("client_id"))

    flow, _source = await _load_agent_flow_v2_spec(agent)
    try:
        draft = _apply_flow_v2_draft_updates(flow, data)
    except FlowSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_live_changed = False
    publish_result = None
    if feature_flags.is_enabled("flow.v2_live"):
        publish_result = _publish_flow_v2_to_runtime(agent, draft, actor=_actor_email(request))
        artifact_path = publish_result["artifact_path"]
        version_status = "published"
        runtime_mode = "live"
        runtime_live_changed = True
    else:
        artifact_path = _write_flow_v2_draft_artifact(agent, draft)
        version_status = "draft"
        runtime_mode = "shadow"

    flow_version = await db.create_agent_flow_version(
        agent_id,
        client_id=agent.get("client_id"),
        schema_version="2.0",
        status=version_status,
        runtime_mode=runtime_mode,
        artifact_path=artifact_path,
        validation=draft.get("validation", {}),
    )
    logger.info(
        "flow_v2_draft_updated agent=%s nodes=%d runtime_live_changed=%s",
        agent_id,
        len(draft.get("nodes") or []),
        runtime_live_changed,
    )
    preview = await _load_agent_flow_preview(agent)
    preview["publish"] = {
        "flow_version_id": flow_version.get("id"),
        "runtime_live_changed": runtime_live_changed,
        "published_live": runtime_live_changed,
        "runtime_schema_path": publish_result.get("runtime_schema_path") if publish_result else None,
        "rollback_backup_path": publish_result.get("backup_path") if publish_result else None,
        "disable_live_flag": feature_flags.env_name("flow.v2_live"),
    }
    return preview

async def _append_scrape_job_event_if_enabled(
    job_id: str,
    event_type: str,
    *,
    status: Optional[str] = None,
    actor: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    if not job_id:
        return
    if not feature_flags.is_enabled("scrape.job_events"):
        return
    try:
        await db.append_scrape_job_event(
            job_id,
            event_type,
            status=status,
            actor=actor,
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.warning(
            "scrape_job_event_append_failed job=%s event=%s error_type=%s",
            job_id,
            event_type,
            type(exc).__name__,
        )

@app.get("/api/intelligence/readiness", dependencies=[Depends(require_auth)])
async def get_website_intelligence_readiness(request: Request):
    context = _tenant_context_from_request(request)
    if context and context.tenant_id and not context.is_admin:
        logger.info(
            "[INTEL] readiness requested with tenant hint=%s auth_state=%s",
            context.tenant_id,
            context.auth_state,
        )
    return _build_website_intelligence_readiness()

@app.get("/api/intelligence/live-qa/readiness", dependencies=[Depends(require_auth)])
async def get_website_live_qa_readiness(request: Request, clientId: Optional[str] = None):
    if not feature_flags.is_enabled("scrape.live_qa_readiness"):
        raise HTTPException(status_code=403, detail="scrape.live_qa_readiness is disabled")
    client_id = _resolve_intelligence_client_id(request, clientId)
    return await _build_website_live_qa_readiness(client_id=client_id)

@app.get("/api/intelligence/generated-draft-qa/readiness", dependencies=[Depends(require_auth)])
async def get_generated_draft_qa_readiness(request: Request, clientId: Optional[str] = None):
    if not feature_flags.is_enabled("scrape.generated_draft_qa_readiness"):
        raise HTTPException(status_code=403, detail="scrape.generated_draft_qa_readiness is disabled")
    client_id = _resolve_intelligence_client_id(request, clientId)
    return await _build_generated_draft_qa_readiness(client_id=client_id)

@app.post("/api/intelligence/scrape-jobs", dependencies=[Depends(require_auth)])
async def create_scrape_job(data: WebsiteScrapeStart, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    client_id = _resolve_intelligence_client_id(request, data.clientId)
    if data.agentId:
        agent = await db.get_agent(data.agentId)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
        if client_id and agent.get("client_id") and client_id != agent["client_id"]:
            raise HTTPException(status_code=403, detail="Agent is outside tenant scope")
        client_id = client_id or agent.get("client_id")
    if client_id and not await db.get_client(client_id):
        raise HTTPException(
            status_code=400,
            detail=f"Client '{client_id}' does not exist. Create the client before generating website drafts.",
        )
    pipeline = WebsiteIntelligencePipeline(db)
    try:
        job = await pipeline.create_job(
            client_id=client_id,
            agent_id=data.agentId,
            url=data.url,
            requested_by=data.requestedBy,
            reuse_existing=data.reuseExisting,
        )
        await _append_scrape_job_event_if_enabled(
            job["id"],
            "job_reused" if job.get("cache", {}).get("reused") else "job_created",
            status=job.get("status"),
            actor=data.requestedBy,
            metadata={
                "client_id": client_id,
                "agent_id": data.agentId,
                "domain": job.get("domain"),
                "reused": bool(job.get("cache", {}).get("reused")),
            },
        )
        return job
    except URLSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/api/intelligence/scrape-jobs", dependencies=[Depends(require_auth)])
async def list_scrape_jobs(
    request: Request,
    clientId: Optional[str] = None,
    agentId: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    context = _tenant_context_from_request(request)
    client_id = _resolve_intelligence_client_id(request, clientId)
    if agentId:
        agent = await db.get_agent(agentId)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
        if client_id and agent.get("client_id") and client_id != agent["client_id"]:
            raise HTTPException(status_code=403, detail="Agent is outside tenant scope")
        client_id = client_id or agent.get("client_id")
    if not (context and context.is_admin) and not client_id:
        raise HTTPException(status_code=403, detail="Tenant context required")
    jobs = await db.list_scrape_jobs(
        client_id=client_id,
        agent_id=agentId,
        status=status,
        limit=limit,
    )
    for job in jobs:
        _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    logger.info(
        "[INTEL] listed scrape jobs count=%d client=%s status=%s",
        len(jobs),
        client_id or "all",
        status or "any",
    )
    return {"items": jobs, "clientId": client_id, "limit": max(1, min(int(limit or 50), 200))}

@app.get("/api/intelligence/scrape-jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_scrape_job(job_id: str, request: Request):
    job = await db.get_scrape_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    _shadow_tenant_scoped_read(request, "scrape_job", job.get("client_id"))
    return job

@app.get("/api/intelligence/scrape-jobs/{job_id}/diagnostics", dependencies=[Depends(require_auth)])
async def get_scrape_job_diagnostics(job_id: str, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    diagnostics = await db.get_scrape_job_diagnostics(job_id)
    if not diagnostics:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, diagnostics.get("client_id"), "Scrape job")
    _shadow_tenant_scoped_read(request, "scrape_job", diagnostics.get("client_id"))
    return diagnostics

async def _run_scrape_job_background(job_id: str, industry_hint: Optional[str] = None) -> None:
    pipeline = WebsiteIntelligencePipeline(db)
    await _append_scrape_job_event_if_enabled(
        job_id,
        "worker_started",
        status="running",
        metadata={"industry_hint": industry_hint},
    )
    try:
        result = await pipeline.run_job(job_id=job_id, industry_hint=industry_hint)
        await _append_scrape_job_event_if_enabled(
            job_id,
            "worker_finished",
            status=result.get("status"),
            metadata={
                "pages_crawled": result.get("pages_crawled"),
                "extraction_id": result.get("extraction_id"),
                "skipped": bool(result.get("skipped")),
                "cancelled": bool(result.get("cancelled")),
            },
        )
    except Exception as exc:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "worker_failed",
            status="failed",
            metadata={"error_type": type(exc).__name__},
        )
        logger.warning(
            "scrape_worker_v1_background_failed job=%s error_type=%s",
            job_id,
            type(exc).__name__,
        )

@app.post("/api/intelligence/scrape-jobs/{job_id}/dispatch", dependencies=[Depends(require_auth)])
async def dispatch_scrape_job(
    job_id: str,
    data: WebsiteScrapeDispatch,
    request: Request,
    background_tasks: BackgroundTasks,
):
    if not feature_flags.is_enabled("scrape.worker_v1"):
        raise HTTPException(status_code=403, detail="scrape.worker_v1 is disabled")
    job = await db.get_scrape_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    if job.get("status") == "dispatching":
        await _append_scrape_job_event_if_enabled(
            job_id,
            "dispatch_skipped",
            status="already_queued",
            actor=data.requestedBy,
            metadata={"previous_status": job.get("status")},
        )
        return {"status": "already_queued", "job_id": job_id}
    if job.get("status") == "running":
        await _append_scrape_job_event_if_enabled(
            job_id,
            "dispatch_skipped",
            status="already_running",
            actor=data.requestedBy,
            metadata={"previous_status": job.get("status")},
        )
        return {"status": "already_running", "job_id": job_id}
    if job.get("status") in {"completed", "draft_ready", "cancelled"}:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "dispatch_skipped",
            status=job.get("status"),
            actor=data.requestedBy,
            metadata={"previous_status": job.get("status")},
        )
        return {"status": job.get("status"), "job_id": job_id}
    queued = await db.queue_scrape_job_for_dispatch(job_id)
    if not queued or not queued.get("_dispatch_enqueued"):
        await _append_scrape_job_event_if_enabled(
            job_id,
            "dispatch_skipped",
            status=queued.get("status") if queued else "not_found",
            actor=data.requestedBy,
            metadata={"reason": "state_changed_before_dispatch"},
        )
        return {"status": queued.get("status") if queued else "not_found", "job_id": job_id}
    background_tasks.add_task(_run_scrape_job_background, job_id, data.industryHint)
    await _append_scrape_job_event_if_enabled(
        job_id,
        "dispatch_accepted",
        status="dispatching",
        actor=data.requestedBy,
        metadata={"industry_hint": data.industryHint},
    )
    logger.info(
        "[INTEL] dispatched scrape job=%s requested_by=%s",
        job_id,
        data.requestedBy or "unknown",
    )
    return {"status": "accepted", "job_id": job_id, "mode": "background"}

@app.post("/api/intelligence/scrape-jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_scrape_job(job_id: str, data: WebsiteScrapeCancel, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    if not feature_flags.is_enabled("scrape.job_cancel"):
        raise HTTPException(status_code=403, detail="scrape.job_cancel is disabled")
    job = await db.get_scrape_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    if job.get("status") in {"completed", "draft_ready", "failed", "cancelled"}:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "cancel_skipped",
            status=job.get("status"),
            actor=data.requestedBy,
            metadata={"previous_status": job.get("status")},
        )
        return {"status": job.get("status"), "job_id": job_id, "job": job, "changed": False}
    updated = await db.cancel_scrape_job(
        job_id,
        reason=data.reason or "cancelled_by_admin",
    )
    await _append_scrape_job_event_if_enabled(
        job_id,
        "job_cancelled",
        status=updated.get("status") if updated else "cancelled",
        actor=data.requestedBy,
        metadata={"previous_status": job.get("status"), "reason": data.reason or "cancelled_by_admin"},
    )
    logger.info(
        "[INTEL] cancelled scrape job=%s requested_by=%s previous_status=%s",
        job_id,
        data.requestedBy or "unknown",
        job.get("status"),
    )
    return {"status": "cancelled", "job_id": job_id, "job": updated, "changed": bool(updated)}

@app.post("/api/intelligence/scrape-jobs/{job_id}/recover-stale", dependencies=[Depends(require_auth)])
async def recover_stale_scrape_job(job_id: str, data: WebsiteScrapeStaleRecovery, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    if not feature_flags.is_enabled("scrape.stale_recovery"):
        raise HTTPException(status_code=403, detail="scrape.stale_recovery is disabled")
    job = await db.get_scrape_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    stale_after_minutes = max(1, min(int(data.staleAfterMinutes or 15), 1440))
    recovered = await db.recover_stale_scrape_job(
        job_id,
        stale_after_minutes=stale_after_minutes,
        reason=data.reason or "stale_worker_recovered",
    )
    changed = bool(recovered and recovered.get("_stale_recovered"))
    await _append_scrape_job_event_if_enabled(
        job_id,
        "stale_recovery" if changed else "stale_recovery_skipped",
        status=recovered.get("status") if recovered else "not_found",
        actor=data.requestedBy,
        metadata={
            "previous_status": job.get("status"),
            "stale_after_minutes": stale_after_minutes,
            "reason": data.reason or "stale_worker_recovered",
        },
    )
    logger.info(
        "[INTEL] stale scrape recovery job=%s changed=%s requested_by=%s previous_status=%s",
        job_id,
        changed,
        data.requestedBy or "unknown",
        job.get("status"),
    )
    return {
        "status": "recovered" if changed else "not_stale",
        "job_id": job_id,
        "job": recovered,
        "changed": changed,
        "stale_after_minutes": stale_after_minutes,
    }

@app.post("/api/intelligence/scrape-jobs/{job_id}/run", dependencies=[Depends(require_auth)])
async def run_scrape_job(job_id: str, request: Request, industryHint: Optional[str] = None):
    if not feature_flags.is_enabled("scrape.worker_v1"):
        raise HTTPException(status_code=403, detail="scrape.worker_v1 is disabled")
    job = await db.get_scrape_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    pipeline = WebsiteIntelligencePipeline(db)
    try:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "manual_run_requested",
            status=job.get("status"),
            metadata={"industry_hint": industryHint},
        )
        result = await pipeline.run_job(job_id=job_id, industry_hint=industryHint)
        await _append_scrape_job_event_if_enabled(
            job_id,
            "manual_run_finished",
            status=result.get("status"),
            metadata={
                "pages_crawled": result.get("pages_crawled"),
                "extraction_id": result.get("extraction_id"),
                "skipped": bool(result.get("skipped")),
                "cancelled": bool(result.get("cancelled")),
            },
        )
        return result
    except CrawlError as exc:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "manual_run_failed",
            status="failed",
            metadata={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        await _append_scrape_job_event_if_enabled(
            job_id,
            "manual_run_failed",
            status="failed",
            metadata={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/intelligence/script-drafts", dependencies=[Depends(require_auth)])
async def list_script_drafts(
    request: Request,
    agentId: str,
    clientId: Optional[str] = None,
    limit: int = 20,
):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    agent = await db.get_agent(agentId)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    requested_client_id = _resolve_intelligence_client_id(request, clientId)
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent", allow_unassigned=True)
    if requested_client_id and agent.get("client_id") and requested_client_id != agent["client_id"]:
        raise HTTPException(status_code=403, detail="Agent is outside tenant scope")
    drafts = await db.list_generated_script_drafts(
        agent_id=agentId,
        client_id=agent.get("client_id") or requested_client_id,
        limit=limit,
    )
    return {"agentId": agentId, "items": drafts}

@app.delete("/api/intelligence/script-drafts/{draft_id}", dependencies=[Depends(require_auth)])
async def delete_script_draft(draft_id: str, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    draft = await db.get_generated_script_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Generated script draft not found")
    _assert_intelligence_scope(request, draft.get("client_id"), "Generated script draft")
    deleted = await db.delete_generated_script_draft(draft_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete generated script draft")
    return {"status": "deleted", "id": draft_id}

@app.post("/api/intelligence/script-drafts", dependencies=[Depends(require_auth)])
async def create_script_draft(data: WebsiteScriptDraftCreate, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    agent = await db.get_agent(data.agentId)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    job = await db.get_scrape_job(data.jobId)
    if not job:
        raise HTTPException(status_code=404, detail="Scrape job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "Scrape job")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    if job.get("agent_id") and job["agent_id"] != data.agentId:
        raise HTTPException(status_code=400, detail="Scrape job does not belong to this agent")
    if job.get("client_id") and agent.get("client_id") and job["client_id"] != agent["client_id"]:
        raise HTTPException(status_code=403, detail="Agent is outside scrape job tenant scope")
    latest_extraction = await db.get_latest_scrape_extraction(data.jobId)
    if (
        feature_flags.is_enabled("scrape.worker_v1")
        and job.get("status") in {"dispatching", "running"}
        and not latest_extraction
    ):
        await _append_scrape_job_event_if_enabled(
            data.jobId,
            "draft_waiting_for_worker",
            status=job.get("status"),
            actor=data.agentName,
            metadata={"agent_id": data.agentId},
        )
        raise HTTPException(
            status_code=409,
            detail="Scrape job is still running. Please wait for completion before creating a draft.",
        )
    existing_draft = await db.get_generated_script_draft_for_job(job_id=data.jobId, agent_id=data.agentId)
    if existing_draft:
        logger.info(
            "[INTEL] reused existing generated draft=%s job=%s agent=%s",
            existing_draft.get("id"),
            data.jobId,
            data.agentId,
        )
        await _append_scrape_job_event_if_enabled(
            data.jobId,
            "draft_reused",
            status=job.get("status"),
            actor=data.agentName,
            metadata={"draft_id": existing_draft.get("id"), "agent_id": data.agentId},
        )
        return existing_draft
    pipeline = WebsiteIntelligencePipeline(db)
    try:
        draft = await pipeline.create_draft_from_job(
            job_id=data.jobId,
            agent=agent,
            industry_hint=data.industryHint,
            use_live_extraction=feature_flags.is_enabled("scrape.worker_v1"),
        )
        await _append_scrape_job_event_if_enabled(
            data.jobId,
            "draft_created",
            status="draft_ready",
            actor=data.agentName,
            metadata={"draft_id": draft.get("id"), "agent_id": data.agentId},
        )
        return draft
    except CrawlError as exc:
        await _append_scrape_job_event_if_enabled(
            data.jobId,
            "draft_failed",
            status="failed",
            actor=data.agentName,
            metadata={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        await _append_scrape_job_event_if_enabled(
            data.jobId,
            "draft_failed",
            status=job.get("status"),
            actor=data.agentName,
            metadata={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.post("/api/intelligence/script-drafts/{draft_id}/preflight-flow-draft", dependencies=[Depends(require_auth)])
async def preflight_script_draft_to_agent_flow(draft_id: str, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    if not feature_flags.is_enabled("flow.visualization"):
        raise HTTPException(status_code=403, detail="flow.visualization is disabled")
    if not feature_flags.is_enabled("flow.v2_shadow"):
        raise HTTPException(status_code=403, detail="flow.v2_shadow is disabled")

    script_draft = await db.get_generated_script_draft(draft_id)
    if not script_draft:
        raise HTTPException(status_code=404, detail="Generated script draft not found")
    agent = await db.get_agent(script_draft.get("agent_id"))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    _assert_intelligence_scope(request, script_draft.get("client_id"), "Generated script draft")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    if script_draft.get("client_id") and agent.get("client_id") and script_draft["client_id"] != agent["client_id"]:
        raise HTTPException(status_code=403, detail="Generated draft is outside agent tenant scope")

    try:
        draft = validate_flow_spec(_prepare_generated_script_flow_for_agent(script_draft, agent))
    except FlowSpecValidationError as exc:
        await _append_scrape_job_event_if_enabled(
            script_draft.get("job_id"),
            "draft_preflight_failed",
            status="invalid",
            metadata={"draft_id": draft_id, "agent_id": agent["id"], "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    preview = build_flow_preview(draft)
    preview["agent"] = {
        "id": agent["id"],
        "name": agent.get("name"),
        "agent_type": agent.get("agent_type"),
        "client_id": agent.get("client_id"),
    }
    preview["source"] = "generated_script_preflight"
    preview["editable_flow"] = _build_editable_flow_payload(draft)
    review_policy = _build_generated_script_review_policy(
        script_draft,
        draft,
        review_acknowledged=False,
    )
    if review_policy["enabled"]:
        preview["review_policy"] = review_policy
    preview["preflight"] = {
        "status": "valid",
        "draft_id": draft_id,
        "can_save_flow_draft": True,
        "would_block_if_enforced": review_policy["would_block_if_enforced"],
        "persisted": False,
        "runtime_live_changed": False,
    }
    if script_draft.get("job_id"):
        await _append_scrape_job_event_if_enabled(
            script_draft["job_id"],
            "draft_preflight_valid",
            status="valid",
            metadata={"draft_id": draft_id, "agent_id": agent["id"]},
        )
    return preview

@app.post("/api/intelligence/script-drafts/{draft_id}/apply-flow-draft", dependencies=[Depends(require_auth)])
async def apply_script_draft_to_agent_flow(draft_id: str, request: Request):
    if not feature_flags.is_enabled("scrape.generate_script"):
        raise HTTPException(status_code=403, detail="scrape.generate_script is disabled")
    if not feature_flags.is_enabled("flow.visualization"):
        raise HTTPException(status_code=403, detail="flow.visualization is disabled")
    if not feature_flags.is_enabled("flow.v2_shadow"):
        raise HTTPException(status_code=403, detail="flow.v2_shadow is disabled")

    script_draft = await db.get_generated_script_draft(draft_id)
    if not script_draft:
        raise HTTPException(status_code=404, detail="Generated script draft not found")
    agent = await db.get_agent(script_draft.get("agent_id"))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    _assert_intelligence_scope(request, script_draft.get("client_id"), "Generated script draft")
    _assert_intelligence_scope(request, agent.get("client_id"), "Agent")
    if script_draft.get("client_id") and agent.get("client_id") and script_draft["client_id"] != agent["client_id"]:
        raise HTTPException(status_code=403, detail="Generated draft is outside agent tenant scope")

    flow = _prepare_generated_script_flow_for_agent(script_draft, agent)
    try:
        draft = validate_flow_spec(flow)
    except FlowSpecValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    apply_payload = {}
    try:
        apply_payload = await request.json()
    except Exception:
        apply_payload = {}
    if not isinstance(apply_payload, dict):
        apply_payload = {}
    actor = _actor_email(request)
    review_notes = str(apply_payload.get("reviewNotes") or "").strip()[:500] or None
    raw_review_acknowledged = apply_payload.get("reviewAcknowledged", True)
    review_acknowledged = (
        raw_review_acknowledged
        if isinstance(raw_review_acknowledged, bool)
        else feature_flags.parse_bool(str(raw_review_acknowledged), default=True)
    )
    review_policy = _build_generated_script_review_policy(
        script_draft,
        draft,
        review_acknowledged=review_acknowledged,
    )
    if feature_flags.is_enabled("flow.v2_live") and review_policy["enabled"] and review_policy["would_block_if_enforced"]:
        raise HTTPException(
            status_code=400,
            detail=f"Generated draft review gate blocked live publish: {', '.join(review_policy['blockers'])}",
        )

    runtime_live_changed = False
    publish_result = None
    if feature_flags.is_enabled("flow.v2_live"):
        publish_result = _publish_flow_v2_to_runtime(agent, draft, actor=actor)
        artifact_path = publish_result["artifact_path"]
        flow_status = "published"
        runtime_mode = "live"
        runtime_live_changed = True
    else:
        artifact_path = _write_flow_v2_draft_artifact(agent, draft)
        flow_status = "draft"
        runtime_mode = "shadow"

    flow_version = await db.create_agent_flow_version(
        agent["id"],
        client_id=agent.get("client_id"),
        schema_version="2.0",
        status=flow_status,
        runtime_mode=runtime_mode,
        artifact_path=artifact_path,
        validation=draft.get("validation", {}),
    )
    reviewed_draft = await db.mark_generated_script_draft_reviewed(
        draft_id,
        status="published_live" if runtime_live_changed else "flow_draft_saved",
        reviewed_by=actor,
        review_notes=review_notes,
        flow_version_id=flow_version.get("id"),
    )
    logger.info(
        "generated_script_draft_applied draft=%s agent=%s flow_version=%s review_acknowledged=%s runtime_live_changed=%s",
        draft_id,
        agent["id"],
        flow_version.get("id"),
        review_acknowledged,
        runtime_live_changed,
    )
    if script_draft.get("job_id"):
        await _append_scrape_job_event_if_enabled(
            script_draft["job_id"],
            "flow_published_live" if runtime_live_changed else "flow_draft_saved",
            status="draft_ready",
            actor=actor,
            metadata={
                "draft_id": draft_id,
                "agent_id": agent["id"],
                "flow_version_id": flow_version.get("id"),
                "review_acknowledged": review_acknowledged,
                "review_gate_shadow": {
                    "enabled": review_policy["enabled"],
                    "would_block_if_enforced": review_policy["would_block_if_enforced"],
                    "blockers": review_policy["blockers"],
                },
                "runtime_live_changed": runtime_live_changed,
                "runtime_schema_path": publish_result.get("runtime_schema_path") if publish_result else None,
                "rollback_backup_path": publish_result.get("backup_path") if publish_result else None,
            },
        )
    preview = await _load_agent_flow_preview(agent)
    preview["generated_script_review"] = {
        "draft_id": draft_id,
        "status": reviewed_draft.get("status"),
        "reviewed_at": reviewed_draft.get("reviewed_at"),
        "reviewed_by": reviewed_draft.get("reviewed_by"),
        "flow_version_id": reviewed_draft.get("flow_version_id"),
        "review_acknowledged": review_acknowledged,
        "review_policy": review_policy if review_policy["enabled"] else None,
        "runtime_live_changed": runtime_live_changed,
        "published_live": runtime_live_changed,
        "runtime_schema_path": publish_result.get("runtime_schema_path") if publish_result else None,
        "rollback_backup_path": publish_result.get("backup_path") if publish_result else None,
        "disable_live_flag": feature_flags.env_name("flow.v2_live"),
    }
    return preview


@app.post("/api/memory/agents/{agent_id}/collections", dependencies=[Depends(require_auth)])
async def create_agent_memory_collection(agent_id: str, data: AgentMemoryCollectionCreate, request: Request):
    _require_memory_enabled()
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    client_id = _resolve_memory_client_id(request, agent, data.clientId)
    service = AgentMemoryService(db)
    try:
        collection = await service.create_collection(
            client_id=client_id,
            agent_id=agent_id,
            source_type=data.sourceType,
            source_id=data.sourceId,
            metadata=data.metadata or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "created", "collection": collection, "runtime_injection": False}


@app.post("/api/memory/agents/{agent_id}/seed", dependencies=[Depends(require_auth)])
async def seed_agent_memory(agent_id: str, data: AgentMemoryCollectionCreate, request: Request):
    _require_memory_enabled()
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    client_id = _resolve_memory_client_id(request, agent, data.clientId)
    service = AgentMemoryService(db)
    try:
        result = await service.seed_from_agent(client_id=client_id, agent=agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "seeded", **result, "runtime_injection": False}


@app.post("/api/memory/agents/{agent_id}/items", dependencies=[Depends(require_auth)])
async def add_agent_memory_item(agent_id: str, data: AgentMemoryItemCreate, request: Request):
    _require_memory_enabled()
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    client_id = _resolve_memory_client_id(request, agent, data.clientId)
    service = AgentMemoryService(db)
    try:
        item = await service.add_item(
            collection_id=data.collectionId,
            client_id=client_id,
            agent_id=agent_id,
            content=data.content,
            metadata=data.metadata or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "created", "item": item, "runtime_injection": False}


@app.get("/api/memory/agents/{agent_id}/items", dependencies=[Depends(require_auth)])
async def list_agent_memory_items(agent_id: str, request: Request, clientId: Optional[str] = None, includeDeleted: bool = False):
    _require_memory_enabled()
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    client_id = _resolve_memory_client_id(request, agent, clientId)
    service = AgentMemoryService(db)
    return {
        "agent_id": agent_id,
        "client_id": client_id,
        "runtime_injection": False,
        "items": await service.list_items(
            client_id=client_id,
            agent_id=agent_id,
            include_deleted=includeDeleted,
        ),
    }


@app.post("/api/memory/agents/{agent_id}/reset", dependencies=[Depends(require_auth)])
async def reset_agent_memory(agent_id: str, data: AgentMemoryResetRequest, request: Request):
    _require_memory_enabled()
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    client_id = _resolve_memory_client_id(request, agent, data.clientId)
    service = AgentMemoryService(db)
    return await service.reset(client_id=client_id, agent_id=agent_id, reason=data.reason)


@app.post("/api/crm/connections", dependencies=[Depends(require_auth)])
async def create_crm_connection(data: CRMConnectionCreate, request: Request):
    _require_crm_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, data.clientId),
        "CRM connection",
    )
    service = CRMIntegrationService(db)
    try:
        connection = await service.create_connection(
            client_id=client_id,
            provider=data.provider,
            display_name=data.displayName,
            external_account_id=data.externalAccountId,
            config=data.config or {},
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "created", "connection": connection, "runtime_sync": False}


@app.get("/api/crm/connections", dependencies=[Depends(require_auth)])
async def list_crm_connections(
    request: Request,
    clientId: Optional[str] = None,
    includeDisabled: bool = False,
):
    _require_crm_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, clientId),
        "CRM connection list",
    )
    service = CRMIntegrationService(db)
    return {
        "client_id": client_id,
        "runtime_sync": False,
        "connections": await service.list_connections(
            client_id=client_id,
            include_disabled=includeDisabled,
        ),
    }


@app.post("/api/crm/connections/{connection_id}/secret-reference", dependencies=[Depends(require_auth)])
async def configure_crm_connection_secret_reference(
    connection_id: str,
    data: CRMSecretReferenceUpdate,
    request: Request,
):
    _require_crm_enabled()
    connection = await db.get_crm_connection(connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="CRM connection not found")
    _assert_intelligence_scope(request, connection.get("client_id"), "CRM connection")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or connection["client_id"]
    if client_id != connection["client_id"]:
        raise HTTPException(status_code=403, detail="CRM connection is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        updated = await service.configure_secret_reference(
            client_id=client_id,
            connection_id=connection_id,
            vault_provider=data.vaultProvider,
            reference_id=data.referenceId,
            rotation_due_at=data.rotationDueAt,
            metadata=data.metadata or {},
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "configured", "connection": updated, "runtime_sync": False}


@app.get("/api/crm/connections/{connection_id}/provider-contract", dependencies=[Depends(require_auth)])
async def get_crm_provider_contract(
    connection_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_provider_contracts_enabled()
    connection = await db.get_crm_connection(connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="CRM connection not found")
    _assert_intelligence_scope(request, connection.get("client_id"), "CRM connection")
    _shadow_tenant_scoped_read(request, "crm_connection", connection.get("client_id"))
    client_id = _resolve_intelligence_client_id(request, clientId) or connection["client_id"]
    if client_id != connection["client_id"]:
        raise HTTPException(status_code=403, detail="CRM connection is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        contract = await service.get_provider_contract(
            client_id=client_id,
            connection_id=connection_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "contract": contract,
    }


@app.post("/api/crm/sync-jobs", dependencies=[Depends(require_auth)])
async def create_crm_sync_job(data: CRMSyncPlanCreate, request: Request):
    _require_crm_enabled()
    client_id = _resolve_intelligence_client_id(request, data.clientId)
    if data.campaignId:
        campaign = await db.get_campaign(data.campaignId)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
        if not campaign.get("client_id"):
            raise HTTPException(status_code=400, detail="Campaign must be tenant-owned before CRM sync")
        if client_id and client_id != campaign["client_id"]:
            raise HTTPException(status_code=403, detail="Campaign is outside CRM tenant scope")
        client_id = client_id or campaign["client_id"]
    client_id = _require_resolved_client_id(client_id, "CRM sync job")

    service = CRMIntegrationService(db)
    try:
        result = await service.plan_campaign_sync(
            client_id=client_id,
            connection_id=data.connectionId,
            campaign_id=data.campaignId,
            direction=data.direction,
            requested_by=_actor_email(request, data.requestedBy),
            idempotency_key=data.idempotencyKey,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "planned", **result, "runtime_sync": False}


@app.get("/api/crm/campaigns/{campaign_id}/payload-preview", dependencies=[Depends(require_auth)])
async def get_crm_campaign_payload_preview(
    campaign_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_enabled()
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
    _shadow_tenant_scoped_read(request, "campaign", campaign.get("client_id"))
    if not campaign.get("client_id"):
        raise HTTPException(status_code=400, detail="Campaign must be tenant-owned before CRM payload preview")
    client_id = _resolve_intelligence_client_id(request, clientId) or campaign["client_id"]
    if client_id != campaign["client_id"]:
        raise HTTPException(status_code=403, detail="Campaign is outside CRM tenant scope")
    service = CRMIntegrationService(db)
    try:
        preview = await service.build_campaign_payload_preview(
            client_id=client_id,
            campaign_id=campaign_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "ready", "preview": preview, "runtime_sync": False}


@app.get("/api/crm/sync-jobs", dependencies=[Depends(require_auth)])
async def list_crm_sync_jobs(
    request: Request,
    clientId: Optional[str] = None,
    connectionId: Optional[str] = None,
    campaignId: Optional[str] = None,
):
    _require_crm_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, clientId),
        "CRM sync job list",
    )
    service = CRMIntegrationService(db)
    return {
        "client_id": client_id,
        "runtime_sync": False,
        "jobs": await service.list_sync_jobs(
            client_id=client_id,
            connection_id=connectionId,
            campaign_id=campaignId,
        ),
    }


@app.post("/api/crm/sync-jobs/{job_id}/dry-run", dependencies=[Depends(require_auth)])
async def execute_crm_sync_job_dry_run(job_id: str, data: CRMSyncDryRunExecute, request: Request):
    _require_crm_enabled()
    job = await db.get_crm_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CRM sync job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "CRM sync job")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or job["client_id"]
    if client_id != job["client_id"]:
        raise HTTPException(status_code=403, detail="CRM sync job is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.execute_dry_run_sync(
            client_id=client_id,
            job_id=job_id,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "validated", **result, "runtime_sync": False}


@app.post("/api/crm/sync-jobs/{job_id}/preflight", dependencies=[Depends(require_auth)])
async def run_crm_sync_job_preflight(job_id: str, data: CRMSyncPreflightExecute, request: Request):
    _require_crm_preflight_enabled()
    job = await db.get_crm_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CRM sync job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "CRM sync job")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or job["client_id"]
    if client_id != job["client_id"]:
        raise HTTPException(status_code=403, detail="CRM sync job is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.run_sync_preflight(
            client_id=client_id,
            job_id=job_id,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "preflight_validated", **result, "runtime_sync": False}


@app.post("/api/crm/sync-jobs/{job_id}/outbox", dependencies=[Depends(require_auth)])
async def queue_crm_sync_job_outbox(job_id: str, data: CRMSyncOutboxQueue, request: Request):
    _require_crm_outbox_enabled()
    job = await db.get_crm_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CRM sync job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "CRM sync job")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or job["client_id"]
    if client_id != job["client_id"]:
        raise HTTPException(status_code=403, detail="CRM sync job is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.queue_sync_outbox(
            client_id=client_id,
            job_id=job_id,
            requested_by=_actor_email(request, data.requestedBy),
            idempotency_key=data.idempotencyKey,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "queued_shadow", **result, "runtime_sync": False}


@app.get("/api/crm/outbox", dependencies=[Depends(require_auth)])
async def list_crm_sync_outbox(
    request: Request,
    clientId: Optional[str] = None,
    jobId: Optional[str] = None,
    status: Optional[str] = None,
):
    _require_crm_outbox_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, clientId),
        "CRM outbox list",
    )
    service = CRMIntegrationService(db)
    try:
        items = await service.list_sync_outbox(
            client_id=client_id,
            job_id=jobId,
            status=status,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "client_id": client_id,
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "outbox": items,
    }


@app.get("/api/crm/outbox/summary", dependencies=[Depends(require_auth)])
async def get_crm_outbox_summary(
    request: Request,
    clientId: Optional[str] = None,
    jobId: Optional[str] = None,
    connectionId: Optional[str] = None,
    campaignId: Optional[str] = None,
):
    _require_crm_observability_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, clientId),
        "CRM outbox summary",
    )
    service = CRMIntegrationService(db)
    try:
        summary = await service.get_sync_outbox_summary(
            client_id=client_id,
            job_id=jobId,
            connection_id=connectionId,
            campaign_id=campaignId,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "summary": summary,
    }


@app.get("/api/crm/outbox/{outbox_id}/delivery-plan", dependencies=[Depends(require_auth)])
async def get_crm_outbox_delivery_plan(
    outbox_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_delivery_plan_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        plan = await service.build_outbox_delivery_plan(
            client_id=client_id,
            outbox_id=outbox_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "delivery_plan": plan,
    }


@app.post("/api/crm/outbox/{outbox_id}/delivery-approval", dependencies=[Depends(require_auth)])
async def approve_crm_outbox_delivery_plan(
    outbox_id: str,
    data: CRMDeliveryApprovalCreate,
    request: Request,
):
    _require_crm_delivery_approval_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.approve_outbox_delivery_plan(
            client_id=client_id,
            outbox_id=outbox_id,
            approved_by=_actor_email(request, data.approvedBy),
            requested_by=_actor_email(request, data.requestedBy),
            idempotency_key=data.idempotencyKey,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "approved_shadow", **result, "runtime_sync": False}


@app.get("/api/crm/delivery-approvals", dependencies=[Depends(require_auth)])
async def list_crm_delivery_approvals(
    request: Request,
    clientId: Optional[str] = None,
    outboxId: Optional[str] = None,
    status: Optional[str] = None,
):
    _require_crm_delivery_approval_enabled()
    client_id = _require_resolved_client_id(
        _resolve_intelligence_client_id(request, clientId),
        "CRM delivery approval list",
    )
    service = CRMIntegrationService(db)
    try:
        approvals = await service.list_delivery_approvals(
            client_id=client_id,
            outbox_id=outboxId,
            status=status,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "client_id": client_id,
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "live_sync_enabled": False,
        "approvals": approvals,
    }


@app.post("/api/crm/delivery-approvals/{approval_id}/revoke-shadow", dependencies=[Depends(require_auth)])
async def revoke_crm_delivery_approval(
    approval_id: str,
    data: CRMDeliveryApprovalRevoke,
    request: Request,
):
    _require_crm_delivery_approval_revoke_enabled()
    approval = await db.get_crm_delivery_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="CRM delivery approval not found")
    _assert_intelligence_scope(request, approval.get("client_id"), "CRM delivery approval")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or approval["client_id"]
    if client_id != approval["client_id"]:
        raise HTTPException(status_code=403, detail="CRM delivery approval is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.revoke_delivery_approval(
            client_id=client_id,
            approval_id=approval_id,
            revoked_by=_actor_email(request, data.revokedBy),
            reason=data.reason,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "revoked_shadow", **result, "runtime_sync": False}


@app.get("/api/crm/outbox/{outbox_id}/live-readiness", dependencies=[Depends(require_auth)])
async def get_crm_outbox_live_readiness(
    outbox_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_live_readiness_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        readiness = await service.get_outbox_live_readiness(
            client_id=client_id,
            outbox_id=outbox_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "live_sync_enabled": False,
        "readiness": readiness,
    }


@app.get("/api/crm/outbox/{outbox_id}/provider-sandbox", dependencies=[Depends(require_auth)])
async def get_crm_outbox_provider_sandbox(
    outbox_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_provider_sandbox_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        sandbox = await service.build_outbox_provider_sandbox(
            client_id=client_id,
            outbox_id=outbox_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "live_sync_enabled": False,
        "provider_sandbox": sandbox,
    }


@app.get("/api/crm/outbox/{outbox_id}/dispatch-canary", dependencies=[Depends(require_auth)])
async def get_crm_outbox_dispatch_canary(
    outbox_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_dispatch_canary_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        canary = await service.build_outbox_dispatch_canary(
            client_id=client_id,
            outbox_id=outbox_id,
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "status": "ready",
        "runtime_sync": False,
        "external_execution": False,
        "worker_dispatch_enabled": False,
        "live_sync_enabled": False,
        "dispatch_canary": canary,
    }


@app.post("/api/crm/outbox/{outbox_id}/shadow-run", dependencies=[Depends(require_auth)])
async def run_crm_outbox_shadow_worker(outbox_id: str, data: CRMSyncOutboxShadowRun, request: Request):
    _require_crm_worker_shadow_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.run_outbox_shadow_worker(
            client_id=client_id,
            outbox_id=outbox_id,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "completed_shadow", **result, "runtime_sync": False}


@app.post("/api/crm/outbox/{outbox_id}/retry-shadow", dependencies=[Depends(require_auth)])
async def schedule_crm_outbox_shadow_retry(outbox_id: str, data: CRMSyncOutboxRetryUpdate, request: Request):
    _require_crm_worker_retries_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.schedule_outbox_shadow_retry(
            client_id=client_id,
            outbox_id=outbox_id,
            error=data.error,
            next_retry_at=data.nextRetryAt,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "retry_scheduled_shadow", **result, "runtime_sync": False}


@app.post("/api/crm/outbox/{outbox_id}/requeue-shadow", dependencies=[Depends(require_auth)])
async def requeue_crm_outbox_shadow_retry(outbox_id: str, data: CRMSyncOutboxRequeue, request: Request):
    _require_crm_worker_retries_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.requeue_outbox_shadow_retry(
            client_id=client_id,
            outbox_id=outbox_id,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "queued_shadow", **result, "runtime_sync": False}


@app.post("/api/crm/outbox/{outbox_id}/dead-letter-shadow", dependencies=[Depends(require_auth)])
async def dead_letter_crm_outbox_shadow_item(outbox_id: str, data: CRMSyncOutboxRetryUpdate, request: Request):
    _require_crm_worker_retries_enabled()
    item = await db.get_crm_sync_outbox_item(outbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="CRM outbox item not found")
    _assert_intelligence_scope(request, item.get("client_id"), "CRM outbox item")
    client_id = _resolve_intelligence_client_id(request, data.clientId) or item["client_id"]
    if client_id != item["client_id"]:
        raise HTTPException(status_code=403, detail="CRM outbox item is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        result = await service.dead_letter_outbox_shadow_item(
            client_id=client_id,
            outbox_id=outbox_id,
            error=data.error,
            requested_by=_actor_email(request, data.requestedBy),
        )
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {"status": "dead_letter_shadow", **result, "runtime_sync": False}


@app.get("/api/crm/sync-jobs/{job_id}/events", dependencies=[Depends(require_auth)])
async def list_crm_sync_job_events(
    job_id: str,
    request: Request,
    clientId: Optional[str] = None,
):
    _require_crm_enabled()
    job = await db.get_crm_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CRM sync job not found")
    _assert_intelligence_scope(request, job.get("client_id"), "CRM sync job")
    client_id = _resolve_intelligence_client_id(request, clientId) or job["client_id"]
    if client_id != job["client_id"]:
        raise HTTPException(status_code=403, detail="CRM sync job is outside tenant scope")

    service = CRMIntegrationService(db)
    try:
        events = await service.list_sync_events(client_id=client_id, job_id=job_id)
    except ValueError as exc:
        raise _crm_value_error(exc) from exc
    return {
        "job_id": job_id,
        "client_id": client_id,
        "runtime_sync": False,
        "events": events,
    }


# Agent editing intentionally updates DB metadata and runtime JSON schema together.
@app.post("/api/leads/upload", dependencies=[Depends(require_auth)])
async def upload_leads(data: LeadsUpload, request: Request):
    if not data.leads:
        raise HTTPException(status_code=400, detail="At least one lead is required")
    leads, summary = _normalize_campaign_leads(data.leads)
    if not leads:
        raise HTTPException(status_code=400, detail="No valid leads found")
    if len(leads) > summary["limit"]:
        raise HTTPException(status_code=413, detail=f"Campaign lead limit exceeded: {summary['limit']}")
    client_id = await _resolve_campaign_launch_client_id(request, data.clientId)
    await db.upsert_campaign(data.campaignId, {
        "name": data.campaignName or data.campaignId,
        "status": "Pending",
        "agent_id": data.agentId,
        "client_id": client_id,
        "telephony_provider": data.telephonyProvider or "demo",
        "created_at": datetime.now().isoformat(),
    })
    await db.upsert_leads(data.campaignId, leads)
    return {"status": "success", "count": len(leads), "summary": summary}


# ── Campaigns ─────────────────────────────────────────────────────────────────
@app.get("/api/campaigns")
async def list_campaigns(request: Request, includeArchived: bool = False, includeDeleted: bool = False, clientId: Optional[str] = None):
    if (includeArchived or includeDeleted) and not feature_flags.is_enabled("campaign.lifecycle_management"):
        raise HTTPException(status_code=403, detail="campaign.lifecycle_management is disabled")
    client_id = _resolve_intelligence_client_id(request, clientId)
    return await db.list_campaigns_with_lifecycle(
        client_id=client_id,
        include_archived=includeArchived,
        include_deleted=includeDeleted,
    )


@app.get("/api/campaigns/e2e-qa/readiness", dependencies=[Depends(require_auth)])
async def get_campaign_e2e_qa_readiness(request: Request, clientId: Optional[str] = None):
    if not feature_flags.is_enabled("campaign.e2e_qa_readiness"):
        raise HTTPException(status_code=403, detail="campaign.e2e_qa_readiness is disabled")
    _require_global_monitor_admin(_tenant_context_from_request(request), "Campaign E2E QA")
    client_id = _resolve_intelligence_client_id(request, clientId) if clientId else None
    return await _build_campaign_e2e_qa_readiness(client_id=client_id)


async def _campaign_for_lifecycle_or_404(campaign_id: str, request: Request) -> dict:
    _require_campaign_lifecycle_enabled()
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
    return campaign


def _assert_campaign_inactive_for_lifecycle(campaign: dict) -> None:
    if str(campaign.get("status") or "").lower() == "active":
        raise HTTPException(status_code=409, detail="Active campaigns cannot be archived or deleted")


@app.get("/api/campaigns/{campaign_id}/lifecycle", dependencies=[Depends(require_auth)])
async def get_campaign_lifecycle(campaign_id: str, request: Request):
    await _campaign_for_lifecycle_or_404(campaign_id, request)
    summary = await db.get_campaign_lifecycle_summary(campaign_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return summary


@app.post("/api/campaigns/{campaign_id}/archive", dependencies=[Depends(require_auth)])
async def archive_campaign(campaign_id: str, request: Request, data: Optional[CampaignLifecycleRequest] = None):
    campaign = await _campaign_for_lifecycle_or_404(campaign_id, request)
    _assert_campaign_inactive_for_lifecycle(campaign)
    updated = await db.set_campaign_archived(
        campaign_id,
        archived=True,
        actor_email=_actor_email(request, data.actorEmail if data else None),
    )
    return {"status": "archived", "campaign": updated}


@app.post("/api/campaigns/{campaign_id}/restore", dependencies=[Depends(require_auth)])
async def restore_campaign(campaign_id: str, request: Request, data: Optional[CampaignLifecycleRequest] = None):
    await _campaign_for_lifecycle_or_404(campaign_id, request)
    updated = await db.restore_campaign_lifecycle(
        campaign_id,
        actor_email=_actor_email(request, data.actorEmail if data else None),
    )
    return {"status": "restored", "campaign": updated}


@app.delete("/api/campaigns/{campaign_id}", dependencies=[Depends(require_auth)])
async def delete_campaign(campaign_id: str, request: Request, data: CampaignLifecycleRequest | None = None):
    campaign = await _campaign_for_lifecycle_or_404(campaign_id, request)
    _assert_campaign_inactive_for_lifecycle(campaign)
    result = await db.soft_delete_campaign(
        campaign_id,
        reason=data.reason if data else None,
        actor_email=_actor_email(request, data.actorEmail if data else None),
    )
    return {"status": "soft_deleted", **(result or {})}

@app.post("/api/campaigns/start", dependencies=[Depends(require_auth)])
async def start_campaign(data: CampaignStart, background_tasks: BackgroundTasks, request: Request):
    campaign = await db.get_campaign(data.campaignId)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    _assert_campaign_startable(campaign)
    requested_client_id = _resolve_intelligence_client_id(request, data.clientId)
    if campaign.get("client_id"):
        _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
        if requested_client_id and requested_client_id != campaign["client_id"]:
            raise HTTPException(status_code=403, detail="Campaign is outside tenant scope")
    await db.set_campaign_status(data.campaignId, "Active")
    provider_slug     = data.telephonyProvider or "demo"
    agent_id          = data.agentId or "default"
    worker_v2_execution: dict[str, Any] | None = None
    if feature_flags.is_enabled("campaign.worker_v2"):
        try:
            control_plane = CampaignWorkerV2ControlPlane(db)
            execution = await control_plane.prepare_execution(
                campaign_id=data.campaignId,
                agent_id=agent_id,
                telephony_provider=provider_slug,
                client_id=campaign.get("client_id"),
                config=CampaignWorkerV2Config(mode="live_metadata", max_concurrency=1, max_attempts=1),
            )
            worker_v2_execution = {
                "mode": execution.get("mode", "live_metadata"),
                "executionId": execution.get("id"),
                "status": execution.get("status"),
                "liveDispatchRunner": "v1_compatible",
            }
        except Exception as exc:
            logger.exception("Campaign worker v2 live metadata preparation failed; continuing v1 runner: %s", exc)
    if provider_slug == "demo":
        engine           = DemoCallEngine(ws_manager=ws_manager, db=db)
        agent_schema_path = _resolve_schema(agent_id)
        background_tasks.add_task(
            engine.run_demo_campaign,
            data.campaignId,
            agent_schema_path,
            campaign.get("client_id") or "global",
        )
    else:
        background_tasks.add_task(run_campaign, data.campaignId, agent_id, provider_slug, campaign.get("client_id") or "global")
    response = {"status": "started", "provider": provider_slug}
    if worker_v2_execution:
        response["campaignWorkerV2"] = worker_v2_execution
    return response

@app.get("/api/campaigns/{campaign_id}/results")
async def get_results(campaign_id: str, request: Request, clientId: Optional[str] = None):
    client_id = _resolve_intelligence_client_id(request, clientId)
    campaign = None
    if client_id or feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        campaign = await db.get_campaign(campaign_id)
    if client_id:
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
        if campaign.get("client_id") != client_id:
            raise HTTPException(status_code=403, detail="Campaign is outside tenant scope")
    if feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        _shadow_tenant_scoped_read(
            request,
            "call_result",
            campaign.get("client_id") if campaign else None,
            resource_found=bool(campaign),
        )
    return await db.get_results_for_campaign(campaign_id, client_id)

@app.get("/api/results/{lead_id}/transcript")
async def get_transcript(lead_id: str, request: Request, clientId: Optional[str] = None):
    client_id = _resolve_intelligence_client_id(request, clientId)
    await _shadow_transcript_access(request, lead_id)
    owner = None
    if client_id or feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        owner = await db.get_call_result_owner_for_transcript(lead_id)
    if client_id:
        if not owner or not owner.get("found"):
            return []
        _assert_intelligence_scope(request, owner.get("owner_client_id"), "Call result")
        if owner.get("owner_client_id") != client_id:
            raise HTTPException(status_code=403, detail="Transcript is outside tenant scope")
    if feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        _shadow_tenant_scoped_read(
            request,
            "call_result",
            owner.get("owner_client_id"),
            resource_found=bool(owner.get("found")),
        )
    return await db.get_transcript_for_lead(lead_id, client_id)


def _recording_file_path_from_url(recording_url: str) -> str:
    if not str(recording_url or "").startswith("/recordings/"):
        raise HTTPException(status_code=400, detail="recordingUrl must target /recordings")
    relative_path = str(recording_url)[len("/recordings/"):]
    if not relative_path or relative_path != os.path.basename(relative_path):
        raise HTTPException(status_code=400, detail="Invalid recording path")
    recordings_root = os.path.abspath("recordings")
    file_path = os.path.abspath(os.path.join(recordings_root, relative_path))
    try:
        if os.path.commonpath([recordings_root, file_path]) != recordings_root:
            raise HTTPException(status_code=400, detail="Invalid recording path")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid recording path") from exc
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Recording file not found")
    return file_path


def _recording_media_type(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(extension, "application/octet-stream")


@app.get("/api/recordings/protected")
async def get_protected_recording(request: Request, recordingUrl: str, clientId: Optional[str] = None):
    client_id = _resolve_intelligence_client_id(request, clientId) if clientId else None
    owner = await db.get_recording_asset_owner(recordingUrl)
    if not owner.get("found"):
        raise HTTPException(status_code=404, detail="Recording not found")
    if client_id:
        _assert_intelligence_scope(request, owner.get("owner_client_id"), "Recording")
        if owner.get("owner_client_id") != client_id:
            raise HTTPException(status_code=403, detail="Recording is outside tenant scope")
    if feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        _shadow_tenant_scoped_read(
            request,
            "recording_asset",
            owner.get("owner_client_id"),
            resource_found=bool(owner.get("found")),
        )
    file_path = _recording_file_path_from_url(recordingUrl)
    return FileResponse(
        file_path,
        media_type=_recording_media_type(file_path),
        filename=os.path.basename(file_path),
    )

@app.get("/api/campaigns/all/live")
async def get_all_live_state(request: Request, clientId: Optional[str] = None):
    client_id = _resolve_intelligence_client_id(request, clientId)
    return await db.get_all_live_state(client_id)

@app.get("/api/campaigns/{campaign_id}/live")
async def get_live_state(campaign_id: str, request: Request, clientId: Optional[str] = None):
    client_id = _resolve_intelligence_client_id(request, clientId)
    campaign = None
    if client_id or feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        campaign = await db.get_campaign(campaign_id)
    if client_id:
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        _assert_intelligence_scope(request, campaign.get("client_id"), "Campaign")
        if campaign.get("client_id") != client_id:
            raise HTTPException(status_code=403, detail="Campaign is outside tenant scope")
    if feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow"):
        _shadow_tenant_scoped_read(
            request,
            "live_call_state",
            campaign.get("client_id") if campaign else None,
            resource_found=bool(campaign),
        )
    return await db.get_live_state(campaign_id, client_id)

@app.get("/api/campaigns/{campaign_id}/executions", dependencies=[Depends(require_auth)])
async def list_campaign_executions(campaign_id: str):
    return await db.list_campaign_executions(campaign_id=campaign_id)

async def _execution_for_campaign_or_404(campaign_id: str, execution_id: str) -> dict:
    execution = await db.get_campaign_execution(execution_id)
    if not execution or execution.get("campaign_id") != campaign_id:
        raise HTTPException(status_code=404, detail="Campaign execution not found")
    return execution

@app.post("/api/campaigns/{campaign_id}/executions/{execution_id}/pause", dependencies=[Depends(require_auth)])
async def pause_campaign_execution(campaign_id: str, execution_id: str):
    await _execution_for_campaign_or_404(campaign_id, execution_id)
    control_plane = CampaignWorkerV2ControlPlane(db)
    updated = await control_plane.pause(execution_id, reason="api_request")
    return {"status": "paused", "execution": updated}

@app.post("/api/campaigns/{campaign_id}/executions/{execution_id}/resume", dependencies=[Depends(require_auth)])
async def resume_campaign_execution(campaign_id: str, execution_id: str):
    await _execution_for_campaign_or_404(campaign_id, execution_id)
    control_plane = CampaignWorkerV2ControlPlane(db)
    updated = await control_plane.resume(execution_id, reason="api_request")
    return {"status": "planned", "execution": updated}

@app.post("/api/campaigns/{campaign_id}/executions/{execution_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_campaign_execution(campaign_id: str, execution_id: str):
    await _execution_for_campaign_or_404(campaign_id, execution_id)
    control_plane = CampaignWorkerV2ControlPlane(db)
    updated = await control_plane.cancel(execution_id, reason="api_request")
    return {"status": "cancelled", "execution": updated}


# ── Demo Mode (simulated calls — AI vs AI) ────────────────────────────────────
@app.post("/api/demo/start", dependencies=[Depends(require_auth)])
async def start_demo(data: DemoStart, background_tasks: BackgroundTasks):
    """
    Start a simulated demo campaign (AI generates human responses).
    Dashboard updates in real-time via WebSocket.
    """
    campaign_id       = data.campaignId
    agent_id          = data.agentId or "default"
    agent_schema_path = _resolve_schema(agent_id)
    client_id         = data.clientId or "global"

    existing = await db.get_campaign(campaign_id)
    if not existing:
        await db.upsert_campaign(campaign_id, {
            "status": "Active",
            "telephony_provider": "demo",
            "client_id": client_id,
            "created_at": datetime.now().isoformat(),
        })
    else:
        _assert_campaign_startable(existing)
        await db.set_campaign_status(campaign_id, "Active")

    engine = DemoCallEngine(ws_manager=ws_manager, db=db)
    if data.leadOverride:
        background_tasks.add_task(engine.run_demo_call, campaign_id, data.leadOverride, agent_schema_path, client_id)
    else:
        background_tasks.add_task(engine.run_demo_campaign, campaign_id, agent_schema_path, client_id)

    return {"status": "demo_started", "campaign_id": campaign_id}


# ── Clients ───────────────────────────────────────────────────────────────────
@app.get("/api/clients")
async def list_clients():
    return await db.list_clients()

@app.post("/api/clients", dependencies=[Depends(require_auth)])
async def create_client(client: ClientCreate):
    client_data = client.dict()
    # If agent info is provided, also set the assignment
    result = await db.create_client(client.id, client_data)
    if client.agentId:
        await db.set_assignment(client.id, client.agentId)
    return result


# ── Assignments ───────────────────────────────────────────────────────────────
@app.get("/api/assignments/{client_id}")
async def get_assignment(client_id: str):
    agent_id = await db.get_assignment(client_id)
    if not agent_id:
        return {"agentId": None}
    agents = await db.list_agents()
    agent  = next((a for a in agents if a["id"] == agent_id), None)
    return {"agentId": agent_id, "agent": agent}

@app.post("/api/assignments", dependencies=[Depends(require_auth)])
async def update_assignment(data: AssignmentUpdate):
    await db.set_assignment(data.clientId, data.agentId)
    return {"status": "success"}


# ── Telephony Providers ───────────────────────────────────────────────────────
def _normalize_available_number(raw: dict[str, Any], provider: str, country_code: str) -> dict[str, Any]:
    phone = (raw.get("phone") or raw.get("phone_number") or "").strip()
    region = raw.get("region") or raw.get("locality") or country_code
    capabilities = raw.get("capabilities") or {"voice": True}
    if isinstance(capabilities, list):
        capabilities = {str(item): True for item in capabilities}
    return {
        **raw,
        "phone": phone,
        "phone_number": phone,
        "region": region,
        "locality": raw.get("locality") or region,
        "provider": raw.get("provider") or provider,
        "monthly_cost": raw.get("monthly_cost") or raw.get("cost") or "",
        "capabilities": capabilities,
    }


@app.get("/api/telephony/providers")
async def get_providers():
    return list_providers()

@app.get("/api/telephony/live-qa/readiness", dependencies=[Depends(require_auth)])
async def get_telephony_live_qa_readiness(
    request: Request,
    provider: str = "twilio",
    clientId: Optional[str] = None,
    countryCode: str = "IN",
    includeProviderProbe: bool = False,
):
    if not feature_flags.is_enabled("telephony.live_qa_readiness"):
        raise HTTPException(status_code=403, detail="telephony.live_qa_readiness is disabled")
    _require_global_monitor_admin(_tenant_context_from_request(request), "Telephony live QA")
    client_id = _resolve_intelligence_client_id(request, clientId) if clientId else None
    return await _build_telephony_live_qa_readiness(
        provider_slug=provider,
        client_id=client_id,
        country_code=countryCode,
        include_provider_probe=includeProviderProbe,
    )

@app.get("/api/telephony/numbers")
async def list_numbers(client_id: Optional[str] = None):
    return await db.list_phone_numbers(client_id)

@app.post("/api/telephony/numbers/search")
async def search_numbers(provider: str = "twilio", country_code: str = "IN"):
    p = get_provider(provider)
    results = await p.list_available_numbers(country_code)
    return [_normalize_available_number(item, provider, country_code) for item in results]

@app.post("/api/telephony/numbers/purchase", dependencies=[Depends(require_auth)])
async def purchase_number(data: PhoneNumberPurchase, request: Request):
    provider = get_provider(data.provider)
    result   = await provider.purchase_number(data.phoneNumber)
    if result.get("status") in ("active", "simulated"):
        if feature_flags.is_enabled("telephony.tenant_numbers") and data.clientId:
            _assert_intelligence_scope(request, data.clientId, "Phone number")
        number_data = {
            "phone":     result["phone"],
            "sid":       result.get("sid", ""),
            "provider":  data.provider,
            "client_id": data.clientId,
            "region":    "",
        }
        try:
            saved = await db.add_phone_number(number_data)
            if feature_flags.is_enabled("telephony.tenant_numbers") and data.clientId:
                saved["route"] = await db.upsert_phone_number_route(
                    number_id=saved["id"],
                    client_id=data.clientId,
                    metadata={"source": "purchase"},
                )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "success", **saved}
    raise HTTPException(status_code=400, detail=result.get("error", "Purchase failed"))

@app.post("/api/telephony/numbers/assign", dependencies=[Depends(require_auth)])
async def assign_number(data: PhoneNumberAssign, request: Request):
    if feature_flags.is_enabled("telephony.tenant_numbers"):
        await _assert_phone_number_scope(request, data.numberId, data.clientId)
        route = await db.upsert_phone_number_route(
            number_id=data.numberId,
            client_id=data.clientId,
            metadata={"source": "assign_endpoint"},
        )
        return {"status": "success", "route": route}
    await db.assign_number_to_client(data.numberId, data.clientId)
    return {"status": "success"}

@app.post("/api/telephony/numbers/routes", dependencies=[Depends(require_auth)])
async def upsert_number_route(data: PhoneNumberRouteUpdate, request: Request):
    _require_tenant_numbers_enabled()
    await _assert_phone_number_scope(request, data.numberId, data.clientId)
    try:
        route = await db.upsert_phone_number_route(
            number_id=data.numberId,
            client_id=data.clientId,
            agent_id=data.agentId,
            campaign_id=data.campaignId,
            routing_mode=data.routingMode,
            metadata=data.metadata or {"source": "route_endpoint"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "route": route}

@app.get("/api/telephony/numbers/{number_id}/route", dependencies=[Depends(require_auth)])
async def get_number_route(number_id: str, request: Request, routingMode: str = "tenant_default"):
    _require_tenant_numbers_enabled()
    number = await _assert_phone_number_scope(request, number_id)
    route = await db.get_phone_number_route(number_id, routingMode)
    if route:
        _assert_intelligence_scope(request, route.get("client_id"), "Phone number route")
    return {"number": number, "route": route}

@app.get("/api/telephony/routes/resolve", dependencies=[Depends(require_auth)])
async def resolve_number_route(phone: str, request: Request, provider: str = "twilio"):
    _require_tenant_numbers_enabled()
    route = await db.resolve_phone_number_route(phone, provider)
    if not route:
        raise HTTPException(status_code=404, detail="Phone route not found")
    _assert_intelligence_scope(request, route.get("client_id"), "Phone number route")
    return route

@app.post("/api/telephony/buy")  # legacy compat
async def legacy_buy_number():
    return {"status": "success", "phone": "+91 9122 " + str(uuid.uuid4().int)[:6]}


# ── Twilio Webhooks ───────────────────────────────────────────────────────────
@app.post("/telephony/twiml/{call_id}")
async def twilio_twiml(call_id: str, request: Request):
    from telephony.twilio_handler import build_twiml
    route = await _resolve_tenant_phone_route_from_webhook(request, provider="twilio")
    ws_url     = WEBHOOK_BASE_URL.replace("http://", "wss://").replace("https://", "wss://")
    stream_url = f"{ws_url}/telephony/stream/{call_id}"
    if route:
        stream_url = f"{stream_url}?{urlencode({'tenantId': route.get('client_id') or '', 'numberId': route.get('number_id') or ''})}"
    twiml      = build_twiml(stream_url)
    return HTMLResponse(content=twiml, media_type="application/xml")

@app.websocket("/telephony/stream/{call_id}")
async def twilio_stream(websocket: WebSocket, call_id: str):
    from telephony.twilio_handler import handle_twilio_stream
    from ws_hub import call_registry

    # Resolve per-call context registered by agent_runner.run_campaign()
    # Falls back gracefully for inbound/unknown calls.
    meta = call_registry.get(call_id) or {}
    agent_schema_path = meta.get("agent_schema_path") or _resolve_schema("default")
    _audit_ws_connection(websocket, "/telephony/stream/{call_id}", meta.get("client_id", "global"))

    try:
        await handle_twilio_stream(
            websocket,
            call_id=call_id,
            agent_schema_path=agent_schema_path,
            ws_manager=ws_manager,
            db=db,
            campaign_id=meta.get("campaign_id", ""),
            lead_id=call_id,
            lead_name=meta.get("lead_name", "Lead"),
            phone=meta.get("phone", ""),
            client_id=meta.get("client_id", "global"),
        )
    finally:
        # Always clean up the registry entry even if the handler raises
        call_registry.clear(call_id)


# ── WebSocket Dashboard Hub ───────────────────────────────────────────────────
def _normalize_dashboard_ws_client_id(client_id: str | None) -> str:
    return str(client_id or "global").strip() or "global"


@app.websocket("/ws/dashboard/{client_id}")
async def dashboard_ws(websocket: WebSocket, client_id: str = "global"):
    client_id = _normalize_dashboard_ws_client_id(client_id)
    context = _audit_ws_connection(websocket, "/ws/dashboard/{client_id}", client_id)
    if client_id == "global" and _should_enforce_global_monitor_admin() and not context.is_admin:
        logger.warning("Dashboard WS rejected: global monitor requires admin context")
        await websocket.accept()
        await websocket.close(code=1008)
        return
    await ws_manager.connect(websocket, client_id)
    logger.info("Dashboard WS connected: client=%s", client_id)
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.receive":
                # handle ping frames from frontend by replying with pong
                text_data = msg.get("text")
                if text_data == "ping":
                    try:
                        await websocket.send_text("pong")
                    except Exception:
                        pass
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        logger.info("Dashboard WS disconnected: client=%s", client_id)
    except Exception as e:
        logger.error("Dashboard WS error: %s", e)
    finally:
        await ws_manager.disconnect(websocket, client_id)

@app.websocket("/ws/dashboard")
async def dashboard_ws_global(websocket: WebSocket):
    client_id = websocket.query_params.get("clientId") or websocket.query_params.get("client_id") or "global"
    await dashboard_ws(websocket, client_id=client_id)


# ── VoiceLiveSource / VoiceLiveSink (shared by /api/voice-live and /api/voice-demo) ──


import time

class SessionRecorder(TimelineSessionRecorder):
    """Compatibility alias for the timeline-aware recorder."""
    pass

class VoiceLiveSource(FrameProcessor):
    """Bridges browser mic audio into the Pipecat pipeline with backpressure."""

    def __init__(self, recorder=None, recording_turn_state=None):
        super().__init__()
        self.recorder = recorder
        self.recording_turn_state = recording_turn_state
        self._started = False
        self._terminated = False
        self._sample_rate = 16000 # Default fallback
        # Keep a little extra headroom so short browser/network jitter does not drop speech.
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._process_task = None
        self._frames_received = 0
        self._frames_dropped = 0
        self._stats_started_at = time.monotonic()
        self._last_stats_log_at = 0.0

    def set_sample_rate(self, rate: int):
        self._sample_rate = rate
        logger.info("VoiceLiveSource: Input sample rate set to %d", rate)

    def start(self):
        """
        Eagerly mark the source as started so _process_queue begins draining
        immediately after the pipeline asyncio task is launched.
        Call this right after asyncio.create_task(runner.run(task)).
        """
        if not self._started:
            self._started = True
            logger.info("VoiceLiveSource: start() called — queue will now drain immediately.")
        if not self._process_task:
            self._process_task = asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        # Wait for started flag (will be instant if start() was already called)
        while not self._started:
            await asyncio.sleep(0.02)
        while not self._terminated:
            try:
                frame = await self._queue.get()
                await self.push_frame(frame, FrameDirection.DOWNSTREAM)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("VoiceLiveSource queue error: %s", e)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Hard Session State Machine enforcement."""
        await super().process_frame(frame, direction)
        
        if isinstance(frame, StartFrame):
            self._started = True
            logger.info("Session started handshake received.")
            if not self._process_task:
                self._process_task = asyncio.create_task(self._process_queue())
        
        if isinstance(frame, EndFrame):
            self._terminated = True
            logger.info("Session terminated.")

        await self.push_frame(frame, direction)


    def queue_audio(self, data: bytes):
        """Intelligent Selective Backpressure (Senior Requirement)."""
        if self._terminated:
            return
        if not data:
            return
            
        # Use the dynamically detected sample rate from the client
        frame = AudioRawFrame(audio=data, sample_rate=self._sample_rate, num_channels=1)
        _ensure_frame_attrs(frame)
        self._frames_received += 1
        now = time.monotonic()
        if (now - self._last_stats_log_at) >= 1.0:
            queue_depth = self._queue.qsize()
            frame_ms = (len(data) / 2 / max(self._sample_rate, 1)) * 1000.0
            logger.info(
                "VoiceLiveSource: frame_bytes=%d frame_ms=%.1f queue=%d/%d dropped=%d received=%d sample_rate=%d",
                len(data),
                frame_ms,
                queue_depth,
                self._queue.maxsize,
                self._frames_dropped,
                self._frames_received,
                self._sample_rate,
            )
            self._last_stats_log_at = now
        
        try:
            self._queue.put_nowait(frame)
            recording_blocked = bool(
                self.recording_turn_state
                and self.recording_turn_state.is_stt_blocked()
            )
            if self.recorder and not recording_blocked:
                self.recorder.add_user_audio(data, sample_rate=self._sample_rate)
            elif self.recorder and recording_blocked:
                logger.debug("VoiceLiveSource: skipped user recording during agent playback")
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                if isinstance(dropped, (StartFrame, EndFrame)):
                    self._queue.put_nowait(dropped)
                    return 
                self._frames_dropped += 1
                self._queue.put_nowait(frame)
            except Exception:
                pass


def _ensure_frame_attrs(frame: Frame) -> None:
    try:
        if not hasattr(frame, "id") or getattr(frame, "id", None) is None:
            frame.id = f"ws_{uuid.uuid4().hex[:12]}"
    except Exception:
        pass
    try:
        if not hasattr(frame, "broadcast_sibling_id"):
            frame.broadcast_sibling_id = None
    except Exception:
        pass

class VoiceLiveSink(FrameProcessor):
    """Sends pipeline output back to the browser WebSocket."""

    def __init__(self, websocket: WebSocket, on_transcript=None, recorder=None):
        super().__init__()
        self.ws = websocket
        self.on_transcript = on_transcript
        self.recorder = recorder

        # Track which generation was already signalled to frontend.
        self._last_signalled_gen_id = -1

    def _is_ws_connected(self) -> bool:
        """
        Check whether the WebSocket is still connected before sending data.
        """
        try:
            return self.ws.client_state == WebSocketState.CONNECTED
        except Exception:
            return False

    async def process_frame(self, frame, direction):
        frame_type = type(frame).__name__

        # ============================================================
        # CANCEL FRAME
        # ============================================================
        if isinstance(frame, CancelFrame):
            logger.info("[PIPELINE] SINK <- CancelFrame")

            if self._is_ws_connected():
                try:
                    await self.ws.send_text(
                        json.dumps({"type": "cancel"})
                    )
                except Exception as exc:
                    logger.warning(
                        "[PIPELINE] SINK -> Failed sending cancel payload: %s",
                        exc,
                    )

            # Reset generation tracking.
            self._last_signalled_gen_id = -1

            await self.push_frame(frame, direction)
            return

        # ============================================================
        # END FRAME
        # ============================================================
        #
        # IMPORTANT:
        # EndFrame can represent completion of the current pipeline
        # processing turn. It must NOT automatically close the browser
        # WebSocket, otherwise the next user turn cannot be processed.
        #
        if isinstance(frame, EndFrame):
            logger.info(
                "[PIPELINE] SINK <- EndFrame received. "
                "Keeping WebSocket open for conversation."
            )

            await self.push_frame(frame, direction)
            return

        # ============================================================
        # NORMAL FRAME PROCESSING
        # ============================================================
        try:
            await super().process_frame(frame, direction)

        except BaseException as exc:
            logger.error(
                "[PIPELINE] SINK -> super().process_frame failed "
                "for type=%s: %s",
                frame_type,
                exc,
            )

            if not isinstance(frame, (AudioRawFrame, TextFrame)):
                return

        # ============================================================
        # AUDIO FRAME
        # ============================================================
        if isinstance(frame, AudioRawFrame):

            # If browser disconnected, do not attempt to send audio.
            if not self._is_ws_connected():
                logger.warning(
                    "[WS] SINK -> WebSocket is not connected. "
                    "Dropping audio frame of size=%d bytes.",
                    len(frame.audio),
                )
                return

            # --------------------------------------------------------
            # Generation ID signal
            # --------------------------------------------------------
            frame_gen_id = getattr(frame, "gen_id", 0)

            if frame_gen_id != self._last_signalled_gen_id:

                self._last_signalled_gen_id = frame_gen_id

                try:
                    await self.ws.send_text(
                        json.dumps({
                            "type": "gen_id",
                            "value": frame_gen_id
                        })
                    )

                    logger.info(
                        "[SINK] Sent gen_id=%d pre-signal to frontend",
                        frame_gen_id,
                    )

                except RuntimeError as exc:
                    if (
                        "Cannot call" in str(exc)
                        and "close message" in str(exc)
                    ):
                        logger.warning(
                            "[SINK] WebSocket closed before gen_id "
                            "could be sent."
                        )
                        return

                    logger.exception(
                        "[SINK] Failed sending gen_id: %s",
                        exc,
                    )
                    return

                except Exception as exc:
                    logger.warning(
                        "[SINK] Failed sending gen_id: %s",
                        exc,
                    )
                    return

            # --------------------------------------------------------
            # Send PCM16 audio
            # --------------------------------------------------------
            try:

                if not self._is_ws_connected():
                    logger.warning(
                        "[WS] SINK -> WebSocket closed before "
                        "audio send. Dropping remaining audio."
                    )
                    return

                logger.info(
                    "[WS] SINK: Attempting to send_bytes of size %d...",
                    len(frame.audio),
                )

                await self.ws.send_bytes(frame.audio)

                if self.recorder:
                    try:
                        self.recorder.add_agent_audio(
                            frame.audio,
                            sample_rate=frame.sample_rate,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[RECORDER] Failed recording agent audio: %s",
                            exc,
                        )

                logger.info(
                    "[WS] Audio Chunk Sent size=%d",
                    len(frame.audio),
                )

            except RuntimeError as exc:

                if (
                    "Cannot call" in str(exc)
                    and "close message" in str(exc)
                ):
                    logger.warning(
                        "[WS] SINK -> WebSocket closed while sending "
                        "audio. Dropping remaining audio."
                    )
                    return

                logger.exception(
                    "[PIPELINE] SINK -> RuntimeError sending audio: %s",
                    exc,
                )

            except Exception as exc:
                logger.exception(
                    "[PIPELINE] SINK -> Failed sending audio frame "
                    "over WebSocket: %s",
                    exc,
                )

        # ============================================================
        # TEXT FRAME
        # ============================================================
        elif isinstance(frame, TextFrame):

            is_agent = isinstance(frame, AgentTextFrame)
            speaker = "agent" if is_agent else "user"

            if self._is_ws_connected():

                try:
                    payload = {
                        "type": "transcript",
                        "speaker": speaker,
                        "text": frame.text,
                    }

                    await self.ws.send_text(
                        json.dumps(payload)
                    )

                except RuntimeError as exc:

                    if (
                        "Cannot call" in str(exc)
                        and "close message" in str(exc)
                    ):
                        logger.warning(
                            "[WS] SINK -> WebSocket closed while "
                            "sending transcript."
                        )
                    else:
                        logger.exception(
                            "[WS] SINK -> RuntimeError sending "
                            "transcript: %s",
                            exc,
                        )

                except Exception as exc:
                    logger.error(
                        "[PIPELINE] SINK -> Failed sending "
                        "transcript payload: %s",
                        exc,
                    )

            else:
                logger.warning(
                    "[WS] SINK -> WebSocket disconnected. "
                    "Dropping transcript: %r",
                    frame.text,
                )

            # --------------------------------------------------------
            # Transcript callback
            # --------------------------------------------------------
            if self.on_transcript:
                try:
                    await self.on_transcript(
                        speaker,
                        frame.text,
                    )

                except Exception as exc:
                    logger.error(
                        "[PIPELINE] SINK -> on_transcript "
                        "callback failed: %s",
                        exc,
                    )

        # ============================================================
        # PUSH FRAME FORWARD
        # ============================================================
        await self.push_frame(frame, direction)


# ── Live Voice — plain voice chat (no dashboard events) ──────────────────────
@app.websocket("/api/voice-live")
async def websocket_voice_live(websocket: WebSocket):
    """
    Simple browser mic → STT → LLM → TTS → browser speakers.
    No dashboard events. Used by the Talk Live page.
    Fix Issue 2: websocket.accept() is now called FIRST.
    """
    # Accept BEFORE touching anything else
    await websocket.accept()

    agent_id  = websocket.query_params.get("agentId", "default")
    client_id = websocket.query_params.get("clientId")
    lead_name = websocket.query_params.get("leadName", "Prashant")

    # IMPORTANT:
    # Real-estate voice agent must always use the canonical 29-node schema.
    schema_path = _resolve_schema(agent_id)

    _audit_ws_connection(websocket, "/api/voice-live", client_id)
    logger.info(
        "Live Voice: Connected — agent=%s schema=%s",
        agent_id,
        schema_path,
    )

    turn_state = VoiceTurnState()
    source = VoiceLiveSource(recording_turn_state=turn_state)
    #vad    = VADProcessor(turn_state=turn_state)
    stt    = RealEstateSTTProcessor(turn_state=turn_state, agent_id=agent_id, vad_enabled=False)
    """ llm = RealEstateLLMProcessor(turn_state=turn_state)
    # Always use the canonical Real Estate conversation flow.
    # The browser agentId identifies the configured agent, but it
    # must not select a different/generic conversation schema.
    canonical_schema = os.path.join(AGENTS_DIR, "real_estate")
    logger.info(
        "[VOICE-DEMO] Using canonical Real Estate schema: %s | agent_id=%s",
        canonical_schema,
        agent_id,)
    llm.state_manager = StateManager(canonical_schema)
    llm.state_manager.reset_state()                          # Fix: prevent stale session carry-over
    llm.state_manager.conversation_data["name"] = lead_name
    llm.state_manager.conversation_data["lead_name"] = lead_name
    llm.history.clear()  """  
    llm = RealEstateLLMProcessor(turn_state=turn_state,  schema_path=schema_path)

    # Use the canonical StateManager created by RealEstateLLMProcessor.
    # Do NOT replace it with the split directory schema.
    llm.state_manager.reset_state()

    llm.state_manager.conversation_data["name"] = lead_name
    llm.state_manager.conversation_data["lead_name"] = lead_name
    llm.history.clear()                                   # Fix: flush any prior in-memory history
    tts    = RealEstateTTSProcessor(turn_state=turn_state, agent_id=agent_id)
    sink   = VoiceLiveSink(websocket)

    pipeline    = Pipeline([source, stt, llm, tts, sink])
    runner      = PipelineRunner()
    task        = PipelineTask(pipeline)
    runner_task = asyncio.create_task(runner.run(task))
    source.start()  # Fix: eagerly start queue draining to prevent StartFrame race condition

    async def reader():
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.receive":
                    text = msg.get("text")
                    if text:
                        try:
                            # Handle dynamic sample rate handshake
                            payload = json.loads(text)
                            message_type = payload.get("type")
                            if message_type == "mic_ready":
                                source.set_sample_rate(payload.get("sampleRate", 16000))
                            elif message_type == "ping":
                                try:
                                    await websocket.send_text(json.dumps({"type": "pong"}))
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        continue

                    data = msg.get("bytes") or b""
                    if data:
                        if data == b"ping":
                            try: await websocket.send_bytes(b"pong")
                            except Exception: pass
                            continue
                        source.queue_audio(data)
                elif msg["type"] == "websocket.disconnect":
                    logger.info("Live Voice: websocket disconnected code=%s", msg.get("code"))
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("Live Voice Reader Task Error: %s", e)

    try:
        await reader()
    finally:
        try:
            if source._process_task:
                source._process_task.cancel()
            await source.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.3)
            runner_task.cancel()
            await runner_task
        except (asyncio.CancelledError, Exception):
            pass


# ── Voice Demo — browser mic + live dashboard events  ────────────────────────
@app.websocket("/api/voice-demo")
async def websocket_voice_demo(websocket: WebSocket):
    """
    Client speaks via browser mic. The AI pipeline runs normally.
    ADDITIONALLY: every transcript exchange fires dashboard WebSocket events
    so the Live Feed panel shows ringing → talking → transcript → completed,
    exactly the same as a real campaign call.

    Query params:
      agentId    — which agent schema to use
      clientId   — for WS broadcast scoping (defaults to 'global')
      leadName   — display name in the live feed (defaults to 'Demo User')
    """
    # Accept FIRST (Issue 2)
    await websocket.accept()
    print("Connected")

    agent_id  = websocket.query_params.get("agentId", "default")
    client_id = websocket.query_params.get("clientId", "global")
    lead_name = websocket.query_params.get("leadName", "Demo User")

    # IMPORTANT:
    # Real-estate voice agent must always use the canonical 29-node schema.
    schema_path = _resolve_schema(agent_id)
    _audit_ws_connection(websocket, "/api/voice-demo", client_id)
    campaign_id = f"demo_mic_{uuid.uuid4().hex[:8]}"
    lead_uid    = f"{campaign_id}_demo"
    transcripts: list[dict] = []

    logger.info("Voice Demo: Connected — agent=%s client=%s campaign=%s", agent_id, client_id, campaign_id)

    try:
        # Create campaign record in DB
        # Note: client_id and agent_id are FK columns — pass None (NULL) to avoid
        # FK constraint errors for demo sessions which aren't tied to real DB rows.
        await db.upsert_campaign(campaign_id, {
            "name":               f"Demo — {lead_name}",
            "status":             "Active",
            "agent_id":           None,           # NULL is allowed in FK cols
            "client_id":          None,           # NULL is allowed in FK cols
            "telephony_provider": "demo_mic",
            "created_at":         datetime.now().isoformat(),
        })
        logger.info("Voice Demo: DB record created — %s", campaign_id)

        # Emit "Ringing" event so the live feed shows a call card immediately
        await ws_manager.send_call_event(
            "call_ringing",
            campaign_id=campaign_id,
            lead_id=lead_uid,
            lead_name=lead_name,
            status="Ringing...",
            snippet="Connecting to agent...",
            provider="demo_mic",
            client_id=client_id,
        )
        try:
            await websocket.send_text(json.dumps({
                "type": "call_ringing",
                "campaign_id": campaign_id,
                "leadId": lead_uid,
                "status": "Ringing...",
                "snippet": "Connecting to agent..."
            }))
        except Exception:
            pass
        logger.info("Voice Demo: Ringing event sent")
    except Exception as _setup_err:
        logger.exception("Voice Demo: Setup FAILED — %s", _setup_err)
        try:
            await websocket.close(code=1011, reason="Setup error")
        except Exception:
            pass
        return

    async def on_transcript(speaker: str, text: str):
        """Called by VoiceLiveSink on every transcript line — forwards to dashboard."""
        role = "assistant" if speaker == "agent" else "user"
        transcripts.append({"role": role, "content": text})

        await ws_manager.send_call_event(
            "call_talking",
            campaign_id=campaign_id,
            lead_id=lead_uid,
            lead_name=lead_name,
            status="Talking",
            snippet=text,
            transcripts=list(transcripts),
            provider="demo_mic",
            client_id=client_id,
        )
        try:
            await websocket.send_text(json.dumps({
                "type": "call_talking",
                "campaign_id": campaign_id,
                "leadId": lead_uid,
                "status": "Talking",
                "snippet": text
            }))
        except Exception:
            pass

    # Keep a reference to the LLM processor so we can read extracted conversation
    # data (budget, location, etc.) from the StateManager after the call ends.
    llm_ref   = None
    source    = None
    runner_task = None
    pipeline_ok = False

    recorder = TimelineSessionRecorder(sample_rate=24000)
    if _PIPECAT_AVAILABLE:
        try:
            turn_state = VoiceTurnState()
            source = VoiceLiveSource(recorder=recorder, recording_turn_state=turn_state)
            #vad    = VADProcessor(turn_state=turn_state)
            stt    = RealEstateSTTProcessor(turn_state=turn_state, agent_id=agent_id, vad_enabled=False)
            """ llm    = RealEstateLLMProcessor(turn_state=turn_state)
            llm.state_manager = StateManager(schema_path)
            llm.state_manager.reset_state()
            llm.state_manager.conversation_data["name"] = lead_name
            llm.state_manager.conversation_data["lead_name"] = lead_name
            llm.history.clear()
            llm_ref = llm """  # capture ref BEFORE runner_task starts
            llm = RealEstateLLMProcessor(turn_state=turn_state, schema_path=schema_path)

            # Use the canonical StateManager initialized by RealEstateLLMProcessor.
            llm.state_manager.reset_state()

            llm.state_manager.conversation_data["name"] = lead_name
            llm.state_manager.conversation_data["lead_name"] = lead_name
            llm.history.clear()
            llm_ref = llm

            tts    = RealEstateTTSProcessor(turn_state=turn_state, agent_id=agent_id)
            sink   = VoiceLiveSink(websocket, on_transcript=on_transcript, recorder=recorder)
            logger.info("Voice Demo: Pipeline components created")

            pipeline    = Pipeline([source, stt, llm, tts, sink])
            runner      = PipelineRunner()
            task        = PipelineTask(pipeline)
            runner_task = asyncio.create_task(runner.run(task))
            source.start()  # Fix: eagerly start queue draining to prevent StartFrame race condition
            logger.info("Voice Demo: Pipeline running")
            pipeline_ok = True
        except Exception as _pipe_err:
            logger.exception("Voice Demo: Pipeline creation FAILED — %s", _pipe_err)
            # Do NOT close — fall through to keep-alive fallback loop below
            pipeline_ok = False
    else:
        logger.warning("Voice Demo: pipecat not available — running in keep-alive mode (no AI pipeline)")

    # Emit "Connected" once pipeline is wired (or fallback mode)
    await ws_manager.send_call_event(
        "call_connected",
        campaign_id=campaign_id,
        lead_id=lead_uid,
        lead_name=lead_name,
        status="Connected",
        snippet="Speaking with agent..." if pipeline_ok else "[No AI pipeline — keep-alive mode]",
        provider="demo_mic",
        client_id=client_id,
    )
    try:
        await websocket.send_text(json.dumps({
            "type":        "call_connected",
            "campaign_id": campaign_id,
            "leadId":      lead_uid,
            "status":      "Connected",
            "pipeline":    "active" if pipeline_ok else "unavailable",
        }))
    except Exception:
        pass
    logger.info("Voice Demo: Connected event sent — pipeline_ok=%s", pipeline_ok)

    # ── HEARTBEAT TASK ─────────────────────────────────────────────────────────
    # Railway's reverse proxy drops idle WebSocket connections after ~30 s.
    # Send a JSON ping every 15 s to keep the connection alive regardless of
    # whether the AI pipeline is active.
    _hb_stop = asyncio.Event()

    async def _heartbeat():
        """Server-side keepalive — fires every 15 s."""
        while not _hb_stop.is_set():
            try:
                await asyncio.wait_for(_hb_stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            if _hb_stop.is_set():
                break
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break  # WS already closed

    heartbeat_task = asyncio.create_task(_heartbeat())

    async def reader():
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.receive":
                    text = msg.get("text")
                    if text:
                        # Handle ping/pong from frontend
                        if text == "ping" or text == "PING":
                            try:
                                await websocket.send_text("pong")
                            except Exception:
                                pass
                            continue
                        try:
                            payload = json.loads(text)
                            message_type = payload.get("type")
                            if message_type == "mic_ready" and source is not None:
                                source.set_sample_rate(payload.get("sampleRate", 16000))
                            elif message_type == "ping":
                                try:
                                    await websocket.send_text(json.dumps({"type": "pong"}))
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        continue

                    data = msg.get("bytes") or b""
                    if data:
                        if data == b"ping":
                            try:
                                await websocket.send_bytes(b"pong")
                            except Exception:
                                pass
                            continue
                        if source is not None:
                            source.queue_audio(data)
                elif msg["type"] == "websocket.disconnect":
                    logger.info("Voice Demo: websocket disconnected code=%s campaign=%s", msg.get("code"), campaign_id)
                    break
        except WebSocketDisconnect:
            print("Client disconnected normally")
        except Exception as e:
            print("WebSocket error:", e)
            logger.error("Voice Demo Reader Task Error: %s", e)

    try:
        await reader()
    finally:
        _hb_stop.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        print("Closing connection")
        # Tear down pipeline
        try:
            if source is not None and source._process_task:
                source._process_task.cancel()
            if source is not None:
                await source.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0.3)
            if runner_task is not None:
                runner_task.cancel()
                await runner_task
        except (asyncio.CancelledError, Exception):
            pass

        # ── Extract real lead data from the StateManager conversation_data ──
        duration_s = 0.0
        recording_url = ""
        try:
            filename = f"rec_{lead_uid}.wav"
            filepath = os.path.join("recordings", filename)
            duration_s = recorder.finalize(filepath)
            if duration_s > 0:
                recording_url = f"/recordings/{filename}"
        except Exception as _rec_err:
            logger.warning("Voice Demo: Audio recording failed - %s", _rec_err)

        conv_data: dict = {}
        try:
            if llm_ref and hasattr(llm_ref, "state_manager"):
                conv_data = llm_ref.state_manager.conversation_data or {}
        except Exception as _cd_err:
            logger.warning("Voice Demo: Could not read conversation_data — %s", _cd_err)

        interested = "Yes" if (conv_data.get("intent_value") or conv_data.get("location")) else "No"

        callback_value = conv_data.get("timeline", "—")
        result = {
            "name":          lead_name,
            "phone":         "browser-mic",
            "calledAt":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration":      f"{len(transcripts) * 8}s (est.)",
            "status":        "Connected",
            "interested":    interested,
            "budget":        conv_data.get("budget", "—"),
            "location":      conv_data.get("location", "—"),
            "timeline":      callback_value,
            "callback":      callback_value,
            "property_type": conv_data.get("property_type", "—"),
            "transcription": transcripts,
            "provider":      "demo_mic",
            "processed":     True,
            "lead_data":     conv_data,  # send raw extracted slots for frontend card
            "recording_url": recording_url,
            "duration":      int(duration_s) if duration_s else len(transcripts) * 8
        }
        logger.info("Voice Demo: Extracted lead data — %s", conv_data)
        await ws_manager.send_call_event(
            "call_completed",
            campaign_id=campaign_id,
            lead_id=lead_uid,
            lead_name=lead_name,
            status="Completed",
            snippet="Demo session ended",
            transcripts=transcripts,
            result=result,
            provider="demo_mic",
            client_id=client_id,
        )
        # Also push the completed result directly to the voice WebSocket so the
        # frontend Demo page (which listens on liveSocket.onmessage) can render
        # the lead summary card without waiting for the dashboard WS.
        try:
            await websocket.send_text(json.dumps({
                "type":   "call_completed",
                "result": result,
                "leadId": lead_uid,
                "leadName": lead_name,
            }))
        except Exception:
            pass  # WebSocket may already be closed — that's fine
        # Persist to DB
        try:
            await db.append_call_result(campaign_id, result)
            await db.update_live_state(lead_uid, campaign_id, lead_name, "Completed", "Demo session ended", transcripts, "demo_mic")
            await db.set_campaign_status(campaign_id, "Done")
        except Exception as e:
            logger.error("Voice Demo DB persist error: %s", e)


# ── Helper ────────────────────────────────────────────────────────────────────
# Shared "rich" domain templates (built by hand or upgraded over time) used
# ONLY as a safety net when an agent has no usable schema of its own yet
# (e.g. very old agents created before the generator existed).
DOMAIN_SCHEMA_MAP = {
    "hr": "hr",
    "hr_agent": "hr",
    "hr-demo": "hr",
    "recruitment": "hr",
    "real_estate": "real_estate",
    "real_estate_sales": "real_estate",
    "real-estate-demo": "real_estate",
    "finance": "finance",
    "insurance": "insurance",
    "education": "education.json",

    "default": "real_estate",
}


def _agent_type_from_db(agent_id: str) -> str | None:
    """Agent UUID se uska agent_type nikalo (sqlite se, sync)."""
    import sqlite3
    try:
        conn = sqlite3.connect(os.path.join(DB_DIR, "platform.db"), timeout=5)
        try:
            row = conn.execute(
                "SELECT agent_type FROM agents WHERE id=?", (agent_id,)
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[SCHEMA] DB lookup failed for %s: %s", agent_id, exc)
        return None


def _is_usable_agent_schema(path: str) -> bool:
    """
    A per-agent schema file (db/agents/{agent_id}.json) is "usable" only if
    it actually has a real conversation in it — more than the bare
    root_greeting/discovery skeleton that used to be written when nothing
    else was available. This stops us from trusting an empty/corrupt file.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        nodes = (schema.get("conversationFlow") or {}).get("nodes") or []
        start_id = (schema.get("conversationFlow") or {}).get("start_node_id")
        node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
        return bool(nodes) and start_id in node_ids
    except Exception:
        return False


def _resolve_schema(agent_id: str) -> str:
    """
    Priority order:
      1. The agent's OWN generated schema file (db/agents/{agent_id}.json).
         Both "Manual Setup" and "Generate from Prompt" agent creation write
         a complete, agent-specific conversation flow here — this is always
         the most correct source for that particular agent, regardless of
         which creation path was used.
      2. A shared domain template folder (hr / finance / insurance /
         real_estate) keyed off agent_type — used only as a safety net for
         agents that predate the per-agent generator, or whose own file is
         missing/corrupt.
      3. real_estate, as the final fallback (legacy behaviour).
    """
    key = str(agent_id or "").strip().lower()

    # 1. Agent's own per-agent schema file — highest priority.
    own_schema = os.path.join(AGENTS_DIR, f"{agent_id}.json")
    if os.path.isfile(own_schema) and _is_usable_agent_schema(own_schema):
        logger.info("[SCHEMA] agent_id=%s -> own generated schema %s", agent_id, own_schema)
        return own_schema

    # 2. Domain alias / agent_type -> shared template.
    folder = DOMAIN_SCHEMA_MAP.get(key)
    agent_type = None
    if not folder:
        agent_type = _agent_type_from_db(agent_id)
        if agent_type:
            folder = DOMAIN_SCHEMA_MAP.get(agent_type.strip().lower())
            logger.info("[SCHEMA] agent_id=%s -> agent_type=%s", agent_id, agent_type)

    if not folder:
        folder = "real_estate"

    schema_path = os.path.join(AGENTS_DIR, folder)
    if os.path.isdir(schema_path) or os.path.isfile(schema_path):
        logger.info("[SCHEMA] agent_id=%s -> shared template %s", agent_id, schema_path)
        return schema_path

    logger.error("[SCHEMA] %s missing, falling back to real_estate", schema_path)
    schema_path = os.path.join(AGENTS_DIR, "real_estate")
    logger.info("[SCHEMA] agent_id=%s -> %s", agent_id, schema_path)
    return schema_path

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    PORT = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)

# Dummy comment to trigger uvicorn reload trigger 123


