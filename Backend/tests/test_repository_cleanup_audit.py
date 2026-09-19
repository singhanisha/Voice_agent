import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from platform_migration import repository_cleanup


class RepositoryCleanupAuditTest(unittest.TestCase):
    def test_cleanup_candidates_are_audit_only(self):
        repository_cleanup.assert_audit_only()
        self.assertTrue(repository_cleanup.DEFAULT_CANDIDATES)
        self.assertTrue(
            all(
                candidate.recommended_action == repository_cleanup.SAFE_ACTION
                for candidate in repository_cleanup.DEFAULT_CANDIDATES
            )
        )

    def test_duplicate_runtime_candidates_are_tracked(self):
        paths = {candidate.path for candidate in repository_cleanup.DEFAULT_CANDIDATES}

        self.assertIn("Backend/main_working.py", paths)
        self.assertIn("Backend/main_pipeline.py", paths)
        self.assertIn("Backend/flows/runtime_working.py", paths)

    def test_analysis_reports_existing_and_missing_candidates(self):
        inspected = repository_cleanup.analyze_cleanup_candidates(REPO_ROOT)
        by_path = {item["path"]: item for item in inspected}

        self.assertTrue(by_path["Backend/main_working.py"]["exists"])
        self.assertEqual(by_path["Backend/main_working.py"]["path_type"], "file")
        self.assertTrue(by_path["Backend/recordings"]["exists"])
        self.assertEqual(by_path["Backend/recordings"]["path_type"], "directory")

    def test_risk_summary_keeps_high_risk_items_visible(self):
        summary = repository_cleanup.summarize_by_risk()

        self.assertGreaterEqual(summary.get("high", 0), 1)
        self.assertGreaterEqual(summary.get("medium", 0), 1)
        self.assertGreaterEqual(summary.get("low", 0), 1)

    def test_trace_candidate_references_ignores_cleanup_self_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Backend" / "platform_migration").mkdir(parents=True)
            (root / "Backend" / "main_working.py").write_text("# legacy\n", encoding="utf-8")
            (root / "Backend" / "platform_migration" / "repository_cleanup.py").write_text(
                '"Backend/main_working.py"\n',
                encoding="utf-8",
            )
            (root / "operator_runbook.md").write_text(
                "Start the old server with Backend/main_working.py only for diagnostics.\n",
                encoding="utf-8",
            )

            candidate = repository_cleanup.CleanupCandidate(
                path="Backend/main_working.py",
                category="duplicate_runtime_entrypoint",
                risk="high",
                reason="test candidate",
                required_validation=("trace references",),
            )

            traces = repository_cleanup.trace_candidate_references(
                root,
                candidates=(candidate,),
            )

        self.assertEqual(len(traces["Backend/main_working.py"]), 1)
        self.assertEqual(
            traces["Backend/main_working.py"][0]["file"],
            "operator_runbook.md",
        )

    def test_quarantine_readiness_never_allows_quarantine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Backend").mkdir()
            (root / "Backend" / "tmp_explore.py").write_text("# scratch\n", encoding="utf-8")

            candidate = repository_cleanup.CleanupCandidate(
                path="Backend/tmp_explore.py",
                category="scratch_file",
                risk="low",
                reason="test candidate",
                required_validation=("trace references",),
            )

            readiness = repository_cleanup.quarantine_readiness(
                root,
                candidates=(candidate,),
            )

        self.assertFalse(readiness[0]["quarantine_allowed"])
        self.assertEqual(readiness[0]["quarantine_status"], "ready_for_owner_review")

    def test_quarantine_readiness_blocks_candidates_with_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Backend").mkdir()
            (root / "Backend" / "verify_pipeline.py").write_text("# verifier\n", encoding="utf-8")
            (root / "README.md").write_text(
                "Run python Backend/verify_pipeline.py before demos.\n",
                encoding="utf-8",
            )

            candidate = repository_cleanup.CleanupCandidate(
                path="Backend/verify_pipeline.py",
                category="manual_verification_script",
                risk="medium",
                reason="test candidate",
                required_validation=("trace references",),
            )

            readiness = repository_cleanup.quarantine_readiness(
                root,
                candidates=(candidate,),
            )

        self.assertEqual(readiness[0]["quarantine_status"], "blocked_by_references")
        self.assertEqual(readiness[0]["reference_count"], 1)

    def test_quarantine_manifest_is_dry_run_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Backend").mkdir()
            (root / "Backend" / "tmp_explore.py").write_text("# scratch\n", encoding="utf-8")

            candidate = repository_cleanup.CleanupCandidate(
                path="Backend/tmp_explore.py",
                category="scratch_file",
                risk="low",
                reason="test candidate",
                required_validation=("trace references",),
            )

            manifest = repository_cleanup.build_quarantine_manifest(
                root,
                candidates=(candidate,),
            )

        repository_cleanup.assert_manifest_is_dry_run(manifest)
        self.assertFalse(manifest["execution_allowed"])
        self.assertEqual(manifest["quarantine_root"], "Backend/_deprecated_quarantine")
        self.assertEqual(manifest["backup_root"], "Backend/_cleanup_backups")
        self.assertEqual(manifest["items"][0]["path_type"], "file")
        self.assertEqual(manifest["items"][0]["file_count"], 1)
        self.assertFalse(manifest["items"][0]["execution_allowed"])

    def test_quarantine_manifest_keeps_referenced_candidates_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Backend").mkdir()
            (root / "Backend" / "verify_pipeline.py").write_text("# verifier\n", encoding="utf-8")
            (root / "operator.md").write_text(
                "Use verify_pipeline.py before demo calls.\n",
                encoding="utf-8",
            )

            candidate = repository_cleanup.CleanupCandidate(
                path="Backend/verify_pipeline.py",
                category="manual_verification_script",
                risk="medium",
                reason="test candidate",
                required_validation=("trace references",),
            )

            manifest = repository_cleanup.build_quarantine_manifest(
                root,
                candidates=(candidate,),
            )

        item = manifest["items"][0]
        self.assertEqual(item["quarantine_status"], "blocked_by_references")
        self.assertEqual(item["reference_count"], 1)
        self.assertFalse(item["execution_allowed"])

    def test_backup_procedure_contains_restore_and_validation_steps(self):
        procedure = repository_cleanup.backup_procedure()

        self.assertTrue(any("rollback" in step for step in procedure))
        self.assertTrue(any("backend regression" in step for step in procedure))
        self.assertTrue(any("voice demo smoke" in step for step in procedure))


if __name__ == "__main__":
    unittest.main()
