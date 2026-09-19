import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend-next"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class CRMFrontendReadinessUITest(unittest.TestCase):
    def test_crm_readiness_navigation_is_admin_and_feature_flagged(self):
        layout = _read(FRONTEND_ROOT / "src" / "components" / "DashboardLayout.js")
        page = _read(FRONTEND_ROOT / "src" / "app" / "crm-readiness" / "page.js")

        self.assertIn("NEXT_PUBLIC_CRM_READINESS_UI_ENABLED", layout)
        self.assertIn("CRM_READINESS_UI_ENABLED", layout)
        self.assertIn("CRM Readiness", layout)
        self.assertIn("path: '/crm-readiness'", layout)
        self.assertIn("user?.role !== 'admin'", page)
        self.assertIn("CRM rollout checks are available only to platform admins.", page)
        self.assertIn("CRM readiness UI disabled", page)

    def test_crm_readiness_page_is_read_only(self):
        page = _read(FRONTEND_ROOT / "src" / "app" / "crm-readiness" / "page.js")

        self.assertIn("method: 'GET'", page)
        self.assertIn("/live-readiness", page)
        self.assertIn("/provider-sandbox", page)
        self.assertIn("/dispatch-canary", page)
        self.assertNotIn("method: 'POST'", page)
        self.assertNotIn("/delivery-approval", page)
        self.assertNotIn("/revoke-shadow", page)
        self.assertNotIn("/shadow-run", page)
        self.assertNotIn("/retry-shadow", page)
        self.assertNotIn("/dead-letter-shadow", page)
        self.assertNotIn("Approve", page)
        self.assertNotIn("Revoke", page)
        self.assertNotIn("Send", page)

    def test_crm_readiness_page_surfaces_safety_flags_without_payloads(self):
        page = _read(FRONTEND_ROOT / "src" / "app" / "crm-readiness" / "page.js")

        self.assertIn("network_call_performed", page)
        self.assertIn("sent_to_provider", page)
        self.assertIn("provider_payload_included", page)
        self.assertIn("request_body_included", page)
        self.assertIn("credential_value_included", page)
        self.assertIn("No external dispatch", page)
        self.assertNotIn("provider_payload.records", page)
        self.assertNotIn("recording_url", page)
        self.assertNotIn("transcript_content", page)


if __name__ == "__main__":
    unittest.main()
