# EPMCDME-17317 — Desktop-hosted sessions must not show their auto-generated chat name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `_served_prompt` ignores a session's `session_title` when the session's `entrypoint` shows it was hosted inside Claude Desktop (Code tab or Cowork), falling through to `first_prompt → repository → branch` unchanged; every other session keeps today's behavior exactly.

**Architecture:** One shared predicate decides title eligibility; `_served_prompt` and `named_first_prompt`'s search guard both call it instead of writing the Desktop-entrypoint check twice. `entrypoint` is threaded from three existing reader.py queries through three existing handler call sites — no new columns, no schema change, no new response field.

**Tech Stack:** Python, pytest (`pytest.mark.asyncio`, `pytest.mark.parametrize`), PostgreSQL (SQL built as f-strings, tested via `_SqlRecorder`).

**Spec:** `docs/superpowers/tasks/2026-11-16-epmcdme-17317-desktop-session-title/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Desktop entrypoints: exactly `{"claude-desktop", "claude-desktop-3p", "local-agent"}` (spec Approach step 1).
- Blank or missing `entrypoint` must behave exactly as today — title wins if non-blank (AC6); the exclusion never widens onto rows with no entrypoint evidence.
- `entrypoint` is compared by plain equality/membership, not through `BLANK_CHARS` trimming (spec Decisions, bullet 3).
- One shared predicate for title eligibility, used by both `_served_prompt` and `named_first_prompt`'s guard (arch-review plan_notes finding) — not two independently-written checks.
- No new REST/OpenAPI field, no schema migration, no new enum/shared-vocabulary module beyond the local constant in `cli_analytics_handler.py` (spec Non-goals).
- No change to the `entrypoint` "latest non-null wins" merge rule in `rollups.py` (spec Non-goals N6; carried as an open, accepted risk — AR-1).

## Review Focus

- A session whose `entrypoint` is a Desktop value but whose `session_title` is already blank must still fall through exactly as it does today (no new "double miss" branch) — covered in Task 1.
- A non-Desktop entrypoint value the fix has never seen before (e.g. `"cli"`, `"codemie-cli"`, a typo, future client) must keep title-wins behavior, since membership is a narrow allow-list, not a deny-list — covered in Task 1.
- `named_first_prompt`'s exclusion must key off the same session's `entrypoint`, not some other row's, when multiple cost-fact rows share a session id — covered in Task 2.
- `get_efficiency`'s `worst_session_prompt` must apply the exclusion per iterated row, not only to whichever row currently holds `worst_prompt` — covered in Task 2.
- Reader changes must not alter any existing projected column or row shape for callers that ignore the new key (dict rows tolerate an added key silently) — implicitly covered by Tasks 3-4's SQL-recorder assertions only adding, never replacing, substrings.

---

### Task 1: Shared title-eligibility predicate and `_served_prompt` extension

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:142-148`
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (new cases near line 1879)

**Interfaces:**
- Produces: `_DESKTOP_ENTRYPOINTS: frozenset[str]`; `_title_is_eligible(title: object, entrypoint: object) -> bool`; `_served_prompt(title, first_prompt, repository, branch, entrypoint=None) -> str` — `entrypoint` keyword defaults to `None` so Task 2's call sites can be updated independently without breaking this task's tests in the interim.

- [ ] **Step 1: Write the failing tests** — call `_served_prompt` directly (module-private import):
```python
@pytest.mark.parametrize("entrypoint", ["claude-desktop", "claude-desktop-3p", "local-agent"])
def test_a_desktop_entrypoint_ignores_the_title(entrypoint):
    assert _served_prompt("Auto Name", "first prompt", "repo", "branch", entrypoint) == "first prompt"

@pytest.mark.parametrize("entrypoint", ["cli", "codemie-cli", None, "", "   "])
def test_a_non_desktop_or_blank_entrypoint_keeps_the_title(entrypoint):
    assert _served_prompt("My Name", "first prompt", "repo", "branch", entrypoint) == "My Name"
```
- [ ] **Step 2: Run** `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k desktop_entrypoint -v` — expect FAIL (`TypeError: _served_prompt() takes 4 positional arguments`).
- [ ] **Step 3: Implement** `_DESKTOP_ENTRYPOINTS = frozenset({"claude-desktop", "claude-desktop-3p", "local-agent"})` next to `_served_prompt`; add `_title_is_eligible(title, entrypoint) -> bool` returning `entrypoint not in _DESKTOP_ENTRYPOINTS` (blank/missing `entrypoint` is never a member, so it returns `True` unchanged); rewrite `_served_prompt`'s body to use `title if _title_is_eligible(title, entrypoint) else None` in place of `title` before the existing `_s(...).strip(BLANK_CHARS) or ...` chain.
- [ ] **Step 4: Run** the same command — expect PASS, and run the full existing `_served_prompt`/`_served_sessions` suite (`-k served_sessions or served_prompt`) to confirm no regression.
- [ ] **Step 5: Commit**

Test-first: yes — failing unit test that a Desktop-entrypoint call to `_served_prompt` ignores a non-blank title and falls to `first_prompt`.

---

### Task 2: Thread `entrypoint` through the three callers and the search fallback

**Files:**
- Modify: `cli_analytics_handler.py:843-845` (`_session_row`), `:972-976` (`named_first_prompt`), `:1073-1075` (`get_session_detail`), `:1308-1310` (`get_efficiency`)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: Task 1's `_title_is_eligible(title, entrypoint)` and `_served_prompt(..., entrypoint=None)`.
- Produces: every caller now passes the session row/meta's `entrypoint` value through; no new response field is added (the response dicts still carry no `session_title`/`entrypoint` key, only `prompt`).

- [ ] **Step 1: Write the failing tests**, extending the `BASE_COST_ROW`/`_served_sessions` pattern (merge extra keys into `BASE_COST_ROW`, which has no `entrypoint` key today):
  - `test_a_code_tab_session_falls_back_to_its_first_prompt` — row `{"prompt": "Hey Desktop claude hey...", "session_title": "Hello world text and PDF", "entrypoint": "claude-desktop"}` via `_served_sessions`; assert `row["prompt"]` is the first prompt (AC1-shape).
  - `test_a_cowork_session_falls_back_to_its_first_prompt` — same shape with `entrypoint="local-agent"` (AC2-shape).
  - `test_a_codemie_claude_session_keeps_its_explicit_title` — `entrypoint="codemie-claude"` with a title set; assert the title is served (AC5).
  - `test_a_desktop_named_session_is_not_found_by_its_hidden_title` — via `_served_sessions(..., search=<title word>)`, a Desktop-entrypoint row with a title is absent from results (AC4), mirroring `test_a_search_for_the_markup_name_finds_no_unnamed_session_either`'s style.
  - `test_session_detail_ignores_a_desktop_titled_session` — via `make_detail_handler`, meta carries `"entrypoint": "claude-desktop"` and a title; assert `detail["prompt"]` is the first prompt (AC3).
  - `test_efficiency_worst_session_prompt_ignores_a_desktop_titled_session` — via `_efficiency_handler`, the single row carries a Desktop `entrypoint` and a title; assert `worst_session_prompt` is the first prompt, not the title (AC3).
- [ ] **Step 2: Run** the new tests — expect FAIL (rows still serve the title, since `entrypoint` isn't passed yet).
- [ ] **Step 3: Implement** — add the 5th positional/keyword argument `entrypoint=row.get("entrypoint")` (and `meta.get("entrypoint")`) to each of the three `_served_prompt` calls; change `named_first_prompt`'s guard condition from `_s(r.get("session_title")).strip(BLANK_CHARS)` to `_s(r.get("session_title")).strip(BLANK_CHARS) and _title_is_eligible(r.get("session_title"), r.get("entrypoint"))`.
- [ ] **Step 4: Run** the new tests plus the full `test_cli_analytics_handler.py` file — expect PASS, zero regressions.
- [ ] **Step 5: Commit**

Test-first: yes — failing tests for AC1/AC2/AC3/AC4/AC5 above, each failing until `entrypoint` is threaded through.

---

### Task 3: reader.py — `entrypoint` column in `_sessions_cte` and `get_session_cost_facts` (coupled)

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:426` (`_sessions_cte`'s `sel` CTE), `:872` (`get_session_cost_facts`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: `sel.entrypoint` (coalesced to `''`) available to every query built on `_sessions_cte`; `get_session_cost_facts`'s rows gain an `entrypoint` key (`max(s.entrypoint)`), consumed by Task 2's `_session_row`/`get_efficiency` once wired to the live reader in the running app (handler tests from Task 2 already pass with mocked rows; this task makes the real reader supply the same key).

- [ ] **Step 1: Write the failing test**, mirroring `test_the_selected_sessions_carry_the_session_title`:
```python
async def test_the_selected_sessions_carry_the_entrypoint():
    recorder = _SqlRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_cost_facts(_flt())
    sql = " ".join(recorder.statements[0].split())
    assert "coalesce(d.entrypoint, '') AS entrypoint" in sql
    assert "max(s.entrypoint) AS entrypoint" in sql
```
- [ ] **Step 2: Run** `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k entrypoint -v` — expect FAIL (substrings absent).
- [ ] **Step 3: Implement** — add `coalesce(d.entrypoint, '') AS entrypoint` as a new projected column in the `sel` CTE (line 426's `SELECT` list, alongside `session_title`), and `max(s.entrypoint) AS entrypoint` in `get_session_cost_facts`'s `SELECT` list (alongside `max(s.session_title) AS session_title`, line 872).
- [ ] **Step 4: Run** the same test — expect PASS; run the full `test_reader.py` to confirm no existing SQL-substring assertion broke.
- [ ] **Step 5: Commit**

Test-first: yes — failing SQL-recorder assertion that `get_session_cost_facts`'s generated SQL projects `entrypoint`.

---

### Task 4: reader.py — `entrypoint` column in `get_session_detail_meta` (independent)

**Files:**
- Modify: `reader.py:1051`
- Test: `test_reader.py`

**Interfaces:**
- Produces: `get_session_detail_meta`'s row gains an `entrypoint` key (`nullif(d.entrypoint, '') AS entrypoint`), the real-reader counterpart of Task 2's `meta.get("entrypoint")`.

- [ ] **Step 1: Write the failing test**, mirroring `test_session_detail_meta_reads_the_session_title_next_to_the_first_prompt`:
```python
async def test_session_detail_meta_reads_the_entrypoint_next_to_the_first_prompt():
    recorder = _SqlRecorder()
    await PostgresCliAnalyticsReader(recorder).get_session_detail_meta("s1", scope=DETAIL_SCOPE)
    sql = " ".join(recorder.statements[0].split())
    assert "nullif(d.entrypoint, '') AS entrypoint" in sql
```
- [ ] **Step 2: Run** — expect FAIL (substring absent).
- [ ] **Step 3: Implement** — add `nullif(d.entrypoint, '') AS entrypoint` to `get_session_detail_meta`'s `SELECT` list (alongside `nullif(d.session_title, '') AS session_title`, line 1051).
- [ ] **Step 4: Run** the same test plus the full `test_reader.py` — expect PASS, zero regressions.
- [ ] **Step 5: Commit**

Test-first: yes — failing SQL-recorder assertion that `get_session_detail_meta`'s generated SQL projects `entrypoint`.

---

### Task 5: Docs — one-sentence Desktop exception in both guides

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-events.md:132` (the `session_title` common-field row)
- Modify: `.ai-run/guides/integration/cli-analytics-storage.md:155` (the "latest session name" rule row)

**Interfaces:** None — prose only, no code or test consumes these files.

- [ ] **Step 1: Edit `cli-analytics-events.md:132`** — append one sentence to the `session_title` row noting that for a Desktop-hosted session (`entrypoint` one of `claude-desktop`, `claude-desktop-3p`, `local-agent`), `session_title` is still stored but is not used to build the served `prompt`.
- [ ] **Step 2: Edit `cli-analytics-storage.md:155`** — append the same exception to the "latest session name" row, stated against the storage/merge rule.
- [ ] **Step 3: Commit**

Test-first: no — documentation-only change, nothing to assert against.

---

## Self-Review

**Spec coverage:** AC1/AC2 → Task 2's Desktop/Cowork tests. AC3 → Task 2's `get_session_detail`/`get_efficiency` tests. AC4 → Task 2's search-exclusion test. AC5 → Task 2's `codemie-claude` control test. AC6 → Task 1's blank/missing-entrypoint test. AC7 → no task adds an `entrypoint` or `session_title` key to any response dict (verified against current code in Task 2's description). AC8 → Task 5.

**negative-constraints:** "No new REST/OpenAPI field" (spec Non-goals) — honored: Tasks 1-4 only add `entrypoint` as an internal parameter/row key consumed before the response dict is built; no task adds it to a returned dict. "No schema migration" — honored: no task touches `migrations/`. "No new enum/shared-vocabulary module" (spec Non-goals, Decisions bullet 1) — honored: Task 1's `_DESKTOP_ENTRYPOINTS`/`_title_is_eligible` live in `cli_analytics_handler.py`, not a new module. "entrypoint not BLANK_CHARS-trimmed" (spec Decisions bullet 3) — honored: `_title_is_eligible` uses membership, no `.strip()`. "must not widen onto rows with no entrypoint evidence" (spec Decisions bullet 2, AC6) — honored: `entrypoint=None` default and membership-only check mean an unknown entrypoint is never treated as Desktop. "No change to the entrypoint merge rule in rollups.py" (spec Non-goals N6) — honored: no task touches `rollups.py`.
