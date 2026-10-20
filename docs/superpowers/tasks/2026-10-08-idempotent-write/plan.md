# Idempotent write path (EPMCDME-15310) Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Re-PUT of an unchanged document leaves the stored entity unchanged; documented rewrites stay exact; no contract change.

**Architecture:** Characterization-first. Baseline tests pin current behaviour, an audit and red idempotency tests prove where a valid document is rewritten, and fixes land only there. Tests go through TestClient at the router boundary per `.ai-run/guides/testing/testing-api-patterns.md`.

**Tech Stack:** Python, FastAPI, pytest, SQLModel. Branch `EPMCDME-15310_idempotent-write` in `codemie`.

## Global Constraints

- Commit per task using the repository's existing convention (`.ai-run/guides/standards/git-workflow.md`). Ask the user before EVERY commit; nothing is committed without explicit approval. Never commit unrelated dirty files.
- New code: cyclomatic complexity <= 15 (Sonar).
- No change to status codes, response schemas, validation rules (AC6). No new routes. No change to `sanitize_for_save`, `_restore_existing_encrypted_values`, `SENSITIVE_VALUE_MASK`, encryption, timestamps, `last_reindex_date` semantics, or the assistant double write.
- "Unchanged" means every user-settable field equal, excluding `update_date`, `updated_date`, `last_reindex_date` and re-randomised ciphertext; no new version row (`AssistantVersionCompareService.has_configuration_changes`); guardrail assignment rows unchanged. Assert on Plain/Base64 encryption.
- No whole-suite gate, review or browser tasks (owned by the calling flow).

## Shared helper

Create `tests/codemie/rest_api/routers/idempotency_helpers.py` (Task 1) with `assert_stored_unchanged(before: dict, after: dict, ignore=("update_date","updated_date","last_reindex_date"))`. Later tasks import it.

---

### Task 1: Contract baseline characterization tests

**Files:**
- Create: `tests/codemie/rest_api/routers/test_contract_baseline.py`, `tests/codemie/rest_api/routers/idempotency_helpers.py`
- Read for shape: `src/codemie/rest_api/routers/index.py` (PUT routes at ~661 svn, 888 repo, 1427 google, 1583 git_faq, 1688 file, 1710 confluence, 1787 jira, 1885 xray, 1965 ADO wiki, 2077 xwiki, 2173 ADO work item, 2338 sharepoint, 2520 provider), `assistant.py` (create/update), `workflow.py:101-147`, `skill.py`, `category.py`, `project_settings.py`, `user_settings.py`

**Interfaces:** Produces `assert_stored_unchanged`.

Test-first: no — characterization tests that must PASS on current code; they are the AC6 safety net.

- [ ] Add one parametrized case per datasource PUT/POST route above plus one create/update case each for assistants, workflows, skills, categories, project and user settings. Each records status code, response keys, and one validation-failure case.
- [ ] Add silent-rewrite cases (AC4): assistant PUT drops a context entry absent from the project's `IndexInfo` with no error; a masked sensitive prompt variable is restored from stored value; workflow YAML is normalised (`workflow.py:101-147`); a test proving `sanitize_for_save` returns the servers unmutated and raises on violation.
- [ ] Run `pytest tests/codemie/rest_api/routers/test_contract_baseline.py -v`; all PASS on unmodified code.
- [ ] **Ask the user for approval, then commit (first commit point; must precede any src change).** The file stays unedited afterwards.

### Task 2: Per-route datasource PUT audit

**Files:** Read `src/codemie/rest_api/routers/index.py` PUT bodies and `models/index.py:887` (`update_index`).

Test-first: no — read-only audit, no code change.

- [ ] For each PUT route, note what a valid unchanged request rewrites beyond the excluded timestamps (reset `error`/`completed`/counters via `update_index` defaults, normalised fields, dropped optional fields). Record the result as a `# audit:` comment table at the top of the Task 5 test module (one line per route: rewrites / none).
- [ ] Also check `Context.index_info_type_from_index_type` (`models/assistant.py:128`) for index types falling back to `KNOWLEDGE_BASE`; list types where a valid entry would be dropped.
- [ ] If a route body is too large to judge, flag it in the Task 5 table as "needs test proof" rather than guessing.

### Task 3: AC1 idempotency tests for entity routers (expected red where rewritten)

**Files:** Create `tests/codemie/rest_api/routers/test_idempotent_write.py`.

Test-first: yes — failing test: PUT the same document twice and assert the stored entity after apply 2 equals apply 1 (assistant, workflow fixed point, skill, category, project/user settings SCHEDULER, LITE_LLM, GIT, WEBHOOK).

- [ ] One test per entity using `assert_stored_unchanged`; assistant tests also assert no new version row and unchanged guardrail rows; skill asserts no new version without content change; workflow asserts PUT2 equals PUT1 (hand-authored YAML may change on PUT1).
- [ ] Add assistant keep-case per datasource type: a valid context entry of each type survives PUT (`drop_missing_context`, `routers/assistant.py:2911`, `assistant_service.py:968`).
- [ ] Run; record which fail. Passing tests stay as regression guards.

### Task 4: AC2 mask tests

**Files:** Modify `tests/codemie/rest_api/routers/test_assistant.py` (existing mask test); add integration case to `test_idempotent_write.py`.

Test-first: yes — failing test: PUT a sensitive prompt variable as `SENSITIVE_VALUE_MASK` and assert the stored value is untouched; same for an integration credential sent as the mask (`service/settings/settings.py:461-539`).

- [ ] Extend the existing assistant mask test with a double-PUT assertion; add the integration credential case. Expected to pass if the audit is right; any failure goes to Task 6.

### Task 5: AC3 per-datasource-type idempotency tests

**Files:** Modify `tests/codemie/rest_api/routers/test_index.py`.

Test-first: yes — failing test: re-submit the same body through each type-specific PUT keyed by `(project_name, name)` and assert the stored `IndexInfo` unchanged apart from `update_date`/`last_reindex_date`.

- [ ] Parametrize over confluence, jira, xray, ADO wiki, ADO work item, xwiki, sharepoint, google, git_faq, file, provider, svn, code repo. Include the Task 2 audit table at the top. Mock external indexers only; keep DB/router real.
- [ ] Run; record failures for Task 6.

### Task 6: Fix proven rewrites only

**Files:** Modify only the source file(s) named by a red test from Tasks 3-5 (likely `routers/index.py`, `models/index.py`, `models/assistant.py`, or `service/settings/settings.py`).

Test-first: yes — failing test: the red test from Task 3/4/5 for that entity turns green with Task 1 still green.

- [ ] For each red test, change the minimal code so a valid unchanged document is not rewritten (1-2 sentence change per site, complexity <= 15). Do not touch the non-goal items.
- [ ] Re-run Task 1, 3, 4, 5 tests: baseline green and unedited, idempotency green.
- [ ] If nothing is red, no source change; note this in the digest. Ask the user before committing.

### Task 7: AC5 create-always-new

**Files:** Create `tests/codemie/rest_api/routers/test_create_always_new.py`.

Test-first: yes — failing test: POST an identical document twice with no id and assert two distinct ids / two stored rows (assistants, workflows, skills, categories, project settings).

- [ ] Implement per entity; expected green on current code. Ask the user before committing.

### Task 8: Document intentional rewrites

**Files:** Modify `.ai-run/guides/api/endpoint-conventions.md` (short section).

Test-first: no — documentation only.

- [ ] Document: invalid datasource context dropped silently; masked prompt variable restored; workflow YAML normalised (fixed point after first PUT); datasource PUTs keyed by `(project_name, name)` not id; known limitation on randomised ciphertext for plaintext resubmission. Ask the user before committing.

---

## Self-review

- Coverage: AC1 T3, AC2 T4, AC3 T5, AC4 T1, AC5 T7, AC6 T1 + Global Constraints; audit T2; fixes T6; docs T8.
- negative-constraints: honoured — no decrypt-and-compare, no write skipping, no new routes, no status/schema/validation change, no edits to `sanitize_for_save` / `_restore_existing_encrypted_values` / mask constant (Global Constraints, T6), no auto-commits (every task asks), no id-keyed datasource route (T5 keys by name), no identity resolution or bulk update (none planned).
