import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "Backend"
FRONTEND_ROOT = REPO_ROOT / "frontend-next"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PhaseOneContractsTest(unittest.TestCase):
    def test_main_has_audit_first_tenant_auth_hooks(self):
        source = _read(BACKEND_ROOT / "main.py")

        self.assertIn("tenant_auth_audit_middleware", source)
        self.assertIn("build_http_tenant_context", source)
        self.assertIn("should_reject_http_request", source)
        self.assertIn("_audit_ws_connection", source)
        self.assertIn('"/api/voice-demo"', source)
        self.assertIn('"/api/voice-live"', source)
        self.assertIn('"/ws/dashboard/{client_id}"', source)
        self.assertIn("_normalize_dashboard_ws_client_id", source)
        self.assertIn('websocket.query_params.get("clientId")', source)
        self.assertIn("_require_global_monitor_admin", source)
        self.assertIn('"/api/provider-metrics"', source)
        self.assertIn('feature_flags.is_enabled("ws.scoped_events")', source)
        self.assertIn('feature_flags.is_enabled("auth.enforce_backend")', source)
        self.assertIn("await websocket.close(code=1008)", source)
        self.assertIn('"/telephony/stream/{call_id}"', source)

    def test_websocket_scope_shadow_is_flagged_and_payload_neutral(self):
        source = _read(BACKEND_ROOT / "ws_hub.py")

        self.assertIn('feature_flags.is_enabled("ws.scoped_events_shadow")', source)
        self.assertIn("build_ws_event_scope_shadow_manifest", source)
        self.assertIn("ws_scoped_event_shadow event_type=%s mode=%s", source)
        self.assertIn('self._shadow_event_scope(broadcast_mode="client"', source)
        self.assertIn('self._shadow_event_scope(broadcast_mode="all"', source)
        self.assertIn('feature_flags.is_enabled("ws.scoped_events")', source)
        self.assertIn('snapshot = {"global": snapshot.get("global", set())}', source)
        self.assertIn("payload = json.dumps(message, default=str)", source)
        self.assertNotIn('"ws_event_scope_shadow"', source)

    def test_normal_client_telephony_navigation_is_removed(self):
        layout = _read(FRONTEND_ROOT / "src" / "components" / "DashboardLayout.js")
        numbers_page = _read(FRONTEND_ROOT / "src" / "app" / "numbers" / "page.js")

        self.assertIn("{ label: 'Telephony', path: '/numbers'", layout)
        self.assertNotIn("My Phone Numbers", layout)
        self.assertIn("user?.role !== 'admin'", numbers_page)
        self.assertIn("Phone provisioning is available only to platform admins.", numbers_page)

    def test_monitor_admin_auth_proof_is_rollout_gated(self):
        monitor_page = _read(FRONTEND_ROOT / "src" / "app" / "monitor" / "page.js")

        self.assertIn("NEXT_PUBLIC_MONITOR_AUTH_PROOF_ENABLED", monitor_page)
        self.assertIn("firebaseUser.getIdToken()", monitor_page)
        self.assertIn("Authorization: `Bearer ${token}`", monitor_page)
        self.assertIn("url.searchParams.set('access_token', token)", monitor_page)
        self.assertIn("event.code === 1008", monitor_page)


if __name__ == "__main__":
    unittest.main()
