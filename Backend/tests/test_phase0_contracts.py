import os
import sys
import unittest
from array import array
from pathlib import Path


os.environ.setdefault("GROQ_API_KEY", "phase-zero-contract-test-key")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = REPO_ROOT / "frontend-next"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from flows import runtime
from flows.runtime import VoiceTurnState


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PhaseZeroContractsTest(unittest.TestCase):
    def test_audio_pcm_contract_helpers_remain_stable(self):
        pcm_samples = array("h", [0, 1024, -1024, 32767, -32768])
        pcm_bytes = pcm_samples.tobytes()

        self.assertEqual(runtime._ms_to_bytes(1000, 16000), 32000)
        self.assertEqual(runtime._ensure_pcm16(pcm_bytes, 16000, 16000), pcm_bytes)

        float_bytes = array("f", [0.0, -1.0, 1.0]).tobytes()
        converted = runtime._ensure_pcm16(float_bytes, 16000, 16000)
        converted_samples = array("h")
        converted_samples.frombytes(converted)

        self.assertEqual(len(converted), 6)
        self.assertEqual(converted_samples[0], 0)
        self.assertLessEqual(converted_samples[1], -32767)
        self.assertGreaterEqual(converted_samples[2], 32766)

    def test_turn_state_preserves_tts_blocking_contract(self):
        turn_state = VoiceTurnState()
        self.assertFalse(turn_state.is_stt_blocked())

        turn_state.mark_tts_started()
        self.assertTrue(turn_state.is_stt_blocked())

        turn_state.mark_tts_finished(cooldown_ms=0)
        self.assertFalse(turn_state.tts_active)

    def test_backend_voice_and_telephony_routes_remain_present(self):
        source = _read(BACKEND_ROOT / "main.py")

        for route in [
            '@app.websocket("/api/voice-live")',
            '@app.websocket("/api/voice-demo")',
            '@app.post("/telephony/twiml/{call_id}")',
            '@app.websocket("/telephony/stream/{call_id}")',
            'app.mount("/recordings"',
        ]:
            self.assertIn(route, source)

    def test_recording_access_shadow_keeps_static_mount_contract(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('app.mount("/recordings"', source)
        self.assertIn('feature_flags.is_enabled("recordings.access_shadow")', source)
        self.assertIn('feature_flags.is_enabled("recordings.owner_lookup_shadow")', source)
        self.assertIn('feature_flags.is_enabled("recordings.access_enforcement_shadow")', source)
        self.assertIn('feature_flags.is_enabled("recordings.access_gate_dry_run")', source)
        self.assertIn("build_recording_access_shadow_manifest", source)
        self.assertIn("build_recording_owner_lookup_shadow_manifest", source)
        self.assertIn("build_recording_access_enforcement_readiness_manifest", source)
        self.assertIn("build_recording_access_gate_dry_run_manifest", source)
        self.assertIn("recording_access_shadow requested=%s extension=%s", source)
        self.assertIn("recording_owner_lookup_shadow found=%s owner_tenant_present=%s", source)
        self.assertIn("recording_access_enforcement_shadow ready=%s would_allow=%s", source)
        self.assertIn("recording_access_gate_dry_run ready=%s would_allow=%s", source)
        self.assertIn("get_recording_asset_owner", source)
        self.assertIn("/api/tenant/recording-access-canary", source)
        self.assertIn("/api/tenant/recording-access-enforcement-readiness", source)
        self.assertIn("/api/tenant/recording-access-gate-dry-run", source)
        self.assertIn('"static_file_serving_changed": False', source)
        self.assertIn('"recording_playback_changed": False', source)
        self.assertIn('"recording_response_changed": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn("return await call_next(request)", source)
        self.assertNotIn('"recording_access_shadow":', source)

    def test_transcript_access_shadow_keeps_transcript_response_contract(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("transcripts.access_shadow")', source)
        self.assertIn("build_transcript_access_shadow_manifest", source)
        self.assertIn("async def _shadow_transcript_access(", source)
        self.assertIn("transcript_access_shadow found=%s owner_tenant_present=%s", source)
        self.assertIn("@app.get(\"/api/results/{lead_id}/transcript\")", source)
        self.assertIn("await _shadow_transcript_access(request, lead_id)", source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)
        self.assertNotIn('"transcript_access_shadow":', source)

    def test_transcript_access_canary_is_separate_from_transcript_endpoint(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("transcripts.access_canary")', source)
        self.assertIn("/api/tenant/transcript-access-canary", source)
        self.assertIn("build_transcript_access_canary_manifest", source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)

    def test_transcript_enforcement_readiness_is_separate_from_transcript_endpoint(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("transcripts.access_enforcement_shadow")', source)
        self.assertIn("/api/tenant/transcript-access-enforcement-readiness", source)
        self.assertIn("build_transcript_access_enforcement_readiness_manifest", source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)

    def test_transcript_gate_dry_run_is_separate_from_transcript_endpoint(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("transcripts.access_gate_dry_run")', source)
        self.assertIn("/api/tenant/transcript-access-gate-dry-run", source)
        self.assertIn("build_transcript_access_gate_dry_run_manifest", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"transcript_response_changed": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"transcript_turn_count_returned": False', source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)

    def test_transcript_protected_route_stub_is_separate_from_transcript_endpoint(self):
        source = _read(BACKEND_ROOT / "main.py")
        route_source = source.split('/api/protected/transcripts/{lead_id}', 1)[1].split(
            '@app.get("/api/agents")',
            1,
        )[0]

        self.assertIn('feature_flags.is_enabled("transcripts.protected_route_stub")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_route_permission_shadow")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_response_shape_canary")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_payload_dry_run")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_enforcement_readiness")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_live_activation_plan")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.protected_rollback_readiness")', source)
        self.assertIn('feature_flags.is_enabled("transcripts.frontend_migration_readiness")', source)
        self.assertIn("/api/protected/transcripts/{lead_id}", source)
        self.assertIn("/api/tenant/transcript-protected-enforcement-readiness", source)
        self.assertIn("/api/tenant/transcript-protected-live-activation-plan", source)
        self.assertIn("/api/tenant/transcript-protected-rollback-readiness", source)
        self.assertIn("/api/tenant/transcript-frontend-migration-readiness", source)
        self.assertIn("build_transcript_protected_route_stub_manifest", source)
        self.assertIn("build_transcript_protected_route_permission_shadow_manifest", source)
        self.assertIn("build_transcript_protected_response_shape_canary_manifest", source)
        self.assertIn("build_transcript_protected_payload_dry_run_manifest", source)
        self.assertIn("build_transcript_protected_enforcement_readiness_manifest", source)
        self.assertIn("build_transcript_protected_live_activation_plan_manifest", source)
        self.assertIn("build_transcript_protected_rollback_readiness_manifest", source)
        self.assertIn("build_transcript_frontend_migration_readiness_manifest", source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"live_payload_route_enabled": False', source)
        self.assertIn('"rollback_action_performed": False', source)
        self.assertIn('"frontend_code_changed": False', source)
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
        self.assertIn('"transcript_protected_enforcement_readiness"', source)
        self.assertIn('"transcript_protected_live_activation_plan"', source)
        self.assertIn('"transcript_protected_rollback_readiness"', source)
        self.assertIn('"transcript_frontend_migration_readiness"', source)
        self.assertIn("return await db.get_transcript_for_lead(lead_id, client_id)", source)
        self.assertNotIn("get_transcript_for_lead", route_source)

    def test_tenant_leak_regression_matrix_is_no_payload_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.leak_regression_matrix")', source)
        self.assertIn("/api/tenant/leak-regression-matrix", source)
        self.assertIn("build_tenant_leak_regression_matrix_manifest", source)
        self.assertIn("tenant leak regression matrix requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"call_result_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)
        self.assertIn('"cross_tenant_data_returned": False', source)

    def test_result_asset_readiness_is_no_payload_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.result_asset_readiness")', source)
        self.assertIn("/api/tenant/result-asset-readiness", source)
        self.assertIn("_require_result_asset_readiness_enabled", source)
        self.assertIn("build_result_asset_readiness_manifest", source)
        self.assertIn("tenant result asset readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_final_rollout_report_is_no_payload_runtime_neutral_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.final_rollout_report")', source)
        self.assertIn("/api/tenant/final-rollout-report", source)
        self.assertIn("_require_final_rollout_report_enabled", source)
        self.assertIn("build_final_rollout_report_readiness_manifest", source)
        self.assertIn("tenant final rollout report requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"audio_runtime_changed": False', source)
        self.assertIn('"websocket_contract_changed": False', source)
        self.assertIn('"campaign_runtime_changed": False', source)
        self.assertIn('"results_endpoint_changed": False', source)
        self.assertIn('"protected_transcript_route_activated": False', source)
        self.assertIn('"protected_recording_route_activated": False', source)
        self.assertIn('"db_payload_read_performed": False', source)
        self.assertIn('"file_bytes_read": False', source)
        self.assertIn('"resource_payload_returned": False', source)
        self.assertIn('"lead_id_returned": False', source)
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_rollout_approval_packet_is_action_free_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.rollout_approval_packet")', source)
        self.assertIn("/api/tenant/rollout-approval-packet", source)
        self.assertIn("_require_rollout_approval_packet_enabled", source)
        self.assertIn("build_rollout_approval_packet_manifest", source)
        self.assertIn("tenant rollout approval packet requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"approval_state_changed": False', source)
        self.assertIn('"feature_flags_modified": False', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_rollout_canary_plan_is_plan_only_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.rollout_canary_plan")', source)
        self.assertIn("/api/tenant/rollout-canary-plan", source)
        self.assertIn("_require_rollout_canary_plan_enabled", source)
        self.assertIn("build_rollout_canary_plan_manifest", source)
        self.assertIn("tenant rollout canary plan requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"approval_state_changed": False', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_rollback_drill_readiness_is_dry_run_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.rollback_drill_readiness")', source)
        self.assertIn("/api/tenant/rollback-drill-readiness", source)
        self.assertIn("_require_rollback_drill_readiness_enabled", source)
        self.assertIn("build_rollback_drill_readiness_manifest", source)
        self.assertIn("tenant rollback drill readiness requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
        self.assertIn('"approval_state_changed": False', source)
        self.assertIn('"feature_flags_modified": False', source)
        self.assertIn('"canary_started": False', source)
        self.assertIn('"traffic_shifted": False', source)
        self.assertIn('"rollback_action_performed": False', source)
        self.assertIn('"routes_modified": False', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_rollout_evidence_bundle_is_no_payload_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.rollout_evidence_bundle")', source)
        self.assertIn("/api/tenant/rollout-evidence-bundle", source)
        self.assertIn("_require_rollout_evidence_bundle_enabled", source)
        self.assertIn("build_rollout_evidence_bundle_manifest", source)
        self.assertIn("tenant rollout evidence bundle requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_canary_observation_checklist_is_passive_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.canary_observation_checklist")', source)
        self.assertIn("/api/tenant/canary-observation-checklist", source)
        self.assertIn("_require_canary_observation_checklist_enabled", source)
        self.assertIn("build_canary_observation_checklist_manifest", source)
        self.assertIn("tenant canary observation checklist requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_production_go_no_go_gate_is_report_only_admin_surface(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn('feature_flags.is_enabled("tenant.production_go_no_go_gate")', source)
        self.assertIn("/api/tenant/production-go-no-go-gate", source)
        self.assertIn("_require_production_go_no_go_gate_enabled", source)
        self.assertIn("build_production_go_no_go_gate_manifest", source)
        self.assertIn("tenant production go/no-go gate requires admin context", source)
        self.assertIn("get_call_result_owner_for_transcript(leadId)", source)
        self.assertIn("get_recording_asset_owner(recordingUrl)", source)
        self.assertIn('get_tenant_scoped_resource_owner("campaign", campaignId)', source)
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
        self.assertIn('"campaign_id_returned": False', source)
        self.assertIn('"recording_url_returned": False', source)
        self.assertIn('"transcript_content_returned": False', source)
        self.assertIn('"tenant_data_returned": False', source)

    def test_final_activation_surfaces_are_disabled_report_only(self):
        source = _read(BACKEND_ROOT / "main.py")
        flags_source = _read(BACKEND_ROOT / "platform_migration" / "feature_flags.py")
        auth_source = _read(BACKEND_ROOT / "platform_migration" / "auth_context.py")

        expected = {
            "tenant.production_activation_contract_stub": (
                "/api/tenant/production-activation-contract-stub",
                "_require_production_activation_contract_stub_enabled",
                "build_production_activation_contract_stub_manifest",
            ),
            "tenant.production_activation_permission_shadow": (
                "/api/tenant/production-activation-permission-shadow",
                "_require_production_activation_permission_shadow_enabled",
                "build_production_activation_permission_shadow_manifest",
            ),
            "tenant.production_activation_payload_dry_run": (
                "/api/tenant/production-activation-payload-dry-run",
                "_require_production_activation_payload_dry_run_enabled",
                "build_production_activation_payload_dry_run_manifest",
            ),
            "tenant.production_activation_readiness": (
                "/api/tenant/production-activation-readiness",
                "_require_production_activation_readiness_enabled",
                "build_production_activation_readiness_manifest",
            ),
            "tenant.production_activation_rollback_confirmation": (
                "/api/tenant/production-activation-rollback-confirmation",
                "_require_production_activation_rollback_confirmation_enabled",
                "build_production_activation_rollback_confirmation_manifest",
            ),
            "tenant.controlled_handoff_readiness": (
                "/api/tenant/controlled-handoff-readiness",
                "_require_controlled_handoff_readiness_enabled",
                "build_controlled_handoff_readiness_manifest",
            ),
        }
        for flag, fragments in expected.items():
            self.assertIn(f'"{flag}": False', flags_source)
            self.assertIn(f'feature_flags.is_enabled("{flag}")', source + auth_source)
            for fragment in fragments:
                self.assertIn(fragment, source + auth_source)

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
        self.assertIn('"tenant_data_returned": False', source)

    def test_runtime_processor_audio_and_cancellation_contracts_remain_present(self):
        source = _read(BACKEND_ROOT / "flows" / "runtime.py")

        self.assertIn("class RealEstateSTTProcessor", source)
        self.assertIn("class RealEstateLLMProcessor", source)
        self.assertIn("class RealEstateTTSProcessor", source)
        self.assertIn("sample_rate=24000", source)
        self.assertIn("CancelFrame", source)
        self.assertIn("transcribe_audio(chunk, self.agent_id, language=session_lang)", source)
        self.assertIn("generate_speech_stream(text, preferred_lang, self.agent_id)", source)

    def test_frontend_voice_socket_contract_remains_present(self):
        source = _read(FRONTEND_ROOT / "src" / "hooks" / "useVoiceSocket.js")

        self.assertIn("socket.binaryType = 'arraybuffer'", source)
        self.assertIn("toPcm16Buffer", source)
        self.assertIn("mic_ready", source)
        self.assertIn("api/voice-demo?agentId=", source)
        self.assertIn("api/voice-live?agentId=", source)
        self.assertIn("createBuffer(1, floatData.length, 24000)", source)
        self.assertIn("expectsGenHeaderRef.current = false", source)

    def test_scoped_db_methods_exist_for_phase_one_hardening(self):
        source = _read(BACKEND_ROOT / "db" / "db_manager.py")

        for signature in [
            "async def list_agents(self, client_id: Optional[str] = None)",
            "async def list_campaigns(self, client_id: Optional[str] = None)",
            "async def list_phone_numbers(self, client_id: Optional[str] = None)",
        ]:
            self.assertIn(signature, source)

    def test_provider_contracts_remain_reversible(self):
        stt_source = _read(BACKEND_ROOT / "stt" / "provider.py")
        tts_source = _read(BACKEND_ROOT / "tts" / "provider.py")

        self.assertIn('DEFAULT_PROVIDER = "groq"', stt_source)
        self.assertIn('SUPPORTED_PROVIDERS = {"groq", "deepgram", "indic_seamless"}', stt_source)
        self.assertIn("STT_SHADOW_MODE", stt_source)
        self.assertIn("STT_FALLBACK_ENABLED", stt_source)

        self.assertIn('DEFAULT_PROVIDER = "edge"', tts_source)
        self.assertIn('SUPPORTED_PROVIDERS = {"edge", "cartesia"}', tts_source)
        self.assertIn("Providers must yield raw PCM16 mono chunks at 24kHz", tts_source)
        self.assertIn("TTS_SHADOW_MODE", tts_source)
        self.assertIn("TTS_FALLBACK_ENABLED", tts_source)


if __name__ == "__main__":
    unittest.main()
