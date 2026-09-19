"""Repository cleanup audit helpers.

This module is intentionally non-destructive. It records cleanup candidates and
the validation required before any later quarantine or deletion phase.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SAFE_ACTION = "audit_only"
DRY_RUN_ONLY = True
QUARANTINE_ROOT = "Backend/_deprecated_quarantine"
BACKUP_ROOT = "Backend/_cleanup_backups"

DEPRECATION_STAGES = (
    "audit_only",
    "dependency_trace",
    "owner_review",
    "quarantine_with_rollback",
    "delete_after_release_validation",
)

MAX_SCAN_BYTES = 2_000_000

SCAN_EXCLUDED_DIRS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
}

SCAN_EXCLUDED_SUFFIXES = {
    ".db",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".jsonl",
    ".log",
    ".mp3",
    ".mp4",
    ".png",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".wav",
}

TRACE_SELF_PATHS = {
    "Backend/platform_migration/repository_cleanup.py",
    "Backend/repository_cleanup_audit.md",
    "Backend/repository_quarantine_plan.md",
    "Backend/repository_quarantine_manifest.md",
    "Backend/tests/test_repository_cleanup_audit.py",
}

GLOBAL_QUARANTINE_GATES = (
    "dependency trace reviewed and unresolved references closed or documented",
    "owner approval recorded for every candidate",
    "high-risk data retention/export decision recorded",
    "runtime duplicate diff completed before moving runtime-adjacent files",
    "rollback path validated in a branch before merge",
    "full backend regression passed",
    "voice demo and campaign smoke checks scheduled for release validation",
)


@dataclass(frozen=True)
class CleanupCandidate:
    path: str
    category: str
    risk: str
    reason: str
    required_validation: tuple[str, ...]
    recommended_action: str = SAFE_ACTION

    def inspect(self, repo_root: str | Path) -> dict[str, object]:
        candidate_path = Path(repo_root) / self.path
        details = asdict(self)
        details["exists"] = candidate_path.exists()
        if candidate_path.is_dir():
            details["path_type"] = "directory"
        elif candidate_path.is_file():
            details["path_type"] = "file"
        else:
            details["path_type"] = "missing"
        return details


DEFAULT_CANDIDATES: tuple[CleanupCandidate, ...] = (
    CleanupCandidate(
        path="Backend/main_working.py",
        category="duplicate_runtime_entrypoint",
        risk="high",
        reason="Legacy server snapshot overlaps with Backend/main.py route and websocket ownership.",
        required_validation=(
            "confirm no process manager, docs, or operator scripts start this entrypoint",
            "compare route ownership with Backend/main.py",
            "complete demo call, campaign, websocket, transcript, and recording regression tests",
        ),
    ),
    CleanupCandidate(
        path="Backend/main_pipeline.py",
        category="legacy_pipeline_entrypoint",
        risk="high",
        reason="Standalone pipeline runner can drift from production runtime contracts.",
        required_validation=(
            "trace prompt/docs references",
            "replace useful checks with maintained tests before quarantine",
            "confirm no production deployment invokes this file",
        ),
    ),
    CleanupCandidate(
        path="Backend/flows/runtime_working.py",
        category="duplicate_voice_runtime",
        risk="high",
        reason="Duplicate of the voice runtime path; unsafe to remove without audio contract parity checks.",
        required_validation=(
            "trace imports and operator usage",
            "diff against Backend/flows/runtime.py for any live-only fixes",
            "run audio, barge-in, websocket, and multilingual regression tests",
        ),
    ),
    CleanupCandidate(
        path="Backend/flows/conversation.py",
        category="legacy_conversation_path",
        risk="medium",
        reason="Older conversation helper may still be useful for diagnostics but is not the primary runtime.",
        required_validation=(
            "trace imports and manual runbook references",
            "capture any diagnostic behavior in tests or documentation",
        ),
    ),
    CleanupCandidate(
        path="Backend/flows/mic_conversation.py",
        category="manual_mic_runtime",
        risk="medium",
        reason="Manual microphone runtime path overlaps with demo-call behavior and must not be removed blindly.",
        required_validation=(
            "trace local demo workflows",
            "document replacement command if still needed",
            "confirm browser demo websocket remains unaffected",
        ),
    ),
    CleanupCandidate(
        path="Backend/patch_json_disconnect.py",
        category="one_off_patch_script",
        risk="medium",
        reason="Mutates the default agent JSON directly and should be retired only after schema history is understood.",
        required_validation=(
            "confirm patch has already been applied or is obsolete",
            "preserve schema migration history if useful",
        ),
    ),
    CleanupCandidate(
        path="Backend/test_backend.py",
        category="manual_test_script",
        risk="medium",
        reason="Manual test script outside the maintained test suite.",
        required_validation=(
            "convert any unique coverage into Backend/tests",
            "confirm no operator runbook depends on it",
        ),
    ),
    CleanupCandidate(
        path="Backend/test_ws.py",
        category="manual_test_script",
        risk="medium",
        reason="Manual websocket probe outside the maintained test suite.",
        required_validation=(
            "preserve websocket contract coverage in automated tests",
            "confirm no demo support workflow depends on it",
        ),
    ),
    CleanupCandidate(
        path="Backend/mic_test.py",
        category="manual_audio_test_script",
        risk="medium",
        reason="Manual microphone/audio test path can be useful for production incident diagnosis.",
        required_validation=(
            "document replacement diagnostics",
            "confirm audio contract tests cover its core checks",
        ),
    ),
    CleanupCandidate(
        path="Backend/verify_pipeline.py",
        category="manual_verification_script",
        risk="medium",
        reason="Referenced by prompt docs as a stack verification command.",
        required_validation=(
            "replace prompt/runbook references",
            "move useful provider checks into maintained smoke tests",
        ),
    ),
    CleanupCandidate(
        path="Backend/tmp_explore.py",
        category="scratch_file",
        risk="low",
        reason="Exploration script with no expected runtime ownership.",
        required_validation=(
            "trace imports",
            "quarantine after confirming it is scratch-only",
        ),
    ),
    CleanupCandidate(
        path="Backend/tmp_explore_out.txt",
        category="scratch_output",
        risk="low",
        reason="Output generated by scratch exploration.",
        required_validation=(
            "confirm no tests or docs read this output",
            "quarantine with scratch file if still present",
        ),
    ),
    CleanupCandidate(
        path="Backend/scratch/test_fixes.py",
        category="scratch_test_script",
        risk="low",
        reason="Scratch regression notes should be promoted to maintained tests before removal.",
        required_validation=(
            "compare coverage with Backend/tests",
            "move any missing assertions into maintained tests",
        ),
    ),
    CleanupCandidate(
        path="Backend/logs.txt",
        category="local_runtime_artifact",
        risk="medium",
        reason="Local log artifact; may contain debugging context or sensitive call metadata.",
        required_validation=(
            "confirm retention policy",
            "verify no needed incident data remains",
        ),
    ),
    CleanupCandidate(
        path="voice_agent.log",
        category="local_runtime_artifact",
        risk="medium",
        reason="Runtime log artifact; should be governed by retention policy, not ad hoc cleanup.",
        required_validation=(
            "confirm log retention policy",
            "verify it contains no active investigation data",
        ),
    ),
    CleanupCandidate(
        path="Backend/call_logs.jsonl",
        category="local_runtime_artifact",
        risk="high",
        reason="Call log artifact can contain transcript/call metadata and must follow tenant-safe retention.",
        required_validation=(
            "define tenant-safe export and retention policy",
            "confirm no production call results rely on this file",
        ),
    ),
    CleanupCandidate(
        path="Backend/db/platform.db",
        category="local_database_artifact",
        risk="high",
        reason="Local database may contain campaign, transcript, recording, phone, or tenant records.",
        required_validation=(
            "backup or confirm disposable local-only data",
            "verify migrations are reproducible from source",
            "confirm no demo/customer data needs retention",
        ),
    ),
    CleanupCandidate(
        path="Backend/recordings",
        category="recording_artifact_directory",
        risk="high",
        reason="Recordings are user/campaign data and require explicit retention and tenant isolation policy.",
        required_validation=(
            "define recording retention policy",
            "confirm tenant ownership and export requirements",
            "never delete active campaign or demo-call recordings blindly",
        ),
    ),
)


def analyze_cleanup_candidates(repo_root: str | Path = ".") -> list[dict[str, object]]:
    """Return cleanup candidates with existence metadata.

    The result is suitable for audits, CI checks, or admin review. It performs no
    writes and exposes no delete operation.
    """
    return [candidate.inspect(repo_root) for candidate in DEFAULT_CANDIDATES]


def _repo_path(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _iter_scannable_files(repo_root: str | Path) -> Iterable[Path]:
    root = Path(repo_root)
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in SCAN_EXCLUDED_DIRS
        ]
        current_path = Path(current_root)
        for filename in files:
            path = current_path / filename
            try:
                rel_path = _repo_path(path, root)
                size = path.stat().st_size
            except OSError:
                continue
            if rel_path in TRACE_SELF_PATHS:
                continue
            if path.suffix.lower() in SCAN_EXCLUDED_SUFFIXES:
                continue
            if size > MAX_SCAN_BYTES:
                continue
            yield path


def _reference_tokens(candidate: CleanupCandidate) -> tuple[str, ...]:
    normalized = candidate.path.replace("\\", "/")
    tokens = {normalized, normalized.replace("/", "\\")}
    candidate_path = Path(normalized)
    if candidate_path.suffix:
        tokens.add(candidate_path.name)
        if candidate_path.suffix == ".py":
            tokens.add(normalized[:-3].replace("/", "."))
    return tuple(sorted(tokens, key=len, reverse=True))


def trace_candidate_references(
    repo_root: str | Path = ".",
    candidates: Iterable[CleanupCandidate] = DEFAULT_CANDIDATES,
    *,
    max_matches_per_candidate: int = 50,
) -> dict[str, list[dict[str, object]]]:
    """Trace text references to cleanup candidates without mutating the repo."""
    root = Path(repo_root)
    scan_files = list(_iter_scannable_files(root))
    traces: dict[str, list[dict[str, object]]] = {}

    for candidate in candidates:
        candidate_path = candidate.path.replace("\\", "/").rstrip("/")
        tokens = _reference_tokens(candidate)
        references: list[dict[str, object]] = []

        for path in scan_files:
            rel_path = _repo_path(path, root)
            if rel_path == candidate_path or rel_path.startswith(f"{candidate_path}/"):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            except OSError:
                continue

            for line_number, line in enumerate(lines, start=1):
                matched_tokens = [token for token in tokens if token and token in line]
                if not matched_tokens:
                    continue
                references.append(
                    {
                        "file": rel_path,
                        "line": line_number,
                        "tokens": matched_tokens,
                    }
                )
                if len(references) >= max_matches_per_candidate:
                    break
            if len(references) >= max_matches_per_candidate:
                break

        traces[candidate.path] = references

    return traces


def quarantine_readiness(
    repo_root: str | Path = ".",
    candidates: Iterable[CleanupCandidate] = DEFAULT_CANDIDATES,
) -> list[dict[str, object]]:
    """Return conservative quarantine readiness details.

    This helper never approves deletion. It only separates candidates that are
    blocked by references from candidates ready for human owner review.
    """
    candidate_tuple = tuple(candidates)
    traces = trace_candidate_references(repo_root, candidate_tuple)
    readiness: list[dict[str, object]] = []

    for candidate in candidate_tuple:
        references = traces.get(candidate.path, [])
        if references:
            status = "blocked_by_references"
            next_step = "resolve or document external references before owner review"
        elif candidate.risk == "high":
            status = "blocked_by_high_risk"
            next_step = "complete data/runtime retention review before quarantine"
        else:
            status = "ready_for_owner_review"
            next_step = "assign owner and prepare rollback-safe quarantine proposal"

        details = candidate.inspect(repo_root)
        details.update(
            {
                "reference_count": len(references),
                "references": references,
                "quarantine_status": status,
                "quarantine_allowed": False,
                "next_step": next_step,
            }
        )
        readiness.append(details)

    return readiness


def _manifest_slug(candidate_path: str) -> str:
    normalized = candidate_path.replace("\\", "/").strip("/")
    return normalized.replace("/", "__")


def collect_path_metadata(repo_root: str | Path, candidate_path: str) -> dict[str, object]:
    """Collect non-content metadata for a cleanup candidate path."""
    path = Path(repo_root) / candidate_path
    metadata: dict[str, object] = {
        "exists": path.exists(),
        "path_type": "missing",
        "size_bytes": 0,
        "file_count": 0,
    }
    try:
        if path.is_file():
            metadata.update(
                {
                    "path_type": "file",
                    "size_bytes": path.stat().st_size,
                    "file_count": 1,
                }
            )
        elif path.is_dir():
            total_size = 0
            file_count = 0
            for child in path.rglob("*"):
                if not child.is_file():
                    continue
                try:
                    total_size += child.stat().st_size
                    file_count += 1
                except OSError:
                    continue
            metadata.update(
                {
                    "path_type": "directory",
                    "size_bytes": total_size,
                    "file_count": file_count,
                }
            )
    except OSError:
        metadata["path_type"] = "unreadable"
    return metadata


def backup_procedure() -> tuple[str, ...]:
    """Return the human-reviewed backup procedure for a later quarantine phase."""
    return (
        "create a branch or release tag before quarantine work starts",
        "capture this dry-run manifest in the release record",
        "copy candidate files to the approved backup location without modifying originals",
        "verify backup file counts and byte totals against manifest metadata",
        "move candidates to the quarantine path only after owner approval",
        "run backend regression, voice demo smoke, campaign smoke, and tenant leak checks",
        "rollback by moving quarantined paths back to their original locations from backup",
    )


def build_quarantine_manifest(
    repo_root: str | Path = ".",
    candidates: Iterable[CleanupCandidate] = DEFAULT_CANDIDATES,
) -> dict[str, object]:
    """Build a dry-run manifest for a future rollback-safe quarantine phase."""
    candidate_tuple = tuple(candidates)
    readiness = quarantine_readiness(repo_root, candidate_tuple)
    readiness_by_path = {item["path"]: item for item in readiness}
    items: list[dict[str, object]] = []

    for candidate in candidate_tuple:
        status = readiness_by_path[candidate.path]
        metadata = collect_path_metadata(repo_root, candidate.path)
        slug = _manifest_slug(candidate.path)
        proposed_quarantine_path = f"{QUARANTINE_ROOT}/{slug}"
        proposed_backup_path = f"{BACKUP_ROOT}/{slug}"

        items.append(
            {
                "original_path": candidate.path,
                "category": candidate.category,
                "risk": candidate.risk,
                "exists": metadata["exists"],
                "path_type": metadata["path_type"],
                "size_bytes": metadata["size_bytes"],
                "file_count": metadata["file_count"],
                "reference_count": status["reference_count"],
                "quarantine_status": status["quarantine_status"],
                "proposed_quarantine_path": proposed_quarantine_path,
                "proposed_backup_path": proposed_backup_path,
                "execution_allowed": False,
                "rollback_steps": (
                    f"restore {candidate.path} from {proposed_backup_path}",
                    "rerun backend regression and targeted smoke checks",
                    "reopen any resolved references if behavior regresses",
                ),
                "remaining_gates": GLOBAL_QUARANTINE_GATES,
            }
        )

    return {
        "dry_run": DRY_RUN_ONLY,
        "quarantine_root": QUARANTINE_ROOT,
        "backup_root": BACKUP_ROOT,
        "execution_allowed": False,
        "global_gates": GLOBAL_QUARANTINE_GATES,
        "backup_procedure": backup_procedure(),
        "items": items,
    }


def assert_manifest_is_dry_run(manifest: dict[str, object]) -> None:
    """Raise if a manifest looks executable instead of review-only."""
    if manifest.get("dry_run") is not True:
        raise ValueError("Quarantine manifest must be dry-run only")
    if manifest.get("execution_allowed") is not False:
        raise ValueError("Quarantine manifest must not allow execution")

    items = manifest.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Quarantine manifest items must be a list")
    executable = [
        item.get("original_path")
        for item in items
        if isinstance(item, dict) and item.get("execution_allowed") is not False
    ]
    if executable:
        raise ValueError(f"Quarantine manifest contains executable items: {executable}")


def summarize_by_risk(
    candidates: Iterable[CleanupCandidate] = DEFAULT_CANDIDATES,
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for candidate in candidates:
        summary[candidate.risk] = summary.get(candidate.risk, 0) + 1
    return summary


def assert_audit_only(candidates: Iterable[CleanupCandidate] = DEFAULT_CANDIDATES) -> None:
    """Raise if a cleanup candidate tries to bypass the safe audit stage."""
    unsafe = [
        candidate.path
        for candidate in candidates
        if candidate.recommended_action != SAFE_ACTION
    ]
    if unsafe:
        raise ValueError(f"Cleanup candidates must start audit-only: {unsafe}")
