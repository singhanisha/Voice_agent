"""Disabled-by-default CRM sync foundation.

The CRM surface stores tenant-scoped connection metadata, external credential
references, dry-run sync plans, redacted previews, and readiness evidence. It
does not store credentials and never sends data to external systems.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SUPPORTED_PROVIDERS = {"hubspot", "salesforce", "zoho", "custom_webhook"}
PROVIDER_CONTRACTS = {
    "hubspot": {
        "display_name": "HubSpot",
        "object_type": "contacts",
        "supported_directions": ["outbound"],
        "supported_objects": ["contacts"],
        "idempotency_field": "external_id",
    },
    "salesforce": {
        "display_name": "Salesforce",
        "object_type": "Lead",
        "supported_directions": ["outbound"],
        "supported_objects": ["Lead"],
        "idempotency_field": "external_id",
    },
    "zoho": {
        "display_name": "Zoho CRM",
        "object_type": "Leads",
        "supported_directions": ["outbound"],
        "supported_objects": ["Leads"],
        "idempotency_field": "external_id",
    },
    "custom_webhook": {
        "display_name": "Custom Webhook",
        "object_type": "custom_webhook.leads",
        "supported_directions": ["outbound"],
        "supported_objects": ["leads"],
        "idempotency_field": "external_id",
    },
}
SUPPORTED_SECRET_REFERENCE_PROVIDERS = {
    "env",
    "vault",
    "aws_secrets_manager",
    "azure_key_vault",
    "gcp_secret_manager",
    "external",
}
SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
RAW_SECRET_PREFIXES = ("bearer ", "ghp_", "sk-", "xox", "ya29.")


@dataclass(frozen=True)
class CRMSyncPlan:
    client_id: str
    connection_id: str
    campaign_id: str | None
    direction: str
    requested_by: str | None


class CRMIntegrationService:
    def __init__(self, db):
        self.db = db

    async def create_connection(
        self,
        *,
        client_id: str,
        provider: str,
        display_name: str | None = None,
        external_account_id: str | None = None,
        config: dict[str, Any] | None = None,
        requested_by: str | None = None,
    ) -> dict:
        normalized_provider = self._normalize_provider(provider)
        public_config = self._sanitize_public_config(config or {})
        return await self.db.create_crm_connection(
            client_id=client_id,
            provider=normalized_provider,
            display_name=(display_name or normalized_provider.title()).strip(),
            external_account_id=external_account_id,
            config=public_config,
            requested_by=requested_by,
        )

    async def list_connections(self, *, client_id: str, include_disabled: bool = False) -> list[dict]:
        return await self.db.list_crm_connections(
            client_id=client_id,
            include_disabled=include_disabled,
        )

    async def configure_secret_reference(
        self,
        *,
        client_id: str,
        connection_id: str,
        vault_provider: str,
        reference_id: str,
        rotation_due_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        requested_by: str | None = None,
    ) -> dict:
        reference = self._build_secret_reference(
            vault_provider=vault_provider,
            reference_id=reference_id,
            rotation_due_at=rotation_due_at,
            metadata=metadata or {},
        )
        return await self.db.configure_crm_connection_secret_reference(
            connection_id=connection_id,
            client_id=client_id,
            credential_reference=reference,
            requested_by=requested_by,
        )

    async def get_provider_contract(
        self,
        *,
        client_id: str,
        connection_id: str,
    ) -> dict:
        connection = await self.db.get_crm_connection(connection_id)
        if not connection:
            raise ValueError(f"crm connection not found: {connection_id}")
        if connection.get("client_id") != client_id:
            raise ValueError("crm connection is outside tenant scope")
        if connection.get("disabled_at"):
            raise ValueError("crm connection is disabled")

        provider = self._normalize_provider(connection["provider"])
        base_contract = dict(PROVIDER_CONTRACTS[provider])
        config = connection.get("config") if isinstance(connection.get("config"), dict) else {}
        credential_reference = config.get("credential_reference")
        credential_reference = credential_reference if isinstance(credential_reference, dict) else {}
        secret_configured = bool(
            connection.get("secrets_configured")
            and credential_reference.get("configured")
            and credential_reference.get("external_secret_storage")
            and not credential_reference.get("secret_value_stored")
        )
        blockers = ["live_sync_requires_future_phase"]
        if not secret_configured:
            blockers.append("credential_reference_missing")

        return {
            "contract_version": "crm_provider_contract.v1",
            "client_id": client_id,
            "connection_id": connection_id,
            "provider": provider,
            "display_name": base_contract["display_name"],
            "connection_status": connection.get("status"),
            "object_type": base_contract["object_type"],
            "supported_directions": base_contract["supported_directions"],
            "supported_objects": base_contract["supported_objects"],
            "supported_fields": [
                "external_id",
                "lead_name",
                "phone_redacted",
                "phone_sha256",
                "call_status",
                "call_outcome",
                "interested",
                "budget",
                "callback_time",
                "has_transcript",
                "has_recording",
            ],
            "blocked_exports": [
                "raw_phone",
                "transcript_content",
                "recording_url",
                "recording_content",
                "credential_values",
            ],
            "idempotency_strategy": {
                "field": base_contract["idempotency_field"],
                "source": "tenant_scoped_call_result_fingerprint",
            },
            "credential_reference": {
                "configured": secret_configured,
                "vault_provider": credential_reference.get("vault_provider") if secret_configured else None,
                "reference_hash_present": bool(credential_reference.get("reference_hash")),
                "reference_id_included": False,
                "secret_value_stored": False,
            },
            "public_config_keys": sorted(
                key for key in config.keys() if key != "credential_reference"
            ),
            "readiness": {
                "preflight_supported": True,
                "outbox_supported": True,
                "shadow_worker_supported": True,
                "live_sync_ready": False,
                "blockers": blockers,
            },
            "network_check_performed": False,
            "external_execution": False,
            "runtime_campaign_hook": False,
        }

    async def plan_campaign_sync(
        self,
        *,
        client_id: str,
        connection_id: str,
        campaign_id: str | None = None,
        direction: str = "outbound",
        requested_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        normalized_direction = self._normalize_direction(direction)
        preview = None
        if campaign_id:
            preview = await self.build_campaign_payload_preview(
                client_id=client_id,
                campaign_id=campaign_id,
            )
        payload = {
            "dry_run": True,
            "external_execution": False,
            "runtime_campaign_hook": False,
            "payload_preview": preview,
            "sync_scope": {
                "campaign_results": True,
                "lead_summary": True,
                "transcript_content": False,
                "recording_content": False,
            },
            "tenant_boundary": {
                "client_id": client_id,
                "connection_id": connection_id,
                "campaign_id": campaign_id,
            },
        }
        job = await self.db.create_crm_sync_job(
            client_id=client_id,
            connection_id=connection_id,
            campaign_id=campaign_id,
            mode="dry_run",
            direction=normalized_direction,
            payload=payload,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )
        return {"job": job, "external_execution": False}

    async def build_campaign_payload_preview(
        self,
        *,
        client_id: str,
        campaign_id: str,
    ) -> dict:
        campaign = await self.db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"campaign not found: {campaign_id}")
        if not campaign.get("client_id"):
            raise ValueError("campaign must be tenant-owned before CRM payload preview")
        if campaign["client_id"] != client_id:
            raise ValueError("campaign is outside CRM tenant scope")

        results = await self.db.get_results_for_campaign(campaign_id, client_id=client_id)
        records = [self._build_result_record(result) for result in results]
        return {
            "payload_version": "crm_campaign_preview.v1",
            "dry_run": True,
            "external_execution": False,
            "client_id": client_id,
            "campaign": {
                "id": campaign["id"],
                "name": campaign.get("name"),
                "status": campaign.get("status"),
                "telephony_provider": campaign.get("telephony_provider"),
                "result_count": len(records),
            },
            "summary": self._summarize_records(records),
            "records": records,
            "redaction": {
                "phone": "last4_only_with_sha256_fingerprint",
                "transcript_content": "excluded",
                "recording_content": "excluded",
                "recording_url": "excluded",
                "pii_export_enabled": False,
            },
        }

    async def list_sync_jobs(
        self,
        *,
        client_id: str,
        connection_id: str | None = None,
        campaign_id: str | None = None,
    ) -> list[dict]:
        return await self.db.list_crm_sync_jobs(
            client_id=client_id,
            connection_id=connection_id,
            campaign_id=campaign_id,
        )

    async def execute_dry_run_sync(
        self,
        *,
        client_id: str,
        job_id: str,
        requested_by: str | None = None,
    ) -> dict:
        job = await self.db.get_crm_sync_job(job_id)
        if not job:
            raise ValueError(f"crm sync job not found: {job_id}")
        if job.get("client_id") != client_id:
            raise ValueError("crm sync job is outside tenant scope")
        if job.get("mode") != "dry_run":
            raise ValueError("only dry_run CRM jobs can execute in this phase")

        connection = await self.db.get_crm_connection(job["connection_id"])
        if not connection:
            raise ValueError(f"crm connection not found: {job['connection_id']}")
        if connection.get("client_id") != client_id:
            raise ValueError("crm connection is outside tenant scope")

        preview = (job.get("payload") or {}).get("payload_preview")
        if not preview and job.get("campaign_id"):
            preview = await self.build_campaign_payload_preview(
                client_id=client_id,
                campaign_id=job["campaign_id"],
            )

        rendered = self._render_provider_payload(connection["provider"], preview)
        dry_run_payload = {
            **(job.get("payload") or {}),
            "dry_run": True,
            "external_execution": False,
            "runtime_campaign_hook": False,
            "executed_by": requested_by,
            "provider_payload": rendered,
            "execution_result": {
                "status": "validated",
                "records_rendered": len(rendered["records"]),
                "provider": connection["provider"],
                "sent_to_provider": False,
            },
        }
        updated = await self.db.record_crm_sync_event(
            job_id=job_id,
            client_id=client_id,
            connection_id=job.get("connection_id"),
            campaign_id=job.get("campaign_id"),
            event_type="dry_run_validated",
            payload=dry_run_payload,
            status="validated",
            completed=True,
        )
        return {
            "job": updated,
            "provider_payload": rendered,
            "external_execution": False,
        }

    async def list_sync_events(
        self,
        *,
        client_id: str,
        job_id: str | None = None,
        connection_id: str | None = None,
        campaign_id: str | None = None,
    ) -> list[dict]:
        if job_id:
            job = await self.db.get_crm_sync_job(job_id)
            if not job:
                raise ValueError(f"crm sync job not found: {job_id}")
            if job.get("client_id") != client_id:
                raise ValueError("crm sync job is outside tenant scope")
        return await self.db.list_crm_sync_events(
            client_id=client_id,
            job_id=job_id,
            connection_id=connection_id,
            campaign_id=campaign_id,
        )

    async def run_sync_preflight(
        self,
        *,
        client_id: str,
        job_id: str,
        requested_by: str | None = None,
    ) -> dict:
        job = await self.db.get_crm_sync_job(job_id)
        if not job:
            raise ValueError(f"crm sync job not found: {job_id}")
        if job.get("client_id") != client_id:
            raise ValueError("crm sync job is outside tenant scope")
        if job.get("mode") != "dry_run":
            raise ValueError("only dry_run CRM jobs can be preflighted in this phase")

        connection = await self.db.get_crm_connection(job["connection_id"])
        if not connection:
            raise ValueError(f"crm connection not found: {job['connection_id']}")
        if connection.get("client_id") != client_id:
            raise ValueError("crm connection is outside tenant scope")
        if connection.get("disabled_at"):
            raise ValueError("crm connection is disabled")

        preview = (job.get("payload") or {}).get("payload_preview")
        if not preview and job.get("campaign_id"):
            preview = await self.build_campaign_payload_preview(
                client_id=client_id,
                campaign_id=job["campaign_id"],
            )
        if not preview:
            preview = {
                "payload_version": "crm_campaign_preview.v1",
                "dry_run": True,
                "external_execution": False,
                "records": [],
                "summary": {"total_records": 0},
                "redaction": {"pii_export_enabled": False},
            }

        credential_check = self._validate_credential_reference(connection)
        preview_check = self._validate_payload_preview_safe(preview)
        provider_payload = self._render_provider_payload(connection["provider"], preview)
        provider_check = self._validate_provider_payload_safe(provider_payload)
        payload = job.get("payload") or {}
        contract_check = self._validate_job_contract(payload)

        preflight = {
            "preflight_version": "crm_sync_preflight.v1",
            "status": "passed",
            "ready_for_external_sync": False,
            "external_execution": False,
            "runtime_campaign_hook": False,
            "live_sync_requires_future_phase": True,
            "requested_by": requested_by,
            "checks": [
                {
                    "name": "tenant_scope",
                    "status": "passed",
                    "client_id": client_id,
                    "connection_id": connection["id"],
                    "campaign_id": job.get("campaign_id"),
                },
                credential_check,
                contract_check,
                preview_check,
                provider_check,
            ],
            "summary": {
                "provider": connection["provider"],
                "records_rendered": len(provider_payload.get("records") or []),
                "sent_to_provider": False,
            },
        }
        updated_payload = {
            **payload,
            "dry_run": True,
            "external_execution": False,
            "runtime_campaign_hook": False,
            "preflight": preflight,
        }
        updated = await self.db.record_crm_sync_event(
            job_id=job_id,
            client_id=client_id,
            connection_id=job.get("connection_id"),
            campaign_id=job.get("campaign_id"),
            event_type="preflight_validated",
            payload=updated_payload,
            status="preflight_validated",
            completed=False,
        )
        return {
            "job": updated,
            "preflight": preflight,
            "external_execution": False,
        }

    async def queue_sync_outbox(
        self,
        *,
        client_id: str,
        job_id: str,
        requested_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        job = await self.db.get_crm_sync_job(job_id)
        if not job:
            raise ValueError(f"crm sync job not found: {job_id}")
        if job.get("client_id") != client_id:
            raise ValueError("crm sync job is outside tenant scope")
        if job.get("status") != "preflight_validated":
            raise ValueError("crm sync job must pass preflight before outbox queue")

        connection = await self.db.get_crm_connection(job["connection_id"])
        if not connection:
            raise ValueError(f"crm connection not found: {job['connection_id']}")
        if connection.get("client_id") != client_id:
            raise ValueError("crm connection is outside tenant scope")
        if connection.get("disabled_at"):
            raise ValueError("crm connection is disabled")

        payload = job.get("payload") or {}
        preflight = payload.get("preflight") if isinstance(payload.get("preflight"), dict) else {}
        if preflight.get("status") != "passed":
            raise ValueError("crm sync job has no passing preflight evidence")
        if preflight.get("external_execution") is True:
            raise ValueError("crm preflight is marked for external execution")
        if preflight.get("runtime_campaign_hook") is True:
            raise ValueError("crm preflight is marked for runtime campaign hooks")

        credential_check = self._validate_credential_reference(connection)
        self._validate_job_contract(payload)
        preview = payload.get("payload_preview")
        if preview:
            self._validate_payload_preview_safe(preview)
        provider_payload = self._render_provider_payload(connection["provider"], preview)
        provider_check = self._validate_provider_payload_safe(provider_payload)

        outbox_payload = {
            "outbox_version": "crm_sync_outbox.v1",
            "mode": "shadow",
            "status": "queued_shadow",
            "external_execution": False,
            "runtime_campaign_hook": False,
            "worker_dispatch_enabled": False,
            "future_live_sync_requires_flag": True,
            "tenant_boundary": {
                "client_id": client_id,
                "connection_id": connection["id"],
                "campaign_id": job.get("campaign_id"),
                "job_id": job_id,
            },
            "preflight": preflight,
            "checks": [credential_check, provider_check],
            "provider_payload": provider_payload,
            "requested_by": requested_by,
        }
        outbox_item = await self.db.create_crm_sync_outbox_item(
            client_id=client_id,
            job_id=job_id,
            payload=outbox_payload,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )
        return {
            "outbox_item": outbox_item,
            "external_execution": False,
            "worker_dispatch_enabled": False,
        }

    async def list_sync_outbox(
        self,
        *,
        client_id: str,
        job_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        if job_id:
            job = await self.db.get_crm_sync_job(job_id)
            if not job:
                raise ValueError(f"crm sync job not found: {job_id}")
            if job.get("client_id") != client_id:
                raise ValueError("crm sync job is outside tenant scope")
        return await self.db.list_crm_sync_outbox_items(
            client_id=client_id,
            job_id=job_id,
            status=status,
        )

    async def get_sync_outbox_summary(
        self,
        *,
        client_id: str,
        job_id: str | None = None,
        connection_id: str | None = None,
        campaign_id: str | None = None,
    ) -> dict:
        if job_id:
            job = await self.db.get_crm_sync_job(job_id)
            if not job:
                raise ValueError(f"crm sync job not found: {job_id}")
            if job.get("client_id") != client_id:
                raise ValueError("crm sync job is outside tenant scope")
        if connection_id:
            connection = await self.db.get_crm_connection(connection_id)
            if not connection:
                raise ValueError(f"crm connection not found: {connection_id}")
            if connection.get("client_id") != client_id:
                raise ValueError("crm connection is outside tenant scope")
        if campaign_id:
            campaign = await self.db.get_campaign(campaign_id)
            if not campaign:
                raise ValueError(f"campaign not found: {campaign_id}")
            if campaign.get("client_id") != client_id:
                raise ValueError("campaign is outside CRM tenant scope")

        summary = await self.db.get_crm_sync_outbox_summary(
            client_id=client_id,
            job_id=job_id,
            connection_id=connection_id,
            campaign_id=campaign_id,
        )
        return {
            "summary_version": "crm_outbox_observability.v1",
            **summary,
        }

    async def build_outbox_delivery_plan(
        self,
        *,
        client_id: str,
        outbox_id: str,
    ) -> dict:
        item = await self.db.get_crm_sync_outbox_item(outbox_id)
        if not item:
            raise ValueError(f"crm outbox item not found: {outbox_id}")
        if item.get("client_id") != client_id:
            raise ValueError("crm outbox item is outside tenant scope")
        self._validate_outbox_item_safe(item)

        connection = await self.db.get_crm_connection(item["connection_id"])
        if not connection:
            raise ValueError(f"crm connection not found: {item['connection_id']}")
        if connection.get("client_id") != client_id:
            raise ValueError("crm connection is outside tenant scope")
        contract = await self.get_provider_contract(
            client_id=client_id,
            connection_id=item["connection_id"],
        )
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        provider_payload = payload.get("provider_payload")
        provider_payload = provider_payload if isinstance(provider_payload, dict) else {}
        self._validate_provider_payload_safe(provider_payload)
        records = provider_payload.get("records") if isinstance(provider_payload.get("records"), list) else []
        property_keys: set[str] = set()
        for record in records:
            properties = record.get("properties") if isinstance(record, dict) else {}
            if isinstance(properties, dict):
                property_keys.update(str(key) for key in properties.keys())

        blockers = ["live_sync_requires_future_phase", "network_dispatch_disabled"]
        if item.get("status") in {"dead_letter_shadow", "completed_shadow"}:
            blockers.append(f"outbox_status_{item.get('status')}")

        return {
            "plan_version": "crm_delivery_plan_shadow.v1",
            "client_id": client_id,
            "outbox_id": outbox_id,
            "job_id": item.get("job_id"),
            "connection_id": item.get("connection_id"),
            "campaign_id": item.get("campaign_id"),
            "outbox_status": item.get("status"),
            "provider": contract["provider"],
            "provider_display_name": contract["display_name"],
            "object_type": contract["object_type"],
            "operation": "upsert_shadow",
            "idempotency_strategy": contract["idempotency_strategy"],
            "record_count": len(records),
            "property_keys": sorted(property_keys),
            "credential_reference": {
                "configured": contract["credential_reference"]["configured"],
                "vault_provider": contract["credential_reference"]["vault_provider"],
                "reference_hash_present": contract["credential_reference"]["reference_hash_present"],
                "reference_id_included": False,
                "secret_value_included": False,
            },
            "request_envelope": {
                "method": "POST",
                "url_included": False,
                "endpoint_template_included": False,
                "headers_included": False,
                "auth_header_included": False,
                "body_included": False,
                "body_preview_included": False,
                "provider_payload_included": False,
            },
            "safety": {
                "network_check_performed": False,
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "runtime_campaign_hook": False,
                "worker_dispatch_enabled": False,
                "raw_phone_included": False,
                "transcript_content_included": False,
                "recording_url_included": False,
                "recording_content_included": False,
            },
            "readiness": {
                "delivery_plan_ready": True,
                "live_sync_ready": False,
                "blockers": blockers,
            },
        }

    async def approve_outbox_delivery_plan(
        self,
        *,
        client_id: str,
        outbox_id: str,
        approved_by: str | None = None,
        requested_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        plan = await self.build_outbox_delivery_plan(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        allowed_blockers = {"live_sync_requires_future_phase", "network_dispatch_disabled"}
        blockers = set(plan.get("readiness", {}).get("blockers") or [])
        unexpected_blockers = blockers - allowed_blockers
        if unexpected_blockers:
            raise ValueError("crm delivery plan has blockers that require operator resolution")
        if plan.get("credential_reference", {}).get("configured") is not True:
            raise ValueError("crm delivery approval requires configured credential reference")
        if plan.get("safety", {}).get("external_execution") is True:
            raise ValueError("crm delivery plan is marked for external execution")
        if plan.get("safety", {}).get("worker_dispatch_enabled") is True:
            raise ValueError("crm delivery plan is marked for worker dispatch")

        plan_hash = self._stable_plan_hash(plan)
        plan_summary = self._build_delivery_plan_summary(plan)
        approval = await self.db.create_crm_delivery_approval(
            client_id=client_id,
            outbox_id=outbox_id,
            plan_hash=plan_hash,
            plan_summary=plan_summary,
            approved_by=approved_by,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )
        return {
            "approval": approval,
            "plan_hash": plan_hash,
            "external_execution": False,
            "worker_dispatch_enabled": False,
            "live_sync_enabled": False,
        }

    async def list_delivery_approvals(
        self,
        *,
        client_id: str,
        outbox_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        if outbox_id:
            item = await self.db.get_crm_sync_outbox_item(outbox_id)
            if not item:
                raise ValueError(f"crm outbox item not found: {outbox_id}")
            if item.get("client_id") != client_id:
                raise ValueError("crm outbox item is outside tenant scope")
        return await self.db.list_crm_delivery_approvals(
            client_id=client_id,
            outbox_id=outbox_id,
            status=status,
        )

    async def revoke_delivery_approval(
        self,
        *,
        client_id: str,
        approval_id: str,
        revoked_by: str | None = None,
        reason: str | None = None,
    ) -> dict:
        approval = await self.db.get_crm_delivery_approval(approval_id)
        if not approval:
            raise ValueError(f"crm delivery approval not found: {approval_id}")
        if approval.get("client_id") != client_id:
            raise ValueError("crm delivery approval is outside tenant scope")
        safe_reason = self._sanitize_error_message(reason or "operator revoked shadow approval")
        revoked = await self.db.revoke_crm_delivery_approval(
            client_id=client_id,
            approval_id=approval_id,
            revoked_by=revoked_by,
            reason=safe_reason,
        )
        return {
            "approval": revoked,
            "external_execution": False,
            "worker_dispatch_enabled": False,
            "live_sync_enabled": False,
        }

    async def get_outbox_live_readiness(
        self,
        *,
        client_id: str,
        outbox_id: str,
    ) -> dict:
        plan = await self.build_outbox_delivery_plan(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        approvals = await self.list_delivery_approvals(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        current_plan_hash = self._stable_plan_hash(plan)
        active_matches = [
            approval for approval in approvals
            if approval.get("status") == "approved_shadow"
            and not approval.get("revoked_at")
            and approval.get("plan_hash") == current_plan_hash
        ]
        active_stale = [
            approval for approval in approvals
            if approval.get("status") == "approved_shadow"
            and not approval.get("revoked_at")
            and approval.get("plan_hash") != current_plan_hash
        ]
        revoked_matches = [
            approval for approval in approvals
            if approval.get("status") == "revoked_shadow"
            and approval.get("plan_hash") == current_plan_hash
        ]
        blockers = ["live_sync_feature_disabled", "network_dispatch_disabled"]
        plan_blockers = [
            blocker for blocker in plan.get("readiness", {}).get("blockers", [])
            if blocker not in {"live_sync_requires_future_phase", "network_dispatch_disabled"}
        ]
        blockers.extend(plan_blockers)
        if not active_matches:
            if revoked_matches:
                blockers.append("delivery_approval_revoked")
            elif active_stale:
                blockers.append("delivery_approval_stale")
            else:
                blockers.append("delivery_approval_missing")

        future_prerequisites_met = bool(active_matches) and not plan_blockers
        active_approval = active_matches[0] if active_matches else None
        return {
            "readiness_version": "crm_live_readiness_shadow.v1",
            "client_id": client_id,
            "outbox_id": outbox_id,
            "job_id": plan.get("job_id"),
            "connection_id": plan.get("connection_id"),
            "campaign_id": plan.get("campaign_id"),
            "provider": plan.get("provider"),
            "object_type": plan.get("object_type"),
            "outbox_status": plan.get("outbox_status"),
            "record_count": plan.get("record_count"),
            "current_plan_hash": current_plan_hash,
            "approval": {
                "required": True,
                "active_approval_found": bool(active_approval),
                "approval_id": active_approval.get("id") if active_approval else None,
                "approval_status": active_approval.get("status") if active_approval else None,
                "plan_hash_matches": bool(active_approval),
                "revoked_matching_approvals": len(revoked_matches),
                "stale_active_approvals": len(active_stale),
            },
            "future_live_prerequisites_met": future_prerequisites_met,
            "ready_for_live_dispatch": False,
            "readiness": {
                "live_sync_ready": False,
                "blockers": blockers,
            },
            "safety": {
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "runtime_campaign_hook": False,
                "provider_payload_included": False,
                "request_body_included": False,
                "headers_included": False,
                "credential_value_included": False,
                "secret_value_included": False,
                "raw_phone_included": False,
                "transcript_content_included": False,
                "recording_url_included": False,
                "recording_content_included": False,
            },
        }

    async def build_outbox_provider_sandbox(
        self,
        *,
        client_id: str,
        outbox_id: str,
    ) -> dict:
        readiness = await self.get_outbox_live_readiness(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        allowed_blockers = {"live_sync_feature_disabled", "network_dispatch_disabled"}
        blockers = set(readiness.get("readiness", {}).get("blockers") or [])
        if not readiness.get("future_live_prerequisites_met"):
            raise ValueError("crm provider sandbox requires an active non-revoked delivery approval")
        if blockers - allowed_blockers:
            raise ValueError("crm provider sandbox readiness has blockers that require operator resolution")

        plan = await self.build_outbox_delivery_plan(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        contract = await self.get_provider_contract(
            client_id=client_id,
            connection_id=plan["connection_id"],
        )
        return {
            "sandbox_version": "crm_provider_sandbox_shadow.v1",
            "client_id": client_id,
            "outbox_id": outbox_id,
            "job_id": plan.get("job_id"),
            "connection_id": plan.get("connection_id"),
            "campaign_id": plan.get("campaign_id"),
            "provider": plan.get("provider"),
            "object_type": plan.get("object_type"),
            "record_count": plan.get("record_count"),
            "property_keys": plan.get("property_keys") or [],
            "current_plan_hash": readiness.get("current_plan_hash"),
            "approval": {
                "required": True,
                "approval_id": readiness.get("approval", {}).get("approval_id"),
                "approval_status": readiness.get("approval", {}).get("approval_status"),
                "plan_hash_matches": readiness.get("approval", {}).get("plan_hash_matches") is True,
            },
            "adapter": {
                "adapter_name": f"{plan.get('provider')}.crm_provider_sandbox",
                "provider": contract["provider"],
                "provider_display_name": contract["display_name"],
                "object_type": contract["object_type"],
                "operation": "upsert",
                "idempotency_field": contract["idempotency_strategy"]["field"],
                "supported_direction": "outbound",
                "live_adapter_loaded": False,
                "network_client_loaded": False,
            },
            "request_envelope": {
                "method": "POST",
                "url_included": False,
                "endpoint_template_included": False,
                "headers_included": False,
                "auth_header_included": False,
                "body_included": False,
                "body_preview_included": False,
                "provider_payload_included": False,
                "credential_reference_included": False,
            },
            "execution": {
                "mode": "sandbox_shadow",
                "sandbox_ready": True,
                "live_dispatch_ready": False,
                "dispatch_allowed": False,
                "live_sync_enabled": False,
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "runtime_campaign_hook": False,
            },
            "readiness": {
                "sandbox_ready": True,
                "live_sync_ready": False,
                "blockers": list(readiness.get("readiness", {}).get("blockers") or []),
            },
            "safety": {
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "runtime_campaign_hook": False,
                "provider_payload_included": False,
                "request_body_included": False,
                "headers_included": False,
                "credential_reference_included": False,
                "credential_value_included": False,
                "secret_value_included": False,
                "raw_phone_included": False,
                "transcript_content_included": False,
                "recording_url_included": False,
                "recording_content_included": False,
            },
        }

    async def build_outbox_dispatch_canary(
        self,
        *,
        client_id: str,
        outbox_id: str,
    ) -> dict:
        sandbox = await self.build_outbox_provider_sandbox(
            client_id=client_id,
            outbox_id=outbox_id,
        )
        record_count = int(sandbox.get("record_count") or 0)
        canary_candidate_count = 1 if record_count > 0 else 0
        blockers = ["live_dispatch_feature_disabled", "network_dispatch_disabled"]
        if canary_candidate_count == 0:
            blockers.append("canary_record_missing")

        return {
            "canary_version": "crm_dispatch_canary_shadow.v1",
            "client_id": client_id,
            "outbox_id": outbox_id,
            "job_id": sandbox.get("job_id"),
            "connection_id": sandbox.get("connection_id"),
            "campaign_id": sandbox.get("campaign_id"),
            "provider": sandbox.get("provider"),
            "object_type": sandbox.get("object_type"),
            "current_plan_hash": sandbox.get("current_plan_hash"),
            "approval": {
                "required": True,
                "approval_id": sandbox.get("approval", {}).get("approval_id"),
                "approval_status": sandbox.get("approval", {}).get("approval_status"),
                "plan_hash_matches": sandbox.get("approval", {}).get("plan_hash_matches") is True,
            },
            "adapter": {
                "adapter_name": sandbox.get("adapter", {}).get("adapter_name"),
                "provider": sandbox.get("adapter", {}).get("provider"),
                "object_type": sandbox.get("adapter", {}).get("object_type"),
                "operation": sandbox.get("adapter", {}).get("operation"),
                "idempotency_field": sandbox.get("adapter", {}).get("idempotency_field"),
                "live_adapter_loaded": False,
                "network_client_loaded": False,
            },
            "canary": {
                "mode": "canary_shadow",
                "max_records": 1,
                "candidate_record_count": canary_candidate_count,
                "available_record_count": record_count,
                "selection_strategy": "tenant_scoped_outbox_order",
                "record_identity_included": False,
                "record_body_included": False,
                "idempotency_key_included": False,
                "payload_hash_included": False,
            },
            "execution": {
                "mode": "canary_shadow",
                "canary_manifest_ready": canary_candidate_count == 1,
                "canary_dispatch_ready": False,
                "dispatch_allowed": False,
                "live_sync_enabled": False,
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "runtime_campaign_hook": False,
            },
            "readiness": {
                "canary_manifest_ready": canary_candidate_count == 1,
                "live_sync_ready": False,
                "blockers": blockers,
            },
            "required_future_controls": [
                "operator selects explicit canary record scope",
                "provider credential retrieval is implemented in a later phase",
                "network dispatch is enabled by a separate future flag",
                "rollback and provider-side dedupe runbook is approved",
            ],
            "safety": {
                "network_call_performed": False,
                "sent_to_provider": False,
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "runtime_campaign_hook": False,
                "provider_payload_included": False,
                "request_body_included": False,
                "headers_included": False,
                "credential_reference_included": False,
                "credential_value_included": False,
                "secret_value_included": False,
                "raw_phone_included": False,
                "transcript_content_included": False,
                "recording_url_included": False,
                "recording_content_included": False,
            },
        }

    async def run_outbox_shadow_worker(
        self,
        *,
        client_id: str,
        outbox_id: str,
        requested_by: str | None = None,
    ) -> dict:
        item = await self.db.get_crm_sync_outbox_item(outbox_id)
        if not item:
            raise ValueError(f"crm outbox item not found: {outbox_id}")
        if item.get("client_id") != client_id:
            raise ValueError("crm outbox item is outside tenant scope")
        if item.get("status") != "queued_shadow":
            raise ValueError("crm outbox item must be queued_shadow before shadow worker run")
        self._validate_outbox_item_safe(item)

        started = await self.db.start_crm_sync_outbox_shadow_processing(
            client_id=client_id,
            outbox_id=outbox_id,
            requested_by=requested_by,
        )
        self._validate_outbox_item_safe(started)
        provider_payload = (started.get("payload") or {}).get("provider_payload")
        provider_payload = provider_payload if isinstance(provider_payload, dict) else {}
        records = provider_payload.get("records") if isinstance(provider_payload.get("records"), list) else []
        result = {
            "status": "shadow_processed",
            "mode": "shadow",
            "provider": provider_payload.get("provider"),
            "records_seen": len(records),
            "sent_to_provider": False,
            "external_execution": False,
            "worker_dispatch_enabled": False,
            "runtime_campaign_hook": False,
            "live_sync_requires_future_phase": True,
        }
        completed = await self.db.complete_crm_sync_outbox_shadow_processing(
            client_id=client_id,
            outbox_id=outbox_id,
            result=result,
            requested_by=requested_by,
        )
        self._validate_outbox_item_safe(completed)
        return {
            "outbox_item": completed,
            "shadow_result": result,
            "external_execution": False,
            "worker_dispatch_enabled": False,
        }

    async def schedule_outbox_shadow_retry(
        self,
        *,
        client_id: str,
        outbox_id: str,
        error: str,
        next_retry_at: str | None = None,
        requested_by: str | None = None,
    ) -> dict:
        item = await self.db.get_crm_sync_outbox_item(outbox_id)
        if not item:
            raise ValueError(f"crm outbox item not found: {outbox_id}")
        if item.get("client_id") != client_id:
            raise ValueError("crm outbox item is outside tenant scope")
        if item.get("status") not in {"queued_shadow", "processing_shadow"}:
            raise ValueError("crm outbox item cannot be marked retry from its current status")
        self._validate_outbox_item_safe(item)
        safe_error = self._sanitize_error_message(error)
        updated = await self.db.mark_crm_sync_outbox_shadow_retry(
            client_id=client_id,
            outbox_id=outbox_id,
            error=safe_error,
            next_retry_at=next_retry_at,
            requested_by=requested_by,
        )
        self._validate_outbox_item_safe(updated)
        return {
            "outbox_item": updated,
            "external_execution": False,
            "worker_dispatch_enabled": False,
        }

    async def requeue_outbox_shadow_retry(
        self,
        *,
        client_id: str,
        outbox_id: str,
        requested_by: str | None = None,
    ) -> dict:
        item = await self.db.get_crm_sync_outbox_item(outbox_id)
        if not item:
            raise ValueError(f"crm outbox item not found: {outbox_id}")
        if item.get("client_id") != client_id:
            raise ValueError("crm outbox item is outside tenant scope")
        if item.get("status") != "retry_scheduled_shadow":
            raise ValueError("crm outbox item must be retry_scheduled_shadow before requeue")
        self._validate_outbox_item_safe(item)
        updated = await self.db.requeue_crm_sync_outbox_shadow_retry(
            client_id=client_id,
            outbox_id=outbox_id,
            requested_by=requested_by,
        )
        self._validate_outbox_item_safe(updated)
        return {
            "outbox_item": updated,
            "external_execution": False,
            "worker_dispatch_enabled": False,
        }

    async def dead_letter_outbox_shadow_item(
        self,
        *,
        client_id: str,
        outbox_id: str,
        error: str,
        requested_by: str | None = None,
    ) -> dict:
        item = await self.db.get_crm_sync_outbox_item(outbox_id)
        if not item:
            raise ValueError(f"crm outbox item not found: {outbox_id}")
        if item.get("client_id") != client_id:
            raise ValueError("crm outbox item is outside tenant scope")
        if item.get("status") == "completed_shadow":
            raise ValueError("completed crm outbox item cannot be dead-lettered")
        self._validate_outbox_item_safe(item)
        safe_error = self._sanitize_error_message(error)
        updated = await self.db.mark_crm_sync_outbox_shadow_dead_letter(
            client_id=client_id,
            outbox_id=outbox_id,
            error=safe_error,
            requested_by=requested_by,
        )
        self._validate_outbox_item_safe(updated)
        return {
            "outbox_item": updated,
            "external_execution": False,
            "worker_dispatch_enabled": False,
        }

    def _build_result_record(self, result: dict) -> dict:
        phone = str(result.get("phone") or "")
        lead_data = result.get("lead_data") if isinstance(result.get("lead_data"), dict) else {}
        callback_value = result.get("callback") or result.get("timeline") or result.get("callback_time")
        return {
            "result_id": result.get("id"),
            "lead_key": result.get("id"),
            "source_call_key_sha256": self._stable_fingerprint(result.get("lead_id")),
            "lead_name": result.get("name") or result.get("lead_name"),
            "phone_redacted": self._redact_phone(phone),
            "phone_sha256": self._phone_fingerprint(phone),
            "status": result.get("status"),
            "outcome": result.get("outcome"),
            "interested": result.get("interested"),
            "budget": result.get("budget") or lead_data.get("budget"),
            "callback_time": callback_value,
            "has_transcript": bool(result.get("has_transcript")),
            "has_recording": bool(result.get("has_recording")),
            "transcript_content": None,
            "recording_url": None,
            "recording_content": None,
        }

    def _render_provider_payload(self, provider: str, preview: dict | None) -> dict:
        safe_preview = preview or {
            "campaign": {"id": None, "result_count": 0},
            "records": [],
            "summary": {"total_records": 0},
        }
        records = safe_preview.get("records") or []
        if provider == "hubspot":
            object_type = "contacts"
        elif provider == "salesforce":
            object_type = "Lead"
        elif provider == "zoho":
            object_type = "Leads"
        else:
            object_type = "custom_webhook.leads"

        rendered_records = []
        for record in records:
            rendered_records.append({
                "external_id": record.get("source_call_key_sha256") or record.get("result_id"),
                "object_type": object_type,
                "properties": {
                    "lead_name": record.get("lead_name"),
                    "phone_redacted": record.get("phone_redacted"),
                    "phone_sha256": record.get("phone_sha256"),
                    "call_status": record.get("status"),
                    "call_outcome": record.get("outcome"),
                    "interested": record.get("interested"),
                    "budget": record.get("budget"),
                    "callback_time": record.get("callback_time"),
                    "has_transcript": record.get("has_transcript"),
                    "has_recording": record.get("has_recording"),
                },
            })

        return {
            "provider": provider,
            "object_type": object_type,
            "dry_run": True,
            "external_execution": False,
            "campaign": safe_preview.get("campaign") or {},
            "summary": safe_preview.get("summary") or {},
            "records": rendered_records,
            "redaction": safe_preview.get("redaction") or {},
        }

    def _validate_credential_reference(self, connection: dict) -> dict:
        config = connection.get("config") if isinstance(connection.get("config"), dict) else {}
        reference = config.get("credential_reference") if isinstance(config.get("credential_reference"), dict) else {}
        if not connection.get("secrets_configured"):
            raise ValueError("CRM connection secret reference is required before preflight")
        if not reference.get("configured"):
            raise ValueError("CRM credential reference is not configured")
        if not reference.get("external_secret_storage"):
            raise ValueError("CRM credential reference must use external secret storage")
        if reference.get("secret_value_stored"):
            raise ValueError("CRM credential reference must not store secret values")
        vault_provider = (reference.get("vault_provider") or "").strip().lower()
        if vault_provider not in SUPPORTED_SECRET_REFERENCE_PROVIDERS:
            raise ValueError(f"unsupported credential reference provider: {vault_provider}")
        if not reference.get("reference_hash"):
            raise ValueError("CRM credential reference hash is required")
        reference_id = str(reference.get("reference_id") or "")
        if self._looks_like_raw_secret(reference_id):
            raise ValueError("CRM credential reference id looks like a raw secret")
        metadata = reference.get("metadata") if isinstance(reference.get("metadata"), dict) else {}
        sensitive_path = self._find_sensitive_config_key(metadata)
        if sensitive_path:
            raise ValueError(f"CRM credential metadata contains sensitive key: {sensitive_path}")
        secret_value_path = self._find_raw_secret_value(metadata)
        if secret_value_path:
            raise ValueError(f"CRM credential metadata contains raw secret value: {secret_value_path}")
        return {
            "name": "credential_reference",
            "status": "passed",
            "vault_provider": vault_provider,
            "reference_hash": reference.get("reference_hash"),
            "secret_value_stored": False,
        }

    def _validate_job_contract(self, payload: dict) -> dict:
        if payload.get("external_execution") is True:
            raise ValueError("CRM job payload is marked for external execution")
        if payload.get("runtime_campaign_hook") is True:
            raise ValueError("CRM job payload is marked for runtime campaign hooks")
        if payload.get("sync_scope", {}).get("transcript_content") is True:
            raise ValueError("CRM job payload cannot export transcript content in this phase")
        if payload.get("sync_scope", {}).get("recording_content") is True:
            raise ValueError("CRM job payload cannot export recording content in this phase")
        return {
            "name": "job_contract",
            "status": "passed",
            "dry_run": True,
            "external_execution": False,
            "runtime_campaign_hook": False,
        }

    def _validate_payload_preview_safe(self, preview: dict) -> dict:
        if preview.get("external_execution") is True:
            raise ValueError("CRM payload preview is marked for external execution")
        redaction = preview.get("redaction") if isinstance(preview.get("redaction"), dict) else {}
        if redaction.get("pii_export_enabled") is True:
            raise ValueError("CRM payload preview has PII export enabled")
        records = preview.get("records") or []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"CRM payload preview record {index} is invalid")
            if record.get("phone"):
                raise ValueError(f"CRM payload preview record {index} contains raw phone")
            for blocked_key in ("transcript_content", "recording_url", "recording_content"):
                if record.get(blocked_key):
                    raise ValueError(
                        f"CRM payload preview record {index} contains {blocked_key}"
                    )
        return {
            "name": "payload_preview_redaction",
            "status": "passed",
            "records_checked": len(records),
            "transcript_content": False,
            "recording_content": False,
            "recording_url": False,
        }

    def _validate_provider_payload_safe(self, provider_payload: dict) -> dict:
        if provider_payload.get("external_execution") is True:
            raise ValueError("CRM provider payload is marked for external execution")
        records = provider_payload.get("records") or []
        for index, record in enumerate(records):
            properties = record.get("properties") if isinstance(record, dict) else {}
            if not isinstance(properties, dict):
                raise ValueError(f"CRM provider payload record {index} properties are invalid")
            for blocked_key in ("phone", "transcript_content", "recording_url", "recording_content"):
                if properties.get(blocked_key):
                    raise ValueError(
                        f"CRM provider payload record {index} contains {blocked_key}"
                    )
        return {
            "name": "provider_payload_redaction",
            "status": "passed",
            "records_checked": len(records),
            "sent_to_provider": False,
        }

    def _validate_outbox_item_safe(self, item: dict) -> None:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if payload.get("external_execution") is True:
            raise ValueError("CRM outbox payload is marked for external execution")
        if payload.get("runtime_campaign_hook") is True:
            raise ValueError("CRM outbox payload is marked for runtime campaign hooks")
        if payload.get("worker_dispatch_enabled") is True:
            raise ValueError("CRM outbox payload is marked for worker dispatch")
        provider_payload = payload.get("provider_payload")
        if provider_payload:
            self._validate_provider_payload_safe(provider_payload)
        preflight = payload.get("preflight")
        if isinstance(preflight, dict):
            if preflight.get("external_execution") is True:
                raise ValueError("CRM outbox preflight is marked for external execution")
            if preflight.get("runtime_campaign_hook") is True:
                raise ValueError("CRM outbox preflight is marked for runtime campaign hooks")

    def _sanitize_error_message(self, error: str) -> str:
        normalized = str(error or "shadow worker retry requested").strip()
        if not normalized:
            normalized = "shadow worker retry requested"
        for prefix in RAW_SECRET_PREFIXES:
            if normalized.lower().startswith(prefix):
                return "redacted_credential_like_error"
        if self._looks_like_raw_secret(normalized):
            return "redacted_credential_like_error"
        return normalized[:500]

    def _stable_plan_hash(self, plan: dict) -> str:
        canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _build_delivery_plan_summary(self, plan: dict) -> dict:
        return {
            "plan_version": plan.get("plan_version"),
            "provider": plan.get("provider"),
            "object_type": plan.get("object_type"),
            "operation": plan.get("operation"),
            "record_count": plan.get("record_count"),
            "property_keys": list(plan.get("property_keys") or []),
            "outbox_status": plan.get("outbox_status"),
            "credential_reference": {
                "configured": bool(plan.get("credential_reference", {}).get("configured")),
                "vault_provider": plan.get("credential_reference", {}).get("vault_provider"),
                "reference_hash_present": bool(
                    plan.get("credential_reference", {}).get("reference_hash_present")
                ),
                "reference_id_included": False,
                "secret_value_included": False,
            },
            "safety": {
                "external_execution": False,
                "worker_dispatch_enabled": False,
                "network_call_performed": False,
                "sent_to_provider": False,
                "provider_payload_included": False,
                "request_body_included": False,
            },
            "readiness": {
                "live_sync_ready": False,
                "blockers": list(plan.get("readiness", {}).get("blockers") or []),
            },
        }

    def _summarize_records(self, records: list[dict]) -> dict:
        status_counts: dict[str, int] = {}
        interested_count = 0
        for record in records:
            status = str(record.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            if str(record.get("interested") or "").strip().lower() in {"yes", "true", "interested"}:
                interested_count += 1
        return {
            "total_records": len(records),
            "status_counts": status_counts,
            "interested_count": interested_count,
            "transcripts_available": sum(1 for record in records if record.get("has_transcript")),
            "recordings_available": sum(1 for record in records if record.get("has_recording")),
        }

    def _redact_phone(self, phone: str) -> str | None:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            return None
        return f"***{digits[-4:]}"

    def _phone_fingerprint(self, phone: str) -> str | None:
        normalized = "".join(ch for ch in phone if ch.isdigit())
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _stable_fingerprint(self, value: Any) -> str | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _normalize_provider(self, provider: str) -> str:
        normalized = (provider or "").strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported CRM provider: {provider}")
        return normalized

    def _build_secret_reference(
        self,
        *,
        vault_provider: str,
        reference_id: str,
        rotation_due_at: str | None,
        metadata: dict[str, Any],
    ) -> dict:
        provider = (vault_provider or "").strip().lower()
        if provider not in SUPPORTED_SECRET_REFERENCE_PROVIDERS:
            raise ValueError(f"unsupported credential reference provider: {vault_provider}")
        reference = (reference_id or "").strip()
        if not reference:
            raise ValueError("credential reference id is required")
        if self._looks_like_raw_secret(reference):
            raise ValueError("credential reference id looks like a raw secret")
        safe_metadata = self._sanitize_public_config(metadata or {})
        return {
            "configured": True,
            "vault_provider": provider,
            "reference_id": reference,
            "reference_hash": self._stable_fingerprint(reference),
            "rotation_due_at": rotation_due_at,
            "metadata": safe_metadata,
            "external_secret_storage": True,
            "secret_value_stored": False,
        }

    def _normalize_direction(self, direction: str) -> str:
        normalized = (direction or "outbound").strip().lower()
        if normalized not in {"outbound", "bidirectional_shadow"}:
            raise ValueError("CRM sync direction must be outbound or bidirectional_shadow")
        return normalized

    def _sanitize_public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        sensitive_path = self._find_sensitive_config_key(config)
        if sensitive_path:
            raise ValueError(
                f"CRM credentials must not be stored in public config: {sensitive_path}"
            )
        secret_value_path = self._find_raw_secret_value(config)
        if secret_value_path:
            raise ValueError(
                f"CRM credential-like values must use a secret reference: {secret_value_path}"
            )
        return dict(config)

    def _find_sensitive_config_key(self, value: Any, prefix: str = "") -> str | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                normalized_key = key_text.strip().lower()
                path = f"{prefix}.{key_text}" if prefix else key_text
                if normalized_key in SENSITIVE_CONFIG_KEYS:
                    return path
                found = self._find_sensitive_config_key(nested, path)
                if found:
                    return found
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found = self._find_sensitive_config_key(nested, f"{prefix}[{index}]")
                if found:
                    return found
        return None

    def _find_raw_secret_value(self, value: Any, prefix: str = "") -> str | None:
        if isinstance(value, dict):
            for key, nested in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                found = self._find_raw_secret_value(nested, path)
                if found:
                    return found
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found = self._find_raw_secret_value(nested, f"{prefix}[{index}]")
                if found:
                    return found
        elif isinstance(value, str) and self._looks_like_raw_secret(value):
            return prefix or "value"
        return None

    def _looks_like_raw_secret(self, value: str) -> bool:
        normalized = value.strip()
        lowered = normalized.lower()
        if lowered.startswith(RAW_SECRET_PREFIXES):
            return True
        compact = "".join(ch for ch in normalized if ch.isalnum())
        return len(compact) >= 48 and "/" not in normalized and ":" not in normalized
