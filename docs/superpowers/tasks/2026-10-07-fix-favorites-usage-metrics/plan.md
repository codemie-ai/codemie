# Fix Favorites Usage Metrics (EPMCDME-15284) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `unique_users_count` and `clone_count` on `GET /v1/preferences/{user_id}/favorites/assistants` so the UI stops making per-item backfill calls.

**Architecture:** `FavoriteItem` (shared DTO) gains two `int = 0` fields; `UserPreferencesService.get_favorite_assistants` copies the already-loaded `Assistant.unique_users_count` / `Assistant.clone_count` columns onto each item, using the exact `a.<field> or 0` idiom already used for the sibling `unique_likes_count`/`unique_dislikes_count` fields on the adjacent lines. No new query, join, projection, or migration — `select(Assistant)` already loads every column.

**Tech Stack:** Python, Pydantic (`FavoriteItem`), SQLModel (`Assistant`), pytest/unittest.mock.

**Requirements source:** inline (ticket EPMCDME-15284, no spec.md for this task).

**Commit per task using the repository's existing convention.**

## Global Constraints

- No new query, join, column projection, or migration — both DB columns already exist and are already loaded.
- Scope is strictly the assistants branch of `get_favorite_assistants`; `get_favorite_skills`/`get_favorite_workflows` and the router layer are untouched — the new fields simply default to `0` there.
- `FavoriteItem` fields are non-optional `int`; `Assistant.unique_users_count`/`clone_count` are `Optional[int]` — bridge with `or 0`, matching the existing sibling fields.

## Review Focus

- A `None` value on `Assistant.unique_users_count`/`clone_count` must map to `0` on the response, not raise a Pydantic validation error — covered by the `or 0` idiom and by not loosening the two mocks' values to `None`.
- The two existing `MagicMock()`-based tests must not silently auto-vivify child mocks into the new `int` fields — covered by explicitly setting both attributes on every mock `Assistant` row touched by this task.
- `get_favorite_skills`/`get_favorite_workflows` must keep reporting `0` for the two new fields (unchanged behavior) — confirmed by not modifying those methods or their tests.

## Acceptance criteria

- [ ] `GET /v1/preferences/{user_id}/favorites/assistants` response items include `unique_users_count` and `clone_count`, carrying the real value from the underlying `Assistant` row (defaulting to `0` when the column is `null`).
- [ ] `get_favorite_skills` and `get_favorite_workflows` responses are unchanged (new fields default to `0`, no code touched).
- [ ] No new DB query, join, projection, or migration is introduced.

---

### Task 1: Add `unique_users_count`/`clone_count` fields to `FavoriteItem`

**Files:**
- Modify: `src/codemie/rest_api/models/user_preferences.py:73` (immediately after `unique_dislikes_count: int = 0`)

**Interfaces:**
- Produces: `FavoriteItem.unique_users_count: int = 0`, `FavoriteItem.clone_count: int = 0` — consumed by Task 2.

Test-first: no — purely additive default-valued Pydantic fields with no standalone behavior; their only observable effect is exercised by Task 2's test.

- [ ] **Step 1:** Insert two lines after `user_preferences.py:73`, matching the existing style exactly:
  ```python
  unique_users_count: int = 0
  clone_count: int = 0
  ```
- [ ] **Step 2:** Commit.

---

### Task 2: Populate the new fields in `get_favorite_assistants` and update existing tests

**Files:**
- Modify: `src/codemie/service/user_preferences_service.py:140-141` (the `unique_likes_count`/`unique_dislikes_count` mapping lines inside the `FavoriteItem(...)` construction)
- Modify: `tests/codemie/service/test_user_preferences_service.py:275`, `:290`, `:335` (mock attribute setup), and the assertions in `test_missing_ids_silently_skipped` (around line 303-305) and `test_categories_populated_in_favorite_item` (around line 346)

**Interfaces:**
- Consumes: `FavoriteItem.unique_users_count`/`clone_count` from Task 1.

Test-first: yes — strengthen the two existing unit tests to assert `result.data[*].unique_users_count` / `.clone_count` equal distinct nonzero mock values; these assertions fail before the fix (fields don't exist / aren't populated) and pass after.

- [ ] **Step 1:** In `test_missing_ids_silently_skipped`, after `mock_a1.unique_dislikes_count = 0` (line 275) add `mock_a1.unique_users_count = 5` and `mock_a1.clone_count = 2`; after `mock_a2.unique_dislikes_count = 0` (line 290) add `mock_a2.unique_users_count = 7` and `mock_a2.clone_count = 3`. After the existing assertions (around line 305) add:
  ```python
  assert result.data[0].unique_users_count == 5
  assert result.data[0].clone_count == 2
  assert result.data[1].unique_users_count == 7
  assert result.data[1].clone_count == 3
  ```
  In `test_categories_populated_in_favorite_item`, after `mock_a1.unique_dislikes_count = 0` (line 335) add `mock_a1.unique_users_count = 9` and `mock_a1.clone_count = 4`, then after the existing assertion add `assert result.data[0].unique_users_count == 9` and `assert result.data[0].clone_count == 4`.
- [ ] **Step 2:** Run `pytest tests/codemie/service/test_user_preferences_service.py -k "missing_ids_silently_skipped or categories_populated_in_favorite_item" -v`. Expected: FAIL — `FavoriteItem` has no such fields yet / values unset.
- [ ] **Step 3:** In `user_preferences_service.py`, after line 141 (`unique_dislikes_count=a.unique_dislikes_count or 0,`) add:
  ```python
  unique_users_count=a.unique_users_count or 0,
  clone_count=a.clone_count or 0,
  ```
- [ ] **Step 4:** Re-run the same `pytest` command. Expected: PASS.
- [ ] **Step 5:** Commit.

---

**Negative-constraints pass:**
- No new query/join/migration: honored — Task 2 only adds field-mapping lines inside the existing `FavoriteItem(...)` construction; `select(Assistant)` at `user_preferences_service.py:98` is untouched.
- `get_favorite_skills`/`get_favorite_workflows`/router untouched: honored — no task modifies those methods, their tests, or `user_preferences_router.py`.
- No whole-suite quality-gate, manual/browser-verification, code-review, or commit-format task: honored — each task's own test step is the only validation; commits follow the repo's existing convention per the header note.
