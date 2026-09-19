"""Tenant/auth context helpers for audit-first hardening.

Phase 1 deliberately keeps enforcement disabled by default. These helpers
extract a conservative context from request metadata so later phases can add
tenant checks without changing public API contracts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from . import feature_flags


PUBLIC_PATH_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/favicon.ico",
)

TENANT_SCOPED_PATH_PREFIXES = (
    "/api/agents",
    "/api/assignments",
    "/api/campaigns",
    "/api/clients",
    "/api/crm",
    "/api/demo",
    "/api/intelligence",
    "/api/leads",
    "/api/memory",
    "/api/protected",
    "/api/results",
    "/api/telephony",
    "/api/voice-demo",
    "/api/voice-live",
    "/ws/dashboard",
)


TENANT_SCOPED_READ_POLICY_TYPES = (
    ("agent", "agent_management", True),
    ("campaign", "campaign_management", True),
    ("call_result", "call_outputs", True),
    ("live_call_state", "call_outputs", True),
    ("recording_asset", "recordings", True),
    ("phone_number", "telephony", True),
    ("scrape_job", "website_intelligence", True),
    ("crm_connection", "crm", True),
    ("crm_sync_job", "crm", True),
    ("crm_outbox", "crm", True),
    ("crm_delivery_approval", "crm", True),
)

TENANT_SCOPED_READ_EXCLUDED_SURFACES = (
    ("websocket_audio_streams", "audio_contract_unchanged"),
    ("tts_audio_chunks", "audio_contract_unchanged"),
    ("stt_audio_chunks", "audio_contract_unchanged"),
    ("recording_file_bytes", "storage_isolation_requires_separate_phase"),
    ("transcript_body", "content_reads_require_endpoint_specific_phase"),
    ("campaign_worker_execution", "worker_dispatch_requires_separate_phase"),
)


def _configured_admin_emails() -> set[str]:
    raw = os.getenv("PLATFORM_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _dev_admin_role_for_email(email: str | None) -> str | None:
    """Allow local admin email mapping only while active auth is disabled."""
    if feature_flags.is_enabled("auth.enforce_backend"):
        return None
    normalized = (email or "").strip().lower()
    if normalized and normalized in _configured_admin_emails():
        return "admin"
    return None


@dataclass(frozen=True)
class TenantContext:
    auth_state: str = "anonymous"
    role: str = "anonymous"
    user_email: str | None = None
    subject: str | None = None
    tenant_id: str | None = None
    requested_tenant_id: str | None = None
    source: str = "none"
    warnings: tuple[str, ...] = ()

    @property
    def is_verified(self) -> bool:
        return self.auth_state == "api_key"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def summary(self) -> dict[str, Any]:
        return {
            "auth_state": self.auth_state,
            "role": self.role,
            "user_email": self.user_email,
            "tenant_id": self.tenant_id,
            "requested_tenant_id": self.requested_tenant_id,
            "source": self.source,
            "verified": self.is_verified,
            "warnings": list(self.warnings),
        }


def build_http_tenant_context(request, *, api_key_secret: str = "") -> TenantContext:
    return build_tenant_context(
        headers=request.headers,
        query=request.query_params,
        api_key_secret=api_key_secret,
    )


def build_ws_tenant_context(
    websocket,
    *,
    path_tenant_id: str | None = None,
    api_key_secret: str = "",
) -> TenantContext:
    return build_tenant_context(
        headers=websocket.headers,
        query=websocket.query_params,
        path_tenant_id=path_tenant_id,
        api_key_secret=api_key_secret,
    )


def build_tenant_context(
    *,
    headers: Mapping[str, str] | None = None,
    query: Mapping[str, str] | None = None,
    path_tenant_id: str | None = None,
    api_key_secret: str = "",
) -> TenantContext:
    header_map = _normalize_mapping(headers)
    query_map = _normalize_mapping(query)
    warnings: list[str] = []

    requested_tenant_id = _first_present(
        path_tenant_id,
        header_map.get("x_tenant_id"),
        header_map.get("x-tenant-id"),
        query_map.get("tenant_id"),
        query_map.get("tenantid"),
        query_map.get("client_id"),
        query_map.get("clientid"),
    )

    api_key = _first_present(header_map.get("x_api_key"), header_map.get("x-api-key"))
    if api_key_secret and api_key and _constant_time_equal(api_key, api_key_secret):
        return TenantContext(
            auth_state="api_key",
            role="admin",
            tenant_id=requested_tenant_id,
            requested_tenant_id=requested_tenant_id,
            source="x-api-key",
        )

    auth_header = header_map.get("authorization", "")
    query_token = _first_present(query_map.get("access_token"), query_map.get("id_token"))
    token_source = None
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        token_source = "authorization"
    elif query_token:
        token = query_token
        token_source = "query_token"
        warnings.append("token_in_query")

    if token:
        payload = _decode_unverified_jwt_payload(token)
        warnings.append("bearer_unverified")
        user_email = _claim(payload, "email")
        tenant_id = _first_present(
            _claim(payload, "tenant_id"),
            _claim(payload, "client_id"),
            requested_tenant_id,
        )
        role = _claim(payload, "role") or "unknown"
        if role not in {"admin", "client", "tenant_admin", "user"}:
            role = "unknown"
        if role == "unknown":
            dev_admin_role = _dev_admin_role_for_email(user_email)
            if dev_admin_role:
                role = dev_admin_role
                warnings.append("admin_email_unverified")
        if requested_tenant_id and tenant_id and requested_tenant_id != tenant_id:
            warnings.append("tenant_claim_mismatch")
        return TenantContext(
            auth_state="bearer_unverified",
            role=role,
            user_email=user_email,
            subject=_claim(payload, "sub") or _claim(payload, "user_id"),
            tenant_id=tenant_id,
            requested_tenant_id=requested_tenant_id,
            source=token_source or "authorization",
            warnings=tuple(warnings),
        )

    user_email = _first_present(header_map.get("x_user_email"), query_map.get("user_email"))
    if user_email:
        warnings.append("identity_unverified")
        return TenantContext(
            auth_state="identity_unverified",
            role="unknown",
            user_email=user_email,
            tenant_id=requested_tenant_id,
            requested_tenant_id=requested_tenant_id,
            source="request_metadata",
            warnings=tuple(warnings),
        )

    if requested_tenant_id:
        warnings.append("tenant_unverified")
        return TenantContext(
            auth_state="anonymous",
            role="anonymous",
            tenant_id=requested_tenant_id,
            requested_tenant_id=requested_tenant_id,
            source="tenant_hint",
            warnings=tuple(warnings),
        )

    return TenantContext()


def should_reject_http_request(context: TenantContext, path: str) -> bool:
    if _is_public_path(path):
        return False
    if not feature_flags.is_enabled("auth.enforce_backend"):
        return False
    return not context.is_verified


def build_tenant_enforcement_readiness(context: TenantContext, *, path: str = "") -> dict[str, Any]:
    """Build a no-data shadow manifest for later tenant enforcement rollout."""
    normalized_path = path or ""
    public_path = _is_public_path(normalized_path)
    tenant_scope_required = _requires_tenant_scope(normalized_path)
    flags = {
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "ws.scoped_events": feature_flags.is_enabled("ws.scoped_events"),
    }

    blockers: list[str] = []
    if not flags["auth.enforce_backend"]:
        blockers.append("auth.enforce_backend_disabled")
    if tenant_scope_required and not flags["tenant.scoped_reads"]:
        blockers.append("tenant.scoped_reads_disabled")
    if not public_path and not context.is_verified:
        blockers.append("verified_backend_identity_missing")
    if tenant_scope_required and not context.is_admin and not context.tenant_id:
        blockers.append("tenant_context_missing")
    if "tenant_claim_mismatch" in context.warnings:
        blockers.append("tenant_claim_mismatch")

    tenant_match = (
        context.tenant_id == context.requested_tenant_id
        if context.tenant_id and context.requested_tenant_id
        else None
    )
    would_reject_auth = not public_path and not context.is_verified
    would_reject_tenant = (
        tenant_scope_required
        and not context.is_admin
        and (not context.tenant_id or tenant_match is False)
    )

    return {
        "manifest_version": "tenant_enforcement_readiness.v1",
        "path": normalized_path,
        "auth": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "source": context.source,
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "tenant": {
            "tenant_id_present": bool(context.tenant_id),
            "requested_tenant_id_present": bool(context.requested_tenant_id),
            "tenant_match": tenant_match,
            "tenant_value_included": False,
            "requested_tenant_value_included": False,
        },
        "flags": flags,
        "readiness": {
            "public_path": public_path,
            "tenant_scope_required": tenant_scope_required,
            "backend_auth_ready": public_path or context.is_verified,
            "tenant_scope_ready": (
                not tenant_scope_required
                or context.is_admin
                or (bool(context.tenant_id) and tenant_match is not False)
            ),
            "shadow_enforcement_ready": not any(
                blocker in blockers
                for blocker in {
                    "verified_backend_identity_missing",
                    "tenant_context_missing",
                    "tenant_claim_mismatch",
                }
            ),
            "active_enforcement": flags["auth.enforce_backend"] and (
                flags["tenant.scoped_reads"] or not tenant_scope_required
            ),
            "would_reject_if_auth_enforced": would_reject_auth,
            "would_reject_if_tenant_scoped_reads_enforced": would_reject_tenant,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "db_query_executed": False,
            "tenant_data_returned": False,
            "user_email_included": False,
            "tenant_id_included": False,
            "client_data_included": False,
            "cross_tenant_data_included": False,
        },
    }


def build_tenant_scoped_read_guard_decision(
    context: TenantContext,
    *,
    resource_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    allow_admin_override: bool = True,
    allow_unassigned_resource: bool = False,
) -> dict[str, Any]:
    """Build a reusable no-data decision for future tenant-scoped reads."""
    effective_requested_tenant_id = requested_tenant_id or context.requested_tenant_id or context.tenant_id
    owner_tenant_present = bool(owner_tenant_id)
    requested_tenant_present = bool(effective_requested_tenant_id)
    requester_tenant_present = bool(context.tenant_id)
    tenant_match = (
        owner_tenant_id == effective_requested_tenant_id
        if owner_tenant_present and requested_tenant_present
        else None
    )
    requester_tenant_matches_owner = (
        owner_tenant_id == context.tenant_id
        if owner_tenant_present and requester_tenant_present
        else None
    )

    verified_admin = context.is_verified and context.is_admin
    admin_override_allowed = bool(resource_found and allow_admin_override and verified_admin)
    requested_tenant_allowed = bool(resource_found and owner_tenant_present and tenant_match is True)
    requester_tenant_allowed = bool(
        resource_found
        and context.is_verified
        and owner_tenant_present
        and requester_tenant_matches_owner is True
    )
    unassigned_resource_allowed = bool(
        resource_found
        and allow_unassigned_resource
        and not owner_tenant_present
    )
    current_requester_allowed = bool(
        admin_override_allowed
        or requester_tenant_allowed
        or unassigned_resource_allowed
    )

    flags = {
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "tenant.scoped_read_guard_shadow": feature_flags.is_enabled("tenant.scoped_read_guard_shadow"),
    }

    blockers: list[str] = []
    if not flags["auth.enforce_backend"]:
        blockers.append("auth.enforce_backend_disabled")
    if not flags["tenant.scoped_reads"]:
        blockers.append("tenant.scoped_reads_disabled")
    if not flags["tenant.scoped_read_guard_shadow"]:
        blockers.append("tenant.scoped_read_guard_shadow_disabled")
    if not context.is_verified:
        blockers.append("verified_backend_identity_missing")
    if not context.is_admin and not context.tenant_id:
        blockers.append("tenant_context_missing")
    if "tenant_claim_mismatch" in context.warnings:
        blockers.append("tenant_claim_mismatch")
    if not resource_found:
        blockers.append("resource_not_found")
    elif not owner_tenant_present and not allow_unassigned_resource:
        blockers.append("resource_owner_missing")
    elif requested_tenant_present and tenant_match is False:
        blockers.append("tenant_mismatch_for_requested_tenant")
    elif not requested_tenant_present:
        blockers.append("requested_tenant_missing_for_tenant_simulation")
    if resource_found and not current_requester_allowed:
        blockers.append("current_requester_would_be_denied")

    return {
        "decision_version": "tenant_scoped_read_guard.v1",
        "mode": {
            "shadow_only": True,
            "runtime_enforcement_changed": False,
            "db_write_performed": False,
        },
        "resource": {
            "found": bool(resource_found),
            "owner_tenant_present": owner_tenant_present,
            "owner_tenant_included": False,
            "resource_id_included": False,
            "payload_included": False,
            "unassigned_resource_allowed": unassigned_resource_allowed,
        },
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "verified_admin": verified_admin,
            "tenant_present": requester_tenant_present,
            "requested_tenant_present": requested_tenant_present,
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "tenant": {
            "tenant_match_for_requested_tenant": tenant_match,
            "requester_tenant_matches_owner": requester_tenant_matches_owner,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
        },
        "flags": flags,
        "decision": {
            "admin_override_allowed": admin_override_allowed,
            "requested_tenant_allowed_if_enforced": requested_tenant_allowed,
            "requester_tenant_allowed_if_enforced": requester_tenant_allowed,
            "current_requester_allowed_if_enforced": current_requester_allowed,
            "would_reject_current_requester_if_enforced": not current_requester_allowed,
            "active_enforcement": flags["auth.enforce_backend"] and flags["tenant.scoped_reads"],
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "db_write_performed": False,
            "resource_payload_returned": False,
            "resource_id_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_tenant_scoped_read_policy_manifest(context: TenantContext) -> dict[str, Any]:
    """Build a no-data registry manifest for future scoped-read enforcement."""
    flags = {
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "tenant.enforcement_readiness": feature_flags.is_enabled("tenant.enforcement_readiness"),
        "tenant.scoped_read_guard_shadow": feature_flags.is_enabled("tenant.scoped_read_guard_shadow"),
        "tenant.scoped_read_policy_shadow": feature_flags.is_enabled("tenant.scoped_read_policy_shadow"),
    }

    blockers: list[str] = []
    if not flags["auth.enforce_backend"]:
        blockers.append("auth.enforce_backend_disabled")
    if not flags["tenant.scoped_reads"]:
        blockers.append("tenant.scoped_reads_disabled")
    if not flags["tenant.enforcement_readiness"]:
        blockers.append("tenant.enforcement_readiness_disabled")
    if not flags["tenant.scoped_read_guard_shadow"]:
        blockers.append("tenant.scoped_read_guard_shadow_disabled")
    if not flags["tenant.scoped_read_policy_shadow"]:
        blockers.append("tenant.scoped_read_policy_shadow_disabled")
    if not context.is_verified:
        blockers.append("verified_backend_identity_missing")
    if not context.is_admin:
        blockers.append("admin_context_required")
    if "tenant_claim_mismatch" in context.warnings:
        blockers.append("tenant_claim_mismatch")

    policies = [
        {
            "resource_type": resource_type,
            "surface": surface,
            "ownership_required": True,
            "owner_lookup_ready": owner_lookup_ready,
            "guard_ready": True,
            "canary_supported": True,
            "future_enforcement_flag": "tenant.scoped_reads",
            "shadow_only": True,
            "payload_included": False,
            "resource_id_included": False,
            "owner_tenant_included": False,
            "tenant_data_included": False,
        }
        for resource_type, surface, owner_lookup_ready in TENANT_SCOPED_READ_POLICY_TYPES
    ]
    excluded_surfaces = [
        {
            "surface": surface,
            "reason": reason,
            "policy_registered": False,
            "runtime_enforcement_changed": False,
            "payload_included": False,
        }
        for surface, reason in TENANT_SCOPED_READ_EXCLUDED_SURFACES
    ]

    return {
        "manifest_version": "tenant_scoped_read_policy.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": bool(context.requested_tenant_id),
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "flags": flags,
        "coverage": {
            "policy_count": len(policies),
            "guard_ready_count": sum(1 for policy in policies if policy["guard_ready"]),
            "owner_lookup_ready_count": sum(1 for policy in policies if policy["owner_lookup_ready"]),
            "excluded_surface_count": len(excluded_surfaces),
            "all_policies_shadow_only": all(policy["shadow_only"] for policy in policies),
            "runtime_enforcement_changed": False,
        },
        "policies": policies,
        "excluded_surfaces": excluded_surfaces,
        "readiness": {
            "policy_manifest_ready": True,
            "active_enforcement": flags["auth.enforce_backend"] and flags["tenant.scoped_reads"],
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "db_query_executed": False,
            "db_write_performed": False,
            "resource_payload_returned": False,
            "resource_id_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "phone_number_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "crm_payload_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_ws_event_scope_shadow_manifest(
    *,
    event_type: str | None,
    broadcast_mode: str,
    target_client_id: str | None = None,
) -> dict[str, Any]:
    """Build a no-payload shadow manifest for dashboard websocket event scoping."""
    normalized_mode = _safe_manifest_label(broadcast_mode, default="unknown")
    safe_event_type = _safe_manifest_label(event_type, default="unknown")
    target_tenant_present = bool(target_client_id and target_client_id != "global")
    global_broadcast = normalized_mode == "all"
    client_broadcast = normalized_mode == "client"
    flags = {
        "ws.scoped_events": feature_flags.is_enabled("ws.scoped_events"),
        "ws.scoped_events_shadow": feature_flags.is_enabled("ws.scoped_events_shadow"),
    }

    blockers: list[str] = []
    if not flags["ws.scoped_events"]:
        blockers.append("ws.scoped_events_disabled")
    if not flags["ws.scoped_events_shadow"]:
        blockers.append("ws.scoped_events_shadow_disabled")
    if global_broadcast:
        blockers.append("global_broadcast_requires_admin_channel_review")
    if client_broadcast and not target_tenant_present:
        blockers.append("target_tenant_missing")

    return {
        "manifest_version": "ws_event_scope_shadow.v1",
        "event": {
            "type": safe_event_type,
            "type_value_sanitized": True,
            "payload_included": False,
            "campaign_id_included": False,
            "lead_id_included": False,
            "lead_name_included": False,
            "transcript_content_included": False,
            "result_payload_included": False,
        },
        "delivery": {
            "broadcast_mode": normalized_mode,
            "global_broadcast": global_broadcast,
            "client_broadcast": client_broadcast,
            "target_tenant_present": target_tenant_present,
            "target_tenant_included": False,
            "connection_count_included": False,
        },
        "flags": flags,
        "decision": {
            "shadow_only": True,
            "active_enforcement": flags["ws.scoped_events"],
            "scoped_event_ready": client_broadcast and target_tenant_present,
            "would_require_admin_channel_review": global_broadcast,
            "blockers": blockers,
        },
        "safety": {
            "runtime_delivery_changed": False,
            "websocket_payload_changed": False,
            "db_query_executed": False,
            "db_write_performed": False,
            "message_payload_returned": False,
            "target_tenant_included": False,
            "connection_identity_included": False,
            "client_data_included": False,
            "cross_tenant_data_included": False,
            "audio_contract_changed": False,
        },
    }


def build_recording_access_shadow_manifest(
    context: TenantContext,
    *,
    request_path: str,
) -> dict[str, Any]:
    """Build a no-path shadow manifest for future recording access isolation."""
    normalized_path = str(request_path or "")
    recording_path_requested = normalized_path.startswith("/recordings/")
    extension = "none"
    if recording_path_requested and "." in normalized_path.rsplit("/", 1)[-1]:
        extension = normalized_path.rsplit(".", 1)[-1].lower()
    safe_extension = _safe_manifest_label(extension, default="none", max_length=16)
    flags = {
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "recordings.access_shadow": feature_flags.is_enabled("recordings.access_shadow"),
    }

    blockers: list[str] = []
    if not flags["recordings.access_shadow"]:
        blockers.append("recordings.access_shadow_disabled")
    if not flags["auth.enforce_backend"]:
        blockers.append("auth.enforce_backend_disabled")
    if not flags["tenant.scoped_reads"]:
        blockers.append("tenant.scoped_reads_disabled")
    if not context.is_verified:
        blockers.append("verified_backend_identity_missing")
    if not context.is_admin and not context.tenant_id:
        blockers.append("tenant_context_missing")
    if recording_path_requested:
        blockers.append("recording_owner_lookup_required")

    return {
        "manifest_version": "recording_access_shadow.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": bool(context.requested_tenant_id),
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "recording": {
            "recording_path_requested": recording_path_requested,
            "file_extension": safe_extension,
            "path_included": False,
            "filename_included": False,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_bytes_included": False,
        },
        "flags": flags,
        "decision": {
            "shadow_only": True,
            "active_enforcement": False,
            "owner_lookup_required_before_enforcement": recording_path_requested,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "static_file_serving_changed": False,
            "recording_playback_changed": False,
            "db_query_executed": False,
            "db_write_performed": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_recording_owner_lookup_shadow_manifest(
    context: TenantContext,
    *,
    recording_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-path/no-content manifest for recording owner lookup readiness."""
    guard = build_tenant_scoped_read_guard_decision(
        context,
        resource_found=recording_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id or context.requested_tenant_id or context.tenant_id,
    )
    flags = {
        **guard["flags"],
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "recordings.access_shadow": feature_flags.is_enabled("recordings.access_shadow"),
        "recordings.owner_lookup_shadow": feature_flags.is_enabled("recordings.owner_lookup_shadow"),
    }

    blockers: list[str] = list(guard["decision"]["blockers"])
    if not flags["recordings.access_shadow"]:
        blockers.append("recordings.access_shadow_disabled")
    if not flags["recordings.owner_lookup_shadow"]:
        blockers.append("recordings.owner_lookup_shadow_disabled")

    db_query_executed = (
        flags["recordings.access_shadow"]
        and flags["recordings.owner_lookup_shadow"]
    )

    return {
        "manifest_version": "recording_owner_lookup_shadow.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": guard["requester"]["requested_tenant_present"],
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "recording": {
            "found": bool(recording_found),
            "owner_tenant_present": guard["resource"]["owner_tenant_present"],
            "campaign_id_present": bool(campaign_id_present),
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_bytes_included": False,
            "payload_included": False,
        },
        "flags": flags,
        "decision": {
            "guard_decision_version": guard["decision_version"],
            "tenant_match_for_requested_tenant": guard["tenant"]["tenant_match_for_requested_tenant"],
            "current_requester_allowed_if_enforced": guard["decision"]["current_requester_allowed_if_enforced"],
            "would_reject_current_requester_if_enforced": guard["decision"]["would_reject_current_requester_if_enforced"],
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "static_file_serving_changed": False,
            "recording_playback_changed": False,
            "db_query_executed": db_query_executed,
            "db_write_performed": False,
            "db_lookup_scope": "owner_metadata_only",
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_bytes_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_recording_access_enforcement_readiness_manifest(
    context: TenantContext,
    *,
    recording_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build the no-content decision model for future recording access enforcement."""
    owner_manifest = build_recording_owner_lookup_shadow_manifest(
        context,
        recording_found=recording_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **owner_manifest["flags"],
        "recordings.access_enforcement_shadow": feature_flags.is_enabled("recordings.access_enforcement_shadow"),
    }

    blockers: list[str] = list(owner_manifest["decision"]["blockers"])
    if not flags["recordings.access_enforcement_shadow"]:
        blockers.append("recordings.access_enforcement_shadow_disabled")

    would_allow = bool(owner_manifest["decision"]["current_requester_allowed_if_enforced"])
    owner_lookup_ready = bool(
        flags["recordings.access_shadow"]
        and flags["recordings.owner_lookup_shadow"]
        and owner_manifest["recording"]["found"]
        and owner_manifest["recording"]["owner_tenant_present"]
    )
    future_enforcement_ready = bool(
        flags["auth.enforce_backend"]
        and flags["tenant.scoped_reads"]
        and flags["recordings.access_enforcement_shadow"]
        and owner_lookup_ready
        and would_allow
        and not blockers
    )

    return {
        "manifest_version": "recording_access_enforcement_readiness.v1",
        "requester": owner_manifest["requester"],
        "recording": {
            **owner_manifest["recording"],
            "ownership_lookup_ready": owner_lookup_ready,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_bytes_included": False,
            "payload_included": False,
        },
        "flags": flags,
        "decision": {
            "guard_decision_version": owner_manifest["decision"]["guard_decision_version"],
            "tenant_match_for_requested_tenant": owner_manifest["decision"]["tenant_match_for_requested_tenant"],
            "would_allow_if_recording_access_enforced": would_allow,
            "would_reject_if_recording_access_enforced": not would_allow,
            "ready_for_future_enforcement": future_enforcement_ready,
            "active_enforcement": False,
            "shadow_only": True,
            "future_enforcement_requires_static_auth_gate": True,
            "blockers": blockers,
        },
        "safety": {
            **owner_manifest["safety"],
            "runtime_enforcement_changed": False,
            "static_file_serving_changed": False,
            "recording_playback_changed": False,
            "recording_response_changed": False,
            "db_write_performed": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_bytes_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_recording_access_gate_dry_run_manifest(
    context: TenantContext,
    *,
    recording_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a dry-run decision for a future protected recording access gate."""
    readiness = build_recording_access_enforcement_readiness_manifest(
        context,
        recording_found=recording_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **readiness["flags"],
        "recordings.access_gate_dry_run": feature_flags.is_enabled("recordings.access_gate_dry_run"),
    }

    blockers: list[str] = list(readiness["decision"]["blockers"])
    if not flags["recordings.access_gate_dry_run"]:
        blockers.append("recordings.access_gate_dry_run_disabled")

    would_allow = bool(
        flags["recordings.access_gate_dry_run"]
        and readiness["decision"]["ready_for_future_enforcement"]
        and not blockers
    )

    return {
        "manifest_version": "recording_access_gate_dry_run.v1",
        "requester": readiness["requester"],
        "recording": {
            **readiness["recording"],
            "ownership_lookup_ready": readiness["recording"]["ownership_lookup_ready"],
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_bytes_included": False,
            "payload_included": False,
        },
        "gate": {
            "dry_run_only": True,
            "future_gate_required": True,
            "existing_static_mount_preserved": True,
            "protected_route_active": False,
            "would_serve_file_bytes": False,
            "would_proxy_static_file": False,
            "would_redirect_to_static_file": False,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_bytes_included": False,
        },
        "flags": flags,
        "decision": {
            "enforcement_manifest_version": readiness["manifest_version"],
            "would_allow_if_gate_active": would_allow,
            "would_reject_if_gate_active": not would_allow,
            "ready_for_future_gate": would_allow,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            **readiness["safety"],
            "runtime_enforcement_changed": False,
            "static_file_serving_changed": False,
            "recording_playback_changed": False,
            "recording_response_changed": False,
            "protected_recording_route_activated": False,
            "db_write_performed": False,
            "file_bytes_read": False,
            "recording_path_included": False,
            "recording_filename_included": False,
            "recording_url_included": False,
            "storage_path_included": False,
            "recording_bytes_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_access_shadow_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-content shadow manifest for future transcript access isolation."""
    guard = build_tenant_scoped_read_guard_decision(
        context,
        resource_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id or context.requested_tenant_id or context.tenant_id,
    )
    flags = {
        **guard["flags"],
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "transcripts.access_shadow": feature_flags.is_enabled("transcripts.access_shadow"),
    }

    blockers: list[str] = list(guard["decision"]["blockers"])
    if not flags["transcripts.access_shadow"]:
        blockers.append("transcripts.access_shadow_disabled")

    return {
        "manifest_version": "transcript_access_shadow.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": guard["requester"]["requested_tenant_present"],
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "transcript": {
            "found": bool(transcript_found),
            "owner_tenant_present": guard["resource"]["owner_tenant_present"],
            "campaign_id_present": bool(campaign_id_present),
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "flags": flags,
        "decision": {
            "guard_decision_version": guard["decision_version"],
            "tenant_match_for_requested_tenant": guard["tenant"]["tenant_match_for_requested_tenant"],
            "current_requester_allowed_if_enforced": guard["decision"]["current_requester_allowed_if_enforced"],
            "would_reject_current_requester_if_enforced": guard["decision"]["would_reject_current_requester_if_enforced"],
            "active_enforcement": False,
            "shadow_only": True,
            "future_enforcement_requires_endpoint_auth_gate": True,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "db_query_executed": flags["transcripts.access_shadow"],
            "db_write_performed": False,
            "db_lookup_scope": "owner_metadata_only",
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_access_canary_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build an admin-only no-content canary for transcript access decisions."""
    shadow = build_transcript_access_shadow_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **shadow["flags"],
        "tenant.enforcement_readiness": feature_flags.is_enabled("tenant.enforcement_readiness"),
        "transcripts.access_canary": feature_flags.is_enabled("transcripts.access_canary"),
    }

    blockers: list[str] = list(shadow["decision"]["blockers"])
    if not flags["tenant.enforcement_readiness"]:
        blockers.append("tenant.enforcement_readiness_disabled")
    if not flags["transcripts.access_canary"]:
        blockers.append("transcripts.access_canary_disabled")

    return {
        "manifest_version": "transcript_access_canary.v1",
        "requester": shadow["requester"],
        "transcript": {
            **shadow["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "flags": flags,
        "decision": {
            "shadow_manifest_version": shadow["manifest_version"],
            "tenant_match_for_requested_tenant": shadow["decision"]["tenant_match_for_requested_tenant"],
            "current_requester_allowed_if_enforced": shadow["decision"]["current_requester_allowed_if_enforced"],
            "would_reject_current_requester_if_enforced": shadow["decision"]["would_reject_current_requester_if_enforced"],
            "active_enforcement": False,
            "shadow_only": True,
            "admin_canary_only": True,
            "future_enforcement_requires_endpoint_auth_gate": True,
            "blockers": blockers,
        },
        "safety": {
            **shadow["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "db_write_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_access_enforcement_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build the no-content decision model for future transcript access enforcement."""
    shadow = build_transcript_access_shadow_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **shadow["flags"],
        "tenant.enforcement_readiness": feature_flags.is_enabled("tenant.enforcement_readiness"),
        "transcripts.access_enforcement_shadow": feature_flags.is_enabled("transcripts.access_enforcement_shadow"),
    }

    blockers: list[str] = list(shadow["decision"]["blockers"])
    if not flags["tenant.enforcement_readiness"]:
        blockers.append("tenant.enforcement_readiness_disabled")
    if not flags["transcripts.access_enforcement_shadow"]:
        blockers.append("transcripts.access_enforcement_shadow_disabled")

    owner_lookup_ready = bool(
        flags["transcripts.access_shadow"]
        and shadow["transcript"]["found"]
        and shadow["transcript"]["owner_tenant_present"]
    )
    would_allow = bool(shadow["decision"]["current_requester_allowed_if_enforced"])
    future_enforcement_ready = bool(
        flags["auth.enforce_backend"]
        and flags["tenant.scoped_reads"]
        and flags["tenant.enforcement_readiness"]
        and flags["transcripts.access_enforcement_shadow"]
        and owner_lookup_ready
        and would_allow
        and not blockers
    )

    return {
        "manifest_version": "transcript_access_enforcement_readiness.v1",
        "requester": shadow["requester"],
        "transcript": {
            **shadow["transcript"],
            "ownership_lookup_ready": owner_lookup_ready,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "flags": flags,
        "decision": {
            "shadow_manifest_version": shadow["manifest_version"],
            "tenant_match_for_requested_tenant": shadow["decision"]["tenant_match_for_requested_tenant"],
            "would_allow_if_transcript_access_enforced": would_allow,
            "would_reject_if_transcript_access_enforced": not would_allow,
            "ready_for_future_enforcement": future_enforcement_ready,
            "active_enforcement": False,
            "shadow_only": True,
            "future_enforcement_requires_endpoint_auth_gate": True,
            "blockers": blockers,
        },
        "safety": {
            **shadow["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "db_write_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_access_gate_dry_run_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a dry-run decision for a future protected transcript access gate."""
    readiness = build_transcript_access_enforcement_readiness_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **readiness["flags"],
        "transcripts.access_gate_dry_run": feature_flags.is_enabled("transcripts.access_gate_dry_run"),
    }

    blockers: list[str] = list(readiness["decision"]["blockers"])
    if not flags["transcripts.access_gate_dry_run"]:
        blockers.append("transcripts.access_gate_dry_run_disabled")

    would_allow = bool(
        flags["transcripts.access_gate_dry_run"]
        and readiness["decision"]["ready_for_future_enforcement"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_access_gate_dry_run.v1",
        "requester": readiness["requester"],
        "transcript": {
            **readiness["transcript"],
            "ownership_lookup_ready": readiness["transcript"]["ownership_lookup_ready"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "gate": {
            "dry_run_only": True,
            "future_gate_required": True,
            "existing_transcript_endpoint_preserved": True,
            "protected_route_active": False,
            "would_return_transcript_content": False,
            "would_proxy_transcript_response": False,
            "would_modify_transcript_endpoint": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
        },
        "flags": flags,
        "decision": {
            "enforcement_manifest_version": readiness["manifest_version"],
            "would_allow_if_gate_active": would_allow,
            "would_reject_if_gate_active": not would_allow,
            "ready_for_future_gate": would_allow,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            **readiness["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "db_write_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_route_stub_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-payload contract manifest for the future protected transcript route."""
    dry_run = build_transcript_access_gate_dry_run_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **dry_run["flags"],
        "transcripts.protected_route_stub": feature_flags.is_enabled("transcripts.protected_route_stub"),
    }

    blockers: list[str] = list(dry_run["decision"]["blockers"])
    if not flags["transcripts.protected_route_stub"]:
        blockers.append("transcripts.protected_route_stub_disabled")

    contract_route_ready = bool(
        flags["transcripts.protected_route_stub"]
        and dry_run["decision"]["ready_for_future_gate"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_route_stub.v1",
        "requester": dry_run["requester"],
        "transcript": {
            **dry_run["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "route": {
            "contract_stub_only": True,
            "future_route_template": "/api/protected/transcripts/{lead_id}",
            "route_enabled_by_flag": flags["transcripts.protected_route_stub"],
            "requires_verified_identity": True,
            "requires_tenant_scope": True,
            "existing_transcript_endpoint_preserved": True,
            "protected_route_active": False,
            "payload_route_active": False,
            "would_read_transcript_payload": False,
            "would_return_transcript_content": False,
            "would_proxy_legacy_transcript_response": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
        },
        "flags": flags,
        "decision": {
            "gate_dry_run_manifest_version": dry_run["manifest_version"],
            "contract_route_ready": contract_route_ready,
            "would_allow_contract_route": contract_route_ready,
            "would_reject_contract_route": not contract_route_ready,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            **dry_run["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "contract_stub_only": True,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_route_permission_shadow_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-payload permission decision for the future protected transcript route."""
    stub = build_transcript_protected_route_stub_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    guard = build_tenant_scoped_read_guard_decision(
        context,
        resource_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
    )
    flags = {
        **stub["flags"],
        "transcripts.protected_route_permission_shadow": feature_flags.is_enabled(
            "transcripts.protected_route_permission_shadow"
        ),
    }

    blockers: list[str] = list(stub["decision"]["blockers"])
    if not flags["transcripts.protected_route_permission_shadow"]:
        blockers.append("transcripts.protected_route_permission_shadow_disabled")

    permission_allowed = bool(
        flags["transcripts.protected_route_permission_shadow"]
        and stub["decision"]["contract_route_ready"]
        and guard["decision"]["current_requester_allowed_if_enforced"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_route_permission_shadow.v1",
        "requester": {
            **stub["requester"],
            "verified_identity_required": True,
            "tenant_scope_required": True,
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **stub["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "permission": {
            "shadow_only": True,
            "evaluated": flags["transcripts.protected_route_permission_shadow"],
            "guard_decision_version": guard["decision_version"],
            "contract_stub_manifest_version": stub["manifest_version"],
            "owner_lookup_required": True,
            "owner_lookup_ready": stub["transcript"]["ownership_lookup_ready"],
            "verified_identity_required": True,
            "tenant_scope_required": True,
            "tenant_match_for_requested_tenant": guard["tenant"]["tenant_match_for_requested_tenant"],
            "requester_tenant_matches_owner": guard["tenant"]["requester_tenant_matches_owner"],
            "admin_override_allowed": guard["decision"]["admin_override_allowed"],
            "requested_tenant_allowed_if_enforced": guard["decision"]["requested_tenant_allowed_if_enforced"],
            "requester_tenant_allowed_if_enforced": guard["decision"]["requester_tenant_allowed_if_enforced"],
            "would_allow_payload_if_enforced": permission_allowed,
            "would_reject_payload_if_enforced": not permission_allowed,
            "active_enforcement": False,
            "payload_read_allowed": False,
            "payload_return_allowed": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "safety": {
            **stub["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "permission_shadow_only": True,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_response_shape_canary_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a schema-only canary for the future protected transcript response."""
    permission = build_transcript_protected_route_permission_shadow_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **permission["flags"],
        "transcripts.protected_response_shape_canary": feature_flags.is_enabled(
            "transcripts.protected_response_shape_canary"
        ),
    }

    blockers: list[str] = list(permission["permission"]["blockers"])
    if not flags["transcripts.protected_response_shape_canary"]:
        blockers.append("transcripts.protected_response_shape_canary_disabled")

    schema_ready = bool(
        flags["transcripts.protected_response_shape_canary"]
        and permission["permission"]["would_allow_payload_if_enforced"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_response_shape_canary.v1",
        "requester": {
            **permission["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **permission["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "response_shape": {
            "canary_only": True,
            "schema_only": True,
            "future_envelope_version": "protected_transcript_response.v1",
            "status_field_defined": True,
            "access_field_defined": True,
            "metadata_field_defined": True,
            "transcript_field_defined": True,
            "turns_array_defined": True,
            "turn_item_speaker_field_defined": True,
            "turn_item_text_field_defined": True,
            "turn_item_timestamp_field_defined": True,
            "recording_reference_field_defined": True,
            "payload_values_included": False,
            "lead_id_value_included": False,
            "call_result_id_value_included": False,
            "tenant_value_included": False,
            "transcript_content_values_included": False,
            "recording_url_value_included": False,
            "would_return_schema_only": True,
            "would_return_payload": False,
        },
        "permission": {
            **permission["permission"],
            "payload_read_allowed": False,
            "payload_return_allowed": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "permission_manifest_version": permission["manifest_version"],
            "schema_ready_for_future_payload": schema_ready,
            "would_allow_schema_if_enabled": schema_ready,
            "would_reject_schema_if_enabled": not schema_ready,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            **permission["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "response_shape_canary_only": True,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_payload_dry_run_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-read dry-run decision for a future protected transcript payload."""
    shape = build_transcript_protected_response_shape_canary_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **shape["flags"],
        "transcripts.protected_payload_dry_run": feature_flags.is_enabled(
            "transcripts.protected_payload_dry_run"
        ),
    }

    blockers: list[str] = list(shape["decision"]["blockers"])
    if not flags["transcripts.protected_payload_dry_run"]:
        blockers.append("transcripts.protected_payload_dry_run_disabled")

    would_read_payload = bool(
        flags["transcripts.protected_payload_dry_run"]
        and shape["decision"]["schema_ready_for_future_payload"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_payload_dry_run.v1",
        "requester": {
            **shape["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **shape["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "payload_dry_run": {
            "dry_run_only": True,
            "schema_canary_manifest_version": shape["manifest_version"],
            "future_payload_reader_required": True,
            "future_payload_reader_invoked": False,
            "would_read_payload_if_enabled": would_read_payload,
            "would_return_payload_if_enabled": would_read_payload,
            "payload_read_performed": False,
            "payload_return_performed": False,
            "dry_run_blocks_actual_read": True,
            "lead_id_value_included": False,
            "call_result_id_value_included": False,
            "tenant_value_included": False,
            "transcript_content_values_included": False,
            "transcript_turn_values_included": False,
            "recording_url_value_included": False,
        },
        "permission": {
            **shape["permission"],
            "payload_read_allowed": False,
            "payload_return_allowed": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "response_shape_manifest_version": shape["manifest_version"],
            "ready_for_future_payload_read": would_read_payload,
            "would_allow_payload_read_if_live": would_read_payload,
            "would_reject_payload_read_if_live": not would_read_payload,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            **shape["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "payload_dry_run_only": True,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_enforcement_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build the final no-payload readiness gate for protected transcript enforcement."""
    payload = build_transcript_protected_payload_dry_run_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **payload["flags"],
        "transcripts.protected_enforcement_readiness": feature_flags.is_enabled(
            "transcripts.protected_enforcement_readiness"
        ),
    }

    blockers: list[str] = list(payload["decision"]["blockers"])
    if not flags["transcripts.protected_enforcement_readiness"]:
        blockers.append("transcripts.protected_enforcement_readiness_disabled")

    ready_for_live_candidate = bool(
        flags["transcripts.protected_enforcement_readiness"]
        and payload["decision"]["ready_for_future_payload_read"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_enforcement_readiness.v1",
        "requester": {
            **payload["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **payload["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "readiness": {
            "all_prerequisites_ready": ready_for_live_candidate,
            "metadata_owner_lookup_ready": payload["transcript"]["ownership_lookup_ready"],
            "permission_shadow_ready": payload["permission"]["would_allow_payload_if_enforced"],
            "response_shape_canary_ready": bool(
                flags["transcripts.protected_response_shape_canary"]
                and "transcripts.protected_response_shape_canary_disabled" not in blockers
            ),
            "payload_dry_run_ready": payload["decision"]["ready_for_future_payload_read"],
            "requires_separate_live_activation_phase": True,
            "live_payload_route_enabled": False,
            "legacy_transcript_endpoint_preserved": True,
            "active_enforcement": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "payload_dry_run_manifest_version": payload["manifest_version"],
            "ready_for_future_enforcement_candidate": ready_for_live_candidate,
            "would_allow_live_activation_request": False,
            "would_reject_live_activation_request": True,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_rollout_required": True,
            "blockers": blockers,
        },
        "safety": {
            **payload["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_live_activation_plan_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-payload rollout plan for a future protected transcript live activation."""
    readiness = build_transcript_protected_enforcement_readiness_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **readiness["flags"],
        "transcripts.protected_live_activation_plan": feature_flags.is_enabled(
            "transcripts.protected_live_activation_plan"
        ),
    }

    blockers: list[str] = list(readiness["decision"]["blockers"])
    if not flags["transcripts.protected_live_activation_plan"]:
        blockers.append("transcripts.protected_live_activation_plan_disabled")

    activation_plan_ready = bool(
        flags["transcripts.protected_live_activation_plan"]
        and readiness["decision"]["ready_for_future_enforcement_candidate"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_live_activation_plan.v1",
        "requester": {
            **readiness["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **readiness["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "activation_plan": {
            "plan_only": True,
            "requires_manual_approval": True,
            "requires_separate_live_activation_phase": True,
            "required_readiness_manifest_version": readiness["manifest_version"],
            "legacy_transcript_endpoint_preserved": True,
            "protected_route_contract_ready": readiness["readiness"]["all_prerequisites_ready"],
            "live_payload_route_enabled": False,
            "would_enable_payload_route": False,
            "would_disable_legacy_route": False,
            "would_read_payload": False,
            "would_return_payload": False,
            "activation_sequence": (
                "enable_flag_in_staging",
                "run_tenant_leak_suite",
                "run_demo_call_regression",
                "run_campaign_result_regression",
                "canary_admin_tenant",
                "canary_single_client_tenant",
                "monitor_and_rollback_if_needed",
            ),
            "required_kill_switches": (
                "transcripts.protected_live_activation_plan",
                "transcripts.protected_enforcement_readiness",
                "transcripts.protected_payload_dry_run",
                "transcripts.protected_route_stub",
            ),
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "readiness_manifest_version": readiness["manifest_version"],
            "activation_plan_ready": activation_plan_ready,
            "would_allow_live_activation_now": False,
            "would_reject_live_activation_now": True,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_rollout_required": True,
            "blockers": blockers,
        },
        "safety": {
            **readiness["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "live_payload_route_enabled": False,
            "activation_plan_only": True,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_protected_rollback_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-payload rollback readiness manifest for protected transcript rollout."""
    activation = build_transcript_protected_live_activation_plan_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **activation["flags"],
        "transcripts.protected_rollback_readiness": feature_flags.is_enabled(
            "transcripts.protected_rollback_readiness"
        ),
    }

    blockers: list[str] = list(activation["decision"]["blockers"])
    if not flags["transcripts.protected_rollback_readiness"]:
        blockers.append("transcripts.protected_rollback_readiness_disabled")

    rollback_ready = bool(
        flags["transcripts.protected_rollback_readiness"]
        and activation["decision"]["activation_plan_ready"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_protected_rollback_readiness.v1",
        "requester": {
            **activation["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **activation["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "rollback": {
            "readiness_only": True,
            "rollback_action_performed": False,
            "legacy_transcript_endpoint_preserved": True,
            "live_payload_route_enabled": False,
            "can_disable_future_live_route_by_flag": True,
            "kill_switch_order": (
                "transcripts.protected_payload_live",
                "transcripts.protected_live_activation_plan",
                "transcripts.protected_enforcement_readiness",
                "transcripts.protected_payload_dry_run",
            ),
            "post_rollback_checks": (
                "legacy_transcript_endpoint_returns_existing_shape",
                "protected_route_payload_disabled",
                "tenant_leak_suite_passes",
                "demo_call_transcripts_unchanged",
                "campaign_results_unchanged",
            ),
            "requires_manual_rollback_approval": True,
            "would_modify_flags": False,
            "would_modify_routes": False,
            "would_read_payload": False,
            "would_return_payload": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "activation_plan_manifest_version": activation["manifest_version"],
            "rollback_ready_for_future_live_activation": rollback_ready,
            "would_execute_rollback_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_rollback_required": True,
            "blockers": blockers,
        },
        "safety": {
            **activation["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "live_payload_route_enabled": False,
            "rollback_action_performed": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_transcript_frontend_migration_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
    campaign_id_present: bool = False,
) -> dict[str, Any]:
    """Build a no-payload readiness manifest for migrating transcript UI consumers."""
    rollback = build_transcript_protected_rollback_readiness_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        campaign_id_present=campaign_id_present,
    )
    flags = {
        **rollback["flags"],
        "transcripts.frontend_migration_readiness": feature_flags.is_enabled(
            "transcripts.frontend_migration_readiness"
        ),
    }

    blockers: list[str] = list(rollback["decision"]["blockers"])
    if not flags["transcripts.frontend_migration_readiness"]:
        blockers.append("transcripts.frontend_migration_readiness_disabled")

    frontend_ready = bool(
        flags["transcripts.frontend_migration_readiness"]
        and rollback["decision"]["rollback_ready_for_future_live_activation"]
        and not blockers
    )

    return {
        "manifest_version": "transcript_frontend_migration_readiness.v1",
        "requester": {
            **rollback["requester"],
            "tenant_id_included": False,
            "requested_tenant_id_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "transcript": {
            **rollback["transcript"],
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "payload_included": False,
        },
        "frontend_migration": {
            "readiness_only": True,
            "frontend_code_changed": False,
            "legacy_transcript_endpoint_preserved": True,
            "future_protected_route_template": "/api/protected/transcripts/{lead_id}",
            "future_route_payload_live": False,
            "results_page_can_migrate_later": frontend_ready,
            "transcript_ui_can_migrate_later": frontend_ready,
            "requires_dual_read_canary_before_switch": True,
            "requires_feature_flagged_frontend_switch": True,
            "requires_user_role_gate": True,
            "requires_tenant_scope_headers": True,
            "requires_legacy_fallback": True,
            "migration_sequence": (
                "add_frontend_feature_flag",
                "dual_read_shadow_without_render_change",
                "compare_legacy_and_protected_response_shape",
                "enable_admin_tenant_canary",
                "enable_single_client_canary",
                "keep_legacy_fallback",
            ),
            "lead_id_value_included": False,
            "call_result_id_value_included": False,
            "tenant_value_included": False,
            "transcript_content_values_included": False,
            "recording_url_value_included": False,
            "blockers": blockers,
        },
        "flags": flags,
        "decision": {
            "rollback_manifest_version": rollback["manifest_version"],
            "frontend_migration_ready": frontend_ready,
            "would_switch_frontend_now": False,
            "would_change_transcript_ui_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_frontend_migration_required": True,
            "blockers": blockers,
        },
        "safety": {
            **rollback["safety"],
            "runtime_enforcement_changed": False,
            "transcript_response_changed": False,
            "protected_transcript_route_activated": False,
            "frontend_code_changed": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "recording_url_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_tenant_leak_regression_matrix_manifest(
    context: TenantContext,
    *,
    scenarios: list[dict[str, Any]],
    requested_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build a no-payload leak regression matrix across tenant-scoped surfaces."""
    flags = {
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "tenant.scoped_read_guard_shadow": feature_flags.is_enabled("tenant.scoped_read_guard_shadow"),
        "tenant.leak_regression_matrix": feature_flags.is_enabled("tenant.leak_regression_matrix"),
    }

    matrix: list[dict[str, Any]] = []
    matrix_blockers: list[str] = []
    cross_tenant_leak_detected = False
    for index, scenario in enumerate(scenarios):
        guard = build_tenant_scoped_read_guard_decision(
            context,
            resource_found=bool(scenario.get("resource_found")),
            owner_tenant_id=scenario.get("owner_tenant_id"),
            requested_tenant_id=requested_tenant_id or scenario.get("requested_tenant_id"),
        )
        tenant_match = guard["tenant"]["tenant_match_for_requested_tenant"]
        requested_tenant_allowed = guard["decision"]["requested_tenant_allowed_if_enforced"]
        row_cross_tenant_leak = bool(tenant_match is False and requested_tenant_allowed)
        cross_tenant_leak_detected = cross_tenant_leak_detected or row_cross_tenant_leak
        row_blockers = list(guard["decision"]["blockers"])
        if not flags["tenant.leak_regression_matrix"]:
            row_blockers.append("tenant.leak_regression_matrix_disabled")
        matrix_blockers.extend(row_blockers)
        matrix.append(
            {
                "index": index,
                "resource_type": str(scenario.get("resource_type") or "unknown"),
                "resource_label": str(scenario.get("resource_label") or "Resource"),
                "resource_found": guard["resource"]["found"],
                "owner_tenant_present": guard["resource"]["owner_tenant_present"],
                "requested_tenant_present": guard["requester"]["requested_tenant_present"],
                "tenant_match_for_requested_tenant": tenant_match,
                "requested_tenant_allowed_if_enforced": requested_tenant_allowed,
                "current_requester_allowed_if_enforced": guard["decision"]["current_requester_allowed_if_enforced"],
                "cross_tenant_leak_detected": row_cross_tenant_leak,
                "resource_id_included": False,
                "owner_tenant_included": False,
                "requester_tenant_included": False,
                "requested_tenant_included": False,
                "payload_included": False,
                "transcript_content_included": False,
                "recording_url_included": False,
                "phone_number_included": False,
                "blockers": row_blockers,
            }
        )

    unique_blockers = sorted(set(matrix_blockers))
    matrix_ready = bool(
        flags["tenant.leak_regression_matrix"]
        and bool(matrix)
        and not cross_tenant_leak_detected
        and "tenant_mismatch_for_requested_tenant" not in unique_blockers
    )

    return {
        "manifest_version": "tenant_leak_regression_matrix.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": bool(requested_tenant_id or context.requested_tenant_id),
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "matrix": matrix,
        "flags": flags,
        "decision": {
            "matrix_ready": matrix_ready,
            "scenario_count": len(matrix),
            "cross_tenant_leak_detected": cross_tenant_leak_detected,
            "active_enforcement": False,
            "shadow_only": True,
            "blockers": unique_blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "db_write_performed": False,
            "db_lookup_scope": "owner_metadata_only",
            "resource_payload_returned": False,
            "resource_id_included": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "tenant_data_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "phone_number_included": False,
            "cross_tenant_data_included": False,
        },
    }


def build_result_asset_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-payload readiness aggregate for result-related assets."""
    requested_scope = requested_tenant_id or context.requested_tenant_id or context.tenant_id
    transcript = build_transcript_frontend_migration_readiness_manifest(
        context,
        transcript_found=transcript_found,
        owner_tenant_id=transcript_owner_tenant_id,
        requested_tenant_id=requested_scope,
        campaign_id_present=transcript_campaign_id_present,
    )
    recording = build_recording_access_gate_dry_run_manifest(
        context,
        recording_found=recording_found,
        owner_tenant_id=recording_owner_tenant_id,
        requested_tenant_id=requested_scope,
        campaign_id_present=recording_campaign_id_present,
    )

    scenarios: list[dict[str, Any]] = [
        {
            "resource_type": "transcript",
            "resource_label": "Transcript",
            "resource_found": transcript_found,
            "owner_tenant_id": transcript_owner_tenant_id,
            "requested_tenant_id": requested_scope,
        },
        {
            "resource_type": "call_result",
            "resource_label": "Call result",
            "resource_found": transcript_found,
            "owner_tenant_id": transcript_owner_tenant_id,
            "requested_tenant_id": requested_scope,
        },
    ]
    if recording_required or recording_found:
        scenarios.append(
            {
                "resource_type": "recording_asset",
                "resource_label": "Recording asset",
                "resource_found": recording_found,
                "owner_tenant_id": recording_owner_tenant_id,
                "requested_tenant_id": requested_scope,
            }
        )
    if campaign_required or campaign_found:
        scenarios.append(
            {
                "resource_type": "campaign",
                "resource_label": "Campaign",
                "resource_found": campaign_found,
                "owner_tenant_id": campaign_owner_tenant_id,
                "requested_tenant_id": requested_scope,
            }
        )

    leak_matrix = build_tenant_leak_regression_matrix_manifest(
        context,
        scenarios=scenarios,
        requested_tenant_id=requested_scope,
    )
    flags = {
        **transcript["flags"],
        **recording["flags"],
        **leak_matrix["flags"],
        "tenant.result_asset_readiness": feature_flags.is_enabled("tenant.result_asset_readiness"),
    }

    blockers: list[str] = list(transcript["decision"]["blockers"])
    if recording_required or recording_found:
        blockers.extend(recording["decision"]["blockers"])
    blockers.extend(leak_matrix["decision"]["blockers"])
    if campaign_required and not campaign_found:
        blockers.append("campaign_not_found")
    if not flags["tenant.result_asset_readiness"]:
        blockers.append("tenant.result_asset_readiness_disabled")
    unique_blockers = sorted(set(blockers))

    transcript_ready = bool(transcript["decision"]["frontend_migration_ready"])
    recording_checked = bool(recording_required or recording_found)
    recording_ready = bool(
        not recording_checked or recording["decision"]["ready_for_future_gate"]
    )
    campaign_checked = bool(campaign_required or campaign_found)
    campaign_ready = bool(not campaign_required or campaign_found)
    leak_matrix_ready = bool(leak_matrix["decision"]["matrix_ready"])
    readiness_ready = bool(
        flags["tenant.result_asset_readiness"]
        and transcript_ready
        and recording_ready
        and campaign_ready
        and leak_matrix_ready
        and not unique_blockers
    )

    return {
        "manifest_version": "result_asset_readiness.v1",
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": bool(requested_scope),
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "assets": {
            "readiness_only": True,
            "call_result_checked": True,
            "transcript_checked": True,
            "recording_checked": recording_checked,
            "campaign_checked": campaign_checked,
            "transcript_ready": transcript_ready,
            "recording_ready": recording_ready,
            "campaign_ready": campaign_ready,
            "leak_matrix_ready": leak_matrix_ready,
            "legacy_results_endpoint_preserved": True,
            "legacy_transcript_endpoint_preserved": True,
            "static_recording_mount_preserved": True,
            "protected_transcript_route_required": True,
            "protected_recording_route_required": True,
            "future_frontend_switch_required": True,
            "manual_rollout_required": True,
            "payloads_included": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "transcript_content_included": False,
            "recording_bytes_included": False,
            "owner_tenant_included": False,
            "requested_tenant_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "transcript_frontend_manifest_version": transcript["manifest_version"],
            "recording_gate_manifest_version": recording["manifest_version"],
            "leak_matrix_manifest_version": leak_matrix["manifest_version"],
            "transcript_ready": transcript_ready,
            "recording_ready": recording_ready,
            "campaign_ready": campaign_ready,
            "leak_matrix_ready": leak_matrix_ready,
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "result_asset_readiness_ready": readiness_ready,
            "would_change_results_endpoint_now": False,
            "would_change_transcript_endpoint_now": False,
            "would_change_recording_serving_now": False,
            "would_switch_frontend_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_rollout_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "static_file_serving_changed": False,
            "recording_playback_changed": False,
            "frontend_code_changed": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "transcript_turn_count_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "requested_tenant_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_final_rollout_report_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a final no-payload rollout readiness report for guarded result assets."""
    result_assets = build_result_asset_readiness_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **result_assets["flags"],
        "tenant.final_rollout_report": feature_flags.is_enabled("tenant.final_rollout_report"),
    }
    blockers: list[str] = list(result_assets["decision"]["blockers"])
    if not flags["tenant.final_rollout_report"]:
        blockers.append("tenant.final_rollout_report_disabled")
    unique_blockers = sorted(set(blockers))
    report_ready = bool(
        flags["tenant.final_rollout_report"]
        and result_assets["decision"]["result_asset_readiness_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "final_rollout_report_readiness.v1",
        "requester": {
            **result_assets["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "report": {
            "readiness_only": True,
            "rollout_report_ready": report_ready,
            "phase_55_tenant_leak_matrix_required": True,
            "phase_56_result_asset_readiness_required": True,
            "result_asset_readiness_ready": result_assets["decision"]["result_asset_readiness_ready"],
            "runtime_contracts_preserved": True,
            "audio_contracts_preserved": True,
            "websocket_contracts_preserved": True,
            "legacy_results_endpoint_preserved": result_assets["assets"]["legacy_results_endpoint_preserved"],
            "legacy_transcript_endpoint_preserved": result_assets["assets"]["legacy_transcript_endpoint_preserved"],
            "static_recording_mount_preserved": result_assets["assets"]["static_recording_mount_preserved"],
            "protected_transcript_route_live": False,
            "protected_recording_route_live": False,
            "frontend_switch_live": False,
            "manual_go_live_approval_required": True,
            "rollback_plan_required": True,
            "canary_sequence": (
                "admin_only_readiness",
                "single_tenant_dual_read",
                "frontend_flag_canary",
                "protected_route_canary",
                "manual_go_live_approval",
                "legacy_fallback_retained",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "result_asset_manifest_version": result_assets["manifest_version"],
            "result_asset_readiness_ready": result_assets["decision"]["result_asset_readiness_ready"],
            "transcript_ready": result_assets["assets"]["transcript_ready"],
            "recording_ready": result_assets["assets"]["recording_ready"],
            "campaign_ready": result_assets["assets"]["campaign_ready"],
            "leak_matrix_ready": result_assets["assets"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "final_rollout_report_ready": report_ready,
            "would_activate_live_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_approval_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **result_assets["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_rollout_approval_packet_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-action approval packet for a future guarded rollout."""
    rollout_report = build_final_rollout_report_readiness_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **rollout_report["flags"],
        "tenant.rollout_approval_packet": feature_flags.is_enabled(
            "tenant.rollout_approval_packet"
        ),
    }
    blockers: list[str] = list(rollout_report["decision"]["blockers"])
    if not flags["tenant.rollout_approval_packet"]:
        blockers.append("tenant.rollout_approval_packet_disabled")
    unique_blockers = sorted(set(blockers))
    approval_ready = bool(
        flags["tenant.rollout_approval_packet"]
        and rollout_report["decision"]["final_rollout_report_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "rollout_approval_packet.v1",
        "requester": {
            **rollout_report["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "approval_packet": {
            "readiness_only": True,
            "approval_packet_ready": approval_ready,
            "final_rollout_report_ready": rollout_report["decision"]["final_rollout_report_ready"],
            "manual_go_live_approval_required": True,
            "approval_record_created": False,
            "approval_signature_captured": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "live_routes_activated": False,
            "frontend_switch_activated": False,
            "required_approvals": (
                "platform_owner",
                "tenant_owner",
                "operations_owner",
            ),
            "required_evidence": (
                "full_backend_regression_passed",
                "tenant_leak_matrix_passed",
                "result_asset_readiness_passed",
                "rollback_drill_available",
            ),
            "required_kill_switches": (
                "tenant.final_rollout_report",
                "tenant.result_asset_readiness",
                "tenant.leak_regression_matrix",
                "transcripts.protected_live_activation_plan",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "final_rollout_report_manifest_version": rollout_report["manifest_version"],
            "final_rollout_report_ready": rollout_report["decision"]["final_rollout_report_ready"],
            "result_asset_readiness_ready": rollout_report["components"]["result_asset_readiness_ready"],
            "transcript_ready": rollout_report["components"]["transcript_ready"],
            "recording_ready": rollout_report["components"]["recording_ready"],
            "campaign_ready": rollout_report["components"]["campaign_ready"],
            "leak_matrix_ready": rollout_report["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "rollout_approval_packet_ready": approval_ready,
            "would_record_approval_now": False,
            "would_modify_flags_now": False,
            "would_activate_live_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_approval_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **rollout_report["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_rollout_canary_plan_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-action canary rollout plan for future tenant-safe activation."""
    approval = build_rollout_approval_packet_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **approval["flags"],
        "tenant.rollout_canary_plan": feature_flags.is_enabled("tenant.rollout_canary_plan"),
    }
    blockers: list[str] = list(approval["decision"]["blockers"])
    if not flags["tenant.rollout_canary_plan"]:
        blockers.append("tenant.rollout_canary_plan_disabled")
    unique_blockers = sorted(set(blockers))
    canary_plan_ready = bool(
        flags["tenant.rollout_canary_plan"]
        and approval["decision"]["rollout_approval_packet_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "rollout_canary_plan.v1",
        "requester": {
            **approval["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "canary_plan": {
            "plan_only": True,
            "canary_plan_ready": canary_plan_ready,
            "approval_packet_ready": approval["decision"]["rollout_approval_packet_ready"],
            "single_tenant_canary_required": True,
            "single_campaign_canary_required": True,
            "demo_call_canary_required": True,
            "legacy_fallback_required": True,
            "automatic_activation_enabled": False,
            "feature_flags_modified": False,
            "live_routes_activated": False,
            "frontend_switch_activated": False,
            "traffic_shift_percent": 0,
            "max_initial_tenant_count": 1,
            "minimum_observation_minutes": 30,
            "canary_sequence": (
                "admin_only_plan_review",
                "demo_call_observation",
                "single_tenant_dual_read",
                "single_campaign_result_asset_check",
                "protected_route_shadow_compare",
                "manual_promote_or_rollback_decision",
            ),
            "abort_thresholds": {
                "tenant_leak_count": 0,
                "transcript_shape_mismatch_count": 0,
                "recording_gate_mismatch_count": 0,
                "websocket_contract_errors": 0,
                "audio_runtime_errors": 0,
            },
            "required_kill_switches": (
                "tenant.rollout_canary_plan",
                "tenant.rollout_approval_packet",
                "tenant.final_rollout_report",
                "tenant.result_asset_readiness",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "approval_packet_manifest_version": approval["manifest_version"],
            "approval_packet_ready": approval["decision"]["rollout_approval_packet_ready"],
            "final_rollout_report_ready": approval["components"]["final_rollout_report_ready"],
            "result_asset_readiness_ready": approval["components"]["result_asset_readiness_ready"],
            "leak_matrix_ready": approval["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "rollout_canary_plan_ready": canary_plan_ready,
            "would_start_canary_now": False,
            "would_modify_flags_now": False,
            "would_activate_live_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_canary_start_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **approval["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "canary_started": False,
            "traffic_shifted": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_rollback_drill_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-action rollback drill readiness manifest."""
    canary = build_rollout_canary_plan_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **canary["flags"],
        "tenant.rollback_drill_readiness": feature_flags.is_enabled(
            "tenant.rollback_drill_readiness"
        ),
    }
    blockers: list[str] = list(canary["decision"]["blockers"])
    if not flags["tenant.rollback_drill_readiness"]:
        blockers.append("tenant.rollback_drill_readiness_disabled")
    unique_blockers = sorted(set(blockers))
    drill_ready = bool(
        flags["tenant.rollback_drill_readiness"]
        and canary["decision"]["rollout_canary_plan_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "rollback_drill_readiness.v1",
        "requester": {
            **canary["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "rollback_drill": {
            "readiness_only": True,
            "rollback_drill_ready": drill_ready,
            "canary_plan_ready": canary["decision"]["rollout_canary_plan_ready"],
            "rollback_action_performed": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "frontend_modified": False,
            "traffic_shifted": False,
            "db_write_performed": False,
            "requires_manual_rollback_approval": True,
            "kill_switch_order": (
                "tenant.rollback_drill_readiness",
                "tenant.rollout_canary_plan",
                "tenant.rollout_approval_packet",
                "tenant.final_rollout_report",
                "tenant.result_asset_readiness",
                "tenant.leak_regression_matrix",
            ),
            "post_rollback_checks": (
                "legacy_results_endpoint_available",
                "legacy_transcript_endpoint_available",
                "static_recording_mount_available",
                "demo_voice_call_smoke",
                "dashboard_websocket_smoke",
                "tenant_leak_matrix_recheck",
            ),
            "recovery_sequence": (
                "disable_canary_flags",
                "confirm_legacy_fallback",
                "verify_no_live_payload_route",
                "rerun_backend_regression",
                "notify_operations_owner",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "canary_plan_manifest_version": canary["manifest_version"],
            "canary_plan_ready": canary["decision"]["rollout_canary_plan_ready"],
            "approval_packet_ready": canary["components"]["approval_packet_ready"],
            "final_rollout_report_ready": canary["components"]["final_rollout_report_ready"],
            "result_asset_readiness_ready": canary["components"]["result_asset_readiness_ready"],
            "leak_matrix_ready": canary["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "rollback_drill_readiness_ready": drill_ready,
            "would_execute_rollback_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_rollback_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **canary["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "canary_started": False,
            "traffic_shifted": False,
            "rollback_action_performed": False,
            "routes_modified": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_rollout_evidence_bundle_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-payload evidence bundle for future rollout review."""
    rollback = build_rollback_drill_readiness_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **rollback["flags"],
        "tenant.rollout_evidence_bundle": feature_flags.is_enabled(
            "tenant.rollout_evidence_bundle"
        ),
    }
    blockers: list[str] = list(rollback["decision"]["blockers"])
    if not flags["tenant.rollout_evidence_bundle"]:
        blockers.append("tenant.rollout_evidence_bundle_disabled")
    unique_blockers = sorted(set(blockers))
    evidence_ready = bool(
        flags["tenant.rollout_evidence_bundle"]
        and rollback["decision"]["rollback_drill_readiness_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "rollout_evidence_bundle.v1",
        "requester": {
            **rollback["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "evidence_bundle": {
            "readiness_only": True,
            "evidence_bundle_ready": evidence_ready,
            "rollback_drill_ready": rollback["decision"]["rollback_drill_readiness_ready"],
            "evidence_record_created": False,
            "evidence_persisted": False,
            "live_data_collected": False,
            "metrics_sampled": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "required_evidence_items": (
                "full_backend_regression_passed",
                "focused_tenant_security_suite_passed",
                "rollout_approval_packet_ready",
                "canary_plan_ready",
                "rollback_drill_ready",
                "legacy_fallback_verified",
            ),
            "covered_surfaces": (
                "tenant_leak_matrix",
                "result_assets",
                "protected_transcript_readiness",
                "recording_gate_readiness",
                "frontend_migration_readiness",
                "rollback_drill",
            ),
            "review_sequence": (
                "collect_test_run_ids_externally",
                "attach_operator_review_externally",
                "verify_no_payload_exposure",
                "confirm_kill_switches",
                "hold_manual_go_no_go_review",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "rollback_drill_manifest_version": rollback["manifest_version"],
            "rollback_drill_ready": rollback["decision"]["rollback_drill_readiness_ready"],
            "canary_plan_ready": rollback["components"]["canary_plan_ready"],
            "approval_packet_ready": rollback["components"]["approval_packet_ready"],
            "final_rollout_report_ready": rollback["components"]["final_rollout_report_ready"],
            "result_asset_readiness_ready": rollback["components"]["result_asset_readiness_ready"],
            "leak_matrix_ready": rollback["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "rollout_evidence_bundle_ready": evidence_ready,
            "would_create_evidence_record_now": False,
            "would_collect_live_metrics_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_review_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **rollback["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
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
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_canary_observation_checklist_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a passive observation checklist for a future canary."""
    evidence = build_rollout_evidence_bundle_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **evidence["flags"],
        "tenant.canary_observation_checklist": feature_flags.is_enabled(
            "tenant.canary_observation_checklist"
        ),
    }
    blockers: list[str] = list(evidence["decision"]["blockers"])
    if not flags["tenant.canary_observation_checklist"]:
        blockers.append("tenant.canary_observation_checklist_disabled")
    unique_blockers = sorted(set(blockers))
    checklist_ready = bool(
        flags["tenant.canary_observation_checklist"]
        and evidence["decision"]["rollout_evidence_bundle_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "canary_observation_checklist.v1",
        "requester": {
            **evidence["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "observation_checklist": {
            "checklist_only": True,
            "observation_checklist_ready": checklist_ready,
            "evidence_bundle_ready": evidence["decision"]["rollout_evidence_bundle_ready"],
            "observation_record_created": False,
            "metrics_sampled": False,
            "live_data_collected": False,
            "canary_started": False,
            "traffic_shifted": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "required_observation_windows": (
                "first_demo_call",
                "first_single_tenant_dual_read",
                "first_campaign_result_asset_check",
                "thirty_minute_stability_window",
            ),
            "watch_metrics": (
                "tenant_leak_count",
                "auth_rejection_count",
                "transcript_shape_mismatch_count",
                "recording_gate_mismatch_count",
                "dashboard_websocket_errors",
                "audio_runtime_errors",
                "campaign_result_persistence_errors",
            ),
            "abort_thresholds": {
                "tenant_leak_count": 0,
                "cross_tenant_data_returned": 0,
                "protected_route_payload_mismatch": 0,
                "recording_gate_mismatch_count": 0,
                "audio_runtime_errors": 0,
                "websocket_contract_errors": 0,
            },
            "manual_review_points": (
                "operator_confirms_no_client_data_exposure",
                "operator_confirms_legacy_fallback_available",
                "operator_confirms_no_demo_call_regression",
                "operator_confirms_no_campaign_result_regression",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "evidence_bundle_manifest_version": evidence["manifest_version"],
            "evidence_bundle_ready": evidence["decision"]["rollout_evidence_bundle_ready"],
            "rollback_drill_ready": evidence["components"]["rollback_drill_ready"],
            "canary_plan_ready": evidence["components"]["canary_plan_ready"],
            "approval_packet_ready": evidence["components"]["approval_packet_ready"],
            "leak_matrix_ready": evidence["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "canary_observation_checklist_ready": checklist_ready,
            "would_create_observation_record_now": False,
            "would_sample_metrics_now": False,
            "would_start_canary_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_observation_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **evidence["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
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
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_go_no_go_gate_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a report-only go/no-go gate for future production activation."""
    checklist = build_canary_observation_checklist_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **checklist["flags"],
        "tenant.production_go_no_go_gate": feature_flags.is_enabled(
            "tenant.production_go_no_go_gate"
        ),
    }
    blockers: list[str] = list(checklist["decision"]["blockers"])
    if not flags["tenant.production_go_no_go_gate"]:
        blockers.append("tenant.production_go_no_go_gate_disabled")
    unique_blockers = sorted(set(blockers))
    gate_ready = bool(
        flags["tenant.production_go_no_go_gate"]
        and checklist["decision"]["canary_observation_checklist_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "production_go_no_go_gate.v1",
        "requester": {
            **checklist["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "go_no_go_gate": {
            "readiness_only": True,
            "production_gate_ready": gate_ready,
            "canary_observation_checklist_ready": checklist["decision"]["canary_observation_checklist_ready"],
            "decision_record_created": False,
            "go_decision_recorded": False,
            "no_go_decision_recorded": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "frontend_modified": False,
            "traffic_shifted": False,
            "final_required_signoffs": (
                "platform_owner",
                "security_owner",
                "operations_owner",
                "tenant_success_owner",
            ),
            "hard_stop_conditions": (
                "any_tenant_leak_detected",
                "any_cross_tenant_payload_returned",
                "any_audio_runtime_regression",
                "any_websocket_contract_regression",
                "any_campaign_result_persistence_regression",
            ),
            "go_live_prerequisites": (
                "evidence_bundle_reviewed",
                "canary_observation_checklist_reviewed",
                "rollback_drill_reviewed",
                "legacy_fallback_confirmed",
                "manual_approval_recorded_externally",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "canary_observation_manifest_version": checklist["manifest_version"],
            "canary_observation_checklist_ready": checklist["decision"]["canary_observation_checklist_ready"],
            "evidence_bundle_ready": checklist["components"]["evidence_bundle_ready"],
            "rollback_drill_ready": checklist["components"]["rollback_drill_ready"],
            "canary_plan_ready": checklist["components"]["canary_plan_ready"],
            "approval_packet_ready": checklist["components"]["approval_packet_ready"],
            "leak_matrix_ready": checklist["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_go_no_go_gate_ready": gate_ready,
            "would_record_go_decision_now": False,
            "would_record_no_go_decision_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_go_no_go_review_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **checklist["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
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
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_activation_contract_stub_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a disabled contract stub for a future production activation request."""
    gate = build_production_go_no_go_gate_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **gate["flags"],
        "tenant.production_activation_contract_stub": feature_flags.is_enabled(
            "tenant.production_activation_contract_stub"
        ),
    }
    blockers: list[str] = list(gate["decision"]["blockers"])
    if not flags["tenant.production_activation_contract_stub"]:
        blockers.append("tenant.production_activation_contract_stub_disabled")
    unique_blockers = sorted(set(blockers))
    contract_ready = bool(
        flags["tenant.production_activation_contract_stub"]
        and gate["decision"]["production_go_no_go_gate_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "production_activation_contract_stub.v1",
        "requester": {
            **gate["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "activation_contract": {
            "contract_stub_only": True,
            "contract_stub_ready": contract_ready,
            "production_go_no_go_gate_ready": gate["decision"]["production_go_no_go_gate_ready"],
            "future_activation_route": "/api/tenant/production-activation/execute",
            "future_activation_route_live": False,
            "activation_request_recorded": False,
            "activation_executed": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "frontend_modified": False,
            "traffic_shifted": False,
            "required_request_fields": (
                "change_ticket_reference",
                "operator_approval_reference",
                "tenant_canary_reference",
                "rollback_plan_reference",
            ),
            "future_response_fields": (
                "status",
                "activation_id",
                "rollback_token",
                "legacy_fallback_status",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "production_go_no_go_manifest_version": gate["manifest_version"],
            "production_go_no_go_gate_ready": gate["decision"]["production_go_no_go_gate_ready"],
            "canary_observation_checklist_ready": gate["components"]["canary_observation_checklist_ready"],
            "evidence_bundle_ready": gate["components"]["evidence_bundle_ready"],
            "rollback_drill_ready": gate["components"]["rollback_drill_ready"],
            "leak_matrix_ready": gate["components"]["leak_matrix_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_activation_contract_stub_ready": contract_ready,
            "would_accept_activation_request_now": False,
            "would_record_activation_request_now": False,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_activation_approval_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **gate["safety"],
            "runtime_enforcement_changed": False,
            "audio_runtime_changed": False,
            "websocket_contract_changed": False,
            "campaign_runtime_changed": False,
            "results_endpoint_changed": False,
            "transcript_response_changed": False,
            "recording_response_changed": False,
            "frontend_code_changed": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "canary_started": False,
            "traffic_shifted": False,
            "rollback_action_performed": False,
            "routes_modified": False,
            "decision_record_created": False,
            "activation_request_recorded": False,
            "production_activation_started": False,
            "live_data_collected": False,
            "metrics_sampled": False,
            "protected_transcript_route_activated": False,
            "protected_recording_route_activated": False,
            "live_payload_route_enabled": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "file_bytes_read": False,
            "resource_payload_returned": False,
            "lead_id_included": False,
            "call_result_id_included": False,
            "campaign_id_included": False,
            "recording_url_included": False,
            "recording_bytes_returned": False,
            "transcript_content_included": False,
            "transcript_content_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_activation_permission_shadow_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-action permission shadow for future production activation."""
    contract = build_production_activation_contract_stub_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **contract["flags"],
        "tenant.production_activation_permission_shadow": feature_flags.is_enabled(
            "tenant.production_activation_permission_shadow"
        ),
    }
    blockers: list[str] = list(contract["decision"]["blockers"])
    if not context.is_verified:
        blockers.append("verified_backend_identity_required")
    if not context.is_admin:
        blockers.append("admin_context_required")
    if not flags["tenant.production_activation_permission_shadow"]:
        blockers.append("tenant.production_activation_permission_shadow_disabled")
    unique_blockers = sorted(set(blockers))
    permission_ready = bool(
        flags["tenant.production_activation_permission_shadow"]
        and contract["decision"]["production_activation_contract_stub_ready"]
        and context.is_verified
        and context.is_admin
        and not unique_blockers
    )

    return {
        "manifest_version": "production_activation_permission_shadow.v1",
        "requester": {
            **contract["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "permission_shadow": {
            "shadow_only": True,
            "permission_shadow_ready": permission_ready,
            "activation_contract_ready": contract["decision"]["production_activation_contract_stub_ready"],
            "requester_verified": context.is_verified,
            "requester_admin": context.is_admin,
            "would_allow_activation_request_if_enforced": permission_ready,
            "would_record_permission_decision": False,
            "activation_request_recorded": False,
            "activation_executed": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "activation_contract_manifest_version": contract["manifest_version"],
            "activation_contract_ready": contract["decision"]["production_activation_contract_stub_ready"],
            "production_go_no_go_gate_ready": contract["components"]["production_go_no_go_gate_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_activation_permission_shadow_ready": permission_ready,
            "would_accept_activation_request_now": False,
            "would_record_activation_request_now": False,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_activation_approval_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **contract["safety"],
            "runtime_enforcement_changed": False,
            "approval_state_changed": False,
            "feature_flags_modified": False,
            "permission_decision_recorded": False,
            "activation_request_recorded": False,
            "production_activation_started": False,
            "routes_modified": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_activation_payload_dry_run_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-read/no-write dry run for future activation payload shape."""
    permission = build_production_activation_permission_shadow_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **permission["flags"],
        "tenant.production_activation_payload_dry_run": feature_flags.is_enabled(
            "tenant.production_activation_payload_dry_run"
        ),
    }
    blockers: list[str] = list(permission["decision"]["blockers"])
    if not flags["tenant.production_activation_payload_dry_run"]:
        blockers.append("tenant.production_activation_payload_dry_run_disabled")
    unique_blockers = sorted(set(blockers))
    payload_ready = bool(
        flags["tenant.production_activation_payload_dry_run"]
        and permission["decision"]["production_activation_permission_shadow_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "production_activation_payload_dry_run.v1",
        "requester": {
            **permission["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "payload_dry_run": {
            "dry_run_only": True,
            "payload_dry_run_ready": payload_ready,
            "permission_shadow_ready": permission["decision"]["production_activation_permission_shadow_ready"],
            "request_payload_read": False,
            "response_payload_returned": False,
            "activation_payload_persisted": False,
            "activation_request_recorded": False,
            "activation_executed": False,
            "production_activation_started": False,
            "future_required_request_fields": (
                "change_ticket_reference",
                "operator_approval_reference",
                "tenant_canary_reference",
                "rollback_plan_reference",
            ),
            "future_response_shape_ready": payload_ready,
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "permission_shadow_manifest_version": permission["manifest_version"],
            "permission_shadow_ready": permission["decision"]["production_activation_permission_shadow_ready"],
            "activation_contract_ready": permission["components"]["activation_contract_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_activation_payload_dry_run_ready": payload_ready,
            "would_read_activation_payload_now": False,
            "would_return_activation_payload_now": False,
            "would_record_activation_request_now": False,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_activation_approval_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **permission["safety"],
            "request_payload_read": False,
            "response_payload_returned": False,
            "activation_payload_persisted": False,
            "activation_request_recorded": False,
            "production_activation_started": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_activation_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build final report-only readiness for a future production activation."""
    payload = build_production_activation_payload_dry_run_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **payload["flags"],
        "tenant.production_activation_readiness": feature_flags.is_enabled(
            "tenant.production_activation_readiness"
        ),
    }
    blockers: list[str] = list(payload["decision"]["blockers"])
    if not flags["tenant.production_activation_readiness"]:
        blockers.append("tenant.production_activation_readiness_disabled")
    unique_blockers = sorted(set(blockers))
    activation_ready = bool(
        flags["tenant.production_activation_readiness"]
        and payload["decision"]["production_activation_payload_dry_run_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "production_activation_readiness.v1",
        "requester": {
            **payload["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "activation_readiness": {
            "readiness_only": True,
            "production_activation_ready": activation_ready,
            "payload_dry_run_ready": payload["decision"]["production_activation_payload_dry_run_ready"],
            "activation_live": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "frontend_modified": False,
            "traffic_shifted": False,
            "legacy_fallback_retained": True,
            "requires_final_human_handoff": True,
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "payload_dry_run_manifest_version": payload["manifest_version"],
            "payload_dry_run_ready": payload["decision"]["production_activation_payload_dry_run_ready"],
            "permission_shadow_ready": payload["components"]["permission_shadow_ready"],
            "activation_contract_ready": payload["components"]["activation_contract_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_activation_readiness_ready": activation_ready,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_activation_handoff_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **payload["safety"],
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_production_activation_rollback_confirmation_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build a no-action rollback confirmation for future production activation."""
    readiness = build_production_activation_readiness_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **readiness["flags"],
        "tenant.production_activation_rollback_confirmation": feature_flags.is_enabled(
            "tenant.production_activation_rollback_confirmation"
        ),
    }
    blockers: list[str] = list(readiness["decision"]["blockers"])
    if not flags["tenant.production_activation_rollback_confirmation"]:
        blockers.append("tenant.production_activation_rollback_confirmation_disabled")
    unique_blockers = sorted(set(blockers))
    rollback_confirmed = bool(
        flags["tenant.production_activation_rollback_confirmation"]
        and readiness["decision"]["production_activation_readiness_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "production_activation_rollback_confirmation.v1",
        "requester": {
            **readiness["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "rollback_confirmation": {
            "confirmation_only": True,
            "rollback_confirmation_ready": rollback_confirmed,
            "activation_readiness_ready": readiness["decision"]["production_activation_readiness_ready"],
            "rollback_action_performed": False,
            "rollback_token_issued": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "required_rollback_checks": (
                "legacy_results_endpoint_available",
                "legacy_transcript_endpoint_available",
                "static_recording_mount_available",
                "disable_activation_flags_order_known",
                "post_rollback_regression_known",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "activation_readiness_manifest_version": readiness["manifest_version"],
            "activation_readiness_ready": readiness["decision"]["production_activation_readiness_ready"],
            "payload_dry_run_ready": readiness["components"]["payload_dry_run_ready"],
            "permission_shadow_ready": readiness["components"]["permission_shadow_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "production_activation_rollback_confirmation_ready": rollback_confirmed,
            "would_issue_rollback_token_now": False,
            "would_execute_rollback_now": False,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_activation_handoff_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **readiness["safety"],
            "rollback_action_performed": False,
            "rollback_token_issued": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_controlled_handoff_readiness_manifest(
    context: TenantContext,
    *,
    transcript_found: bool,
    transcript_owner_tenant_id: str | None,
    recording_found: bool = False,
    recording_owner_tenant_id: str | None = None,
    campaign_found: bool = False,
    campaign_owner_tenant_id: str | None = None,
    requested_tenant_id: str | None = None,
    transcript_campaign_id_present: bool = False,
    recording_campaign_id_present: bool = False,
    recording_required: bool = False,
    campaign_required: bool = False,
) -> dict[str, Any]:
    """Build the final no-action handoff readiness manifest for this migration run."""
    rollback = build_production_activation_rollback_confirmation_manifest(
        context,
        transcript_found=transcript_found,
        transcript_owner_tenant_id=transcript_owner_tenant_id,
        recording_found=recording_found,
        recording_owner_tenant_id=recording_owner_tenant_id,
        campaign_found=campaign_found,
        campaign_owner_tenant_id=campaign_owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
        transcript_campaign_id_present=transcript_campaign_id_present,
        recording_campaign_id_present=recording_campaign_id_present,
        recording_required=recording_required,
        campaign_required=campaign_required,
    )
    flags = {
        **rollback["flags"],
        "tenant.controlled_handoff_readiness": feature_flags.is_enabled(
            "tenant.controlled_handoff_readiness"
        ),
    }
    blockers: list[str] = list(rollback["decision"]["blockers"])
    if not flags["tenant.controlled_handoff_readiness"]:
        blockers.append("tenant.controlled_handoff_readiness_disabled")
    unique_blockers = sorted(set(blockers))
    handoff_ready = bool(
        flags["tenant.controlled_handoff_readiness"]
        and rollback["decision"]["production_activation_rollback_confirmation_ready"]
        and not unique_blockers
    )

    return {
        "manifest_version": "controlled_handoff_readiness.v1",
        "requester": {
            **rollback["requester"],
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
        },
        "handoff": {
            "readiness_only": True,
            "controlled_handoff_ready": handoff_ready,
            "rollback_confirmation_ready": rollback["decision"]["production_activation_rollback_confirmation_ready"],
            "live_activation_performed": False,
            "handoff_record_created": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "no_more_migration_layers_required": True,
            "next_required_action": "manual_architectural_review_before_any_live_activation",
            "handoff_artifacts": (
                "production_activation_contract_stub",
                "production_activation_permission_shadow",
                "production_activation_payload_dry_run",
                "production_activation_readiness",
                "production_activation_rollback_confirmation",
            ),
            "ids_included": False,
            "payloads_included": False,
            "tenant_values_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "recording_bytes_included": False,
            "blockers": unique_blockers,
        },
        "components": {
            "rollback_confirmation_manifest_version": rollback["manifest_version"],
            "rollback_confirmation_ready": rollback["decision"]["production_activation_rollback_confirmation_ready"],
            "activation_readiness_ready": rollback["components"]["activation_readiness_ready"],
            "payload_dry_run_ready": rollback["components"]["payload_dry_run_ready"],
            "permission_shadow_ready": rollback["components"]["permission_shadow_ready"],
            "component_payloads_included": False,
            "component_ids_included": False,
            "component_tenant_values_included": False,
        },
        "flags": flags,
        "decision": {
            "controlled_handoff_readiness_ready": handoff_ready,
            "would_create_handoff_record_now": False,
            "would_execute_activation_now": False,
            "would_start_production_activation_now": False,
            "would_modify_flags_now": False,
            "would_modify_routes_now": False,
            "would_change_frontend_now": False,
            "would_change_audio_runtime_now": False,
            "would_change_websocket_contract_now": False,
            "would_change_campaign_runtime_now": False,
            "active_enforcement": False,
            "shadow_only": True,
            "manual_architectural_review_required": True,
            "blockers": unique_blockers,
        },
        "safety": {
            **rollback["safety"],
            "handoff_record_created": False,
            "live_activation_performed": False,
            "production_activation_started": False,
            "feature_flags_modified": False,
            "routes_modified": False,
            "traffic_shifted": False,
            "db_write_performed": False,
            "db_payload_read_performed": False,
            "resource_payload_returned": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def build_tenant_scoped_read_canary(
    context: TenantContext,
    *,
    resource_type: str,
    resource_label: str,
    resource_found: bool,
    owner_tenant_id: str | None,
    requested_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build a no-payload shadow decision for future tenant-scoped reads."""
    normalized_type = (resource_type or "").strip().lower()
    guard = build_tenant_scoped_read_guard_decision(
        context,
        resource_found=resource_found,
        owner_tenant_id=owner_tenant_id,
        requested_tenant_id=requested_tenant_id,
    )
    flags = {
        **guard["flags"],
        "auth.enforce_backend": feature_flags.is_enabled("auth.enforce_backend"),
        "tenant.scoped_reads": feature_flags.is_enabled("tenant.scoped_reads"),
        "tenant.enforcement_readiness": feature_flags.is_enabled("tenant.enforcement_readiness"),
        "tenant.scoped_read_canary": feature_flags.is_enabled("tenant.scoped_read_canary"),
    }

    blockers: list[str] = list(guard["decision"]["blockers"])
    if not flags["tenant.enforcement_readiness"]:
        blockers.append("tenant.enforcement_readiness_disabled")
    if not flags["tenant.scoped_read_canary"]:
        blockers.append("tenant.scoped_read_canary_disabled")

    return {
        "manifest_version": "tenant_scoped_read_canary.v1",
        "resource": {
            "type": normalized_type,
            "label": resource_label,
            "found": bool(resource_found),
            "owner_tenant_present": guard["resource"]["owner_tenant_present"],
            "resource_id_included": False,
            "owner_tenant_included": False,
            "payload_included": False,
        },
        "requester": {
            "auth_state": context.auth_state,
            "role": context.role,
            "verified": context.is_verified,
            "is_admin": context.is_admin,
            "tenant_present": bool(context.tenant_id),
            "requested_tenant_present": guard["requester"]["requested_tenant_present"],
            "user_email_present": bool(context.user_email),
            "subject_present": bool(context.subject),
            "tenant_included": False,
            "requested_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "warnings": list(context.warnings),
        },
        "flags": flags,
        "decision": {
            "guard_decision_version": guard["decision_version"],
            "tenant_match_for_requested_tenant": guard["tenant"]["tenant_match_for_requested_tenant"],
            "requested_tenant_allowed_if_scoped_reads_enforced": guard["decision"]["requested_tenant_allowed_if_enforced"],
            "current_requester_allowed_if_scoped_reads_enforced": guard["decision"]["current_requester_allowed_if_enforced"],
            "would_reject_current_requester_if_scoped_reads_enforced": guard["decision"]["would_reject_current_requester_if_enforced"],
            "active_enforcement": guard["decision"]["active_enforcement"],
            "shadow_only": True,
            "blockers": blockers,
        },
        "safety": {
            "runtime_enforcement_changed": False,
            "db_write_performed": False,
            "db_lookup_scope": "owner_metadata_only",
            "resource_payload_returned": False,
            "resource_id_included": False,
            "owner_tenant_included": False,
            "requester_tenant_included": False,
            "user_email_included": False,
            "subject_included": False,
            "phone_number_included": False,
            "transcript_content_included": False,
            "recording_url_included": False,
            "crm_payload_included": False,
            "tenant_data_returned": False,
            "cross_tenant_data_included": False,
        },
    }


def audit_context(
    logger: logging.Logger,
    context: TenantContext,
    *,
    surface: str,
    route: str,
    method: str = "",
) -> None:
    if _is_public_path(route):
        return

    level = logging.WARNING if context.warnings else logging.INFO
    logger.log(
        level,
        "tenant_auth_audit surface=%s method=%s route=%s auth_state=%s role=%s "
        "tenant_id=%s requested_tenant_id=%s verified=%s source=%s warnings=%s",
        surface,
        method,
        route,
        context.auth_state,
        context.role,
        context.tenant_id,
        context.requested_tenant_id,
        context.is_verified,
        context.source,
        ",".join(context.warnings) if context.warnings else "",
    )


def _normalize_mapping(values: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not values:
        return result
    for key, value in values.items():
        key_text = str(key).strip().lower()
        value_text = str(value).strip()
        result[key_text] = value_text
        result[key_text.replace("-", "_")] = value_text
    return result


def _safe_manifest_label(value: str | None, *, default: str = "unknown", max_length: int = 64) -> str:
    text = str(value or "").strip() or default
    sanitized = "".join(
        ch if ch.isalnum() or ch in {"_", "-", "."} else "_"
        for ch in text
    ).strip("_")
    return (sanitized or default)[:max_length]


def _first_present(*values: str | None) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _claim(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    return str(value).strip() or None


def _decode_unverified_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _constant_time_equal(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(str(left), str(right))


def _is_public_path(path: str) -> bool:
    return any((path or "").startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _requires_tenant_scope(path: str) -> bool:
    return any((path or "").startswith(prefix) for prefix in TENANT_SCOPED_PATH_PREFIXES)
