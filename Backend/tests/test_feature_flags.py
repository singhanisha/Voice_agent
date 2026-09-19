import os
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from platform_migration import feature_flags


class FeatureFlagsTest(unittest.TestCase):
    def test_all_roadmap_flags_are_registered_with_rollback_defaults(self):
        self.assertEqual(
            set(feature_flags.known_flags()),
            {
                "auth.enforce_backend",
                "tenant.scoped_reads",
                "tenant.enforcement_readiness",
                "tenant.scoped_read_canary",
                "tenant.scoped_read_guard_shadow",
                "tenant.scoped_read_policy_shadow",
                "tenant.scoped_read_endpoint_shadow",
                "tenant.leak_regression_matrix",
                "tenant.security_leak_audit_readiness",
                "tenant.result_asset_readiness",
                "tenant.final_rollout_report",
                "tenant.rollout_approval_packet",
                "tenant.rollout_canary_plan",
                "tenant.rollback_drill_readiness",
                "tenant.rollout_evidence_bundle",
                "tenant.canary_observation_checklist",
                "tenant.production_go_no_go_gate",
                "tenant.production_activation_contract_stub",
                "tenant.production_activation_permission_shadow",
                "tenant.production_activation_payload_dry_run",
                "tenant.production_activation_readiness",
                "tenant.production_activation_rollback_confirmation",
                "tenant.controlled_handoff_readiness",
                "tenant.final_canary_rollback_readiness",
                "ws.scoped_events",
                "ws.scoped_events_shadow",
                "recordings.access_shadow",
                "recordings.owner_lookup_shadow",
                "recordings.access_enforcement_shadow",
                "recordings.access_gate_dry_run",
                "transcripts.access_shadow",
                "transcripts.access_canary",
                "transcripts.access_enforcement_shadow",
                "transcripts.access_gate_dry_run",
                "transcripts.protected_route_stub",
                "transcripts.protected_route_permission_shadow",
                "transcripts.protected_response_shape_canary",
                "transcripts.protected_payload_dry_run",
                "transcripts.protected_enforcement_readiness",
                "transcripts.protected_live_activation_plan",
                "transcripts.protected_rollback_readiness",
                "transcripts.frontend_migration_readiness",
                "campaign.worker_v2",
                "campaign.lifecycle_management",
                "campaign.e2e_qa_readiness",
                "flow.v2_shadow",
                "flow.v2_live",
                "flow.visualization",
                "demo.runtime_qa_readiness",
                "scrape.generate_script",
                "scrape.worker_v1",
                "scrape.job_cancel",
                "scrape.stale_recovery",
                "scrape.job_events",
                "scrape.review_gate_shadow",
                "scrape.live_qa_readiness",
                "scrape.generated_draft_qa_readiness",
                "telephony.tenant_numbers",
                "telephony.live_qa_readiness",
                "memory.rag_enabled",
                "crm.sync_enabled",
                "crm.sync_preflight",
                "crm.sync_outbox",
                "crm.sync_worker_shadow",
                "crm.sync_worker_retries",
                "crm.sync_observability",
                "crm.provider_contracts",
                "crm.delivery_plan_shadow",
                "crm.delivery_approval_shadow",
                "crm.delivery_approval_revoke",
                "crm.live_readiness_shadow",
                "crm.provider_sandbox_shadow",
                "crm.dispatch_canary_shadow",
            },
        )
        self.assertTrue(all(value is False for value in feature_flags.known_flags().values()))
        self.assertTrue(all(value is False for value in feature_flags.snapshot(env={"PLATFORM_FEATURE_PROFILE": "shadow"}).values()))

    def test_live_profile_enables_completed_features_with_auth_exception(self):
        env = {"PLATFORM_FEATURE_PROFILE": "live"}
        snapshot = feature_flags.snapshot(env=env)

        self.assertEqual(feature_flags.active_profile(env=env), "live")
        self.assertTrue(snapshot["flow.v2_live"])
        self.assertTrue(snapshot["scrape.generate_script"])
        self.assertTrue(snapshot["campaign.worker_v2"])
        self.assertTrue(snapshot["telephony.tenant_numbers"])
        self.assertFalse(snapshot["auth.enforce_backend"])

    def test_environment_override_uses_stable_names(self):
        env = {
            "FEATURE_AUTH_ENFORCE_BACKEND": "true",
            "FEATURE_CAMPAIGN_WORKER_V2": "1",
            "FEATURE_FLOW_V2_LIVE": "false",
        }

        self.assertEqual(
            feature_flags.env_name("auth.enforce_backend"),
            "FEATURE_AUTH_ENFORCE_BACKEND",
        )
        self.assertTrue(feature_flags.is_enabled("auth.enforce_backend", env=env))
        self.assertTrue(feature_flags.is_enabled("campaign.worker_v2", env=env))
        self.assertFalse(feature_flags.is_enabled("flow.v2_live", env=env))

    def test_unknown_flags_are_disabled_without_explicit_default(self):
        self.assertFalse(
            feature_flags.is_enabled("unknown.future_flag", env=os.environ.copy())
        )
        self.assertTrue(
            feature_flags.is_enabled("unknown.future_flag", default=True, env={})
        )


if __name__ == "__main__":
    unittest.main()
