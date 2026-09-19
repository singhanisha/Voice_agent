# Repository Quarantine Readiness Plan

Date: 2026-05-15

This is Phase 11 of the migration. It adds dependency tracing and quarantine readiness rules only. It does not delete, move, rename, archive, or quarantine any file.

## Phase Purpose

- Trace references to Phase 10 cleanup candidates before any future cleanup action.
- Separate source/runtime dependencies from cleanup documentation references.
- Define the minimum gates required before a later quarantine phase.
- Preserve production voice, websocket, campaign, transcript, recording, tenant, memory, and telephony behavior.

## Systems Affected

- Offline migration tooling only: `Backend/platform_migration/repository_cleanup.py`.
- Repository documentation only: this plan and the Phase 10 audit.
- No API, DB, websocket, campaign, telephony, STT, TTS, frontend, or runtime path is changed.

## Quarantine Gates

No candidate may move to quarantine until all gates pass:

1. Dependency trace has no unresolved source, docs, runbook, deployment, or operator references.
2. Any useful manual behavior has been moved into maintained tests or current documentation.
3. Runtime duplicates have been diffed against the active runtime.
4. High-risk data artifacts have an explicit retention/export policy.
5. Owner review is complete.
6. Rollback path is documented.
7. Full backend regression passes.
8. Voice/demo/campaign smoke checks are scheduled for the quarantine release.

## Initial Trace Findings

Manual `rg` inspection found these important references:

| Candidate | Reference | Meaning |
| --- | --- | --- |
| `Backend/flows/mic_conversation.py` | `Backend/audio/mic_utils.py` | Audio utility docs still describe this manual mic path. |
| `Backend/verify_pipeline.py` | `Backend/prompt.txt` | Prompt/runbook text still asks operators to run this verifier. |
| `Backend/tmp_explore_out.txt` | `Backend/tmp_explore.py` | Scratch script writes this scratch output. |
| `Backend/main_pipeline.py` | `Backend/main_pipeline.py` usage text | Self-reference only; still needs operator/deployment trace before action. |

Cleanup docs and cleanup tests intentionally mention candidates and should not be treated as production dependencies.

## Readiness Rules

- `blocked_by_references`: do not quarantine. Resolve or document references first.
- `blocked_by_high_risk`: do not quarantine. Complete runtime, retention, tenant, or recording review first.
- `ready_for_owner_review`: still do not quarantine. Assign owner and prepare a later rollback-safe proposal.

The helper reports `quarantine_allowed: false` for every candidate by design.

## Rollback

Rollback for Phase 11 is removal of the Phase 11 helper additions, this plan, and the new tests. Because the phase is offline and trace-only, no runtime rollback, DB rollback, feature flag change, or data migration is required.

## Next Safe Phase

The next cleanup phase may run the dry-run manifest during release review and attach the result to the worklog. Runtime duplicates and data artifacts should stay blocked until owner review and regression evidence are complete.

## Phase 12 Addendum

Phase 12 added a dry-run quarantine manifest and backup procedure. The manifest is deliberately non-executable: every candidate reports `execution_allowed: false`, proposed quarantine/backup paths are informational only, and no quarantine directories are created.
