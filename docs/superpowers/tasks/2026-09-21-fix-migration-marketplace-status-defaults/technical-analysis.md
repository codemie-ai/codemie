# Technical Research

**Task**: marketplace migration alembic defaults
**Generated**: 2026-09-21
**Research path**: filesystem

---

## 1. Original Context

Our branch with marketplace management automatically sets 'marketplace_status' and 'is_verified' on assistant in release date. We want our Alembic migrations to set 'marketplace_status' to 'DRAFT' and 'is_verified' to false as the default values for existing rows when adding/altering these columns.

---

## 2. Codebase Findings

### Existing Implementations

**Migration files that touch marketplace_status and is_verified:**

- `src/external/alembic/versions/5df1d2e53fbf_marketplace_management.py`
  - Adds `marketplace_status` column to `assistants` table
  - Current server_default: `"APPROVED"` (line 29: `server_default="APPROVED"`)
  - Does NOT set a bulk UPDATE for existing rows
  - This is the root of the problem identified in `d3r4a5f6t7d8`

- `src/external/alembic/versions/d3r4a5f6t7d8_marketplace_status_defaults_to_draft.py`
  - Fixes `5df1d2e53fbf` by altering the column default: `ALTER TABLE codemie.assistants ALTER COLUMN marketplace_status SET DEFAULT 'DRAFT'`
  - Has a **conditional bulk UPDATE** to backfill existing rows, but only rows matching a 5-clause predicate (never submitted, `is_global IS FALSE`, `marketplace_status = 'APPROVED'`, `marketplace_submitted_at IS NULL`, `marketplace_version = 0`, `marketplace_decided_at IS NULL`)
  - Uses a journal table (`mm_status_backfill_journal`) to record changed rows for safe downgrade

- `src/external/alembic/versions/o1r2e3v4i5e6_marketplace_review_decisions.py`
  - Adds `is_verified` column with `server_default=sa.false()` (line 86) — this is correct
  - Has a bulk UPDATE to backfill `is_verified = TRUE` only for rows where `marketplace_status = 'APPROVED' AND is_global IS TRUE` — correct backfill
  - Also adds `marketplace_version`, `marketplace_decided_at`, `marketplace_decided_by`, `marketplace_decision_reason` columns

- `src/external/alembic/versions/k6l7m8n9o0p1_add_workflow_marketplace_columns.py`
  - Does NOT touch `marketplace_status` or `is_verified`
  - Adds `is_global`, `categories`, `unique_users_count` to `workflows` table only

- `src/external/alembic/versions/p7r2i3v4b5f6_demote_private_assistants_to_draft.py`
  - Referenced in `d3r4a5f6t7d8` as "7.2" — sweeps private rows to DRAFT (one-off backfill)

- `src/external/alembic/versions/c0n2c3u4r5i6_marketplace_status_index_concurrent.py`
  - Touches `marketplace_status` index, not defaults

### Architecture and Layers Affected

- **DB-Persistence layer**: Alembic migrations in `src/external/alembic/versions/`
- **ORM/Model layer**: `src/codemie/rest_api/models/assistant.py` — `AssistantModel` (or equivalent SQLModel class)

### Integration Points

- `assistants` table is the central entity
- `mm_status_backfill_journal` table tracks backfill changes for safe downgrades
- `queue_service` uses `marketplace_status` in compare-and-set guards

### Patterns and Conventions

- Column additions use `server_default` in `op.add_column()` to set DB-level default for new rows
- Bulk UPDATE statements are used to backfill **existing** rows when the new default differs from what those rows should have
- Journal pattern (`mm_status_backfill_journal`) used when backfill must be reversible
- The `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT` form is used to change an existing column's default (as in `d3r4a5f6t7d8`)
- Constraint additions use `NOT VALID` + separate `VALIDATE CONSTRAINT` for zero-downtime

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns
- `.ai-run/guides/data/repository-patterns.md` — Repository access patterns

### Architectural Decisions

- `d3r4a5f6t7d8` docstring extensively documents why `DRAFT` is correct and why `APPROVED` as default was wrong. Key points:
  - `APPROVED` is the terminal state; new rows should never be born approved
  - `NULL` is incorrect because `queue_service` SUBMIT guard uses `ANY(:allowed)` and `NULL = NULL` is false in SQL
  - `DRAFT` is the correct initial state per `MarketplaceStatus` state machine

### Derived Conventions

- Every `op.add_column` for non-nullable columns includes a `server_default`
- When changing an existing column default, use `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT`
- When backfilling existing rows, write a targeted UPDATE with a precise predicate to avoid touching rows with real history

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_marketplace_management_contract.py`
- `tests/codemie/rest_api/routers/test_marketplace_approval_lifecycle.py`
- `tests/codemie/rest_api/routers/test_marketplace_draft_default.py`
- `tests/codemie/marketplace_fixtures/conftest.py`
- `tests/codemie/marketplace_fixtures/test_marketplace_seed_fixture.py`

### Testing Framework and Patterns

- pytest with marketplace-specific fixtures in `tests/codemie/marketplace_fixtures/`

### Coverage Gaps

- No migration-level tests (typical for Alembic); behavior is validated through API/service tests
- If migrations are changed, `test_marketplace_draft_default.py` and `test_marketplace_seed_fixture.py` are the most relevant tests to verify

---

## 5. Configuration and Environment

### Environment Variables

None directly relevant to migration defaults.

### Configuration Files

- `src/external/alembic/versions/` — migration files
- `src/codemie/rest_api/models/assistant.py` — ORM model with `default` and `server_default` values

### Feature Flags and Deployment Concerns

None for this change.

---

## 6. Risk Indicators

- **`5df1d2e53fbf` sets `server_default="APPROVED"`** — this is the root problem. If this migration runs on a fresh DB, new rows added before `d3r4a5f6t7d8` runs will have `marketplace_status = 'APPROVED'`. The fix in `d3r4a5f6t7d8` corrects the column default and backfills affected rows, but only with a conditional predicate.
- **`is_verified` in `o1r2e3v4i5e6`** already has correct `server_default=sa.false()` and does a correct conditional backfill (only sets TRUE for APPROVED+global rows). No fix needed here.
- **`d3r4a5f6t7d8` backfill predicate** is intentionally conservative — it only fixes rows that were never submitted. Rows with real history are untouched. This is correct behavior.
- **`5df1d2e53fbf` itself is not changed** in the branch — the fix is additive (a new migration `d3r4a5f6t7d8`). This means the server_default in `5df1d2e53fbf` remains `"APPROVED"` in source, which is technically a historical artifact but intentional.
- **No bulk UPDATE in `5df1d2e53fbf`** for existing rows at column addition time — this gap is addressed by `d3r4a5f6t7d8` as a follow-up migration rather than inline.

---

## 7. Summary for Complexity Assessment

The task is to ensure Alembic migrations set `marketplace_status = 'DRAFT'` and `is_verified = false` as defaults for existing rows. Research shows this is substantially already done across two migrations, but the implementation is split and may be incomplete depending on whether the task requires fixing `5df1d2e53fbf` directly or verifying the downstream migrations are sufficient.

**Current state**: `5df1d2e53fbf` adds `marketplace_status` with `server_default='APPROVED'` and no backfill. `d3r4a5f6t7d8` subsequently fixes the column default to `'DRAFT'` and backfills only rows matching a strict 5-clause "never submitted" predicate. `is_verified` in `o1r2e3v4i5e6` has correct `server_default=sa.false()` and backfills `TRUE` only for APPROVED+global rows, leaving all others at `false`. The ORM model in `assistant.py` has `default='DRAFT'` and `server_default='DRAFT'` for `marketplace_status`, and `default=False`/`server_default=text("false")` for `is_verified`.

**What may need changing**: If the task requires that `5df1d2e53fbf` itself set the correct defaults (not relying on the follow-up `d3r4a5f6t7d8`), then `5df1d2e53fbf` needs `server_default='DRAFT'` and a bulk UPDATE for existing rows at the point of column addition. Alternatively, if `d3r4a5f6t7d8`'s backfill predicate is too conservative and some rows that should be DRAFT are being skipped, the predicate needs widening. The complexity is low — targeted SQL changes to one or two migration files — but requires careful predicate design to avoid demoting legitimately-APPROVED rows.
