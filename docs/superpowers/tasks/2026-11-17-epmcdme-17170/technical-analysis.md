# Technical Research

**Task**: marketplace assistant cleanup queue creator
**Generated**: 2026-11-17
**Research path**: filesystem

---

## 1. Original Context

Summary: Marketplace Cleanup queue should show each assistant's actual creator instead of "System"

## Description
As a Marketplace administrator, I want the Cleanup queue to identify who created each assistant, so I can understand its ownership and follow up with the right person. The queue should show the same creator identity available in the Review queue's "Submitted by" column.

## Preconditions
- An administrator can access Marketplace Management's Cleanup queue.
- Assistant creator information is available to the system.

## Scenarios of Use
1. An administrator opens the Marketplace Management Cleanup queue.
1. The administrator reviews the creator shown for an assistant.
1. The queue displays the actual user who created the assistant, consistent with the identity shown in the Review queue.

## Affected Areas
- Marketplace Management → assistant Cleanup queue table
- Creator attribution displayed for assistants

## Acceptance Criteria
1. Each assistant row in the Cleanup queue displays the actual creator's identity rather than the generic value "System," when creator information is available.
1. The displayed identity uses the same creator/submitter attribution as the Review queue.
1. Existing Cleanup queue behavior, including filtering and actions, is unchanged.
1. If creator information is unavailable, the UI displays a clear fallback rather than incorrectly attributing the assistant to a user.

Repo is the CodeMie backend (current branch EPMCDME-17170_fix-marketplace-assistant-cleanup-queue-creator-display). Focus on backend API/service producing the Cleanup queue rows vs the Review queue "Submitted by".

---

## 2. Codebase Findings

### Existing Implementations

**Purpose: show who created an assistant in the Cleanup queue**
- `GET /entities/{kind}/cleanup` in `src/codemie/rest_api/routers/marketplace_management.py:825`, handled by `_cleanup_payload` (:882). It calls `audit_reads.cleanup_page` and `_cleanup_row`.
- `cleanup_page` in `src/codemie/service/marketplace_management/audit_read_repository.py:238` is raw SQL. It selects `a.creator AS creator` (:262) from `mm_audit_verdict v LEFT JOIN assistants a`. It selects nothing from `a.created_by`.
- `assistants.creator` is `str`, default `"system"`, indexed (`rest_api/models/assistant.py:749`). The `Assistant` model comment in `entity_adapter.py:108` calls it a service-account UUID, not the real human.
- `_cleanup_row` (router :936) passes `creator` through as a flat key. There is no `submitted_by` or name field on cleanup rows. The response is a plain `dict`, not a Pydantic model, and its docstring says each field is emitted unconditionally.
- Unrelated to this fix, `a.creator` is the field that surfaces as the generic "System" value. `SYSTEM_USER` is defined at `core/models.py:1003` (`name="System"`).

**Purpose: Review queue "Submitted by"**
- `_submitted_by(assistant)` (router :1093) returns `assistant.created_by.name`, or `None` if there is no `created_by`.
- It is used in `_pending_payload` (:1245), as `submissions.get(a.id, {}).get("submitted_by") or _submitted_by(a)`. The `submissions` map is sourced from `queue_service.py:1056`, which falls back to `decided_by_email`. It is also used at :1324.
- It populates `PendingRow.submitted_by: Optional[str]` (`service/marketplace_management/models.py:815`). A before-validator at :832 flattens a `CreatedByUser` to its `name`.
- The detail drawer uses `submitted_by=created_by.name` (router :2228) and also returns `creator` (:2229).
- `queue_service.py:1019` has a comment: `assistants.created_by` is the *author*, and on a (truncated in my read) submission it may not be the submitter. The Review queue's "same attribution" therefore has two sources: the submission record first, then `created_by.name`.

**Other touched code**
- `entity_adapter._author_fields` (`entity_adapter.py:107`) maps `created_by` to `author_name` and `author_username` for the audit engine, with `creator` kept separately. `validator/models.py:225` carries these fields.
- `assistants.created_by` is a JSON column of `CreatedByUser` (`assistant.py:679`). It has indexes on `(created_by->>'id')` and a gin trigram index on `(created_by->>'name')` (:814-816).

### Decisions Already in the Code
- Cleanup rows are built from SQL, not from `Assistant` objects (F-61). Fields come from raw column names on `assistants`, so JSON access would be `a.created_by->>'name'`.
- A before-validator on `PendingRow` and the `_submitted_by` helper show the convention of projecting `created_by.name` as a string.
- Rows emit explicit nulls for missing values (`_cleanup_row` docstring). No fallback label is applied in the backend today. For Review, an unknown creator is `None`.
- Stored: `creator` (a string id) and `created_by` (an object). Read: Cleanup reads the id, Review reads the object's name.

### Task Statements Checked Against the Code
- "Cleanup shows System instead of the creator": holds. Cleanup reads `a.creator`, which defaults to `"system"` (`assistant.py:749`).
- "Same attribution as the Review queue's Submitted by": partly holds. `created_by.name` is common to both. The Review queue also prefers the submission record's `submitted_by` (router :1245), and that record is not available in the Cleanup path.
- "Filtering and actions unchanged": the `q` filter only matches `a.name` and `a.description` (`audit_read_repository.py:205`). It does not touch creator. Unverified: how the frontend consumes the `creator` key (the frontend is outside this repository).

### Consumers of Changed Contracts
- The Cleanup row key `creator` is read by the frontend (outside this repository, unverified). Tests that reference the cleanup endpoint: `tests/codemie/rest_api/routers/test_marketplace_management_router.py`, `test_marketplace_management_contract.py` and `test_bucket_all_sentinel.py`. I did not open them to confirm the asserted shape.
- `audit_reads.cleanup_page` has no other callers found in `src`. I only ran a repo-wide search by name for the tests.
- The detail endpoint returns `creator` and `submitted_by` separately (:2228-2229), so it already exposes both.

### Architecture and Layers Affected
Router (`marketplace_management.py`) → audit read repository (raw SQL, `audit_read_repository.py`) → `assistants` table (`created_by` JSON, `creator` str).

### Integration Points
Postgres only. The `mm_audit_verdict` and `assistants` join is local. No external service.

### Patterns and Conventions
Router thin and delegates via `run_in_threadpool`. SQL stays in `audit_read_repository`. Raw-SQL rows are plain dicts.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (not read in depth: API patterns and database guides are the relevant ones). No doc specific to Cleanup creator found in my search.

### Architectural Decisions
Inline markers: F-09 (`created_by` is an object, not a string) and F-61 (SQL-side paging).

### Derived Conventions
Project the name string in the backend. Return explicit nulls.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/service/marketplace_management/test_audit_read_repository.py` has zero mentions of `creator`. `test_pending_row.py` covers the `PendingRow` coercion of `submitted_by`. Router and contract tests listed above cover `/cleanup`.

### Testing Framework and Patterns
pytest, with a conftest in `tests/codemie/service/marketplace_management/`.

### Coverage Gaps
No test asserts the `creator` value on cleanup rows from SQL.

---

## 5. Configuration and Environment

### Environment Variables
None relevant found.

### Configuration Files
None relevant.

### Feature Flags and Deployment Concerns
None found. No migration is involved in the current read path.

---

## 6. Risk Indicators

- Speculative: the Cleanup row and `_submitted_by` both read from different sources (`a.creator` string vs `created_by.name`). Aligning them touches `cleanup_page` SQL and `_cleanup_row`.
- The Review queue's `submitted_by` has a submission-record precedence (router :1245, `queue_service.py:1056`). "Same attribution" is ambiguous when `created_by` differs from the submitter (see `queue_service.py:1019`).
- Keeping the `creator` key in place keeps an existing mechanism in the response for same purpose. Its frontend consumers are not visible from this repository (see Task Statements section).
- Rows where `created_by` is null (legacy or system-seeded, e.g. migrations writing `"System"` as `created_by`: `alembic/versions/d5e9f2a3b1c7...:411`, applies to MCP configs) have no real creator.
- Contract/router tests were not read; they may pin the cleanup row shape.
- Fallback wording is a UI concern. The backend currently only emits null.

---

## 7. Summary for Complexity Assessment

The Cleanup queue is produced by one router function (`_cleanup_payload`/`_cleanup_row`) and one raw-SQL repository function (`cleanup_page`). The row exposes `creator`, taken from `assistants.creator`, which defaults to `"system"`. The Review queue's "Submitted by" comes from `created_by.name` (with a submission record taking priority), which is a different field. The change surface in the backend is small: about 2-3 files (repository SQL, router row builder, tests).

There is no novel technology. The patterns for projecting `created_by.name` already exist in the Review path. Test coverage for the cleanup SQL's creator column is absent. Router and contract tests exist and may need updates.

Key risks are defining "same attribution" (submission record vs `created_by`), the unknown frontend consumer of the `creator` key, and the null-creator fallback.

---

## 8. External References

None named by the task.
