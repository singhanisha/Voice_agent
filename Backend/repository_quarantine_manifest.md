# Repository Quarantine Manifest

Date: 2026-05-15

This is Phase 12 of the migration. It defines a dry-run quarantine manifest and backup procedure only. No files are deleted, moved, renamed, copied, archived, or quarantined in this phase.

## Manifest Rules

- The manifest is review-only.
- Every item must report `execution_allowed: false`.
- Candidate contents are not copied or inspected for sensitive data.
- Metadata is limited to path existence, path type, byte size, file count, reference count, risk, and proposed future paths.
- High-risk runtime/data artifacts remain blocked until retention, ownership, and smoke-test gates are complete.

## Proposed Future Paths

- Quarantine root: `Backend/_deprecated_quarantine`
- Backup root: `Backend/_cleanup_backups`

These directories are not created in Phase 12.

## Backup Procedure For A Later Phase

1. Create a branch or release tag before quarantine work starts.
2. Capture the dry-run manifest in the release record.
3. Copy candidate files to the approved backup location without modifying originals.
4. Verify backup file counts and byte totals against manifest metadata.
5. Move candidates to quarantine only after owner approval.
6. Run backend regression, voice demo smoke, campaign smoke, and tenant leak checks.
7. Roll back by moving quarantined paths back to their original locations from backup.

## Execution Decision

Phase 12 authorizes planning only. Future quarantine work still requires explicit approval and must remain reversible.
