# EPMCDME-17170 Cleanup queue creator Implementation Plan

**Goal:** Cleanup rows show the assistant's real creator name (`created_by.name`) instead of `a.creator` ("system").

Commit per task using the repository's existing convention. Follow `.ai-run/guides/testing/testing-patterns.md` and `.ai-run/guides/data/repository-patterns.md`.

## Assumptions (explicit)
- Source is `assistants.created_by->>'name'`, the same field Review/detail use (`_submitted_by`, router :1093). The Review queue's submission-record precedence (`queue_service.py:1056`) is not available on the Cleanup path and is out of scope.
- The `creator` key stays in the cleanup row for frontend compatibility and now carries the real name.
- Missing or blank `created_by.name` (or a deleted assistant, via the LEFT JOIN) gives an explicit `null`. The backend never emits "System" or the raw `a.creator` id. Fallback wording is the UI's job.
- Minimal change: SQL, row builder docstring, tests. Filtering (`q` on name/description), actions and the other row keys are untouched.

## Task 1: Select the creator name in cleanup_page
- Modify `src/codemie/service/marketplace_management/audit_read_repository.py:262`. Replace `a.creator AS creator` with `NULLIF(BTRIM(a.created_by->>'name'), '') AS creator`. Keep the alias so row consumers are unchanged.
- Test-first: yes — in `tests/codemie/service/marketplace_management/test_audit_read_repository.py`, follow the existing `cleanup_page` fixtures. Seed assistants with `created_by={"name": "Jane Doe"}` and `creator="system"`, and assert `cleanup_page` returns `creator == "Jane Doe"`. Add cases for `created_by` NULL, `{}` and blank name (each returns `None`), and for a verdict whose assistant row is missing (returns `None`). Fails today because it returns "system".

## Task 2: Document and pin the row builder contract
- Modify `src/codemie/rest_api/routers/marketplace_management.py:936-948`. `_cleanup_row` keeps `"creator": row["creator"]`. Update the docstring to say `creator` is the creator's display name (`created_by.name`), or null when unknown.
- Test-first: yes — in `tests/codemie/rest_api/routers/test_marketplace_management_router.py` (fake repo at :334, row at :271), add a case where the fake row's `creator` is `"Jane Doe"` and another where it is `None`. Assert the `/cleanup` items return them verbatim and the key set is unchanged. Also check that `test_marketplace_management_contract.py:893` still passes. The assertions pin the contract. They may pass before Task 1, so the failing coverage is Task 1's.

## Negative-constraint pass
- "Not System / not attributing to a user when unknown": Task 1 emits null, never `a.creator`.
- "Existing behavior unchanged": no change to filters, ordering, actions or other keys, and Task 2 asserts the key set.
- Non-goals and out-of-scope items (frontend, submission-record precedence): no task touches them.
