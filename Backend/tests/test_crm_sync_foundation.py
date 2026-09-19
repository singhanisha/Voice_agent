import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from crm import CRMIntegrationService
from db import db_manager


class CRMSyncFoundationTest(unittest.TestCase):
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
        asyncio.run(self.manager.create_client("client-2", {
            "name": "Client Two",
            "email": "client2@example.com",
            "plan": "assigned",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-1", {
            "name": "Tenant One Campaign",
            "status": "Pending",
            "client_id": "client-1",
            "telephony_provider": "demo",
        }))
        asyncio.run(self.manager.upsert_campaign("campaign-2", {
            "name": "Tenant Two Campaign",
            "status": "Pending",
            "client_id": "client-2",
            "telephony_provider": "demo",
        }))
        self.service = CRMIntegrationService(self.manager)

    def tearDown(self):
        db_manager.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_connection_metadata_is_tenant_scoped_and_secret_free(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="Client One HubSpot",
            external_account_id="portal-123",
            config={"region": "us", "sync_objects": ["contacts", "deals"]},
            requested_by="admin@example.com",
        ))
        tenant_connections = asyncio.run(self.service.list_connections(client_id="client-1"))
        other_tenant_connections = asyncio.run(self.service.list_connections(client_id="client-2"))

        self.assertEqual(connection["client_id"], "client-1")
        self.assertEqual(connection["provider"], "hubspot")
        self.assertFalse(connection["secrets_configured"])
        self.assertEqual(len(tenant_connections), 1)
        self.assertEqual(other_tenant_connections, [])

    def test_connection_rejects_secret_like_public_config(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.service.create_connection(
                client_id="client-1",
                provider="salesforce",
                config={"oauth": {"access_token": "must-not-be-stored"}},
            ))

    def test_secret_reference_configures_connection_without_storing_secret_value(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Secret Ref",
        ))

        configured = asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
            rotation_due_at="2026-08-01",
            metadata={"owner": "platform"},
            requested_by="admin@example.com",
        ))
        reference = configured["config"]["credential_reference"]

        self.assertTrue(configured["secrets_configured"])
        self.assertEqual(configured["status"], "configured")
        self.assertTrue(reference["configured"])
        self.assertTrue(reference["external_secret_storage"])
        self.assertFalse(reference["secret_value_stored"])
        self.assertEqual(reference["reference_id"], "crm/client-1/hubspot/oauth")
        self.assertNotIn("access_token", str(configured))

    def test_secret_reference_rejects_secret_values_and_sensitive_metadata(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Secret Ref",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.configure_secret_reference(
                client_id="client-1",
                connection_id=connection["id"],
                vault_provider="external",
                reference_id="sk-this-is-a-raw-token-not-a-reference",
            ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.configure_secret_reference(
                client_id="client-1",
                connection_id=connection["id"],
                vault_provider="external",
                reference_id="crm/client-1/salesforce/oauth",
                metadata={"refresh_token": "must-not-be-stored"},
            ))

    def test_secret_reference_rejects_cross_tenant_connection(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Secret Ref",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.configure_secret_reference(
                client_id="client-2",
                connection_id=connection["id"],
                vault_provider="external",
                reference_id="crm/client-2/zoho/oauth",
            ))

    def test_provider_contract_reports_capabilities_without_secret_or_config_values(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Contract",
            config={"region": "private-region-value", "sync_mode": "tenant-config-value"},
        ))

        draft_contract = asyncio.run(self.service.get_provider_contract(
            client_id="client-1",
            connection_id=connection["id"],
        ))
        self.assertFalse(draft_contract["credential_reference"]["configured"])
        self.assertIn("credential_reference_missing", draft_contract["readiness"]["blockers"])
        self.assertFalse(draft_contract["readiness"]["live_sync_ready"])
        self.assertFalse(draft_contract["external_execution"])
        self.assertFalse(draft_contract["network_check_performed"])

        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
            metadata={"owner": "platform"},
        ))
        contract = asyncio.run(self.service.get_provider_contract(
            client_id="client-1",
            connection_id=connection["id"],
        ))

        self.assertEqual(contract["contract_version"], "crm_provider_contract.v1")
        self.assertEqual(contract["provider"], "hubspot")
        self.assertEqual(contract["object_type"], "contacts")
        self.assertIn("outbound", contract["supported_directions"])
        self.assertIn("phone_redacted", contract["supported_fields"])
        self.assertIn("transcript_content", contract["blocked_exports"])
        self.assertTrue(contract["credential_reference"]["configured"])
        self.assertEqual(contract["credential_reference"]["vault_provider"], "external")
        self.assertTrue(contract["credential_reference"]["reference_hash_present"])
        self.assertFalse(contract["credential_reference"]["reference_id_included"])
        self.assertFalse(contract["credential_reference"]["secret_value_stored"])
        self.assertEqual(contract["public_config_keys"], ["region", "sync_mode"])
        self.assertEqual(contract["readiness"]["blockers"], ["live_sync_requires_future_phase"])
        self.assertNotIn("crm/client-1/hubspot/oauth", str(contract))
        self.assertNotIn("owner", str(contract))
        self.assertNotIn("private-region-value", str(contract))
        self.assertNotIn("tenant-config-value", str(contract))

    def test_provider_contract_is_provider_specific_and_tenant_scoped(self):
        salesforce = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Contract",
        ))
        zoho = asyncio.run(self.service.create_connection(
            client_id="client-2",
            provider="zoho",
            display_name="Zoho Contract",
        ))

        salesforce_contract = asyncio.run(self.service.get_provider_contract(
            client_id="client-1",
            connection_id=salesforce["id"],
        ))
        self.assertEqual(salesforce_contract["object_type"], "Lead")
        self.assertEqual(salesforce_contract["supported_objects"], ["Lead"])

        with self.assertRaises(ValueError):
            asyncio.run(self.service.get_provider_contract(
                client_id="client-1",
                connection_id=zoho["id"],
            ))

    def test_sync_job_is_dry_run_and_idempotent(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho CRM",
        ))
        first = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-client-1-campaign-1",
        ))
        second = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-client-1-campaign-1",
        ))
        payload = first["job"]["payload"]

        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(first["job"]["mode"], "dry_run")
        self.assertFalse(first["external_execution"])
        self.assertFalse(payload["runtime_campaign_hook"])
        self.assertFalse(payload["sync_scope"]["transcript_content"])
        self.assertFalse(payload["sync_scope"]["recording_content"])

    def test_campaign_payload_preview_is_redacted_and_tenant_scoped(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Asha Lead",
            "phone": "+1 (555) 123-4567",
            "calledAt": "2026-05-15 10:00:00",
            "duration": "60",
            "status": "Connected",
            "interested": "Yes",
            "budget": "500000",
            "callback": "Tomorrow",
            "transcription": [{"role": "user", "content": "private transcript text"}],
            "provider": "demo",
            "processed": True,
            "recording_url": "/recordings/private.wav",
        }))

        preview = asyncio.run(self.service.build_campaign_payload_preview(
            client_id="client-1",
            campaign_id="campaign-1",
        ))
        record = preview["records"][0]

        self.assertTrue(preview["dry_run"])
        self.assertFalse(preview["external_execution"])
        self.assertEqual(preview["campaign"]["result_count"], 1)
        self.assertEqual(record["phone_redacted"], "***4567")
        self.assertIsNotNone(record["phone_sha256"])
        self.assertIsNone(record["transcript_content"])
        self.assertIsNone(record["recording_url"])
        self.assertIsNone(record["recording_content"])
        self.assertTrue(record["has_transcript"])
        self.assertTrue(record["has_recording"])
        self.assertNotIn("+1 (555) 123-4567", str(preview))
        self.assertNotIn("private transcript text", str(preview))
        self.assertNotIn("/recordings/private.wav", str(preview))

    def test_campaign_payload_preview_rejects_cross_tenant_campaign(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_campaign_payload_preview(
                client_id="client-1",
                campaign_id="campaign-2",
            ))

    def test_sync_job_embeds_redacted_payload_preview_only(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Preview Lead",
            "phone": "+91 98765 43210",
            "calledAt": "2026-05-15 10:10:00",
            "duration": "45",
            "status": "Connected",
            "interested": "No",
            "transcription": [{"role": "assistant", "content": "sensitive agent line"}],
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Preview",
        ))

        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-preview",
        ))
        payload = planned["job"]["payload"]
        preview = payload["payload_preview"]

        self.assertEqual(preview["payload_version"], "crm_campaign_preview.v1")
        self.assertEqual(preview["summary"]["total_records"], 1)
        self.assertIsNone(preview["records"][0]["recording_url"])
        self.assertIsNone(preview["records"][0]["transcript_content"])
        self.assertNotIn("+91 98765 43210", str(payload))
        self.assertNotIn("sensitive agent line", str(payload))

    def test_execute_dry_run_sync_renders_provider_payload_without_sending(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Dry Run Lead",
            "phone": "+1 222 333 4444",
            "calledAt": "2026-05-15 10:20:00",
            "duration": "30",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "do not export this"}],
            "recording_url": "/recordings/do-not-export.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Dry Run",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-dry-run-execute",
        ))

        executed = asyncio.run(self.service.execute_dry_run_sync(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
        ))
        updated_job = executed["job"]
        provider_payload = executed["provider_payload"]

        self.assertEqual(updated_job["status"], "validated")
        self.assertFalse(executed["external_execution"])
        self.assertFalse(updated_job["payload"]["execution_result"]["sent_to_provider"])
        self.assertEqual(provider_payload["provider"], "salesforce")
        self.assertEqual(provider_payload["object_type"], "Lead")
        self.assertEqual(provider_payload["records"][0]["properties"]["phone_redacted"], "***4444")
        self.assertNotIn("+1 222 333 4444", str(provider_payload))
        self.assertNotIn("do not export this", str(provider_payload))
        self.assertNotIn("/recordings/do-not-export.wav", str(provider_payload))

    def test_execute_dry_run_sync_rejects_cross_tenant_job(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Dry Run",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-cross-tenant-execute",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.execute_dry_run_sync(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))

    def test_sync_preflight_validates_secret_reference_and_redaction_without_sending(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Preflight Lead",
            "phone": "+1 333 444 5555",
            "calledAt": "2026-05-15 10:25:00",
            "duration": "40",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "preflight private transcript"}],
            "recording_url": "/recordings/preflight-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Preflight",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-preflight",
        ))

        result = asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
        ))
        preflight = result["preflight"]

        self.assertEqual(result["job"]["status"], "preflight_validated")
        self.assertFalse(result["external_execution"])
        self.assertFalse(preflight["ready_for_external_sync"])
        self.assertFalse(preflight["external_execution"])
        self.assertFalse(preflight["runtime_campaign_hook"])
        self.assertIn("credential_reference", {check["name"] for check in preflight["checks"]})
        self.assertIn("payload_preview_redaction", {check["name"] for check in preflight["checks"]})
        self.assertNotIn("crm/client-1/hubspot/oauth", str(preflight))
        self.assertNotIn("+1 333 444 5555", str(result))
        self.assertNotIn("preflight private transcript", str(result))
        self.assertNotIn("/recordings/preflight-private.wav", str(result))

    def test_sync_preflight_requires_configured_secret_reference(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Preflight",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-preflight-missing-secret",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.run_sync_preflight(
                client_id="client-1",
                job_id=planned["job"]["id"],
            ))

    def test_sync_preflight_rejects_unsafe_export_scope(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Preflight",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        job = asyncio.run(self.manager.create_crm_sync_job(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            mode="dry_run",
            direction="outbound",
            idempotency_key="sync-preflight-unsafe",
            payload={
                "dry_run": True,
                "external_execution": False,
                "runtime_campaign_hook": False,
                "sync_scope": {
                    "campaign_results": True,
                    "transcript_content": True,
                    "recording_content": False,
                },
            },
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.run_sync_preflight(
                client_id="client-1",
                job_id=job["id"],
            ))

    def test_sync_preflight_rejects_cross_tenant_job(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Cross Tenant Preflight",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-cross-tenant-preflight",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.run_sync_preflight(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))

    def test_sync_outbox_queues_preflighted_job_without_dispatching(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Outbox Lead",
            "phone": "+1 444 555 6666",
            "calledAt": "2026-05-15 10:40:00",
            "duration": "42",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "outbox private transcript"}],
            "recording_url": "/recordings/outbox-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Outbox",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-outbox",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
        ))

        first = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
            idempotency_key="outbox-idempotent",
        ))
        second = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
            idempotency_key="outbox-idempotent",
        ))
        outbox_item = first["outbox_item"]
        payload = outbox_item["payload"]

        self.assertEqual(outbox_item["id"], second["outbox_item"]["id"])
        self.assertEqual(outbox_item["status"], "queued_shadow")
        self.assertEqual(outbox_item["mode"], "shadow")
        self.assertFalse(first["external_execution"])
        self.assertFalse(first["worker_dispatch_enabled"])
        self.assertFalse(payload["external_execution"])
        self.assertFalse(payload["runtime_campaign_hook"])
        self.assertFalse(payload["worker_dispatch_enabled"])
        self.assertEqual(payload["provider_payload"]["provider"], "zoho")
        self.assertEqual(payload["provider_payload"]["records"][0]["properties"]["phone_redacted"], "***6666")
        self.assertNotIn("crm/client-1/zoho/oauth", str(first))
        self.assertNotIn("+1 444 555 6666", str(first))
        self.assertNotIn("outbox private transcript", str(first))
        self.assertNotIn("/recordings/outbox-private.wav", str(first))

        listed = asyncio.run(self.service.list_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))

        self.assertEqual(len(listed), 1)
        self.assertIn("outbox_queued_shadow", {event["event_type"] for event in events})

    def test_sync_outbox_requires_preflight_validated_job(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Outbox Missing Preflight",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-outbox-no-preflight",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.queue_sync_outbox(
                client_id="client-1",
                job_id=planned["job"]["id"],
            ))

    def test_sync_outbox_rejects_cross_tenant_job(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Cross Tenant Outbox",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-outbox-cross-tenant",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.queue_sync_outbox(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.list_sync_outbox(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))

    def test_outbox_shadow_worker_completes_without_external_dispatch(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Worker Lead",
            "phone": "+1 555 666 7777",
            "calledAt": "2026-05-15 10:45:00",
            "duration": "44",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "worker private transcript"}],
            "recording_url": "/recordings/worker-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Worker",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-worker-shadow",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-worker-shadow",
        ))

        result = asyncio.run(self.service.run_outbox_shadow_worker(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            requested_by="admin@example.com",
        ))
        outbox_item = result["outbox_item"]
        shadow_result = result["shadow_result"]

        self.assertEqual(outbox_item["status"], "completed_shadow")
        self.assertEqual(outbox_item["mode"], "shadow")
        self.assertEqual(outbox_item["attempt_count"], 1)
        self.assertFalse(result["external_execution"])
        self.assertFalse(result["worker_dispatch_enabled"])
        self.assertFalse(shadow_result["sent_to_provider"])
        self.assertFalse(shadow_result["external_execution"])
        self.assertFalse(shadow_result["worker_dispatch_enabled"])
        self.assertEqual(shadow_result["records_seen"], 1)
        self.assertEqual(
            outbox_item["payload"]["shadow_worker_result"]["status"],
            "shadow_processed",
        )
        self.assertNotIn("crm/client-1/hubspot/oauth", str(result))
        self.assertNotIn("+1 555 666 7777", str(result))
        self.assertNotIn("worker private transcript", str(result))
        self.assertNotIn("/recordings/worker-private.wav", str(result))

        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        event_types = {event["event_type"] for event in events}
        self.assertIn("outbox_processing_shadow_started", event_types)
        self.assertIn("outbox_completed_shadow", event_types)

        with self.assertRaises(ValueError):
            asyncio.run(self.service.run_outbox_shadow_worker(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_outbox_shadow_worker_rejects_cross_tenant_item(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Worker Tenant Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-worker-cross-tenant",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-worker-cross-tenant",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.run_outbox_shadow_worker(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_outbox_shadow_retry_requeue_and_dead_letter_are_tenant_scoped(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Retry",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-retry-shadow",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-retry-shadow",
        ))
        outbox_id = queued["outbox_item"]["id"]

        retry = asyncio.run(self.service.schedule_outbox_shadow_retry(
            client_id="client-1",
            outbox_id=outbox_id,
            error="temporary provider timeout",
            next_retry_at="2026-05-15T22:00:00",
            requested_by="admin@example.com",
        ))
        self.assertEqual(retry["outbox_item"]["status"], "retry_scheduled_shadow")
        self.assertEqual(retry["outbox_item"]["last_error"], "temporary provider timeout")
        self.assertEqual(retry["outbox_item"]["next_retry_at"], "2026-05-15T22:00:00")
        self.assertFalse(retry["external_execution"])
        self.assertFalse(retry["worker_dispatch_enabled"])

        requeued = asyncio.run(self.service.requeue_outbox_shadow_retry(
            client_id="client-1",
            outbox_id=outbox_id,
            requested_by="admin@example.com",
        ))
        self.assertEqual(requeued["outbox_item"]["status"], "queued_shadow")
        self.assertIsNone(requeued["outbox_item"]["next_retry_at"])

        dead_lettered = asyncio.run(self.service.dead_letter_outbox_shadow_item(
            client_id="client-1",
            outbox_id=outbox_id,
            error="max retries reached",
            requested_by="admin@example.com",
        ))
        self.assertEqual(dead_lettered["outbox_item"]["status"], "dead_letter_shadow")
        self.assertEqual(dead_lettered["outbox_item"]["last_error"], "max retries reached")
        self.assertFalse(dead_lettered["external_execution"])
        self.assertFalse(dead_lettered["worker_dispatch_enabled"])

        with self.assertRaises(ValueError):
            asyncio.run(self.service.requeue_outbox_shadow_retry(
                client_id="client-2",
                outbox_id=outbox_id,
            ))
        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        event_types = {event["event_type"] for event in events}
        self.assertIn("outbox_retry_scheduled_shadow", event_types)
        self.assertIn("outbox_requeued_shadow", event_types)
        self.assertIn("outbox_dead_letter_shadow", event_types)

    def test_outbox_shadow_retry_redacts_credential_like_errors(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Retry Redaction",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-retry-redaction",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-retry-redaction",
        ))

        retry = asyncio.run(self.service.schedule_outbox_shadow_retry(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            error="sk-this-looks-like-a-secret-token-and-must-not-be-stored",
        ))

        self.assertEqual(
            retry["outbox_item"]["last_error"],
            "redacted_credential_like_error",
        )
        self.assertNotIn("sk-this-looks-like", str(retry))

    def test_outbox_summary_is_tenant_scoped_and_excludes_payload_data(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Summary Lead",
            "phone": "+1 666 777 8888",
            "calledAt": "2026-05-15 10:55:00",
            "duration": "50",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "summary private transcript"}],
            "recording_url": "/recordings/summary-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Summary",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-summary",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-summary-queued",
        ))
        completed = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-summary-completed",
        ))
        asyncio.run(self.service.run_outbox_shadow_worker(
            client_id="client-1",
            outbox_id=completed["outbox_item"]["id"],
        ))
        retry = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-summary-retry",
        ))
        asyncio.run(self.service.schedule_outbox_shadow_retry(
            client_id="client-1",
            outbox_id=retry["outbox_item"]["id"],
            error="summary retry timeout",
            next_retry_at="2026-05-15T23:00:00",
        ))
        dead_letter = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-summary-dead-letter",
        ))
        asyncio.run(self.service.dead_letter_outbox_shadow_item(
            client_id="client-1",
            outbox_id=dead_letter["outbox_item"]["id"],
            error="summary dead letter reason",
        ))

        summary = asyncio.run(self.service.get_sync_outbox_summary(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))

        self.assertEqual(summary["summary_version"], "crm_outbox_observability.v1")
        self.assertEqual(summary["total_items"], 4)
        self.assertEqual(summary["queued_shadow_count"], 1)
        self.assertEqual(summary["completed_shadow_count"], 1)
        self.assertEqual(summary["retry_scheduled_shadow_count"], 1)
        self.assertEqual(summary["dead_letter_shadow_count"], 1)
        self.assertEqual(summary["max_attempt_count"], 1)
        self.assertEqual(summary["oldest_retry_at"], "2026-05-15T23:00:00")
        self.assertFalse(summary["payloads_included"])
        self.assertFalse(summary["provider_records_included"])
        self.assertFalse(summary["last_error_included"])
        self.assertFalse(summary["external_execution"])
        self.assertFalse(summary["worker_dispatch_enabled"])
        self.assertNotIn("crm/client-1/hubspot/oauth", str(summary))
        self.assertNotIn("+1 666 777 8888", str(summary))
        self.assertNotIn("summary private transcript", str(summary))
        self.assertNotIn("/recordings/summary-private.wav", str(summary))
        self.assertNotIn("summary retry timeout", str(summary))
        self.assertNotIn("summary dead letter reason", str(summary))

    def test_outbox_summary_rejects_cross_tenant_filters(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Summary Tenant Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-summary-cross-tenant",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-summary-cross-tenant",
        ))

        tenant_summary = asyncio.run(self.service.get_sync_outbox_summary(
            client_id="client-2",
        ))
        self.assertEqual(tenant_summary["total_items"], 0)

        with self.assertRaises(ValueError):
            asyncio.run(self.service.get_sync_outbox_summary(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.get_sync_outbox_summary(
                client_id="client-2",
                connection_id=connection["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.get_sync_outbox_summary(
                client_id="client-2",
                campaign_id="campaign-1",
            ))

    def test_delivery_plan_renders_metadata_without_body_headers_or_network(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Delivery Lead",
            "phone": "+1 777 888 9999",
            "calledAt": "2026-05-15 11:05:00",
            "duration": "52",
            "status": "Connected",
            "interested": "Yes",
            "budget": "750000",
            "transcription": [{"role": "user", "content": "delivery private transcript"}],
            "recording_url": "/recordings/delivery-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Delivery Plan",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-delivery-plan",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-delivery-plan",
        ))

        plan = asyncio.run(self.service.build_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))

        self.assertEqual(plan["plan_version"], "crm_delivery_plan_shadow.v1")
        self.assertEqual(plan["provider"], "salesforce")
        self.assertEqual(plan["object_type"], "Lead")
        self.assertEqual(plan["operation"], "upsert_shadow")
        self.assertEqual(plan["record_count"], 1)
        self.assertIn("lead_name", plan["property_keys"])
        self.assertIn("phone_sha256", plan["property_keys"])
        self.assertTrue(plan["credential_reference"]["configured"])
        self.assertTrue(plan["credential_reference"]["reference_hash_present"])
        self.assertFalse(plan["credential_reference"]["reference_id_included"])
        self.assertFalse(plan["credential_reference"]["secret_value_included"])
        self.assertFalse(plan["request_envelope"]["url_included"])
        self.assertFalse(plan["request_envelope"]["headers_included"])
        self.assertFalse(plan["request_envelope"]["auth_header_included"])
        self.assertFalse(plan["request_envelope"]["body_included"])
        self.assertFalse(plan["request_envelope"]["provider_payload_included"])
        self.assertFalse(plan["safety"]["network_call_performed"])
        self.assertFalse(plan["safety"]["sent_to_provider"])
        self.assertFalse(plan["safety"]["external_execution"])
        self.assertFalse(plan["safety"]["runtime_campaign_hook"])
        self.assertFalse(plan["readiness"]["live_sync_ready"])
        self.assertIn("network_dispatch_disabled", plan["readiness"]["blockers"])
        self.assertNotIn("crm/client-1/salesforce/oauth", str(plan))
        self.assertNotIn("+1 777 888 9999", str(plan))
        self.assertNotIn("***9999", str(plan))
        self.assertNotIn("delivery private transcript", str(plan))
        self.assertNotIn("/recordings/delivery-private.wav", str(plan))
        self.assertNotIn("Delivery Lead", str(plan))
        self.assertNotIn("750000", str(plan))

    def test_delivery_plan_rejects_cross_tenant_outbox_item(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Delivery Tenant Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-delivery-cross-tenant",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-delivery-cross-tenant",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_delivery_plan(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_delivery_approval_stores_plan_hash_without_dispatch_or_payload_values(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Approval Lead",
            "phone": "+1 888 999 0000",
            "calledAt": "2026-05-15 11:15:00",
            "duration": "55",
            "status": "Connected",
            "interested": "Yes",
            "budget": "880000",
            "transcription": [{"role": "user", "content": "approval private transcript"}],
            "recording_url": "/recordings/approval-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Delivery Approval",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-delivery-approval",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-delivery-approval",
        ))

        first = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            approved_by="approver@example.com",
            requested_by="admin@example.com",
            idempotency_key="approval-idempotent",
        ))
        second = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            approved_by="approver@example.com",
            requested_by="admin@example.com",
            idempotency_key="approval-idempotent",
        ))
        approval = first["approval"]
        summary = approval["plan_summary"]

        self.assertEqual(approval["id"], second["approval"]["id"])
        self.assertEqual(approval["status"], "approved_shadow")
        self.assertEqual(approval["approval_mode"], "shadow")
        self.assertEqual(approval["plan_hash"], first["plan_hash"])
        self.assertEqual(len(approval["plan_hash"]), 64)
        self.assertFalse(first["external_execution"])
        self.assertFalse(first["worker_dispatch_enabled"])
        self.assertFalse(first["live_sync_enabled"])
        self.assertEqual(summary["plan_version"], "crm_delivery_plan_shadow.v1")
        self.assertEqual(summary["provider"], "hubspot")
        self.assertEqual(summary["object_type"], "contacts")
        self.assertEqual(summary["record_count"], 1)
        self.assertFalse(summary["safety"]["external_execution"])
        self.assertFalse(summary["safety"]["worker_dispatch_enabled"])
        self.assertFalse(summary["safety"]["network_call_performed"])
        self.assertFalse(summary["safety"]["request_body_included"])
        self.assertFalse(summary["safety"]["provider_payload_included"])
        self.assertFalse(summary["readiness"]["live_sync_ready"])
        self.assertIn("network_dispatch_disabled", summary["readiness"]["blockers"])
        self.assertNotIn("crm/client-1/hubspot/oauth", str(first))
        self.assertNotIn("+1 888 999 0000", str(first))
        self.assertNotIn("***0000", str(first))
        self.assertNotIn("Approval Lead", str(first))
        self.assertNotIn("approval private transcript", str(first))
        self.assertNotIn("/recordings/approval-private.wav", str(first))
        self.assertNotIn("880000", str(first))

        approvals = asyncio.run(self.service.list_delivery_approvals(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))
        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        self.assertEqual(len(approvals), 1)
        self.assertIn("delivery_approval_shadow_created", {event["event_type"] for event in events})

    def test_delivery_approval_rejects_cross_tenant_and_completed_items(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Approval Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-approval-guard",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-approval-guard",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.approve_outbox_delivery_plan(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.list_delivery_approvals(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

        asyncio.run(self.service.run_outbox_shadow_worker(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.approve_outbox_delivery_plan(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_delivery_approval_revocation_is_idempotent_and_no_dispatch(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Revoke Lead",
            "phone": "+1 999 000 1111",
            "calledAt": "2026-05-15 11:25:00",
            "duration": "57",
            "status": "Connected",
            "interested": "Yes",
            "budget": "990000",
            "transcription": [{"role": "user", "content": "revocation private transcript"}],
            "recording_url": "/recordings/revocation-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Revocation",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-approval-revoke",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-approval-revoke",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            approved_by="approver@example.com",
            idempotency_key="approval-revoke",
        ))
        approval_id = approved["approval"]["id"]

        first = asyncio.run(self.service.revoke_delivery_approval(
            client_id="client-1",
            approval_id=approval_id,
            revoked_by="approver@example.com",
            reason="operator changed rollout plan",
        ))
        second = asyncio.run(self.service.revoke_delivery_approval(
            client_id="client-1",
            approval_id=approval_id,
            revoked_by="approver@example.com",
            reason="operator changed rollout plan",
        ))
        approval = first["approval"]

        self.assertEqual(approval["id"], second["approval"]["id"])
        self.assertEqual(approval["status"], "revoked_shadow")
        self.assertIsNotNone(approval["revoked_at"])
        self.assertFalse(first["external_execution"])
        self.assertFalse(first["worker_dispatch_enabled"])
        self.assertFalse(first["live_sync_enabled"])
        self.assertEqual(approval["plan_summary"]["provider"], "zoho")
        self.assertFalse(approval["plan_summary"]["safety"]["external_execution"])
        self.assertFalse(approval["plan_summary"]["safety"]["worker_dispatch_enabled"])
        self.assertNotIn("crm/client-1/zoho/oauth", str(first))
        self.assertNotIn("+1 999 000 1111", str(first))
        self.assertNotIn("***1111", str(first))
        self.assertNotIn("Revoke Lead", str(first))
        self.assertNotIn("revocation private transcript", str(first))
        self.assertNotIn("/recordings/revocation-private.wav", str(first))
        self.assertNotIn("990000", str(first))

        revoked = asyncio.run(self.service.list_delivery_approvals(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            status="revoked_shadow",
        ))
        active = asyncio.run(self.service.list_delivery_approvals(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            status="approved_shadow",
        ))
        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))

        self.assertEqual(len(revoked), 1)
        self.assertEqual(active, [])
        self.assertIn("delivery_approval_shadow_revoked", {event["event_type"] for event in events})

    def test_delivery_approval_revocation_rejects_cross_tenant(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Revocation Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-approval-revoke-guard",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-approval-revoke-guard",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-revoke-guard",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.revoke_delivery_approval(
                client_id="client-2",
                approval_id=approved["approval"]["id"],
            ))

    def test_live_readiness_reports_active_approval_without_dispatch_or_payload_values(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Live Ready Lead",
            "phone": "+1 777 555 1212",
            "calledAt": "2026-05-15 11:35:00",
            "duration": "59",
            "status": "Connected",
            "interested": "Yes",
            "budget": "770000",
            "transcription": [{"role": "user", "content": "live readiness private transcript"}],
            "recording_url": "/recordings/live-readiness-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Live Readiness",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/live-readiness/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-live-readiness",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-live-readiness",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            approved_by="approver@example.com",
            requested_by="admin@example.com",
            idempotency_key="approval-live-readiness",
        ))

        readiness = asyncio.run(self.service.get_outbox_live_readiness(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))

        self.assertEqual(readiness["readiness_version"], "crm_live_readiness_shadow.v1")
        self.assertEqual(readiness["current_plan_hash"], approved["plan_hash"])
        self.assertTrue(readiness["approval"]["active_approval_found"])
        self.assertEqual(readiness["approval"]["approval_id"], approved["approval"]["id"])
        self.assertEqual(readiness["approval"]["approval_status"], "approved_shadow")
        self.assertTrue(readiness["approval"]["plan_hash_matches"])
        self.assertEqual(readiness["approval"]["revoked_matching_approvals"], 0)
        self.assertEqual(readiness["approval"]["stale_active_approvals"], 0)
        self.assertTrue(readiness["future_live_prerequisites_met"])
        self.assertFalse(readiness["ready_for_live_dispatch"])
        self.assertFalse(readiness["readiness"]["live_sync_ready"])
        self.assertIn("live_sync_feature_disabled", readiness["readiness"]["blockers"])
        self.assertIn("network_dispatch_disabled", readiness["readiness"]["blockers"])
        self.assertFalse(readiness["safety"]["network_call_performed"])
        self.assertFalse(readiness["safety"]["sent_to_provider"])
        self.assertFalse(readiness["safety"]["external_execution"])
        self.assertFalse(readiness["safety"]["worker_dispatch_enabled"])
        self.assertFalse(readiness["safety"]["runtime_campaign_hook"])
        self.assertFalse(readiness["safety"]["provider_payload_included"])
        self.assertFalse(readiness["safety"]["request_body_included"])
        self.assertFalse(readiness["safety"]["credential_value_included"])
        self.assertNotIn("crm/client-1/salesforce/live-readiness/oauth", str(readiness))
        self.assertNotIn("+1 777 555 1212", str(readiness))
        self.assertNotIn("***1212", str(readiness))
        self.assertNotIn("Live Ready Lead", str(readiness))
        self.assertNotIn("live readiness private transcript", str(readiness))
        self.assertNotIn("/recordings/live-readiness-private.wav", str(readiness))
        self.assertNotIn("770000", str(readiness))

    def test_live_readiness_blocks_revoked_or_cross_tenant_approval(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Live Readiness Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/live-readiness/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-live-readiness-guard",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-live-readiness-guard",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-live-readiness-guard",
        ))
        asyncio.run(self.service.revoke_delivery_approval(
            client_id="client-1",
            approval_id=approved["approval"]["id"],
            reason="operator disabled live readiness",
        ))

        readiness = asyncio.run(self.service.get_outbox_live_readiness(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))

        self.assertFalse(readiness["approval"]["active_approval_found"])
        self.assertFalse(readiness["approval"]["plan_hash_matches"])
        self.assertEqual(readiness["approval"]["revoked_matching_approvals"], 1)
        self.assertEqual(readiness["approval"]["stale_active_approvals"], 0)
        self.assertFalse(readiness["future_live_prerequisites_met"])
        self.assertFalse(readiness["ready_for_live_dispatch"])
        self.assertIn("delivery_approval_revoked", readiness["readiness"]["blockers"])
        self.assertNotIn("delivery_approval_missing", readiness["readiness"]["blockers"])
        self.assertFalse(readiness["safety"]["network_call_performed"])
        self.assertFalse(readiness["safety"]["sent_to_provider"])

        with self.assertRaises(ValueError):
            asyncio.run(self.service.get_outbox_live_readiness(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_provider_sandbox_reports_adapter_without_dispatch_or_payload_values(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Provider Sandbox Lead",
            "phone": "+1 444 333 9999",
            "calledAt": "2026-05-15 11:45:00",
            "duration": "61",
            "status": "Connected",
            "interested": "Yes",
            "budget": "440000",
            "transcription": [{"role": "user", "content": "provider sandbox private transcript"}],
            "recording_url": "/recordings/provider-sandbox-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="zoho",
            display_name="Zoho Provider Sandbox",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/zoho/provider-sandbox/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-provider-sandbox",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-provider-sandbox",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-provider-sandbox",
        ))

        sandbox = asyncio.run(self.service.build_outbox_provider_sandbox(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))

        self.assertEqual(sandbox["sandbox_version"], "crm_provider_sandbox_shadow.v1")
        self.assertEqual(sandbox["provider"], "zoho")
        self.assertEqual(sandbox["object_type"], "Leads")
        self.assertEqual(sandbox["record_count"], 1)
        self.assertEqual(sandbox["current_plan_hash"], approved["plan_hash"])
        self.assertEqual(sandbox["approval"]["approval_id"], approved["approval"]["id"])
        self.assertEqual(sandbox["approval"]["approval_status"], "approved_shadow")
        self.assertTrue(sandbox["approval"]["plan_hash_matches"])
        self.assertEqual(sandbox["adapter"]["adapter_name"], "zoho.crm_provider_sandbox")
        self.assertEqual(sandbox["adapter"]["idempotency_field"], "external_id")
        self.assertFalse(sandbox["adapter"]["live_adapter_loaded"])
        self.assertFalse(sandbox["adapter"]["network_client_loaded"])
        self.assertTrue(sandbox["execution"]["sandbox_ready"])
        self.assertFalse(sandbox["execution"]["live_dispatch_ready"])
        self.assertFalse(sandbox["execution"]["dispatch_allowed"])
        self.assertFalse(sandbox["execution"]["network_call_performed"])
        self.assertFalse(sandbox["execution"]["sent_to_provider"])
        self.assertFalse(sandbox["execution"]["external_execution"])
        self.assertFalse(sandbox["execution"]["worker_dispatch_enabled"])
        self.assertFalse(sandbox["execution"]["runtime_campaign_hook"])
        self.assertTrue(sandbox["readiness"]["sandbox_ready"])
        self.assertFalse(sandbox["readiness"]["live_sync_ready"])
        self.assertIn("live_sync_feature_disabled", sandbox["readiness"]["blockers"])
        self.assertIn("network_dispatch_disabled", sandbox["readiness"]["blockers"])
        self.assertFalse(sandbox["request_envelope"]["body_included"])
        self.assertFalse(sandbox["request_envelope"]["headers_included"])
        self.assertFalse(sandbox["request_envelope"]["provider_payload_included"])
        self.assertFalse(sandbox["request_envelope"]["credential_reference_included"])
        self.assertFalse(sandbox["safety"]["provider_payload_included"])
        self.assertFalse(sandbox["safety"]["request_body_included"])
        self.assertFalse(sandbox["safety"]["credential_value_included"])
        self.assertFalse(sandbox["safety"]["secret_value_included"])
        self.assertNotIn("crm/client-1/zoho/provider-sandbox/oauth", str(sandbox))
        self.assertNotIn("+1 444 333 9999", str(sandbox))
        self.assertNotIn("***9999", str(sandbox))
        self.assertNotIn("Provider Sandbox Lead", str(sandbox))
        self.assertNotIn("provider sandbox private transcript", str(sandbox))
        self.assertNotIn("/recordings/provider-sandbox-private.wav", str(sandbox))
        self.assertNotIn("440000", str(sandbox))

    def test_provider_sandbox_requires_active_approval_and_tenant_scope(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="salesforce",
            display_name="Salesforce Provider Sandbox Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/salesforce/provider-sandbox/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-provider-sandbox-guard",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-provider-sandbox-guard",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_provider_sandbox(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))

        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-provider-sandbox-guard",
        ))
        asyncio.run(self.service.revoke_delivery_approval(
            client_id="client-1",
            approval_id=approved["approval"]["id"],
            reason="sandbox guard revoke",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_provider_sandbox(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_provider_sandbox(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_dispatch_canary_manifest_is_non_sending_and_payload_free(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Canary Dispatch Lead",
            "phone": "+1 222 111 3333",
            "calledAt": "2026-05-15 11:55:00",
            "duration": "63",
            "status": "Connected",
            "interested": "Yes",
            "budget": "220000",
            "transcription": [{"role": "user", "content": "dispatch canary private transcript"}],
            "recording_url": "/recordings/dispatch-canary-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Dispatch Canary",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/hubspot/dispatch-canary/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-dispatch-canary",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-dispatch-canary",
        ))
        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-dispatch-canary",
        ))

        canary = asyncio.run(self.service.build_outbox_dispatch_canary(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
        ))

        self.assertEqual(canary["canary_version"], "crm_dispatch_canary_shadow.v1")
        self.assertEqual(canary["provider"], "hubspot")
        self.assertEqual(canary["object_type"], "contacts")
        self.assertEqual(canary["current_plan_hash"], approved["plan_hash"])
        self.assertEqual(canary["approval"]["approval_id"], approved["approval"]["id"])
        self.assertEqual(canary["approval"]["approval_status"], "approved_shadow")
        self.assertTrue(canary["approval"]["plan_hash_matches"])
        self.assertEqual(canary["adapter"]["adapter_name"], "hubspot.crm_provider_sandbox")
        self.assertFalse(canary["adapter"]["live_adapter_loaded"])
        self.assertFalse(canary["adapter"]["network_client_loaded"])
        self.assertEqual(canary["canary"]["max_records"], 1)
        self.assertEqual(canary["canary"]["candidate_record_count"], 1)
        self.assertGreaterEqual(canary["canary"]["available_record_count"], 1)
        self.assertFalse(canary["canary"]["record_identity_included"])
        self.assertFalse(canary["canary"]["record_body_included"])
        self.assertFalse(canary["canary"]["idempotency_key_included"])
        self.assertFalse(canary["canary"]["payload_hash_included"])
        self.assertTrue(canary["execution"]["canary_manifest_ready"])
        self.assertFalse(canary["execution"]["canary_dispatch_ready"])
        self.assertFalse(canary["execution"]["dispatch_allowed"])
        self.assertFalse(canary["execution"]["network_call_performed"])
        self.assertFalse(canary["execution"]["sent_to_provider"])
        self.assertFalse(canary["execution"]["external_execution"])
        self.assertFalse(canary["execution"]["worker_dispatch_enabled"])
        self.assertFalse(canary["execution"]["runtime_campaign_hook"])
        self.assertTrue(canary["readiness"]["canary_manifest_ready"])
        self.assertFalse(canary["readiness"]["live_sync_ready"])
        self.assertIn("live_dispatch_feature_disabled", canary["readiness"]["blockers"])
        self.assertIn("network_dispatch_disabled", canary["readiness"]["blockers"])
        self.assertFalse(canary["safety"]["provider_payload_included"])
        self.assertFalse(canary["safety"]["request_body_included"])
        self.assertFalse(canary["safety"]["headers_included"])
        self.assertFalse(canary["safety"]["credential_reference_included"])
        self.assertFalse(canary["safety"]["credential_value_included"])
        self.assertFalse(canary["safety"]["secret_value_included"])
        self.assertNotIn("crm/client-1/hubspot/dispatch-canary/oauth", str(canary))
        self.assertNotIn("+1 222 111 3333", str(canary))
        self.assertNotIn("***3333", str(canary))
        self.assertNotIn("Canary Dispatch Lead", str(canary))
        self.assertNotIn("dispatch canary private transcript", str(canary))
        self.assertNotIn("/recordings/dispatch-canary-private.wav", str(canary))
        self.assertNotIn("220000", str(canary))

    def test_dispatch_canary_requires_sandbox_readiness_and_tenant_scope(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="custom_webhook",
            display_name="Webhook Dispatch Canary Guard",
        ))
        asyncio.run(self.service.configure_secret_reference(
            client_id="client-1",
            connection_id=connection["id"],
            vault_provider="external",
            reference_id="crm/client-1/webhook/dispatch-canary/oauth",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-dispatch-canary-guard",
        ))
        asyncio.run(self.service.run_sync_preflight(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))
        queued = asyncio.run(self.service.queue_sync_outbox(
            client_id="client-1",
            job_id=planned["job"]["id"],
            idempotency_key="outbox-dispatch-canary-guard",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_dispatch_canary(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))

        approved = asyncio.run(self.service.approve_outbox_delivery_plan(
            client_id="client-1",
            outbox_id=queued["outbox_item"]["id"],
            idempotency_key="approval-dispatch-canary-guard",
        ))
        asyncio.run(self.service.revoke_delivery_approval(
            client_id="client-1",
            approval_id=approved["approval"]["id"],
            reason="dispatch canary guard revoke",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_dispatch_canary(
                client_id="client-1",
                outbox_id=queued["outbox_item"]["id"],
            ))
        with self.assertRaises(ValueError):
            asyncio.run(self.service.build_outbox_dispatch_canary(
                client_id="client-2",
                outbox_id=queued["outbox_item"]["id"],
            ))

    def test_sync_event_readback_is_tenant_scoped_and_redacted(self):
        asyncio.run(self.manager.append_call_result("campaign-1", {
            "name": "Audit Lead",
            "phone": "+44 7700 900123",
            "calledAt": "2026-05-15 10:30:00",
            "duration": "35",
            "status": "Connected",
            "interested": "Yes",
            "transcription": [{"role": "user", "content": "audit private transcript"}],
            "recording_url": "/recordings/audit-private.wav",
            "provider": "demo",
            "processed": True,
        }))
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="hubspot",
            display_name="HubSpot Audit",
        ))
        planned = asyncio.run(self.service.plan_campaign_sync(
            client_id="client-1",
            connection_id=connection["id"],
            campaign_id="campaign-1",
            idempotency_key="sync-audit-readback",
        ))
        asyncio.run(self.service.execute_dry_run_sync(
            client_id="client-1",
            job_id=planned["job"]["id"],
            requested_by="admin@example.com",
        ))

        events = asyncio.run(self.service.list_sync_events(
            client_id="client-1",
            job_id=planned["job"]["id"],
        ))

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "dry_run_validated")
        self.assertNotIn("+44 7700 900123", str(events))
        self.assertNotIn("audit private transcript", str(events))
        self.assertNotIn("/recordings/audit-private.wav", str(events))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.list_sync_events(
                client_id="client-2",
                job_id=planned["job"]["id"],
            ))

    def test_sync_job_rejects_cross_tenant_campaign(self):
        connection = asyncio.run(self.service.create_connection(
            client_id="client-1",
            provider="custom_webhook",
            display_name="Tenant One Webhook",
        ))

        with self.assertRaises(ValueError):
            asyncio.run(self.service.plan_campaign_sync(
                client_id="client-1",
                connection_id=connection["id"],
                campaign_id="campaign-2",
            ))

    def test_main_crm_surface_is_feature_flagged(self):
        source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn('feature_flags.is_enabled("crm.sync_enabled")', source)
        self.assertIn('feature_flags.is_enabled("crm.sync_preflight")', source)
        self.assertIn('feature_flags.is_enabled("crm.sync_outbox")', source)
        self.assertIn('feature_flags.is_enabled("crm.sync_worker_shadow")', source)
        self.assertIn('feature_flags.is_enabled("crm.sync_worker_retries")', source)
        self.assertIn('feature_flags.is_enabled("crm.sync_observability")', source)
        self.assertIn('feature_flags.is_enabled("crm.provider_contracts")', source)
        self.assertIn('feature_flags.is_enabled("crm.delivery_plan_shadow")', source)
        self.assertIn('feature_flags.is_enabled("crm.delivery_approval_shadow")', source)
        self.assertIn('feature_flags.is_enabled("crm.delivery_approval_revoke")', source)
        self.assertIn('feature_flags.is_enabled("crm.live_readiness_shadow")', source)
        self.assertIn('feature_flags.is_enabled("crm.provider_sandbox_shadow")', source)
        self.assertIn('feature_flags.is_enabled("crm.dispatch_canary_shadow")', source)
        self.assertIn("/api/crm/connections", source)
        self.assertIn("/api/crm/connections/{connection_id}/secret-reference", source)
        self.assertIn("/api/crm/connections/{connection_id}/provider-contract", source)
        self.assertIn("/api/crm/sync-jobs", source)
        self.assertIn("/api/crm/campaigns/{campaign_id}/payload-preview", source)
        self.assertIn("/api/crm/sync-jobs/{job_id}/dry-run", source)
        self.assertIn("/api/crm/sync-jobs/{job_id}/preflight", source)
        self.assertIn("/api/crm/sync-jobs/{job_id}/outbox", source)
        self.assertIn("/api/crm/outbox", source)
        self.assertIn("/api/crm/outbox/summary", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/delivery-plan", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/delivery-approval", source)
        self.assertIn("/api/crm/delivery-approvals", source)
        self.assertIn("/api/crm/delivery-approvals/{approval_id}/revoke-shadow", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/live-readiness", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/provider-sandbox", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/dispatch-canary", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/shadow-run", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/retry-shadow", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/requeue-shadow", source)
        self.assertIn("/api/crm/outbox/{outbox_id}/dead-letter-shadow", source)
        self.assertIn("/api/crm/sync-jobs/{job_id}/events", source)
        self.assertIn("runtime_sync", source)


if __name__ == "__main__":
    unittest.main()
