# Harness Display Name on CLI Analytics Session Detail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve and expose a humanized harness/client display name (e.g. "Claude Code", "Cursor") on the CLI Analytics `GET` session-detail response, for both the ClickHouse and PostgreSQL `CliAnalyticsReader` backends.

**Architecture:** Each reader's existing `get_session_detail_meta` query gains one more raw, non-humanized value — the session's earliest non-`'codemie-agent-hooks'` service name — returned under a new dict key `harness_service_name`. A single new helper in `vocabulary.py` humanizes that raw value once, centrally; the handler calls it when assembling the session-detail response and the API model exposes the result as `harness_name: str`. No schema change on either engine; no frontend change.

**Tech Stack:** Python 3, FastAPI/Pydantic models, `pytest`/`pytest-asyncio`; ClickHouse (`clickhouse-connect`-style query function) and PostgreSQL (`asyncpg`-shaped `engine.acquire()/fetch()`) readers behind the `CliAnalyticsReader` Protocol.

**Spec:** No `spec.md` exists for this task — the requirements arrived as inline text from the task's author, reproduced in the Acceptance Criteria section below. The already-completed Stage 1 research lives at `docs/superpowers/tasks/2026-10-02-harness-name-backend/technical-analysis.md`.

**Commits:** one commit per task, following this repository's existing commit-message convention (`.ai-run/guides/standards/git-workflow.md`) — not written out per-task in this plan.

---

## Acceptance Criteria

- [ ] `ClickHouseCliAnalyticsReader.get_session_detail_meta` returns the session's earliest raw service name that is not `'codemie-agent-hooks'` (empty/`None` when none exists) under the key `harness_service_name`, with a passing SQL-body test.
- [ ] `PostgresCliAnalyticsReader.get_session_detail_meta` returns the same shape (`harness_service_name` key, earliest resolvable `otel_resources.service_name` via `log_events`/`spans`, empty/`None` when none exists), with a passing SQL-body test.
- [ ] `vocabulary.py` exposes `HARNESS_LABEL_OVERRIDES` and `humanize_harness_name(raw)`, with `"claude-code" -> "Claude Code"` (override), at least one unmapped value proving the generic humanize path, and `None`/`""` -> `"Unknown"`, each covered by a unit test.
- [ ] `LocalAnalyticsHandler.get_session_detail` applies `humanize_harness_name` to `meta["harness_service_name"]` and assigns the result to `detail["harness_name"]`.
- [ ] `LocalAnalyticsSessionDetail` gains `harness_name: str`. `LocalAnalyticsSessionRow` (and therefore the session *list* endpoint, `get_sessions`) is **not** changed.
- [ ] `delivery_framework` behavior is untouched; no ClickHouse schema migration; no PostgreSQL alembic migration; no codemie-ui change.
- [ ] (Not a plan task — run by the calling flow after implementation) `make ruff` and `make test` pass.

**Negative constraints honored by this plan** (re-checked below in Self-Review):
- `harness_name` is never added to `LocalAnalyticsSessionRow` — Task 4 only.
- Humanization logic exists in exactly one place (`vocabulary.humanize_harness_name`) — Tasks 1–2 return only the raw value; no per-engine `CASE`/label SQL.
- `delivery_framework` is read but never modified by any task.
- No task touches `config/clickhouse/schema.sql` or adds an Alembic revision.
- No task touches codemie-ui or any frontend file.
- No task runs the whole quality-gate suite; each task's own tests are scoped to the file it changes.

## Global Constraints

- New reader dict key, same across both engines: `harness_service_name` (raw, e.g. `"claude-code"`, `"cursor"`, or `None`/`""`).
- New API field: `harness_name: str` on `LocalAnalyticsSessionDetail` only.
- Humanization rule (in `vocabulary.humanize_harness_name`): falsy raw -> `"Unknown"`; else `HARNESS_LABEL_OVERRIDES` lookup (keyed lower-case) -> else `raw.replace("-", " ").replace("_", " ").title()`.
- Port signature of `get_session_detail_meta(self, session_id: str) -> Rows` does not change — only the returned dict grows one key, on both readers in the same change (per `.ai-run/guides/integration/cli-analytics-storage.md`'s "add to the port and to both readers in the same change, with a test for each" rule).

## Review Focus

- A session whose only raw records are hook-originated (ClickHouse: all rows `ServiceName = 'codemie-agent-hooks'`; Postgres: no `log_events`/`spans` rows at all, e.g. hook-only session) must resolve to `harness_name == "Unknown"`, not crash or return `""`. Covered in Task 3 (`None`/`""` -> `"Unknown"`) and Task 4 (handler passes `meta.get("harness_service_name")`, which is `None`/missing in this case, straight into the helper).
- A raw service name present in neither the override map nor using `-`/`_` separators (e.g. `"codex"`) must still humanize sensibly (`"Codex"`), not fall through unchanged or raise. Covered in Task 3's generic-humanize test.
- A raw value mixing both separators (e.g. `"gemini_cli-beta"`) must have *both* replaced before title-casing (`"Gemini Cli Beta"`), not just one. Covered in Task 3.
- ClickHouse can return an empty string (`''`) for "no match" where Postgres's `LEFT JOIN` naturally returns SQL `NULL`; both must land on `"Unknown"` through the same helper, not diverge by engine. Covered by Task 1/Task 2 tests (each engine's own empty-case) plus Task 3's shared-helper test for both `None` and `""`.
- The session *list* endpoint (`get_sessions`) must keep behaving exactly as before — no new field, no new per-row query, `delivery_framework` unaffected. Covered by Task 4's test asserting `LocalAnalyticsSessionRow`'s fields are unchanged (existing `get_sessions` tests continue to pass unmodified, which Task 4's own test run confirms).

---

### Task 1: ClickHouse reader resolves `harness_service_name`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/clickhouse/reader.py:584-608` (`get_session_detail_meta`)
- Test: `tests/codemie/repository/cli_analytics/clickhouse/test_reader.py`

**Interfaces:**
- Consumes: nothing new — same `coding_agent_logs` raw table other session-detail queries already read directly (see `get_session_detail_tools`/`get_session_detail_events` in the same file for the pattern of scanning raw tables by `session_id` outside the `sel` CTE).
- Produces: `get_session_detail_meta(session_id: str) -> list[dict]` now additionally returns `harness_service_name: str | None` in its single-row result, consumed by Task 4's handler change.

**Test-first: yes — a new test asserting the rendered SQL text of `get_session_detail_meta` both selects `harness_service_name` and excludes `'codemie-agent-hooks'`, which fails today because neither appears in the query.**

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/clickhouse/test_reader.py`, alongside the existing `test_session_detail_meta_resolves_a_plugin_less_session`:

```python
@pytest.mark.asyncio
async def test_session_detail_meta_resolves_the_earliest_non_hook_service_name():
    reader, query = _reader([{"session_id": "s1", "harness_service_name": "claude-code"}])

    rows = await reader.get_session_detail_meta("s1")

    assert rows[0]["harness_service_name"] == "claude-code"
    sql = _sql(query)
    assert "harness_service_name" in sql
    assert "codemie-agent-hooks" in sql
    assert "ORDER BY" in sql
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/clickhouse/test_reader.py -k earliest_non_hook_service_name -v`
Expected: FAIL — `assert "harness_service_name" in sql` is false (the current query never selects that column).

- [ ] **Step 3: Implement the resolution query**

In `get_session_detail_meta` (`clickhouse/reader.py:584-608`), add one more joined subquery that scans `coding_agent_logs` for the given `{session_id:String}`, filters `ServiceName NOT IN ('', 'codemie-agent-hooks')`, orders by `Timestamp ASC`, takes the first row, and exposes it as `harness_service_name` — following the file's existing `CROSS JOIN (SELECT ... FROM codemie_analytics.coding_agent_cost_daily WHERE session_id = {session_id:String}) costs` pattern (same file, same method) for shape, and `LEFT JOIN` semantics (not `CROSS JOIN`) so a session with zero matching raw rows still returns one row with `harness_service_name = NULL`, not an empty result set. Add `nullIf(h.harness_service_name, '')  AS harness_service_name` to the outer `SELECT` list.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/clickhouse/test_reader.py -k earliest_non_hook_service_name -v`
Expected: PASS.

- [ ] **Step 5: Run the full reader test file and the port-contract test**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/clickhouse/test_reader.py -v`
Expected: all tests, including `test_reader_implements_the_reader_port` and `test_session_detail_meta_resolves_a_plugin_less_session`, still PASS (the signature did not change, only the returned dict's keys).

- [ ] **Step 6: Commit** (per the repository's existing commit convention)

---

### Task 2: PostgreSQL reader resolves `harness_service_name`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:508-524` (`get_session_detail_meta`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `otel_resources(resource_id, service_name)`, `log_events`/`spans` (`resource_id`, `ts`, `session_id`) — all already exist, no migration.
- Produces: `get_session_detail_meta(session_id: str) -> Rows` now additionally returns `harness_service_name: str | None`, same key as Task 1, consumed by Task 4.

**Test-first: yes — a new test asserting the rendered SQL of `get_session_detail_meta` joins `otel_resources` and selects `harness_service_name`, which fails today because the query never references that table.**

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/postgres/test_reader.py`:

```python
@pytest.mark.asyncio
async def test_session_detail_meta_resolves_the_earliest_service_name():
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)

    await reader.get_session_detail_meta("s1")

    sql = " ".join(recorder.statements[0].split())
    assert "otel_resources" in sql
    assert "harness_service_name" in sql
    assert "ORDER BY" in sql.upper()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k earliest_service_name -v`
Expected: FAIL — `"otel_resources" in sql` is false.

- [ ] **Step 3: Implement the resolution query**

In `get_session_detail_meta` (`postgres/reader.py:508-524`), add a CTE (e.g. `h`) that `UNION ALL`s `session_id`/`resource_id`/`ts` from `log_events` and `spans` filtered to `session_id = $sid::text`, joins `otel_resources o ON o.resource_id = h.resource_id`, orders by `h.ts ASC`, `LIMIT 1`, and selects `o.service_name`. No `'codemie-agent-hooks'` exclusion is needed here — `hook_events` has no `resource_id` column at all, so hook-originated rows structurally never appear in `log_events`/`spans`, and therefore never reach this join. Thread the result into the final `SELECT`'s column list as `nullif(h.service_name, '') AS harness_service_name`, `LEFT JOIN` so a session with no matching resource still returns one row with `harness_service_name = NULL`.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k earliest_service_name -v`
Expected: PASS.

- [ ] **Step 5: Run the full reader test file**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: all tests PASS, including `test_reader_implements_the_reader_port` and `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions` (unaffected — `get_session_detail_*` methods are excluded from that parametrization already).

- [ ] **Step 6: Commit**

---

### Task 3: `vocabulary.py` humanization helper

**Files:**
- Modify: `src/codemie/repository/cli_analytics/vocabulary.py`
- Test: `tests/codemie/repository/cli_analytics/test_vocabulary.py`

**Interfaces:**
- Consumes: nothing (pure function, no I/O).
- Produces: `HARNESS_LABEL_OVERRIDES: dict[str, str]` and `humanize_harness_name(raw: str | None) -> str`, both imported by Task 4's handler change (`from codemie.repository.cli_analytics.vocabulary import humanize_harness_name`).

**Test-first: yes — three new tests (override hit, generic humanize, missing-value fallback) that fail today with `AttributeError: module has no attribute 'humanize_harness_name'`.**

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/repository/cli_analytics/test_vocabulary.py`:

```python
def test_known_harness_uses_its_override_label():
    assert v.humanize_harness_name("claude-code") == "Claude Code"


def test_unmapped_harness_is_generically_humanized():
    assert v.humanize_harness_name("gemini_cli-beta") == "Gemini Cli Beta"


def test_missing_harness_is_unknown():
    assert v.humanize_harness_name(None) == "Unknown"
    assert v.humanize_harness_name("") == "Unknown"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py -k harness -v`
Expected: FAIL — `AttributeError: module 'codemie.repository.cli_analytics.vocabulary' has no attribute 'humanize_harness_name'`.

- [ ] **Step 3: Implement the override map and helper**

Append to `src/codemie/repository/cli_analytics/vocabulary.py`:

```python
# Raw ServiceName/service_name values (ClickHouse/Postgres) needing a specific display
# label, rather than the generic humanize fallback below.
HARNESS_LABEL_OVERRIDES: dict[str, str] = {
    "claude-code": "Claude Code",
}


def humanize_harness_name(raw: str | None) -> str:
    """Turn a raw harness/client service name into its session-detail display label."""
    if not raw:
        return "Unknown"
    override = HARNESS_LABEL_OVERRIDES.get(raw.lower())
    if override:
        return override
    return raw.replace("-", " ").replace("_", " ").title()
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py -v`
Expected: all tests, old and new, PASS.

- [ ] **Step 5: Commit**

---

### Task 4: Thread `harness_name` through the handler and API model

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:671-700` (`get_session_detail`)
- Modify: `src/codemie/rest_api/models/cli_analytics.py:239-247` (`LocalAnalyticsSessionDetail`)
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: `meta["harness_service_name"]` (Tasks 1–2) and `vocabulary.humanize_harness_name` (Task 3).
- Produces: `detail["harness_name"]` in the dict `get_session_detail` returns, validated by `LocalAnalyticsSessionDetail.harness_name: str`. Nothing downstream of this task depends on it within this plan.

**Test-first: yes — a new handler-level test asserting `detail["harness_name"]` is humanized from the mocked reader's raw value, and a second asserting the "Unknown" fallback when raw is missing; both fail today with `KeyError: 'harness_name'`.**

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`, next to the existing `make_handler`/`BASE_COST_ROW` fixtures (extend `make_handler` to also stub the session-detail reader methods it does not yet mock):

```python
def make_detail_handler(meta_row):
    repo = MagicMock()
    repo.get_session_detail_meta = AsyncMock(return_value=[meta_row])
    repo.get_session_detail_cost = AsyncMock(return_value=[{"model_name": "claude", "cost_usd": 1.0,
        "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0, "cache_creation_tokens": 0}])
    repo.get_session_detail_scalars = AsyncMock(return_value=[{"turns": 1, "tool_call_count": 0,
        "lines_added": 0, "lines_removed": 0, "active_ms": 0, "agent_count": 0, "skill_count": 0}])
    repo.get_session_detail_tools = AsyncMock(return_value=[])
    repo.get_session_detail_events = AsyncMock(return_value=[])
    repo.get_session_detail_dispatches = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[{"skill_names": []}])
    return LocalAnalyticsHandler(repo)


@pytest.mark.asyncio
async def test_session_detail_harness_name_is_humanized():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": "claude-code"})
    detail, _ = await handler.get_session_detail("s1")
    assert detail["harness_name"] == "Claude Code"


@pytest.mark.asyncio
async def test_session_detail_harness_name_unknown_when_unresolved():
    handler = make_detail_handler({"session_id": "s1", "harness_service_name": None})
    detail, _ = await handler.get_session_detail("s1")
    assert detail["harness_name"] == "Unknown"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k harness_name -v`
Expected: FAIL — `KeyError: 'harness_name'`.

- [ ] **Step 3: Implement the handler and model change**

In `cli_analytics_handler.py`, add `from codemie.repository.cli_analytics.vocabulary import humanize_harness_name` to the existing imports, then — right after the existing post-hoc line `detail["delivery_framework"] = classify_delivery_framework(skill_names)` (line 699) — add `detail["harness_name"] = humanize_harness_name(meta.get("harness_service_name"))`. In `rest_api/models/cli_analytics.py`, add `harness_name: str = Field(..., description="Resolved display label for the coding harness/client, e.g. 'Claude Code'.")` to `LocalAnalyticsSessionDetail` (not to `LocalAnalyticsSessionRow`).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`
Expected: all tests, old and new, PASS — including the pre-existing `get_sessions`-based tests, confirming `LocalAnalyticsSessionRow`/the list endpoint is unaffected.

- [ ] **Step 5: Commit**

---

## Self-Review

**1. Spec coverage.** Every Acceptance Criteria line above maps to a task: ClickHouse resolution -> Task 1; PostgreSQL resolution -> Task 2; humanization/override/"Unknown" -> Task 3; handler + model field, scoped to detail only -> Task 4. The quality-gate line is explicitly marked as the calling flow's job, not a plan task.

**2. Placeholder scan.** No "TBD"/"handle edge cases"/"similar to Task N" language; every step either names an exact file:line to modify with the one- or two-sentence change it makes, or shows the complete new/test code.

**3. Type consistency.** `harness_service_name` (raw, `str | None`) is the one dict key both readers produce (Tasks 1–2) and the one key the handler reads (Task 4). `humanize_harness_name(raw: str | None) -> str` (Task 3) is the one function the handler calls (Task 4) — no second implementation anywhere. `harness_name: str` is the one new model field (Task 4), never duplicated onto `LocalAnalyticsSessionRow`.

**4. Review Focus.** All five entries above (hook-only session, unmapped value, mixed separators, ClickHouse `''` vs. Postgres `NULL`, list endpoint untouched) have an owning task and an asserting test, as cross-referenced in each Review Focus bullet.

---

**Plan complete and saved to `docs/superpowers/tasks/2026-10-02-harness-name-backend/plan.md`.**
