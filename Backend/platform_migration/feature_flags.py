"""Central feature flags for the phased platform migration.

Registered roadmap defaults stay conservative for local/test/rollback safety.
Production can activate the completed platform surfaces with one explicit
release profile:

    PLATFORM_FEATURE_PROFILE=live

Set PLATFORM_FEATURE_PROFILE=shadow to return the platform to the original
shadow/off posture without changing individual flags. Individual FEATURE_*
environment variables always win over the profile.
"""

from __future__ import annotations

import os
from types import MappingProxyType
from typing import Mapping


_DEFAULTS = MappingProxyType(
    {
        "auth.enforce_backend": False,
        "tenant.scoped_reads": False,
        "tenant.enforcement_readiness": False,
        "tenant.scoped_read_canary": False,
        "tenant.scoped_read_guard_shadow": False,
        "tenant.scoped_read_policy_shadow": False,
        "tenant.scoped_read_endpoint_shadow": False,
        "tenant.leak_regression_matrix": False,
        "tenant.security_leak_audit_readiness": False,
        "tenant.result_asset_readiness": False,
        "tenant.final_rollout_report": False,
        "tenant.rollout_approval_packet": False,
        "tenant.rollout_canary_plan": False,
        "tenant.rollback_drill_readiness": False,
        "tenant.rollout_evidence_bundle": False,
        "tenant.canary_observation_checklist": False,
        "tenant.production_go_no_go_gate": False,
        "tenant.production_activation_contract_stub": False,
        "tenant.production_activation_permission_shadow": False,
        "tenant.production_activation_payload_dry_run": False,
        "tenant.production_activation_readiness": False,
        "tenant.production_activation_rollback_confirmation": False,
        "tenant.controlled_handoff_readiness": False,
        "tenant.final_canary_rollback_readiness": False,
        "ws.scoped_events": False,
        "ws.scoped_events_shadow": False,
        "recordings.access_shadow": False,
        "recordings.owner_lookup_shadow": False,
        "recordings.access_enforcement_shadow": False,
        "recordings.access_gate_dry_run": False,
        "transcripts.access_shadow": False,
        "transcripts.access_canary": False,
        "transcripts.access_enforcement_shadow": False,
        "transcripts.access_gate_dry_run": False,
        "transcripts.protected_route_stub": False,
        "transcripts.protected_route_permission_shadow": False,
        "transcripts.protected_response_shape_canary": False,
        "transcripts.protected_payload_dry_run": False,
        "transcripts.protected_enforcement_readiness": False,
        "transcripts.protected_live_activation_plan": False,
        "transcripts.protected_rollback_readiness": False,
        "transcripts.frontend_migration_readiness": False,
        "campaign.worker_v2": False,
        "campaign.lifecycle_management": False,
        "campaign.e2e_qa_readiness": False,
        "flow.v2_shadow": False,
        "flow.v2_live": False,
        "flow.visualization": False,
        "demo.runtime_qa_readiness": False,
        "scrape.generate_script": False,
        "scrape.worker_v1": False,
        "scrape.job_cancel": False,
        "scrape.stale_recovery": False,
        "scrape.job_events": False,
        "scrape.review_gate_shadow": False,
        "scrape.live_qa_readiness": False,
        "scrape.generated_draft_qa_readiness": False,
        "telephony.tenant_numbers": False,
        "telephony.live_qa_readiness": False,
        "memory.rag_enabled": False,
        "crm.sync_enabled": False,
        "crm.sync_preflight": False,
        "crm.sync_outbox": False,
        "crm.sync_worker_shadow": False,
        "crm.sync_worker_retries": False,
        "crm.sync_observability": False,
        "crm.provider_contracts": False,
        "crm.delivery_plan_shadow": False,
        "crm.delivery_approval_shadow": False,
        "crm.delivery_approval_revoke": False,
        "crm.live_readiness_shadow": False,
        "crm.provider_sandbox_shadow": False,
        "crm.dispatch_canary_shadow": False,
    }
)

_ROLLBACK_PROFILE = "shadow"
_LIVE_PROFILE = "live"
_DEFAULT_PROFILE = _ROLLBACK_PROFILE
_PROFILE_ENV = "PLATFORM_FEATURE_PROFILE"

_LIVE_PROFILE_DISABLED_FLAGS = frozenset(
    {
        # Hard backend auth requires signed server-verifiable identity on every
        # frontend/API call. Keep it individually controlled so go-live does not
        # lock out dashboards that still use tenant headers and Firebase tokens.
        "auth.enforce_backend",
    }
)

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def env_name(flag_name: str) -> str:
    """Return the environment variable name for a dotted feature flag."""
    normalized = "".join(ch if ch.isalnum() else "_" for ch in flag_name.upper())
    return f"FEATURE_{normalized}"


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse a feature flag value using conservative defaults."""
    if value is None:
        return bool(default)
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return bool(default)


def _profile_name(source: Mapping[str, str]) -> str:
    return str(source.get(_PROFILE_ENV) or _DEFAULT_PROFILE).strip().lower()


def _profile_default(flag_name: str, source: Mapping[str, str]) -> bool:
    registered_default = _DEFAULTS.get(flag_name, False)
    profile = _profile_name(source)
    if profile in {_ROLLBACK_PROFILE, "off", "disabled", "safe"}:
        return registered_default
    if profile in {_LIVE_PROFILE, "production", "prod", "on"}:
        if flag_name in _LIVE_PROFILE_DISABLED_FLAGS:
            return registered_default
        return flag_name in _DEFAULTS
    return registered_default


def is_enabled(
    flag_name: str,
    *,
    default: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a feature flag is enabled.

    Unknown flags are disabled unless the caller supplies an explicit default.
    """
    source = env if env is not None else os.environ
    fallback = _profile_default(flag_name, source) if default is None else bool(default)
    return parse_bool(source.get(env_name(flag_name)), fallback)


def known_flags() -> dict[str, bool]:
    """Return the registered flags and their default states."""
    return dict(_DEFAULTS)


def snapshot(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Return current flag states for logging, diagnostics, or health checks."""
    return {flag: is_enabled(flag, env=env) for flag in _DEFAULTS}


def active_profile(env: Mapping[str, str] | None = None) -> str:
    """Return the active release profile name."""
    source = env if env is not None else os.environ
    return _profile_name(source)
