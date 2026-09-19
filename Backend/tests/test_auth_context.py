import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from platform_migration.auth_context import (
    build_tenant_enforcement_readiness,
    build_ws_event_scope_shadow_manifest,
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
    build_tenant_context,
    should_reject_http_request,
)


def _token(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def encode(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


def _final_activation_env() -> dict[str, str]:
    return {
        "FEATURE_AUTH_ENFORCE_BACKEND": "true",
        "FEATURE_TENANT_SCOPED_READS": "true",
        "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
        "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
        "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
        "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
        "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
        "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
        "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
        "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
        "FEATURE_TENANT_ROLLBACK_DRILL_READINESS": "true",
        "FEATURE_TENANT_ROLLOUT_EVIDENCE_BUNDLE": "true",
        "FEATURE_TENANT_CANARY_OBSERVATION_CHECKLIST": "true",
        "FEATURE_TENANT_PRODUCTION_GO_NO_GO_GATE": "true",
        "FEATURE_TENANT_PRODUCTION_ACTIVATION_CONTRACT_STUB": "true",
        "FEATURE_TENANT_PRODUCTION_ACTIVATION_PERMISSION_SHADOW": "true",
        "FEATURE_TENANT_PRODUCTION_ACTIVATION_PAYLOAD_DRY_RUN": "true",
        "FEATURE_TENANT_PRODUCTION_ACTIVATION_READINESS": "true",
        "FEATURE_TENANT_PRODUCTION_ACTIVATION_ROLLBACK_CONFIRMATION": "true",
        "FEATURE_TENANT_CONTROLLED_HANDOFF_READINESS": "true",
        "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
        "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
        "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
        "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
        "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
        "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
        "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
        "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
        "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
        "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
    }


class AuthContextTest(unittest.TestCase):
    def test_anonymous_context_does_not_enforce_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            context = build_tenant_context()
            self.assertEqual(context.auth_state, "anonymous")
            self.assertFalse(context.is_verified)
            self.assertFalse(should_reject_http_request(context, "/api/campaigns"))

    def test_matching_api_key_is_verified_admin_context(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        self.assertTrue(context.is_verified)
        self.assertTrue(context.is_admin)
        self.assertEqual(context.tenant_id, "client-1")
        self.assertEqual(context.source, "x-api-key")

    def test_bearer_payload_is_audit_only_and_unverified(self):
        bearer = _token(
            {
                "email": "client@example.com",
                "sub": "firebase-user-id",
                "role": "client",
                "tenant_id": "tenant-from-token",
            }
        )
        context = build_tenant_context(
            headers={"Authorization": f"Bearer {bearer}"},
            query={"client_id": "tenant-from-query"},
        )

        self.assertEqual(context.auth_state, "bearer_unverified")
        self.assertEqual(context.user_email, "client@example.com")
        self.assertEqual(context.role, "client")
        self.assertEqual(context.tenant_id, "tenant-from-token")
        self.assertIn("bearer_unverified", context.warnings)
        self.assertIn("tenant_claim_mismatch", context.warnings)

    def test_query_token_supports_websocket_admin_context_without_verification(self):
        token = _token(
            {
                "email": "admin@example.com",
                "sub": "firebase-admin-id",
                "role": "admin",
            }
        )
        context = build_tenant_context(query={"access_token": token})

        self.assertEqual(context.auth_state, "bearer_unverified")
        self.assertEqual(context.source, "query_token")
        self.assertEqual(context.role, "admin")
        self.assertTrue(context.is_admin)
        self.assertFalse(context.is_verified)
        self.assertIn("token_in_query", context.warnings)
        self.assertIn("bearer_unverified", context.warnings)

    def test_configured_admin_email_supports_dev_admin_context(self):
        token = _token(
            {
                "email": "admin@example.com",
                "sub": "firebase-admin-id",
            }
        )

        with patch.dict(
            os.environ,
            {
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
                "FEATURE_AUTH_ENFORCE_BACKEND": "false",
            },
            clear=True,
        ):
            context = build_tenant_context(headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(context.auth_state, "bearer_unverified")
        self.assertEqual(context.role, "admin")
        self.assertTrue(context.is_admin)
        self.assertFalse(context.is_verified)
        self.assertIn("admin_email_unverified", context.warnings)

    def test_configured_admin_email_does_not_bypass_backend_auth_enforcement(self):
        token = _token(
            {
                "email": "admin@example.com",
                "sub": "firebase-admin-id",
            }
        )

        with patch.dict(
            os.environ,
            {
                "PLATFORM_ADMIN_EMAILS": "admin@example.com",
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
            },
            clear=True,
        ):
            context = build_tenant_context(headers={"Authorization": f"Bearer {token}"})
            rejected = should_reject_http_request(context, "/api/tenant/security-leak-audit/readiness")

        self.assertEqual(context.auth_state, "bearer_unverified")
        self.assertEqual(context.role, "unknown")
        self.assertFalse(context.is_admin)
        self.assertTrue(rejected)

    def test_unverified_user_email_is_not_treated_as_verified_auth(self):
        context = build_tenant_context(
            headers={"X-User-Email": "client@example.com"},
            query={"client_id": "client-1"},
        )

        self.assertEqual(context.auth_state, "identity_unverified")
        self.assertEqual(context.user_email, "client@example.com")
        self.assertFalse(context.is_verified)
        self.assertIn("identity_unverified", context.warnings)

    def test_auth_enforcement_flag_rejects_unverified_non_public_paths(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {"FEATURE_AUTH_ENFORCE_BACKEND": "true"}, clear=True):
            self.assertTrue(should_reject_http_request(context, "/api/campaigns"))
            self.assertFalse(should_reject_http_request(context, "/health"))

    def test_tenant_enforcement_readiness_is_shadow_only_and_secret_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(os.environ, {}, clear=True):
            readiness = build_tenant_enforcement_readiness(context, path="/api/crm/outbox")

        self.assertEqual(readiness["manifest_version"], "tenant_enforcement_readiness.v1")
        self.assertTrue(readiness["auth"]["verified"])
        self.assertTrue(readiness["tenant"]["tenant_id_present"])
        self.assertFalse(readiness["tenant"]["tenant_value_included"])
        self.assertTrue(readiness["readiness"]["tenant_scope_required"])
        self.assertTrue(readiness["readiness"]["backend_auth_ready"])
        self.assertTrue(readiness["readiness"]["tenant_scope_ready"])
        self.assertTrue(readiness["readiness"]["shadow_enforcement_ready"])
        self.assertFalse(readiness["readiness"]["active_enforcement"])
        self.assertIn("auth.enforce_backend_disabled", readiness["readiness"]["blockers"])
        self.assertIn("tenant.scoped_reads_disabled", readiness["readiness"]["blockers"])
        self.assertFalse(readiness["safety"]["runtime_enforcement_changed"])
        self.assertFalse(readiness["safety"]["db_query_executed"])
        self.assertFalse(readiness["safety"]["tenant_data_returned"])
        self.assertFalse(readiness["safety"]["tenant_id_included"])
        self.assertNotIn("secret", str(readiness))
        self.assertNotIn("client-1", str(readiness))

    def test_tenant_enforcement_readiness_reports_unverified_and_mismatch_blockers(self):
        bearer = _token(
            {
                "email": "client@example.com",
                "sub": "firebase-user-id",
                "role": "client",
                "tenant_id": "client-1",
            }
        )
        context = build_tenant_context(
            headers={"Authorization": f"Bearer {bearer}"},
            query={"client_id": "client-2"},
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
            },
            clear=True,
        ):
            readiness = build_tenant_enforcement_readiness(context, path="/api/campaigns")

        blockers = readiness["readiness"]["blockers"]
        self.assertFalse(readiness["auth"]["verified"])
        self.assertTrue(readiness["auth"]["user_email_present"])
        self.assertFalse(readiness["auth"]["user_email_included"])
        self.assertFalse(readiness["tenant"]["tenant_match"])
        self.assertFalse(readiness["readiness"]["backend_auth_ready"])
        self.assertFalse(readiness["readiness"]["tenant_scope_ready"])
        self.assertFalse(readiness["readiness"]["shadow_enforcement_ready"])
        self.assertTrue(readiness["readiness"]["active_enforcement"])
        self.assertTrue(readiness["readiness"]["would_reject_if_auth_enforced"])
        self.assertTrue(readiness["readiness"]["would_reject_if_tenant_scoped_reads_enforced"])
        self.assertIn("verified_backend_identity_missing", blockers)
        self.assertIn("tenant_claim_mismatch", blockers)
        self.assertNotIn("client@example.com", str(readiness))
        self.assertNotIn("client-1", str(readiness))
        self.assertNotIn("client-2", str(readiness))

    def test_tenant_scoped_read_guard_decision_is_shadow_only_and_secret_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
            },
            clear=True,
        ):
            decision = build_tenant_scoped_read_guard_decision(
                context,
                resource_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
            )

        self.assertEqual(decision["decision_version"], "tenant_scoped_read_guard.v1")
        self.assertTrue(decision["mode"]["shadow_only"])
        self.assertFalse(decision["mode"]["runtime_enforcement_changed"])
        self.assertFalse(decision["mode"]["db_write_performed"])
        self.assertTrue(decision["decision"]["admin_override_allowed"])
        self.assertTrue(decision["decision"]["requested_tenant_allowed_if_enforced"])
        self.assertTrue(decision["decision"]["current_requester_allowed_if_enforced"])
        self.assertEqual(decision["decision"]["blockers"], [])
        self.assertFalse(decision["resource"]["owner_tenant_included"])
        self.assertFalse(decision["resource"]["resource_id_included"])
        self.assertFalse(decision["resource"]["payload_included"])
        self.assertFalse(decision["requester"]["tenant_included"])
        self.assertFalse(decision["requester"]["user_email_included"])
        self.assertFalse(decision["safety"]["tenant_data_returned"])
        self.assertFalse(decision["safety"]["cross_tenant_data_included"])
        self.assertNotIn("secret", str(decision))
        self.assertNotIn("client-1", str(decision))
        self.assertNotIn("operator-tenant", str(decision))

    def test_tenant_scoped_read_guard_denies_unverified_admin_claim(self):
        bearer = _token(
            {
                "email": "admin@example.com",
                "role": "admin",
                "tenant_id": "client-1",
            }
        )
        context = build_tenant_context(
            headers={"Authorization": f"Bearer {bearer}"},
            query={"client_id": "client-1"},
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
            },
            clear=True,
        ):
            decision = build_tenant_scoped_read_guard_decision(
                context,
                resource_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
            )

        self.assertTrue(context.is_admin)
        self.assertFalse(context.is_verified)
        self.assertFalse(decision["requester"]["verified_admin"])
        self.assertFalse(decision["decision"]["admin_override_allowed"])
        self.assertFalse(decision["decision"]["current_requester_allowed_if_enforced"])
        self.assertTrue(decision["decision"]["would_reject_current_requester_if_enforced"])
        self.assertIn("verified_backend_identity_missing", decision["decision"]["blockers"])
        self.assertIn("current_requester_would_be_denied", decision["decision"]["blockers"])
        self.assertNotIn("admin@example.com", str(decision))
        self.assertNotIn("client-1", str(decision))

    def test_tenant_scoped_read_guard_reports_missing_owner_without_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(os.environ, {}, clear=True):
            decision = build_tenant_scoped_read_guard_decision(
                context,
                resource_found=True,
                owner_tenant_id=None,
                requested_tenant_id="client-1",
            )

        self.assertTrue(decision["resource"]["found"])
        self.assertFalse(decision["resource"]["owner_tenant_present"])
        self.assertFalse(decision["decision"]["requested_tenant_allowed_if_enforced"])
        self.assertTrue(decision["decision"]["current_requester_allowed_if_enforced"])
        self.assertIn("resource_owner_missing", decision["decision"]["blockers"])
        self.assertIn("tenant.scoped_read_guard_shadow_disabled", decision["decision"]["blockers"])
        self.assertFalse(decision["safety"]["runtime_enforcement_changed"])
        self.assertFalse(decision["safety"]["owner_tenant_included"])
        self.assertFalse(decision["safety"]["resource_payload_returned"])
        self.assertNotIn("client-1", str(decision))
        self.assertNotIn("operator-tenant", str(decision))

    def test_tenant_scoped_read_policy_manifest_is_admin_shadow_and_payload_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_SCOPED_READ_POLICY_SHADOW": "true",
            },
            clear=True,
        ):
            policy = build_tenant_scoped_read_policy_manifest(context)

        resource_types = {item["resource_type"] for item in policy["policies"]}
        self.assertEqual(policy["manifest_version"], "tenant_scoped_read_policy.v1")
        self.assertTrue(policy["readiness"]["policy_manifest_ready"])
        self.assertTrue(policy["readiness"]["shadow_only"])
        self.assertEqual(policy["readiness"]["blockers"], [])
        self.assertGreaterEqual(policy["coverage"]["policy_count"], 8)
        self.assertEqual(policy["coverage"]["policy_count"], policy["coverage"]["guard_ready_count"])
        self.assertEqual(policy["coverage"]["policy_count"], policy["coverage"]["owner_lookup_ready_count"])
        self.assertTrue(policy["coverage"]["all_policies_shadow_only"])
        self.assertFalse(policy["coverage"]["runtime_enforcement_changed"])
        self.assertTrue({
            "agent",
            "campaign",
            "call_result",
            "live_call_state",
            "recording_asset",
            "phone_number",
            "crm_outbox",
        }.issubset(resource_types))
        self.assertTrue(all(item["shadow_only"] for item in policy["policies"]))
        self.assertTrue(all(item["ownership_required"] for item in policy["policies"]))
        self.assertTrue(all(item["payload_included"] is False for item in policy["policies"]))
        self.assertTrue(all(item["owner_tenant_included"] is False for item in policy["policies"]))
        self.assertTrue(all(item["resource_id_included"] is False for item in policy["policies"]))
        self.assertGreaterEqual(policy["coverage"]["excluded_surface_count"], 4)
        self.assertFalse(policy["safety"]["runtime_enforcement_changed"])
        self.assertFalse(policy["safety"]["db_query_executed"])
        self.assertFalse(policy["safety"]["db_write_performed"])
        self.assertFalse(policy["safety"]["tenant_data_returned"])
        self.assertFalse(policy["safety"]["phone_number_included"])
        self.assertFalse(policy["safety"]["transcript_content_included"])
        self.assertFalse(policy["safety"]["recording_url_included"])
        self.assertFalse(policy["safety"]["crm_payload_included"])
        self.assertNotIn("secret", str(policy))
        self.assertNotIn("operator-tenant", str(policy))

    def test_tenant_scoped_read_policy_manifest_reports_non_admin_blocker_without_values(self):
        context = build_tenant_context(
            headers={"X-User-Email": "client@example.com"},
            query={"client_id": "client-1"},
        )

        with patch.dict(os.environ, {}, clear=True):
            policy = build_tenant_scoped_read_policy_manifest(context)

        blockers = policy["readiness"]["blockers"]
        self.assertFalse(policy["requester"]["verified"])
        self.assertFalse(policy["requester"]["is_admin"])
        self.assertTrue(policy["requester"]["user_email_present"])
        self.assertFalse(policy["requester"]["user_email_included"])
        self.assertIn("verified_backend_identity_missing", blockers)
        self.assertIn("admin_context_required", blockers)
        self.assertIn("tenant.scoped_read_policy_shadow_disabled", blockers)
        self.assertFalse(policy["safety"]["tenant_data_returned"])
        self.assertFalse(policy["safety"]["cross_tenant_data_included"])
        self.assertNotIn("client@example.com", str(policy))
        self.assertNotIn("client-1", str(policy))

    def test_ws_event_scope_shadow_manifest_is_payload_free(self):
        with patch.dict(
            os.environ,
            {
                "FEATURE_WS_SCOPED_EVENTS": "true",
                "FEATURE_WS_SCOPED_EVENTS_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_ws_event_scope_shadow_manifest(
                event_type="call completed",
                broadcast_mode="client",
                target_client_id="client-1",
            )

        self.assertEqual(manifest["manifest_version"], "ws_event_scope_shadow.v1")
        self.assertEqual(manifest["event"]["type"], "call_completed")
        self.assertTrue(manifest["delivery"]["client_broadcast"])
        self.assertTrue(manifest["delivery"]["target_tenant_present"])
        self.assertTrue(manifest["decision"]["scoped_event_ready"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["event"]["payload_included"])
        self.assertFalse(manifest["event"]["campaign_id_included"])
        self.assertFalse(manifest["event"]["lead_name_included"])
        self.assertFalse(manifest["event"]["transcript_content_included"])
        self.assertFalse(manifest["event"]["result_payload_included"])
        self.assertFalse(manifest["delivery"]["target_tenant_included"])
        self.assertFalse(manifest["safety"]["runtime_delivery_changed"])
        self.assertFalse(manifest["safety"]["websocket_payload_changed"])
        self.assertFalse(manifest["safety"]["audio_contract_changed"])
        self.assertNotIn("client-1", str(manifest))

    def test_ws_event_scope_shadow_manifest_flags_global_broadcast_review(self):
        with patch.dict(os.environ, {}, clear=True):
            manifest = build_ws_event_scope_shadow_manifest(
                event_type="call_completed",
                broadcast_mode="all",
                target_client_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertTrue(manifest["delivery"]["global_broadcast"])
        self.assertFalse(manifest["delivery"]["target_tenant_present"])
        self.assertIn("global_broadcast_requires_admin_channel_review", blockers)
        self.assertIn("ws.scoped_events_disabled", blockers)
        self.assertIn("ws.scoped_events_shadow_disabled", blockers)
        self.assertFalse(manifest["safety"]["message_payload_returned"])
        self.assertFalse(manifest["safety"]["target_tenant_included"])

    def test_recording_access_shadow_manifest_is_path_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_recording_access_shadow_manifest(
                context,
                request_path="/recordings/client-1/private-call.wav",
            )

        self.assertEqual(manifest["manifest_version"], "recording_access_shadow.v1")
        self.assertTrue(manifest["recording"]["recording_path_requested"])
        self.assertEqual(manifest["recording"]["file_extension"], "wav")
        self.assertFalse(manifest["recording"]["path_included"])
        self.assertFalse(manifest["recording"]["filename_included"])
        self.assertFalse(manifest["recording"]["recording_url_included"])
        self.assertFalse(manifest["recording"]["recording_bytes_included"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertIn("recording_owner_lookup_required", manifest["decision"]["blockers"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["recording_path_included"])
        self.assertFalse(manifest["safety"]["recording_filename_included"])
        self.assertFalse(manifest["safety"]["recording_bytes_included"])
        self.assertNotIn("/recordings/client-1/private-call.wav", str(manifest))
        self.assertNotIn("private-call.wav", str(manifest))
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("operator-tenant", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_recording_access_shadow_manifest_disabled_has_safe_blockers(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_recording_access_shadow_manifest(
                context,
                request_path="/recordings/private.wav",
            )

        blockers = manifest["decision"]["blockers"]
        self.assertIn("recordings.access_shadow_disabled", blockers)
        self.assertIn("auth.enforce_backend_disabled", blockers)
        self.assertIn("tenant.scoped_reads_disabled", blockers)
        self.assertIn("verified_backend_identity_missing", blockers)
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertNotIn("private.wav", str(manifest))

    def test_recording_owner_lookup_shadow_manifest_is_metadata_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_recording_owner_lookup_shadow_manifest(
                context,
                recording_found=True,
                owner_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "recording_owner_lookup_shadow.v1")
        self.assertTrue(manifest["recording"]["found"])
        self.assertTrue(manifest["recording"]["owner_tenant_present"])
        self.assertTrue(manifest["recording"]["campaign_id_present"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["current_requester_allowed_if_enforced"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertTrue(manifest["safety"]["db_query_executed"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["recording_path_included"])
        self.assertFalse(manifest["safety"]["recording_filename_included"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["storage_path_included"])
        self.assertFalse(manifest["safety"]["recording_bytes_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private.wav", str(manifest))

    def test_recording_owner_lookup_shadow_manifest_tracks_requested_tenant_mismatch_without_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-2"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_recording_owner_lookup_shadow_manifest(
                context,
                recording_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-2",
                campaign_id_present=True,
            )

        self.assertFalse(manifest["decision"]["tenant_match_for_requested_tenant"])
        self.assertIn("tenant_mismatch_for_requested_tenant", manifest["decision"]["blockers"])
        self.assertTrue(manifest["decision"]["current_requester_allowed_if_enforced"])
        self.assertFalse(manifest["recording"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertFalse(manifest["safety"]["requested_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("client-2", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_recording_owner_lookup_shadow_manifest_disabled_has_safe_blockers(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_recording_owner_lookup_shadow_manifest(
                context,
                recording_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertIn("recordings.access_shadow_disabled", blockers)
        self.assertIn("recordings.owner_lookup_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertIn("verified_backend_identity_missing", blockers)
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["db_query_executed"])
        self.assertFalse(manifest["safety"]["recording_url_included"])

    def test_recording_access_enforcement_readiness_manifest_is_shadow_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_recording_access_enforcement_readiness_manifest(
                context,
                recording_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "recording_access_enforcement_readiness.v1")
        self.assertTrue(manifest["recording"]["ownership_lookup_ready"])
        self.assertTrue(manifest["decision"]["would_allow_if_recording_access_enforced"])
        self.assertFalse(manifest["decision"]["would_reject_if_recording_access_enforced"])
        self.assertTrue(manifest["decision"]["ready_for_future_enforcement"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertTrue(manifest["decision"]["future_enforcement_requires_static_auth_gate"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["recording_response_changed"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["recording_bytes_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_recording_access_enforcement_readiness_manifest_disabled_is_not_ready(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_recording_access_enforcement_readiness_manifest(
                context,
                recording_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["recording"]["ownership_lookup_ready"])
        self.assertFalse(manifest["decision"]["ready_for_future_enforcement"])
        self.assertTrue(manifest["decision"]["would_reject_if_recording_access_enforced"])
        self.assertIn("recordings.access_enforcement_shadow_disabled", blockers)
        self.assertIn("recordings.owner_lookup_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["recording_response_changed"])
        self.assertFalse(manifest["safety"]["recording_url_included"])

    def test_recording_access_enforcement_readiness_blocks_mismatch_without_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-2"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_recording_access_enforcement_readiness_manifest(
                context,
                recording_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-2",
                campaign_id_present=True,
            )

        self.assertFalse(manifest["decision"]["tenant_match_for_requested_tenant"])
        self.assertFalse(manifest["decision"]["ready_for_future_enforcement"])
        self.assertIn("tenant_mismatch_for_requested_tenant", manifest["decision"]["blockers"])
        self.assertTrue(manifest["decision"]["would_allow_if_recording_access_enforced"])
        self.assertFalse(manifest["safety"]["recording_response_changed"])
        self.assertFalse(manifest["safety"]["requested_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("client-2", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_recording_access_gate_dry_run_manifest_is_response_neutral(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
            },
            clear=True,
        ):
            manifest = build_recording_access_gate_dry_run_manifest(
                context,
                recording_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "recording_access_gate_dry_run.v1")
        self.assertTrue(manifest["gate"]["dry_run_only"])
        self.assertTrue(manifest["gate"]["future_gate_required"])
        self.assertTrue(manifest["gate"]["existing_static_mount_preserved"])
        self.assertFalse(manifest["gate"]["protected_route_active"])
        self.assertFalse(manifest["gate"]["would_serve_file_bytes"])
        self.assertFalse(manifest["gate"]["would_proxy_static_file"])
        self.assertFalse(manifest["gate"]["would_redirect_to_static_file"])
        self.assertTrue(manifest["decision"]["would_allow_if_gate_active"])
        self.assertTrue(manifest["decision"]["ready_for_future_gate"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["static_file_serving_changed"])
        self.assertFalse(manifest["safety"]["recording_playback_changed"])
        self.assertFalse(manifest["safety"]["recording_response_changed"])
        self.assertFalse(manifest["safety"]["protected_recording_route_activated"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["recording_bytes_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_recording_access_gate_dry_run_manifest_disabled_blocks_activation(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_recording_access_gate_dry_run_manifest(
                context,
                recording_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["ready_for_future_gate"])
        self.assertFalse(manifest["decision"]["would_allow_if_gate_active"])
        self.assertTrue(manifest["decision"]["would_reject_if_gate_active"])
        self.assertIn("recordings.access_gate_dry_run_disabled", blockers)
        self.assertIn("recordings.access_enforcement_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["gate"]["protected_route_active"])
        self.assertFalse(manifest["safety"]["protected_recording_route_activated"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["recording_url_included"])

    def test_transcript_access_shadow_manifest_is_content_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_access_shadow_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_access_shadow.v1")
        self.assertTrue(manifest["transcript"]["found"])
        self.assertTrue(manifest["transcript"]["owner_tenant_present"])
        self.assertTrue(manifest["transcript"]["campaign_id_present"])
        self.assertFalse(manifest["transcript"]["lead_id_included"])
        self.assertFalse(manifest["transcript"]["call_result_id_included"])
        self.assertFalse(manifest["transcript"]["transcript_content_included"])
        self.assertFalse(manifest["transcript"]["transcript_turn_count_included"])
        self.assertFalse(manifest["transcript"]["payload_included"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["current_requester_allowed_if_enforced"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertTrue(manifest["safety"]["db_query_executed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_access_shadow_manifest_disabled_has_safe_blockers(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_access_shadow_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertIn("transcripts.access_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertIn("verified_backend_identity_missing", blockers)
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["db_query_executed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])

    def test_transcript_access_canary_manifest_is_admin_shadow_and_content_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_access_canary_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_access_canary.v1")
        self.assertTrue(manifest["transcript"]["found"])
        self.assertTrue(manifest["transcript"]["owner_tenant_present"])
        self.assertFalse(manifest["transcript"]["lead_id_included"])
        self.assertFalse(manifest["transcript"]["call_result_id_included"])
        self.assertFalse(manifest["transcript"]["transcript_content_included"])
        self.assertFalse(manifest["transcript"]["transcript_turn_count_included"])
        self.assertTrue(manifest["decision"]["admin_canary_only"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_access_canary_manifest_disabled_has_safe_blockers(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_access_canary_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertIn("transcripts.access_shadow_disabled", blockers)
        self.assertIn("transcripts.access_canary_disabled", blockers)
        self.assertIn("tenant.enforcement_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["decision"]["admin_canary_only"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])

    def test_transcript_access_enforcement_readiness_manifest_is_shadow_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_access_enforcement_readiness_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_access_enforcement_readiness.v1")
        self.assertTrue(manifest["transcript"]["ownership_lookup_ready"])
        self.assertFalse(manifest["transcript"]["lead_id_included"])
        self.assertFalse(manifest["transcript"]["call_result_id_included"])
        self.assertFalse(manifest["transcript"]["transcript_content_included"])
        self.assertFalse(manifest["transcript"]["transcript_turn_count_included"])
        self.assertTrue(manifest["decision"]["would_allow_if_transcript_access_enforced"])
        self.assertFalse(manifest["decision"]["would_reject_if_transcript_access_enforced"])
        self.assertTrue(manifest["decision"]["ready_for_future_enforcement"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["shadow_only"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertFalse(manifest["safety"]["owner_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_access_enforcement_readiness_manifest_disabled_is_not_ready(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_access_enforcement_readiness_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["transcript"]["ownership_lookup_ready"])
        self.assertFalse(manifest["decision"]["ready_for_future_enforcement"])
        self.assertTrue(manifest["decision"]["would_reject_if_transcript_access_enforced"])
        self.assertIn("transcripts.access_shadow_disabled", blockers)
        self.assertIn("transcripts.access_enforcement_shadow_disabled", blockers)
        self.assertIn("tenant.enforcement_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])

    def test_transcript_access_enforcement_readiness_blocks_mismatch_without_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-2"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_access_enforcement_readiness_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-2",
                campaign_id_present=True,
            )

        self.assertFalse(manifest["decision"]["tenant_match_for_requested_tenant"])
        self.assertFalse(manifest["decision"]["ready_for_future_enforcement"])
        self.assertIn("tenant_mismatch_for_requested_tenant", manifest["decision"]["blockers"])
        self.assertTrue(manifest["decision"]["would_allow_if_transcript_access_enforced"])
        self.assertFalse(manifest["safety"]["requested_tenant_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("client-2", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_transcript_access_gate_dry_run_manifest_is_response_neutral(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_access_gate_dry_run_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_access_gate_dry_run.v1")
        self.assertTrue(manifest["gate"]["dry_run_only"])
        self.assertTrue(manifest["gate"]["future_gate_required"])
        self.assertTrue(manifest["gate"]["existing_transcript_endpoint_preserved"])
        self.assertFalse(manifest["gate"]["protected_route_active"])
        self.assertFalse(manifest["gate"]["would_return_transcript_content"])
        self.assertFalse(manifest["gate"]["would_proxy_transcript_response"])
        self.assertFalse(manifest["gate"]["would_modify_transcript_endpoint"])
        self.assertTrue(manifest["decision"]["would_allow_if_gate_active"])
        self.assertTrue(manifest["decision"]["ready_for_future_gate"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_access_gate_dry_run_manifest_disabled_blocks_activation(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_access_gate_dry_run_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["ready_for_future_gate"])
        self.assertFalse(manifest["decision"]["would_allow_if_gate_active"])
        self.assertTrue(manifest["decision"]["would_reject_if_gate_active"])
        self.assertIn("transcripts.access_gate_dry_run_disabled", blockers)
        self.assertIn("transcripts.access_enforcement_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["gate"]["protected_route_active"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_route_stub_manifest_is_payload_neutral(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_route_stub_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_route_stub.v1")
        self.assertTrue(manifest["route"]["contract_stub_only"])
        self.assertEqual(manifest["route"]["future_route_template"], "/api/protected/transcripts/{lead_id}")
        self.assertTrue(manifest["route"]["route_enabled_by_flag"])
        self.assertTrue(manifest["route"]["requires_verified_identity"])
        self.assertTrue(manifest["route"]["requires_tenant_scope"])
        self.assertTrue(manifest["route"]["existing_transcript_endpoint_preserved"])
        self.assertFalse(manifest["route"]["protected_route_active"])
        self.assertFalse(manifest["route"]["payload_route_active"])
        self.assertFalse(manifest["route"]["would_read_transcript_payload"])
        self.assertFalse(manifest["route"]["would_return_transcript_content"])
        self.assertTrue(manifest["decision"]["contract_route_ready"])
        self.assertTrue(manifest["decision"]["would_allow_contract_route"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_route_stub_manifest_disabled_blocks_activation(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_route_stub_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["contract_route_ready"])
        self.assertFalse(manifest["decision"]["would_allow_contract_route"])
        self.assertTrue(manifest["decision"]["would_reject_contract_route"])
        self.assertIn("transcripts.protected_route_stub_disabled", blockers)
        self.assertIn("transcripts.access_gate_dry_run_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["route"]["protected_route_active"])
        self.assertFalse(manifest["route"]["payload_route_active"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_route_permission_shadow_manifest_allows_without_payload(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_route_permission_shadow_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_route_permission_shadow.v1")
        self.assertTrue(manifest["permission"]["shadow_only"])
        self.assertTrue(manifest["permission"]["evaluated"])
        self.assertEqual(manifest["permission"]["guard_decision_version"], "tenant_scoped_read_guard.v1")
        self.assertTrue(manifest["permission"]["owner_lookup_required"])
        self.assertTrue(manifest["permission"]["owner_lookup_ready"])
        self.assertTrue(manifest["permission"]["verified_identity_required"])
        self.assertTrue(manifest["permission"]["tenant_scope_required"])
        self.assertTrue(manifest["permission"]["admin_override_allowed"])
        self.assertTrue(manifest["permission"]["requested_tenant_allowed_if_enforced"])
        self.assertTrue(manifest["permission"]["would_allow_payload_if_enforced"])
        self.assertFalse(manifest["permission"]["would_reject_payload_if_enforced"])
        self.assertFalse(manifest["permission"]["active_enforcement"])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["permission"]["payload_return_allowed"])
        self.assertEqual(manifest["permission"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_content_included"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_route_permission_shadow_manifest_blocks_mismatch(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-2"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_route_permission_shadow_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-2",
                campaign_id_present=True,
            )

        self.assertFalse(manifest["permission"]["would_allow_payload_if_enforced"])
        self.assertTrue(manifest["permission"]["would_reject_payload_if_enforced"])
        self.assertIn("tenant_mismatch_for_requested_tenant", manifest["permission"]["blockers"])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["permission"]["payload_return_allowed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["requested_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("client-2", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_transcript_protected_route_permission_shadow_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_route_permission_shadow_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["permission"]["blockers"]
        self.assertFalse(manifest["permission"]["evaluated"])
        self.assertFalse(manifest["permission"]["would_allow_payload_if_enforced"])
        self.assertTrue(manifest["permission"]["would_reject_payload_if_enforced"])
        self.assertIn("transcripts.protected_route_permission_shadow_disabled", blockers)
        self.assertIn("transcripts.protected_route_stub_disabled", blockers)
        self.assertIn("transcripts.access_gate_dry_run_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["permission"]["payload_return_allowed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_response_shape_canary_manifest_is_schema_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_response_shape_canary_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_response_shape_canary.v1")
        self.assertTrue(manifest["response_shape"]["canary_only"])
        self.assertTrue(manifest["response_shape"]["schema_only"])
        self.assertEqual(manifest["response_shape"]["future_envelope_version"], "protected_transcript_response.v1")
        self.assertTrue(manifest["response_shape"]["status_field_defined"])
        self.assertTrue(manifest["response_shape"]["access_field_defined"])
        self.assertTrue(manifest["response_shape"]["metadata_field_defined"])
        self.assertTrue(manifest["response_shape"]["transcript_field_defined"])
        self.assertTrue(manifest["response_shape"]["turns_array_defined"])
        self.assertFalse(manifest["response_shape"]["payload_values_included"])
        self.assertFalse(manifest["response_shape"]["lead_id_value_included"])
        self.assertFalse(manifest["response_shape"]["call_result_id_value_included"])
        self.assertFalse(manifest["response_shape"]["tenant_value_included"])
        self.assertFalse(manifest["response_shape"]["transcript_content_values_included"])
        self.assertFalse(manifest["response_shape"]["recording_url_value_included"])
        self.assertTrue(manifest["response_shape"]["would_return_schema_only"])
        self.assertFalse(manifest["response_shape"]["would_return_payload"])
        self.assertTrue(manifest["decision"]["schema_ready_for_future_payload"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["permission"]["payload_return_allowed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_response_shape_canary_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_response_shape_canary_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["schema_ready_for_future_payload"])
        self.assertFalse(manifest["decision"]["would_allow_schema_if_enabled"])
        self.assertTrue(manifest["decision"]["would_reject_schema_if_enabled"])
        self.assertIn("transcripts.protected_response_shape_canary_disabled", blockers)
        self.assertIn("transcripts.protected_route_permission_shadow_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["response_shape"]["would_return_payload"])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_payload_dry_run_manifest_allows_without_reading_payload(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_payload_dry_run_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_payload_dry_run.v1")
        self.assertTrue(manifest["payload_dry_run"]["dry_run_only"])
        self.assertTrue(manifest["payload_dry_run"]["future_payload_reader_required"])
        self.assertFalse(manifest["payload_dry_run"]["future_payload_reader_invoked"])
        self.assertTrue(manifest["payload_dry_run"]["would_read_payload_if_enabled"])
        self.assertTrue(manifest["payload_dry_run"]["would_return_payload_if_enabled"])
        self.assertFalse(manifest["payload_dry_run"]["payload_read_performed"])
        self.assertFalse(manifest["payload_dry_run"]["payload_return_performed"])
        self.assertTrue(manifest["payload_dry_run"]["dry_run_blocks_actual_read"])
        self.assertFalse(manifest["payload_dry_run"]["lead_id_value_included"])
        self.assertFalse(manifest["payload_dry_run"]["call_result_id_value_included"])
        self.assertFalse(manifest["payload_dry_run"]["tenant_value_included"])
        self.assertFalse(manifest["payload_dry_run"]["transcript_content_values_included"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["ready_for_future_payload_read"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["permission"]["payload_return_allowed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_payload_dry_run_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_payload_dry_run_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["ready_for_future_payload_read"])
        self.assertFalse(manifest["decision"]["would_allow_payload_read_if_live"])
        self.assertTrue(manifest["decision"]["would_reject_payload_read_if_live"])
        self.assertIn("transcripts.protected_payload_dry_run_disabled", blockers)
        self.assertIn("transcripts.protected_response_shape_canary_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["payload_dry_run"]["future_payload_reader_invoked"])
        self.assertFalse(manifest["payload_dry_run"]["payload_read_performed"])
        self.assertFalse(manifest["payload_dry_run"]["payload_return_performed"])
        self.assertFalse(manifest["permission"]["payload_read_allowed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_enforcement_readiness_manifest_is_final_shadow_gate(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_enforcement_readiness_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_enforcement_readiness.v1")
        self.assertTrue(manifest["readiness"]["all_prerequisites_ready"])
        self.assertTrue(manifest["readiness"]["metadata_owner_lookup_ready"])
        self.assertTrue(manifest["readiness"]["permission_shadow_ready"])
        self.assertTrue(manifest["readiness"]["response_shape_canary_ready"])
        self.assertTrue(manifest["readiness"]["payload_dry_run_ready"])
        self.assertTrue(manifest["readiness"]["requires_separate_live_activation_phase"])
        self.assertFalse(manifest["readiness"]["live_payload_route_enabled"])
        self.assertTrue(manifest["readiness"]["legacy_transcript_endpoint_preserved"])
        self.assertFalse(manifest["readiness"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["ready_for_future_enforcement_candidate"])
        self.assertFalse(manifest["decision"]["would_allow_live_activation_request"])
        self.assertTrue(manifest["decision"]["would_reject_live_activation_request"])
        self.assertTrue(manifest["decision"]["manual_rollout_required"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["live_payload_route_enabled"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_enforcement_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_enforcement_readiness_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["readiness"]["all_prerequisites_ready"])
        self.assertFalse(manifest["decision"]["ready_for_future_enforcement_candidate"])
        self.assertFalse(manifest["decision"]["would_allow_live_activation_request"])
        self.assertTrue(manifest["decision"]["would_reject_live_activation_request"])
        self.assertIn("transcripts.protected_enforcement_readiness_disabled", blockers)
        self.assertIn("transcripts.protected_payload_dry_run_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["readiness"]["live_payload_route_enabled"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_live_activation_plan_manifest_is_plan_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_live_activation_plan_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_live_activation_plan.v1")
        self.assertTrue(manifest["activation_plan"]["plan_only"])
        self.assertTrue(manifest["activation_plan"]["requires_manual_approval"])
        self.assertTrue(manifest["activation_plan"]["requires_separate_live_activation_phase"])
        self.assertTrue(manifest["activation_plan"]["legacy_transcript_endpoint_preserved"])
        self.assertTrue(manifest["activation_plan"]["protected_route_contract_ready"])
        self.assertFalse(manifest["activation_plan"]["live_payload_route_enabled"])
        self.assertFalse(manifest["activation_plan"]["would_enable_payload_route"])
        self.assertFalse(manifest["activation_plan"]["would_disable_legacy_route"])
        self.assertFalse(manifest["activation_plan"]["would_read_payload"])
        self.assertFalse(manifest["activation_plan"]["would_return_payload"])
        self.assertIn("run_tenant_leak_suite", manifest["activation_plan"]["activation_sequence"])
        self.assertIn("transcripts.protected_live_activation_plan", manifest["activation_plan"]["required_kill_switches"])
        self.assertTrue(manifest["decision"]["activation_plan_ready"])
        self.assertFalse(manifest["decision"]["would_allow_live_activation_now"])
        self.assertTrue(manifest["decision"]["would_reject_live_activation_now"])
        self.assertTrue(manifest["decision"]["manual_rollout_required"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["live_payload_route_enabled"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_live_activation_plan_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_live_activation_plan_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["activation_plan_ready"])
        self.assertFalse(manifest["decision"]["would_allow_live_activation_now"])
        self.assertTrue(manifest["decision"]["would_reject_live_activation_now"])
        self.assertIn("transcripts.protected_live_activation_plan_disabled", blockers)
        self.assertIn("transcripts.protected_enforcement_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["activation_plan"]["live_payload_route_enabled"])
        self.assertFalse(manifest["activation_plan"]["would_read_payload"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_protected_rollback_readiness_manifest_is_action_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_protected_rollback_readiness_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_protected_rollback_readiness.v1")
        self.assertTrue(manifest["rollback"]["readiness_only"])
        self.assertFalse(manifest["rollback"]["rollback_action_performed"])
        self.assertTrue(manifest["rollback"]["legacy_transcript_endpoint_preserved"])
        self.assertFalse(manifest["rollback"]["live_payload_route_enabled"])
        self.assertTrue(manifest["rollback"]["can_disable_future_live_route_by_flag"])
        self.assertIn("transcripts.protected_payload_live", manifest["rollback"]["kill_switch_order"])
        self.assertIn("tenant_leak_suite_passes", manifest["rollback"]["post_rollback_checks"])
        self.assertTrue(manifest["rollback"]["requires_manual_rollback_approval"])
        self.assertFalse(manifest["rollback"]["would_modify_flags"])
        self.assertFalse(manifest["rollback"]["would_modify_routes"])
        self.assertFalse(manifest["rollback"]["would_read_payload"])
        self.assertFalse(manifest["rollback"]["would_return_payload"])
        self.assertTrue(manifest["decision"]["rollback_ready_for_future_live_activation"])
        self.assertFalse(manifest["decision"]["would_execute_rollback_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["manual_rollback_required"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["runtime_enforcement_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["live_payload_route_enabled"])
        self.assertFalse(manifest["safety"]["rollback_action_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_protected_rollback_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_protected_rollback_readiness_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["rollback_ready_for_future_live_activation"])
        self.assertFalse(manifest["decision"]["would_execute_rollback_now"])
        self.assertIn("transcripts.protected_rollback_readiness_disabled", blockers)
        self.assertIn("transcripts.protected_live_activation_plan_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["rollback"]["rollback_action_performed"])
        self.assertFalse(manifest["rollback"]["would_modify_flags"])
        self.assertFalse(manifest["rollback"]["would_read_payload"])
        self.assertFalse(manifest["safety"]["rollback_action_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_transcript_frontend_migration_readiness_manifest_is_no_frontend_change(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_transcript_frontend_migration_readiness_manifest(
                context,
                transcript_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                campaign_id_present=True,
            )

        self.assertEqual(manifest["manifest_version"], "transcript_frontend_migration_readiness.v1")
        self.assertTrue(manifest["frontend_migration"]["readiness_only"])
        self.assertFalse(manifest["frontend_migration"]["frontend_code_changed"])
        self.assertTrue(manifest["frontend_migration"]["legacy_transcript_endpoint_preserved"])
        self.assertEqual(manifest["frontend_migration"]["future_protected_route_template"], "/api/protected/transcripts/{lead_id}")
        self.assertFalse(manifest["frontend_migration"]["future_route_payload_live"])
        self.assertTrue(manifest["frontend_migration"]["results_page_can_migrate_later"])
        self.assertTrue(manifest["frontend_migration"]["transcript_ui_can_migrate_later"])
        self.assertTrue(manifest["frontend_migration"]["requires_dual_read_canary_before_switch"])
        self.assertTrue(manifest["frontend_migration"]["requires_feature_flagged_frontend_switch"])
        self.assertTrue(manifest["frontend_migration"]["requires_legacy_fallback"])
        self.assertIn("dual_read_shadow_without_render_change", manifest["frontend_migration"]["migration_sequence"])
        self.assertFalse(manifest["frontend_migration"]["lead_id_value_included"])
        self.assertFalse(manifest["frontend_migration"]["transcript_content_values_included"])
        self.assertTrue(manifest["decision"]["frontend_migration_ready"])
        self.assertFalse(manifest["decision"]["would_switch_frontend_now"])
        self.assertFalse(manifest["decision"]["would_change_transcript_ui_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertTrue(manifest["decision"]["manual_frontend_migration_required"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["frontend_code_changed"])
        self.assertFalse(manifest["safety"]["transcript_response_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["transcript_turn_count_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_transcript_frontend_migration_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_transcript_frontend_migration_readiness_manifest(
                context,
                transcript_found=False,
                owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["frontend_migration_ready"])
        self.assertFalse(manifest["decision"]["would_switch_frontend_now"])
        self.assertFalse(manifest["decision"]["would_change_transcript_ui_now"])
        self.assertIn("transcripts.frontend_migration_readiness_disabled", blockers)
        self.assertIn("transcripts.protected_rollback_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["frontend_migration"]["frontend_code_changed"])
        self.assertFalse(manifest["frontend_migration"]["future_route_payload_live"])
        self.assertFalse(manifest["safety"]["frontend_code_changed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])

    def test_tenant_scoped_read_canary_allows_requested_tenant_without_leaking_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
            },
            clear=True,
        ):
            canary = build_tenant_scoped_read_canary(
                context,
                resource_type="campaign",
                resource_label="Campaign",
                resource_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-1",
            )

        self.assertEqual(canary["manifest_version"], "tenant_scoped_read_canary.v1")
        self.assertTrue(canary["resource"]["found"])
        self.assertTrue(canary["resource"]["owner_tenant_present"])
        self.assertFalse(canary["resource"]["resource_id_included"])
        self.assertFalse(canary["resource"]["owner_tenant_included"])
        self.assertFalse(canary["resource"]["payload_included"])
        self.assertTrue(canary["decision"]["requested_tenant_allowed_if_scoped_reads_enforced"])
        self.assertTrue(canary["decision"]["current_requester_allowed_if_scoped_reads_enforced"])
        self.assertEqual(canary["decision"]["guard_decision_version"], "tenant_scoped_read_guard.v1")
        self.assertEqual(canary["decision"]["blockers"], [])
        self.assertFalse(canary["safety"]["runtime_enforcement_changed"])
        self.assertFalse(canary["safety"]["db_write_performed"])
        self.assertFalse(canary["safety"]["resource_payload_returned"])
        self.assertFalse(canary["safety"]["tenant_data_returned"])
        self.assertFalse(canary["safety"]["phone_number_included"])
        self.assertFalse(canary["safety"]["transcript_content_included"])
        self.assertFalse(canary["safety"]["recording_url_included"])
        self.assertFalse(canary["safety"]["crm_payload_included"])
        self.assertNotIn("secret", str(canary))
        self.assertNotIn("client-1", str(canary))
        self.assertNotIn("operator-tenant", str(canary))

    def test_tenant_scoped_read_canary_reports_mismatch_without_owner_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
            },
            clear=True,
        ):
            canary = build_tenant_scoped_read_canary(
                context,
                resource_type="crm_outbox",
                resource_label="CRM outbox item",
                resource_found=True,
                owner_tenant_id="client-1",
                requested_tenant_id="client-2",
            )

        self.assertFalse(canary["decision"]["requested_tenant_allowed_if_scoped_reads_enforced"])
        self.assertTrue(canary["decision"]["current_requester_allowed_if_scoped_reads_enforced"])
        self.assertIn("tenant_mismatch_for_requested_tenant", canary["decision"]["blockers"])
        self.assertFalse(canary["resource"]["owner_tenant_included"])
        self.assertFalse(canary["requester"]["requested_tenant_included"])
        self.assertFalse(canary["safety"]["cross_tenant_data_included"])
        self.assertNotIn("client-1", str(canary))
        self.assertNotIn("client-2", str(canary))
        self.assertNotIn("operator-tenant", str(canary))

    def test_tenant_scoped_read_canary_missing_resource_is_shadow_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "operator-tenant"},
            api_key_secret="secret",
        )

        with patch.dict(os.environ, {}, clear=True):
            canary = build_tenant_scoped_read_canary(
                context,
                resource_type="phone_number",
                resource_label="Phone number",
                resource_found=False,
                owner_tenant_id=None,
                requested_tenant_id="client-1",
            )

        self.assertFalse(canary["resource"]["found"])
        self.assertFalse(canary["decision"]["requested_tenant_allowed_if_scoped_reads_enforced"])
        self.assertFalse(canary["decision"]["current_requester_allowed_if_scoped_reads_enforced"])
        self.assertIn("resource_not_found", canary["decision"]["blockers"])
        self.assertIn("tenant.scoped_read_canary_disabled", canary["decision"]["blockers"])
        self.assertIn("tenant.scoped_read_guard_shadow_disabled", canary["decision"]["blockers"])
        self.assertFalse(canary["safety"]["runtime_enforcement_changed"])
        self.assertFalse(canary["safety"]["db_write_performed"])
        self.assertFalse(canary["safety"]["resource_id_included"])
        self.assertFalse(canary["safety"]["phone_number_included"])
        self.assertNotIn("client-1", str(canary))
        self.assertNotIn("operator-tenant", str(canary))

    def test_tenant_leak_regression_matrix_is_payload_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
            },
            clear=True,
        ):
            manifest = build_tenant_leak_regression_matrix_manifest(
                context,
                scenarios=[
                    {"resource_type": "transcript", "resource_label": "Transcript", "resource_found": True, "owner_tenant_id": "client-1"},
                    {"resource_type": "recording_asset", "resource_label": "Recording asset", "resource_found": True, "owner_tenant_id": "client-1"},
                    {"resource_type": "call_result", "resource_label": "Call result", "resource_found": True, "owner_tenant_id": "client-1"},
                ],
                requested_tenant_id="client-1",
            )

        self.assertEqual(manifest["manifest_version"], "tenant_leak_regression_matrix.v1")
        self.assertTrue(manifest["decision"]["matrix_ready"])
        self.assertEqual(manifest["decision"]["scenario_count"], 3)
        self.assertFalse(manifest["decision"]["cross_tenant_leak_detected"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        for row in manifest["matrix"]:
            self.assertFalse(row["cross_tenant_leak_detected"])
            self.assertFalse(row["resource_id_included"])
            self.assertFalse(row["owner_tenant_included"])
            self.assertFalse(row["payload_included"])
            self.assertFalse(row["transcript_content_included"])
            self.assertFalse(row["recording_url_included"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["lead_id_included"])
        self.assertFalse(manifest["safety"]["call_result_id_included"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertFalse(manifest["safety"]["cross_tenant_data_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))

    def test_tenant_leak_regression_matrix_blocks_mismatch_without_values(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-2"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
            },
            clear=True,
        ):
            manifest = build_tenant_leak_regression_matrix_manifest(
                context,
                scenarios=[
                    {"resource_type": "transcript", "resource_label": "Transcript", "resource_found": True, "owner_tenant_id": "client-1"},
                ],
                requested_tenant_id="client-2",
            )

        self.assertFalse(manifest["decision"]["matrix_ready"])
        self.assertFalse(manifest["decision"]["cross_tenant_leak_detected"])
        self.assertIn("tenant_mismatch_for_requested_tenant", manifest["decision"]["blockers"])
        self.assertFalse(manifest["matrix"][0]["requested_tenant_allowed_if_enforced"])
        self.assertFalse(manifest["matrix"][0]["owner_tenant_included"])
        self.assertFalse(manifest["matrix"][0]["requested_tenant_included"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("client-2", str(manifest))
        self.assertNotIn("secret", str(manifest))

    def test_tenant_leak_regression_matrix_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_tenant_leak_regression_matrix_manifest(
                context,
                scenarios=[
                    {"resource_type": "transcript", "resource_label": "Transcript", "resource_found": False, "owner_tenant_id": None},
                ],
            )

        self.assertFalse(manifest["decision"]["matrix_ready"])
        self.assertIn("tenant.leak_regression_matrix_disabled", manifest["decision"]["blockers"])
        self.assertIn("resource_not_found", manifest["decision"]["blockers"])
        self.assertFalse(manifest["matrix"][0]["resource_id_included"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_result_asset_readiness_manifest_aggregates_without_payloads(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_result_asset_readiness_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "result_asset_readiness.v1")
        self.assertTrue(manifest["decision"]["result_asset_readiness_ready"])
        self.assertFalse(manifest["decision"]["would_change_results_endpoint_now"])
        self.assertFalse(manifest["decision"]["would_change_transcript_endpoint_now"])
        self.assertFalse(manifest["decision"]["would_change_recording_serving_now"])
        self.assertFalse(manifest["decision"]["would_switch_frontend_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertTrue(manifest["assets"]["call_result_checked"])
        self.assertTrue(manifest["assets"]["transcript_checked"])
        self.assertTrue(manifest["assets"]["recording_checked"])
        self.assertTrue(manifest["assets"]["campaign_checked"])
        self.assertTrue(manifest["assets"]["transcript_ready"])
        self.assertTrue(manifest["assets"]["recording_ready"])
        self.assertTrue(manifest["assets"]["campaign_ready"])
        self.assertTrue(manifest["assets"]["leak_matrix_ready"])
        self.assertTrue(manifest["assets"]["legacy_results_endpoint_preserved"])
        self.assertTrue(manifest["assets"]["static_recording_mount_preserved"])
        self.assertFalse(manifest["assets"]["payloads_included"])
        self.assertFalse(manifest["assets"]["lead_id_included"])
        self.assertFalse(manifest["assets"]["campaign_id_included"])
        self.assertFalse(manifest["assets"]["recording_url_included"])
        self.assertFalse(manifest["components"]["component_payloads_included"])
        self.assertFalse(manifest["components"]["component_ids_included"])
        self.assertFalse(manifest["components"]["component_tenant_values_included"])
        self.assertFalse(manifest["safety"]["results_endpoint_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["protected_recording_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["recording_url_included"])
        self.assertFalse(manifest["safety"]["transcript_content_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_result_asset_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_result_asset_readiness_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["result_asset_readiness_ready"])
        self.assertIn("tenant.result_asset_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["assets"]["readiness_only"])
        self.assertFalse(manifest["assets"]["payloads_included"])
        self.assertFalse(manifest["assets"]["lead_id_included"])
        self.assertFalse(manifest["assets"]["recording_url_included"])
        self.assertFalse(manifest["decision"]["would_change_results_endpoint_now"])
        self.assertFalse(manifest["safety"]["results_endpoint_changed"])
        self.assertFalse(manifest["safety"]["recording_response_changed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_final_rollout_report_readiness_manifest_is_shadow_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_final_rollout_report_readiness_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "final_rollout_report_readiness.v1")
        self.assertTrue(manifest["report"]["readiness_only"])
        self.assertTrue(manifest["report"]["rollout_report_ready"])
        self.assertTrue(manifest["report"]["result_asset_readiness_ready"])
        self.assertTrue(manifest["report"]["runtime_contracts_preserved"])
        self.assertTrue(manifest["report"]["audio_contracts_preserved"])
        self.assertTrue(manifest["report"]["websocket_contracts_preserved"])
        self.assertFalse(manifest["report"]["protected_transcript_route_live"])
        self.assertFalse(manifest["report"]["protected_recording_route_live"])
        self.assertFalse(manifest["report"]["frontend_switch_live"])
        self.assertTrue(manifest["report"]["manual_go_live_approval_required"])
        self.assertIn("legacy_fallback_retained", manifest["report"]["canary_sequence"])
        self.assertFalse(manifest["report"]["ids_included"])
        self.assertFalse(manifest["report"]["payloads_included"])
        self.assertFalse(manifest["report"]["tenant_values_included"])
        self.assertFalse(manifest["report"]["transcript_content_included"])
        self.assertFalse(manifest["report"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["final_rollout_report_ready"])
        self.assertFalse(manifest["decision"]["would_activate_live_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_audio_runtime_now"])
        self.assertFalse(manifest["decision"]["would_change_websocket_contract_now"])
        self.assertFalse(manifest["decision"]["would_change_campaign_runtime_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["audio_runtime_changed"])
        self.assertFalse(manifest["safety"]["websocket_contract_changed"])
        self.assertFalse(manifest["safety"]["campaign_runtime_changed"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["protected_recording_route_activated"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_final_rollout_report_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_final_rollout_report_readiness_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["final_rollout_report_ready"])
        self.assertIn("tenant.final_rollout_report_disabled", blockers)
        self.assertIn("tenant.result_asset_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["report"]["readiness_only"])
        self.assertFalse(manifest["report"]["payloads_included"])
        self.assertFalse(manifest["report"]["ids_included"])
        self.assertFalse(manifest["decision"]["would_activate_live_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_frontend_now"])
        self.assertFalse(manifest["safety"]["audio_runtime_changed"])
        self.assertFalse(manifest["safety"]["websocket_contract_changed"])
        self.assertFalse(manifest["safety"]["campaign_runtime_changed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_rollout_approval_packet_manifest_is_action_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_rollout_approval_packet_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "rollout_approval_packet.v1")
        self.assertTrue(manifest["approval_packet"]["readiness_only"])
        self.assertTrue(manifest["approval_packet"]["approval_packet_ready"])
        self.assertTrue(manifest["approval_packet"]["final_rollout_report_ready"])
        self.assertTrue(manifest["approval_packet"]["manual_go_live_approval_required"])
        self.assertFalse(manifest["approval_packet"]["approval_record_created"])
        self.assertFalse(manifest["approval_packet"]["approval_state_changed"])
        self.assertFalse(manifest["approval_packet"]["feature_flags_modified"])
        self.assertFalse(manifest["approval_packet"]["live_routes_activated"])
        self.assertFalse(manifest["approval_packet"]["frontend_switch_activated"])
        self.assertIn("tenant_leak_matrix_passed", manifest["approval_packet"]["required_evidence"])
        self.assertIn("tenant.final_rollout_report", manifest["approval_packet"]["required_kill_switches"])
        self.assertFalse(manifest["approval_packet"]["ids_included"])
        self.assertFalse(manifest["approval_packet"]["payloads_included"])
        self.assertFalse(manifest["approval_packet"]["tenant_values_included"])
        self.assertFalse(manifest["approval_packet"]["transcript_content_included"])
        self.assertFalse(manifest["approval_packet"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["rollout_approval_packet_ready"])
        self.assertFalse(manifest["decision"]["would_record_approval_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_activate_live_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_audio_runtime_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["approval_state_changed"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["protected_transcript_route_activated"])
        self.assertFalse(manifest["safety"]["protected_recording_route_activated"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_rollout_approval_packet_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_rollout_approval_packet_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["rollout_approval_packet_ready"])
        self.assertIn("tenant.rollout_approval_packet_disabled", blockers)
        self.assertIn("tenant.final_rollout_report_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["approval_packet"]["readiness_only"])
        self.assertFalse(manifest["approval_packet"]["approval_record_created"])
        self.assertFalse(manifest["approval_packet"]["approval_state_changed"])
        self.assertFalse(manifest["approval_packet"]["feature_flags_modified"])
        self.assertFalse(manifest["approval_packet"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_record_approval_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["safety"]["approval_state_changed"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_rollout_canary_plan_manifest_is_plan_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_rollout_canary_plan_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "rollout_canary_plan.v1")
        self.assertTrue(manifest["canary_plan"]["plan_only"])
        self.assertTrue(manifest["canary_plan"]["canary_plan_ready"])
        self.assertTrue(manifest["canary_plan"]["approval_packet_ready"])
        self.assertTrue(manifest["canary_plan"]["single_tenant_canary_required"])
        self.assertTrue(manifest["canary_plan"]["legacy_fallback_required"])
        self.assertFalse(manifest["canary_plan"]["automatic_activation_enabled"])
        self.assertFalse(manifest["canary_plan"]["feature_flags_modified"])
        self.assertFalse(manifest["canary_plan"]["live_routes_activated"])
        self.assertFalse(manifest["canary_plan"]["frontend_switch_activated"])
        self.assertEqual(manifest["canary_plan"]["traffic_shift_percent"], 0)
        self.assertEqual(manifest["canary_plan"]["max_initial_tenant_count"], 1)
        self.assertIn("protected_route_shadow_compare", manifest["canary_plan"]["canary_sequence"])
        self.assertEqual(manifest["canary_plan"]["abort_thresholds"]["tenant_leak_count"], 0)
        self.assertIn("tenant.rollout_canary_plan", manifest["canary_plan"]["required_kill_switches"])
        self.assertFalse(manifest["canary_plan"]["ids_included"])
        self.assertFalse(manifest["canary_plan"]["payloads_included"])
        self.assertFalse(manifest["canary_plan"]["tenant_values_included"])
        self.assertFalse(manifest["canary_plan"]["transcript_content_included"])
        self.assertFalse(manifest["canary_plan"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["rollout_canary_plan_ready"])
        self.assertFalse(manifest["decision"]["would_start_canary_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_activate_live_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_websocket_contract_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["canary_started"])
        self.assertFalse(manifest["safety"]["traffic_shifted"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_rollout_canary_plan_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_rollout_canary_plan_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["rollout_canary_plan_ready"])
        self.assertIn("tenant.rollout_canary_plan_disabled", blockers)
        self.assertIn("tenant.rollout_approval_packet_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["canary_plan"]["plan_only"])
        self.assertFalse(manifest["canary_plan"]["automatic_activation_enabled"])
        self.assertFalse(manifest["canary_plan"]["feature_flags_modified"])
        self.assertFalse(manifest["canary_plan"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_start_canary_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["safety"]["canary_started"])
        self.assertFalse(manifest["safety"]["traffic_shifted"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_rollback_drill_readiness_manifest_is_dry_run_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
                "FEATURE_TENANT_ROLLBACK_DRILL_READINESS": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_rollback_drill_readiness_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "rollback_drill_readiness.v1")
        self.assertTrue(manifest["rollback_drill"]["readiness_only"])
        self.assertTrue(manifest["rollback_drill"]["rollback_drill_ready"])
        self.assertTrue(manifest["rollback_drill"]["canary_plan_ready"])
        self.assertFalse(manifest["rollback_drill"]["rollback_action_performed"])
        self.assertFalse(manifest["rollback_drill"]["feature_flags_modified"])
        self.assertFalse(manifest["rollback_drill"]["routes_modified"])
        self.assertFalse(manifest["rollback_drill"]["frontend_modified"])
        self.assertFalse(manifest["rollback_drill"]["traffic_shifted"])
        self.assertFalse(manifest["rollback_drill"]["db_write_performed"])
        self.assertTrue(manifest["rollback_drill"]["requires_manual_rollback_approval"])
        self.assertIn("tenant.rollback_drill_readiness", manifest["rollback_drill"]["kill_switch_order"])
        self.assertIn("demo_voice_call_smoke", manifest["rollback_drill"]["post_rollback_checks"])
        self.assertIn("verify_no_live_payload_route", manifest["rollback_drill"]["recovery_sequence"])
        self.assertFalse(manifest["rollback_drill"]["ids_included"])
        self.assertFalse(manifest["rollback_drill"]["payloads_included"])
        self.assertFalse(manifest["rollback_drill"]["tenant_values_included"])
        self.assertFalse(manifest["rollback_drill"]["transcript_content_included"])
        self.assertFalse(manifest["rollback_drill"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["rollback_drill_readiness_ready"])
        self.assertFalse(manifest["decision"]["would_execute_rollback_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_modify_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_audio_runtime_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["rollback_action_performed"])
        self.assertFalse(manifest["safety"]["routes_modified"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["canary_started"])
        self.assertFalse(manifest["safety"]["traffic_shifted"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_rollback_drill_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_rollback_drill_readiness_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["rollback_drill_readiness_ready"])
        self.assertIn("tenant.rollback_drill_readiness_disabled", blockers)
        self.assertIn("tenant.rollout_canary_plan_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["rollback_drill"]["readiness_only"])
        self.assertFalse(manifest["rollback_drill"]["rollback_action_performed"])
        self.assertFalse(manifest["rollback_drill"]["feature_flags_modified"])
        self.assertFalse(manifest["rollback_drill"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_execute_rollback_now"])
        self.assertFalse(manifest["decision"]["would_modify_routes_now"])
        self.assertFalse(manifest["safety"]["rollback_action_performed"])
        self.assertFalse(manifest["safety"]["routes_modified"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_rollout_evidence_bundle_manifest_is_payload_free(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
                "FEATURE_TENANT_ROLLBACK_DRILL_READINESS": "true",
                "FEATURE_TENANT_ROLLOUT_EVIDENCE_BUNDLE": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_rollout_evidence_bundle_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "rollout_evidence_bundle.v1")
        self.assertTrue(manifest["evidence_bundle"]["readiness_only"])
        self.assertTrue(manifest["evidence_bundle"]["evidence_bundle_ready"])
        self.assertTrue(manifest["evidence_bundle"]["rollback_drill_ready"])
        self.assertFalse(manifest["evidence_bundle"]["evidence_record_created"])
        self.assertFalse(manifest["evidence_bundle"]["evidence_persisted"])
        self.assertFalse(manifest["evidence_bundle"]["live_data_collected"])
        self.assertFalse(manifest["evidence_bundle"]["metrics_sampled"])
        self.assertFalse(manifest["evidence_bundle"]["feature_flags_modified"])
        self.assertFalse(manifest["evidence_bundle"]["routes_modified"])
        self.assertFalse(manifest["evidence_bundle"]["traffic_shifted"])
        self.assertIn("full_backend_regression_passed", manifest["evidence_bundle"]["required_evidence_items"])
        self.assertIn("rollback_drill", manifest["evidence_bundle"]["covered_surfaces"])
        self.assertIn("confirm_kill_switches", manifest["evidence_bundle"]["review_sequence"])
        self.assertFalse(manifest["evidence_bundle"]["ids_included"])
        self.assertFalse(manifest["evidence_bundle"]["payloads_included"])
        self.assertFalse(manifest["evidence_bundle"]["tenant_values_included"])
        self.assertFalse(manifest["evidence_bundle"]["transcript_content_included"])
        self.assertFalse(manifest["evidence_bundle"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["rollout_evidence_bundle_ready"])
        self.assertFalse(manifest["decision"]["would_create_evidence_record_now"])
        self.assertFalse(manifest["decision"]["would_collect_live_metrics_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_modify_routes_now"])
        self.assertFalse(manifest["decision"]["would_change_audio_runtime_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["evidence_record_created"])
        self.assertFalse(manifest["safety"]["live_data_collected"])
        self.assertFalse(manifest["safety"]["metrics_sampled"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["file_bytes_read"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_rollout_evidence_bundle_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_rollout_evidence_bundle_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["rollout_evidence_bundle_ready"])
        self.assertIn("tenant.rollout_evidence_bundle_disabled", blockers)
        self.assertIn("tenant.rollback_drill_readiness_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["evidence_bundle"]["readiness_only"])
        self.assertFalse(manifest["evidence_bundle"]["evidence_record_created"])
        self.assertFalse(manifest["evidence_bundle"]["evidence_persisted"])
        self.assertFalse(manifest["evidence_bundle"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_create_evidence_record_now"])
        self.assertFalse(manifest["decision"]["would_collect_live_metrics_now"])
        self.assertFalse(manifest["safety"]["evidence_record_created"])
        self.assertFalse(manifest["safety"]["live_data_collected"])
        self.assertFalse(manifest["safety"]["metrics_sampled"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_canary_observation_checklist_manifest_is_passive(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
                "FEATURE_TENANT_ROLLBACK_DRILL_READINESS": "true",
                "FEATURE_TENANT_ROLLOUT_EVIDENCE_BUNDLE": "true",
                "FEATURE_TENANT_CANARY_OBSERVATION_CHECKLIST": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_canary_observation_checklist_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "canary_observation_checklist.v1")
        self.assertTrue(manifest["observation_checklist"]["checklist_only"])
        self.assertTrue(manifest["observation_checklist"]["observation_checklist_ready"])
        self.assertTrue(manifest["observation_checklist"]["evidence_bundle_ready"])
        self.assertFalse(manifest["observation_checklist"]["observation_record_created"])
        self.assertFalse(manifest["observation_checklist"]["metrics_sampled"])
        self.assertFalse(manifest["observation_checklist"]["live_data_collected"])
        self.assertFalse(manifest["observation_checklist"]["canary_started"])
        self.assertFalse(manifest["observation_checklist"]["traffic_shifted"])
        self.assertFalse(manifest["observation_checklist"]["feature_flags_modified"])
        self.assertIn("first_demo_call", manifest["observation_checklist"]["required_observation_windows"])
        self.assertIn("tenant_leak_count", manifest["observation_checklist"]["watch_metrics"])
        self.assertEqual(manifest["observation_checklist"]["abort_thresholds"]["tenant_leak_count"], 0)
        self.assertIn(
            "operator_confirms_no_client_data_exposure",
            manifest["observation_checklist"]["manual_review_points"],
        )
        self.assertFalse(manifest["observation_checklist"]["ids_included"])
        self.assertFalse(manifest["observation_checklist"]["payloads_included"])
        self.assertFalse(manifest["observation_checklist"]["tenant_values_included"])
        self.assertFalse(manifest["observation_checklist"]["transcript_content_included"])
        self.assertFalse(manifest["observation_checklist"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["canary_observation_checklist_ready"])
        self.assertFalse(manifest["decision"]["would_create_observation_record_now"])
        self.assertFalse(manifest["decision"]["would_sample_metrics_now"])
        self.assertFalse(manifest["decision"]["would_start_canary_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_change_websocket_contract_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["observation_record_created"])
        self.assertFalse(manifest["safety"]["live_data_collected"])
        self.assertFalse(manifest["safety"]["metrics_sampled"])
        self.assertFalse(manifest["safety"]["canary_started"])
        self.assertFalse(manifest["safety"]["traffic_shifted"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_canary_observation_checklist_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_canary_observation_checklist_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["canary_observation_checklist_ready"])
        self.assertIn("tenant.canary_observation_checklist_disabled", blockers)
        self.assertIn("tenant.rollout_evidence_bundle_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["observation_checklist"]["checklist_only"])
        self.assertFalse(manifest["observation_checklist"]["observation_record_created"])
        self.assertFalse(manifest["observation_checklist"]["metrics_sampled"])
        self.assertFalse(manifest["observation_checklist"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_create_observation_record_now"])
        self.assertFalse(manifest["decision"]["would_sample_metrics_now"])
        self.assertFalse(manifest["safety"]["observation_record_created"])
        self.assertFalse(manifest["safety"]["live_data_collected"])
        self.assertFalse(manifest["safety"]["metrics_sampled"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_production_go_no_go_gate_manifest_is_report_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )

        with patch.dict(
            os.environ,
            {
                "FEATURE_AUTH_ENFORCE_BACKEND": "true",
                "FEATURE_TENANT_SCOPED_READS": "true",
                "FEATURE_TENANT_ENFORCEMENT_READINESS": "true",
                "FEATURE_TENANT_SCOPED_READ_CANARY": "true",
                "FEATURE_TENANT_SCOPED_READ_GUARD_SHADOW": "true",
                "FEATURE_TENANT_LEAK_REGRESSION_MATRIX": "true",
                "FEATURE_TENANT_RESULT_ASSET_READINESS": "true",
                "FEATURE_TENANT_FINAL_ROLLOUT_REPORT": "true",
                "FEATURE_TENANT_ROLLOUT_APPROVAL_PACKET": "true",
                "FEATURE_TENANT_ROLLOUT_CANARY_PLAN": "true",
                "FEATURE_TENANT_ROLLBACK_DRILL_READINESS": "true",
                "FEATURE_TENANT_ROLLOUT_EVIDENCE_BUNDLE": "true",
                "FEATURE_TENANT_CANARY_OBSERVATION_CHECKLIST": "true",
                "FEATURE_TENANT_PRODUCTION_GO_NO_GO_GATE": "true",
                "FEATURE_RECORDINGS_ACCESS_SHADOW": "true",
                "FEATURE_RECORDINGS_OWNER_LOOKUP_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_RECORDINGS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_CANARY": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_ENFORCEMENT_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_ACCESS_GATE_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_STUB": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROUTE_PERMISSION_SHADOW": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_RESPONSE_SHAPE_CANARY": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_PAYLOAD_DRY_RUN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ENFORCEMENT_READINESS": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_LIVE_ACTIVATION_PLAN": "true",
                "FEATURE_TRANSCRIPTS_PROTECTED_ROLLBACK_READINESS": "true",
                "FEATURE_TRANSCRIPTS_FRONTEND_MIGRATION_READINESS": "true",
            },
            clear=True,
        ):
            manifest = build_production_go_no_go_gate_manifest(
                context,
                transcript_found=True,
                transcript_owner_tenant_id="client-1",
                recording_found=True,
                recording_owner_tenant_id="client-1",
                campaign_found=True,
                campaign_owner_tenant_id="client-1",
                requested_tenant_id="client-1",
                transcript_campaign_id_present=True,
                recording_campaign_id_present=True,
                recording_required=True,
                campaign_required=True,
            )

        self.assertEqual(manifest["manifest_version"], "production_go_no_go_gate.v1")
        self.assertTrue(manifest["go_no_go_gate"]["readiness_only"])
        self.assertTrue(manifest["go_no_go_gate"]["production_gate_ready"])
        self.assertTrue(manifest["go_no_go_gate"]["canary_observation_checklist_ready"])
        self.assertFalse(manifest["go_no_go_gate"]["decision_record_created"])
        self.assertFalse(manifest["go_no_go_gate"]["go_decision_recorded"])
        self.assertFalse(manifest["go_no_go_gate"]["no_go_decision_recorded"])
        self.assertFalse(manifest["go_no_go_gate"]["production_activation_started"])
        self.assertFalse(manifest["go_no_go_gate"]["feature_flags_modified"])
        self.assertFalse(manifest["go_no_go_gate"]["routes_modified"])
        self.assertFalse(manifest["go_no_go_gate"]["frontend_modified"])
        self.assertFalse(manifest["go_no_go_gate"]["traffic_shifted"])
        self.assertIn("security_owner", manifest["go_no_go_gate"]["final_required_signoffs"])
        self.assertIn("any_tenant_leak_detected", manifest["go_no_go_gate"]["hard_stop_conditions"])
        self.assertIn("legacy_fallback_confirmed", manifest["go_no_go_gate"]["go_live_prerequisites"])
        self.assertFalse(manifest["go_no_go_gate"]["ids_included"])
        self.assertFalse(manifest["go_no_go_gate"]["payloads_included"])
        self.assertFalse(manifest["go_no_go_gate"]["tenant_values_included"])
        self.assertFalse(manifest["go_no_go_gate"]["transcript_content_included"])
        self.assertFalse(manifest["go_no_go_gate"]["recording_url_included"])
        self.assertTrue(manifest["decision"]["production_go_no_go_gate_ready"])
        self.assertFalse(manifest["decision"]["would_record_go_decision_now"])
        self.assertFalse(manifest["decision"]["would_record_no_go_decision_now"])
        self.assertFalse(manifest["decision"]["would_start_production_activation_now"])
        self.assertFalse(manifest["decision"]["would_modify_flags_now"])
        self.assertFalse(manifest["decision"]["would_change_campaign_runtime_now"])
        self.assertFalse(manifest["decision"]["active_enforcement"])
        self.assertEqual(manifest["decision"]["blockers"], [])
        self.assertFalse(manifest["safety"]["decision_record_created"])
        self.assertFalse(manifest["safety"]["production_activation_started"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["db_write_performed"])
        self.assertFalse(manifest["safety"]["db_payload_read_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])
        self.assertNotIn("client-1", str(manifest))
        self.assertNotIn("secret", str(manifest))
        self.assertNotIn("private transcript", str(manifest))
        self.assertNotIn("/recordings/", str(manifest))

    def test_production_go_no_go_gate_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_production_go_no_go_gate_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["production_go_no_go_gate_ready"])
        self.assertIn("tenant.production_go_no_go_gate_disabled", blockers)
        self.assertIn("tenant.canary_observation_checklist_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertTrue(manifest["go_no_go_gate"]["readiness_only"])
        self.assertFalse(manifest["go_no_go_gate"]["decision_record_created"])
        self.assertFalse(manifest["go_no_go_gate"]["production_activation_started"])
        self.assertFalse(manifest["go_no_go_gate"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_record_go_decision_now"])
        self.assertFalse(manifest["decision"]["would_start_production_activation_now"])
        self.assertFalse(manifest["safety"]["decision_record_created"])
        self.assertFalse(manifest["safety"]["production_activation_started"])
        self.assertFalse(manifest["safety"]["feature_flags_modified"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_final_activation_manifest_chain_is_report_only(self):
        context = build_tenant_context(
            headers={"X-API-Key": "secret"},
            query={"client_id": "client-1"},
            api_key_secret="secret",
        )
        kwargs = {
            "transcript_found": True,
            "transcript_owner_tenant_id": "client-1",
            "recording_found": True,
            "recording_owner_tenant_id": "client-1",
            "campaign_found": True,
            "campaign_owner_tenant_id": "client-1",
            "requested_tenant_id": "client-1",
            "transcript_campaign_id_present": True,
            "recording_campaign_id_present": True,
            "recording_required": True,
            "campaign_required": True,
        }

        with patch.dict(os.environ, _final_activation_env(), clear=True):
            contract = build_production_activation_contract_stub_manifest(context, **kwargs)
            permission = build_production_activation_permission_shadow_manifest(context, **kwargs)
            payload = build_production_activation_payload_dry_run_manifest(context, **kwargs)
            readiness = build_production_activation_readiness_manifest(context, **kwargs)
            rollback = build_production_activation_rollback_confirmation_manifest(context, **kwargs)
            handoff = build_controlled_handoff_readiness_manifest(context, **kwargs)

        self.assertEqual(contract["manifest_version"], "production_activation_contract_stub.v1")
        self.assertTrue(contract["decision"]["production_activation_contract_stub_ready"])
        self.assertFalse(contract["decision"]["would_accept_activation_request_now"])
        self.assertFalse(contract["activation_contract"]["activation_executed"])
        self.assertEqual(permission["manifest_version"], "production_activation_permission_shadow.v1")
        self.assertTrue(permission["decision"]["production_activation_permission_shadow_ready"])
        self.assertTrue(permission["permission_shadow"]["would_allow_activation_request_if_enforced"])
        self.assertFalse(permission["permission_shadow"]["would_record_permission_decision"])
        self.assertEqual(payload["manifest_version"], "production_activation_payload_dry_run.v1")
        self.assertTrue(payload["decision"]["production_activation_payload_dry_run_ready"])
        self.assertFalse(payload["payload_dry_run"]["request_payload_read"])
        self.assertFalse(payload["payload_dry_run"]["activation_payload_persisted"])
        self.assertEqual(readiness["manifest_version"], "production_activation_readiness.v1")
        self.assertTrue(readiness["decision"]["production_activation_readiness_ready"])
        self.assertFalse(readiness["activation_readiness"]["activation_live"])
        self.assertEqual(rollback["manifest_version"], "production_activation_rollback_confirmation.v1")
        self.assertTrue(rollback["decision"]["production_activation_rollback_confirmation_ready"])
        self.assertFalse(rollback["rollback_confirmation"]["rollback_action_performed"])
        self.assertFalse(rollback["rollback_confirmation"]["rollback_token_issued"])
        self.assertEqual(handoff["manifest_version"], "controlled_handoff_readiness.v1")
        self.assertTrue(handoff["decision"]["controlled_handoff_readiness_ready"])
        self.assertTrue(handoff["handoff"]["no_more_migration_layers_required"])
        self.assertFalse(handoff["handoff"]["live_activation_performed"])
        self.assertFalse(handoff["handoff"]["handoff_record_created"])
        self.assertFalse(handoff["decision"]["would_execute_activation_now"])
        self.assertFalse(handoff["decision"]["would_modify_flags_now"])
        self.assertFalse(handoff["decision"]["active_enforcement"])
        self.assertEqual(handoff["decision"]["blockers"], [])
        self.assertFalse(handoff["safety"]["db_write_performed"])
        self.assertFalse(handoff["safety"]["db_payload_read_performed"])
        self.assertFalse(handoff["safety"]["resource_payload_returned"])
        self.assertFalse(handoff["safety"]["tenant_data_returned"])
        self.assertFalse(handoff["safety"]["cross_tenant_data_included"])
        self.assertNotIn("client-1", str(handoff))
        self.assertNotIn("secret", str(handoff))
        self.assertNotIn("/recordings/", str(handoff))

    def test_controlled_handoff_readiness_manifest_disabled_is_safe(self):
        context = build_tenant_context()

        with patch.dict(os.environ, {}, clear=True):
            manifest = build_controlled_handoff_readiness_manifest(
                context,
                transcript_found=False,
                transcript_owner_tenant_id=None,
            )

        blockers = manifest["decision"]["blockers"]
        self.assertFalse(manifest["decision"]["controlled_handoff_readiness_ready"])
        self.assertIn("tenant.controlled_handoff_readiness_disabled", blockers)
        self.assertIn("tenant.production_activation_rollback_confirmation_disabled", blockers)
        self.assertIn("tenant.production_activation_readiness_disabled", blockers)
        self.assertIn("tenant.production_activation_payload_dry_run_disabled", blockers)
        self.assertIn("tenant.production_activation_permission_shadow_disabled", blockers)
        self.assertIn("tenant.production_activation_contract_stub_disabled", blockers)
        self.assertIn("tenant.production_go_no_go_gate_disabled", blockers)
        self.assertIn("resource_not_found", blockers)
        self.assertFalse(manifest["handoff"]["live_activation_performed"])
        self.assertFalse(manifest["handoff"]["handoff_record_created"])
        self.assertFalse(manifest["handoff"]["payloads_included"])
        self.assertFalse(manifest["decision"]["would_create_handoff_record_now"])
        self.assertFalse(manifest["decision"]["would_start_production_activation_now"])
        self.assertFalse(manifest["safety"]["handoff_record_created"])
        self.assertFalse(manifest["safety"]["live_activation_performed"])
        self.assertFalse(manifest["safety"]["resource_payload_returned"])
        self.assertFalse(manifest["safety"]["tenant_data_returned"])

    def test_final_activation_routes_are_admin_only_and_action_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_production_activation_contract_stub", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        expectations = {
            "/api/tenant/production-activation-contract-stub": (
                "_require_production_activation_contract_stub_enabled",
                'feature_flags.is_enabled("tenant.production_activation_contract_stub")',
                "build_production_activation_contract_stub_manifest",
                "tenant production activation contract stub requires admin context",
                "log_name=\"production_activation_contract_stub\"",
                '"production_activation_contract_stub"',
            ),
            "/api/tenant/production-activation-permission-shadow": (
                "_require_production_activation_permission_shadow_enabled",
                'feature_flags.is_enabled("tenant.production_activation_permission_shadow")',
                "build_production_activation_permission_shadow_manifest",
                "tenant production activation permission shadow requires admin context",
                "log_name=\"production_activation_permission_shadow\"",
                '"production_activation_permission_shadow"',
            ),
            "/api/tenant/production-activation-payload-dry-run": (
                "_require_production_activation_payload_dry_run_enabled",
                'feature_flags.is_enabled("tenant.production_activation_payload_dry_run")',
                "build_production_activation_payload_dry_run_manifest",
                "tenant production activation payload dry-run requires admin context",
                "log_name=\"production_activation_payload_dry_run\"",
                '"production_activation_payload_dry_run"',
            ),
            "/api/tenant/production-activation-readiness": (
                "_require_production_activation_readiness_enabled",
                'feature_flags.is_enabled("tenant.production_activation_readiness")',
                "build_production_activation_readiness_manifest",
                "tenant production activation readiness requires admin context",
                "log_name=\"production_activation_readiness\"",
                '"production_activation_readiness"',
            ),
            "/api/tenant/production-activation-rollback-confirmation": (
                "_require_production_activation_rollback_confirmation_enabled",
                'feature_flags.is_enabled("tenant.production_activation_rollback_confirmation")',
                "build_production_activation_rollback_confirmation_manifest",
                "tenant production activation rollback confirmation requires admin context",
                "log_name=\"production_activation_rollback_confirmation\"",
                '"production_activation_rollback_confirmation"',
            ),
            "/api/tenant/controlled-handoff-readiness": (
                "_require_controlled_handoff_readiness_enabled",
                'feature_flags.is_enabled("tenant.controlled_handoff_readiness")',
                "build_controlled_handoff_readiness_manifest",
                "tenant controlled handoff readiness requires admin context",
                "log_name=\"controlled_handoff_readiness\"",
                '"controlled_handoff_readiness"',
            ),
        }
        for route, fragments in expectations.items():
            self.assertIn(route, source)
            for fragment in fragments:
                self.assertIn(fragment, source)

        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("%s ready=%s upstream_ready=%s blockers=%s", source)
        self.assertIn('"activation_request_recorded": False', source)
        self.assertIn('"activation_executed": False', source)
        self.assertIn('"rollback_token_issued": False', source)
        self.assertIn('"handoff_record_created": False', source)
        self.assertIn('"live_activation_performed": False', source)
        self.assertIn('"production_activation_started": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"routes_modified": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_tenant_enforcement_readiness_route_is_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("tenant.enforcement_readiness")', source)
        self.assertIn("/api/tenant/enforcement-readiness", source)
        self.assertIn("build_tenant_enforcement_readiness", source)

    def test_tenant_scoped_read_policy_route_is_feature_flagged_and_admin_only(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("tenant.scoped_read_policy_shadow")', source)
        self.assertIn("/api/tenant/scoped-read-policy", source)
        self.assertIn("build_tenant_scoped_read_policy_manifest", source)
        self.assertIn("tenant scoped-read policy requires admin context", source)

    def test_tenant_scoped_read_endpoint_shadow_is_flagged_and_response_neutral(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("tenant.scoped_read_endpoint_shadow")', source)
        self.assertIn("def _shadow_tenant_scoped_read(", source)
        self.assertIn("build_tenant_scoped_read_guard_decision", source)
        self.assertIn("tenant_scoped_read_endpoint_shadow resource_type=%s allowed=%s", source)
        self.assertIn('_shadow_tenant_scoped_read(request, "agent"', source)
        self.assertIn('_shadow_tenant_scoped_read(request, "scrape_job"', source)
        self.assertIn('_shadow_tenant_scoped_read(request, "crm_connection"', source)
        self.assertIn('_shadow_tenant_scoped_read(request, "campaign"', source)
        self.assertIn('"call_result"', source)
        self.assertIn('"live_call_state"', source)
        self.assertIn("get_call_result_owner_for_transcript", source)
        self.assertNotIn('"tenant_scoped_read_endpoint_shadow":', source)

    def test_tenant_scoped_read_canary_route_is_feature_flagged_and_admin_only(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("tenant.scoped_read_canary")', source)
        self.assertIn("/api/tenant/scoped-read-canary", source)
        self.assertIn("get_tenant_scoped_resource_owner", source)
        self.assertIn("tenant scoped-read canary requires admin context", source)

    def test_tenant_leak_regression_matrix_route_is_admin_only_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_leak_regression_matrix", 1)[1].split(
            "async def get_tenant_recording_access_canary",
            1,
        )[0]

        self.assertIn("/api/tenant/leak-regression-matrix", source)
        self.assertIn("_require_tenant_leak_regression_matrix_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.leak_regression_matrix")', source)
        self.assertIn("tenant leak regression matrix requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_tenant_leak_regression_matrix_manifest", source)
        self.assertIn("tenant_leak_regression_matrix ready=%s", source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"cross_tenant_data_returned": False', source)
        self.assertIn('"tenant_leak_regression_matrix"', source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_recording_access_canary_route_is_flagged_admin_only_and_response_safe(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("/api/tenant/recording-access-canary", source)
        self.assertIn("_require_recording_owner_lookup_shadow_enabled", source)
        self.assertIn('feature_flags.is_enabled("recordings.access_shadow")', source)
        self.assertIn('feature_flags.is_enabled("recordings.owner_lookup_shadow")', source)
        self.assertIn("tenant recording-access canary requires admin context", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn("build_recording_owner_lookup_shadow_manifest", source)
        self.assertIn('"static_file_serving_changed": False', source)
        self.assertIn('"recording_playback_changed": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertNotIn('"recordingUrl":', source)

    def test_recording_access_enforcement_readiness_route_is_shadow_safe(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("/api/tenant/recording-access-enforcement-readiness", source)
        self.assertIn("_require_recording_access_enforcement_shadow_enabled", source)
        self.assertIn('feature_flags.is_enabled("recordings.access_enforcement_shadow")', source)
        self.assertIn("tenant recording-access enforcement readiness requires admin context", source)
        self.assertIn("build_recording_access_enforcement_readiness_manifest", source)
        self.assertIn("recording_access_enforcement_shadow ready=%s would_allow=%s", source)
        self.assertIn('"recording_response_changed": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertNotIn('"recordingUrl":', source)

    def test_recording_access_gate_dry_run_route_is_shadow_safe(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("/api/tenant/recording-access-gate-dry-run", source)
        self.assertIn("_require_recording_access_gate_dry_run_enabled", source)
        self.assertIn('feature_flags.is_enabled("recordings.access_gate_dry_run")', source)
        self.assertIn("tenant recording-access gate dry run requires admin context", source)
        self.assertIn("build_recording_access_gate_dry_run_manifest", source)
        self.assertIn("recording_access_gate_dry_run ready=%s would_allow=%s", source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertNotIn('"recordingUrl":', source)

    def test_transcript_access_shadow_is_flagged_and_response_neutral(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("transcripts.access_shadow")', source)
        self.assertIn("async def _shadow_transcript_access(", source)
        self.assertIn("build_transcript_access_shadow_manifest", source)
        self.assertIn("transcript_access_shadow found=%s owner_tenant_present=%s", source)
        self.assertIn("await _shadow_transcript_access(request, lead_id)", source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)
        self.assertNotIn('"transcript_access_shadow":', source)

    def test_transcript_access_canary_route_is_flagged_admin_only_and_content_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        canary_source = source.split('/api/tenant/transcript-access-canary', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-access-canary", source)
        self.assertIn("_require_transcript_access_canary_enabled", source)
        self.assertIn('feature_flags.is_enabled("transcripts.access_shadow")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.access_canary")', source)
        self.assertIn("tenant transcript-access canary requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_access_canary_manifest", source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertNotIn('"leadId":', canary_source)

    def test_transcript_access_enforcement_readiness_route_is_shadow_safe(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-access-enforcement-readiness', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-access-enforcement-readiness", source)
        self.assertIn("_require_transcript_access_enforcement_shadow_enabled", source)
        self.assertIn('feature_flags.is_enabled("transcripts.access_enforcement_shadow")', source)
        self.assertIn("tenant transcript-access enforcement readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_access_enforcement_readiness_manifest", source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_access_gate_dry_run_route_is_shadow_safe(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-access-gate-dry-run', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-access-gate-dry-run", source)
        self.assertIn("_require_transcript_access_gate_dry_run_enabled", source)
        self.assertIn('feature_flags.is_enabled("transcripts.access_gate_dry_run")', source)
        self.assertIn("tenant transcript-access gate dry run requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_access_gate_dry_run_manifest", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_protected_route_stub_is_disabled_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/protected/transcripts/{lead_id}', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/protected/transcripts/{lead_id}", source)
        self.assertIn("_require_transcript_protected_route_stub_enabled", source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_route_stub")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_route_permission_shadow")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_response_shape_canary")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_payload_dry_run")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_enforcement_readiness")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_live_activation_plan")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_rollback_readiness")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.frontend_migration_readiness")', source)
        self.assertIn("Transcript route not found", source)
        self.assertIn("protected transcript route stub requires verified backend identity", source)
        self.assertIn("protected transcript route stub requires tenant context", source)
        self.assertIn("get_call_result_owner_for_transcript(lead_id)", source)
        self.assertIn("build_transcript_protected_route_stub_manifest", source)
        self.assertIn("build_transcript_protected_route_permission_shadow_manifest", source)
        self.assertIn("build_transcript_protected_response_shape_canary_manifest", source)
        self.assertIn("build_transcript_protected_payload_dry_run_manifest", source)
        self.assertIn("transcript_protected_route_stub ready=%s would_allow=%s", source)
        self.assertIn("transcript_protected_route_permission_shadow would_allow_payload=%s", source)
        self.assertIn("transcript_protected_response_shape_canary schema_ready=%s", source)
        self.assertIn("transcript_protected_payload_dry_run ready_for_payload_read=%s", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn('"transcript_protected_route_permission_shadow"', source)
        self.assertIn('"transcript_protected_response_shape_canary"', source)
        self.assertIn('"transcript_protected_payload_dry_run"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn('"lead_id":', route_source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_protected_enforcement_readiness_route_is_admin_only_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-protected-enforcement-readiness', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-protected-enforcement-readiness", source)
        self.assertIn("_require_transcript_protected_enforcement_readiness_enabled", source)
        self.assertIn("tenant transcript protected enforcement readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_protected_enforcement_readiness_manifest", source)
        self.assertIn("transcript_protected_enforcement_readiness ready=%s", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"live_payload_route_enabled": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn('"transcript_protected_enforcement_readiness"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_protected_live_activation_plan_route_is_admin_only_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-protected-live-activation-plan', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-protected-live-activation-plan", source)
        self.assertIn("_require_transcript_protected_live_activation_plan_enabled", source)
        self.assertIn("tenant transcript protected live activation plan requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_protected_live_activation_plan_manifest", source)
        self.assertIn("transcript_protected_live_activation_plan ready=%s", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"live_payload_route_enabled": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn('"transcript_protected_live_activation_plan"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_protected_rollback_readiness_route_is_admin_only_and_action_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-protected-rollback-readiness', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-protected-rollback-readiness", source)
        self.assertIn("_require_transcript_protected_rollback_readiness_enabled", source)
        self.assertIn("tenant transcript protected rollback readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_protected_rollback_readiness_manifest", source)
        self.assertIn("transcript_protected_rollback_readiness ready=%s", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"live_payload_route_enabled": False', source)
        self.assertIn('"rollback_action_performed": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn('"transcript_protected_rollback_readiness"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn('"leadId":', route_source)

    def test_transcript_frontend_migration_readiness_route_is_admin_only_and_ui_neutral(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split('/api/tenant/transcript-frontend-migration-readiness', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn("/api/tenant/transcript-frontend-migration-readiness", source)
        self.assertIn("_require_transcript_frontend_migration_readiness_enabled", source)
        self.assertIn("tenant transcript frontend migration readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("build_transcript_frontend_migration_readiness_manifest", source)
        self.assertIn("transcript_frontend_migration_readiness ready=%s", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"frontend_code_changed": False', source)
        self.assertIn('"live_payload_route_enabled": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn('"transcript_frontend_migration_readiness"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn('"leadId":', route_source)

    def test_result_asset_readiness_route_is_admin_only_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_result_asset_readiness", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/result-asset-readiness", source)
        self.assertIn("_require_result_asset_readiness_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.result_asset_readiness")', source)
        self.assertIn("tenant result asset readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_result_asset_readiness_manifest", source)
        self.assertIn("result_asset_readiness ready=%s", source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"recording_response_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"cross_tenant_data_returned": False', source)
        self.assertIn('"result_asset_readiness"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_final_rollout_report_route_is_admin_only_and_runtime_neutral(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_final_rollout_report", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/final-rollout-report", source)
        self.assertIn("_require_final_rollout_report_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.final_rollout_report")', source)
        self.assertIn("tenant final rollout report requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_final_rollout_report_readiness_manifest", source)
        self.assertIn("final_rollout_report ready=%s", source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"recording_response_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"recording_bytes_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"cross_tenant_data_returned": False', source)
        self.assertIn('"final_rollout_report"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_rollout_approval_packet_route_is_admin_only_and_action_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_rollout_approval_packet", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/rollout-approval-packet", source)
        self.assertIn("_require_rollout_approval_packet_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.rollout_approval_packet")', source)
        self.assertIn("tenant rollout approval packet requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_rollout_approval_packet_manifest", source)
        self.assertIn("rollout_approval_packet ready=%s", source)
        self.assertIn('"approval_state_changed": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"rollout_approval_packet"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_rollout_canary_plan_route_is_admin_only_and_plan_only(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_rollout_canary_plan", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/rollout-canary-plan", source)
        self.assertIn("_require_rollout_canary_plan_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.rollout_canary_plan")', source)
        self.assertIn("tenant rollout canary plan requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_rollout_canary_plan_manifest", source)
        self.assertIn("rollout_canary_plan ready=%s", source)
        self.assertIn('"approval_state_changed": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"canary_started": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"rollout_canary_plan"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_rollback_drill_readiness_route_is_admin_only_and_dry_run(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_rollback_drill_readiness", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/rollback-drill-readiness", source)
        self.assertIn("_require_rollback_drill_readiness_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.rollback_drill_readiness")', source)
        self.assertIn("tenant rollback drill readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_rollback_drill_readiness_manifest", source)
        self.assertIn("rollback_drill_readiness ready=%s", source)
        self.assertIn('"approval_state_changed": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"canary_started": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"rollback_action_performed": False', source)
        self.assertIn('"routes_modified": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"rollback_drill_readiness"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_rollout_evidence_bundle_route_is_admin_only_and_payload_free(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_rollout_evidence_bundle", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/rollout-evidence-bundle", source)
        self.assertIn("_require_rollout_evidence_bundle_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.rollout_evidence_bundle")', source)
        self.assertIn("tenant rollout evidence bundle requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_rollout_evidence_bundle_manifest", source)
        self.assertIn("rollout_evidence_bundle ready=%s", source)
        self.assertIn('"evidence_record_created": False', source)
        self.assertIn('"live_data_collected": False', source)
        self.assertIn('"metrics_sampled": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"rollout_evidence_bundle"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_canary_observation_checklist_route_is_admin_only_and_passive(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_canary_observation_checklist", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/canary-observation-checklist", source)
        self.assertIn("_require_canary_observation_checklist_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.canary_observation_checklist")', source)
        self.assertIn("tenant canary observation checklist requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_canary_observation_checklist_manifest", source)
        self.assertIn("canary_observation_checklist ready=%s", source)
        self.assertIn('"observation_record_created": False', source)
        self.assertIn('"live_data_collected": False', source)
        self.assertIn('"metrics_sampled": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"canary_started": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"canary_observation_checklist"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)

    def test_production_go_no_go_gate_route_is_admin_only_and_report_only(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")
        route_source = source.split("async def get_tenant_production_go_no_go_gate", 1)[1].split(
            "async def list_agents",
            1,
        )[0]

        self.assertIn("/api/tenant/production-go-no-go-gate", source)
        self.assertIn("_require_production_go_no_go_gate_enabled", source)
        self.assertIn('feature_flags.is_enabled("tenant.production_go_no_go_gate")', source)
        self.assertIn("tenant production go/no-go gate requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn("build_production_go_no_go_gate_manifest", source)
        self.assertIn("production_go_no_go_gate ready=%s", source)
        self.assertIn('"decision_record_created": False', source)
        self.assertIn('"production_activation_started": False', source)
        self.assertIn('"observation_record_created": False', source)
        self.assertIn('"live_data_collected": False', source)
        self.assertIn('"metrics_sampled": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"canary_started": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_write_performed": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"production_go_no_go_gate"', source)
        self.assertNotIn("get_transcript_for_lead", route_source)
        self.assertNotIn("FileResponse", route_source)
        self.assertNotIn('"leadId":', route_source)
        self.assertNotIn('"recordingUrl":', route_source)
        self.assertNotIn('"campaignId":', route_source)


if __name__ == "__main__":
    unittest.main()
