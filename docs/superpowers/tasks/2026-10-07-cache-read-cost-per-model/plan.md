# Per-Model Cache-Read Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price a session's cache-read tokens per model (each model's own tokens at its own rate, summed) instead of at one "top model" rate, in both `get_sessions` and `get_session_detail`; show the most-expensive model as a session's display `model_name` when it used more than one.

**Architecture:** Two new PostgreSQL reader methods return per-`(session, model)` cache-read/cost rows (mirroring `get_model_breakdown`'s per-model GROUP BY, scoped like `get_session_cost_facts`/`get_session_detail_cost`) alongside the existing single-top-model queries, which are left untouched. The handler reuses its existing `_price_cache_reads` sum-per-model pattern against these new rows and adds a `_display_model` tie-break helper, both additive — `_TOP_MODEL`, `get_users`, and `_build_dispatches` are never touched.

**Tech Stack:** Python 3, FastAPI service layer, asyncpg/PostgreSQL, pytest + pytest-asyncio, `_SqlRecorder`/`AsyncMock` test doubles already in the suite.

**Spec:** No `spec.md` — requirements arrived as inline text, reproduced in the Acceptance Criteria below. Research: `docs/superpowers/tasks/2026-10-07-cache-read-cost-per-model/technical-analysis.md`.

## Global Constraints

- Do not modify `_TOP_MODEL`, `_GRAIN_CALLS`, `_cost_rows`, `get_users`, `get_session_cost_facts`, or `get_session_detail_cost` — only add new reader methods beside them.
- Do not touch `_build_dispatches` or `get_session_detail_dispatches` (dispatch-level pricing is out of scope).
- The pinned test `test_the_top_model_is_the_largest_row_of_the_original_cost_grain` (`codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py:370`) must keep passing unchanged.
- No frontend changes — `SessionModal.tsx` already renders whatever `model_name` the API returns; confirmed no other frontend file renders a single session's `model_name`.
- Commit per task using the repository's existing convention.

## Review Focus

- A session where cache-read tokens are concentrated on the *cheapest* model (today's bug) must now cost more, not the same — Task 4/5 tests assert the new total is strictly greater than `cache_read_cost(old_top_model, total_cache_read_tokens)`.
- A session with exactly one model must show byte-identical `bloat_pct`/`cache_read_cost_usd`/`model_name` before and after the change — regression case in Tasks 4 and 5.
- A model absent from pricing config must still be reported via `unpriced`, now per-model-rows-aware, not just for the one top model — Task 5.
- Two models tied on `cost_usd` must pick the display model deterministically (alphabetical), not by dict/row order — Task 3.
- `get_sessions`' sort/paging and `get_session_detail`'s `unpriced` return shape must stay unchanged for callers — Tasks 4 and 5 keep the existing tuple/dict shapes.

---

## Acceptance Criteria

- [ ] For a session with N distinct models, total cache-read cost = sum over each model of (that model's own cache_read tokens in the session) × (that model's own cache-read rate), verified to exceed today's single-cheapest-model total for live session `c9b8c2a5-268b-42de-adbd-92b17f1ec5f0` (haiku, claude-opus-5, claude-sonnet-5).
- [ ] Applies to both `get_session_detail` (session dialog) and `get_sessions` (`bloat_pct`).
- [ ] Session dialog header (and any other single-session model-name display) shows the most expensive model when a session used more than one model; single-model sessions unaffected.
- [ ] No change to `_TOP_MODEL`'s most-called-model selection or its other consumers (leaderboards, dead-session detection, user `top_model` stats).
- [ ] `_build_dispatches` / dispatch Timeline tab untouched.
- [ ] New test coverage for a multi-model session at both reader and handler level, including a case where most-expensive differs from most-called.
- [ ] `Test-first: yes` honored for every behavior-changing task.

---

### Task 1: Reader — per-session, per-model cache cost rows for the session list

**Files:**
- Modify: `codemie/src/codemie/repository/cli_analytics/ports.py` — add one method to `CliAnalyticsReader`, directly after `get_model_breakdown` (line 120).
- Modify: `codemie/src/codemie/repository/cli_analytics/postgres/reader.py` — add the implementation directly after `get_model_breakdown` (after line 229).
- Test: `codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: `async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows`, each row `{"session_id": str, "model_name": str, "cost_usd": float, "cache_read_tokens": int}`, one row per `(session_id, model_name)` pair active in `f`'s window. Task 4 groups these rows by `session_id`.

- [ ] **Step 1: Write the failing test** — add to `test_reader.py`:
```python
@pytest.mark.asyncio
async def test_session_model_costs_groups_by_session_and_model():
    sql, _ = await _sql_of("get_session_model_costs")
    assert "GROUP BY c.session_id, c.model_name" in sql
    assert "c.model_name <> ''" in sql
```
  This method name is new, so the test fails with `AttributeError`.
- [ ] **Step 2: Run it** — `pytest codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_session_model_costs_groups_by_session_and_model -v`; expect FAIL (no such method).
- [ ] **Step 3: Add the port method** — in `ports.py`, after `get_model_breakdown`:
```python
    async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows: ...
```
- [ ] **Step 4: Add the reader implementation** — in `reader.py`, after `get_model_breakdown` (mirrors it, adding `session_id` to the `SELECT`/`GROUP BY`, no `_TOP_MODEL`):
```python
    async def get_session_model_costs(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT c.session_id,
               c.model_name                      AS model_name,
               sum(c.cost_usd)                   AS cost_usd,
               sum(c.cache_read_tokens)::bigint   AS cache_read_tokens
        FROM cost_daily c
        WHERE true{self._scope(f, "c.session_id")}
        GROUP BY c.session_id, c.model_name
        HAVING c.model_name <> ''""",
            window_params(f),
        )
```
  This name does not start with `get_session_detail_`, so the existing generic test `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions` (`test_reader.py:222`) picks it up automatically and asserts the `sel`-scoping — no new scoping test needed.
- [ ] **Step 5: Run it** — same command; expect PASS.
- [ ] **Step 6: Run the full reader suite** — `pytest codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`; expect PASS, including the auto-parametrized scoping test for the new method and the unchanged `test_the_top_model_is_the_largest_row_of_the_original_cost_grain`.
- [ ] **Step 7: Commit.**

**Test-first: yes — `test_session_model_costs_groups_by_session_and_model` fails until the method exists.**

---

### Task 2: Reader — per-model cache cost rows for one session's detail

**Files:**
- Modify: `codemie/src/codemie/repository/cli_analytics/ports.py` — add one method directly after `get_session_detail_cost` (line 167).
- Modify: `codemie/src/codemie/repository/cli_analytics/postgres/reader.py` — add the implementation directly after `get_session_detail_cost` (after line 580).
- Test: `codemie/tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: `async def get_session_detail_model_costs(self, session_id: str) -> Rows`, each row `{"model_name": str, "cost_usd": float, "cache_read_tokens": int}`, one row per model used in that single session. Task 5 consumes this list directly.

- [ ] **Step 1: Write the failing test** — add to `test_reader.py`:
```python
@pytest.mark.asyncio
async def test_session_detail_model_costs_groups_by_model_for_one_session():
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)  # type: ignore[arg-type]
    await reader.get_session_detail_model_costs("s1")
    (statement,) = recorder.statements
    sql = " ".join(statement.split())
    assert "GROUP BY c.model_name" in sql
    assert "c.session_id = $1" in sql.replace("$sid", "$1") or "session_id =" in sql
```
- [ ] **Step 2: Run it** — expect FAIL (no such method).
- [ ] **Step 3: Add the port method** — in `ports.py`, after `get_session_detail_cost`:
```python
    async def get_session_detail_model_costs(self, session_id: str) -> Rows: ...
```
- [ ] **Step 4: Add the reader implementation** — in `reader.py`, after `get_session_detail_cost` (same table, grouped by model instead of collapsed by `_TOP_MODEL`):
```python
    async def get_session_detail_model_costs(self, session_id: str) -> Rows:
        return await self._q(
            f"""
        SELECT c.model_name                      AS model_name,
               sum(c.cost_usd)                    AS cost_usd,
               sum(c.cache_read_tokens)::bigint    AS cache_read_tokens
        FROM cost_daily c
        WHERE c.session_id = $sid::text
        GROUP BY c.model_name
        HAVING c.model_name <> ''""",
            {"sid": session_id},
        )
```
- [ ] **Step 5: Run it** — expect PASS.
- [ ] **Step 6: Run the full reader suite** — expect PASS (method name starts with `get_session_detail_`, so it is excluded from `_WINDOWED`/the scoping test, same as its sibling `get_session_detail_cost`).
- [ ] **Step 7: Commit.**

**Test-first: yes — `test_session_detail_model_costs_groups_by_model_for_one_session` fails until the method exists.**

---

### Task 3: Handler — `_display_model` helper (most-expensive-model tie-break)

**Files:**
- Modify: `codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py` — add directly after `_price_cache_reads` (after line 187).
- Test: `codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: rows shaped like Task 1/2's output (`model_name`, `cost_usd` keys).
- Produces: `LocalAnalyticsHandler._display_model(model_cost_rows: list[dict]) -> str`. Tasks 4 and 5 call this to pick the session's display model.

- [ ] **Step 1: Write the failing tests** — add to `test_cli_analytics_handler.py`:
```python
def test_display_model_picks_the_highest_cost_row():
    rows = [
        {"model_name": "claude-sonnet-5", "cost_usd": 0.12},
        {"model_name": "claude-opus-5", "cost_usd": 0.62},
        {"model_name": "claude-haiku", "cost_usd": 0.05},
    ]
    assert LocalAnalyticsHandler._display_model(rows) == "claude-opus-5"


def test_display_model_breaks_cost_ties_alphabetically():
    rows = [{"model_name": "zeta", "cost_usd": 1.0}, {"model_name": "alpha", "cost_usd": 1.0}]
    assert LocalAnalyticsHandler._display_model(rows) == "alpha"


def test_display_model_returns_the_only_model_unchanged():
    assert LocalAnalyticsHandler._display_model([{"model_name": "claude", "cost_usd": 0.3}]) == "claude"
```
- [ ] **Step 2: Run them** — expect FAIL (`AttributeError: _display_model`).
- [ ] **Step 3: Implement it** — in `cli_analytics_handler.py`, after `_price_cache_reads`:
```python
    @staticmethod
    def _display_model(model_cost_rows: list[dict]) -> str:
        """Model with the highest cost_usd among a session's per-model rows.

        Ties go to the alphabetically-first model_name — the same deterministic
        rule _TOP_MODEL uses for its own tie-break, just on cost instead of calls.
        A session with one row returns it unchanged.
        """
        priced = [(_f(r.get("cost_usd")), _s(r.get("model_name"))) for r in model_cost_rows if _s(r.get("model_name"))]
        if not priced:
            return ""
        return min(priced, key=lambda t: (-t[0], t[1]))[1]
```
- [ ] **Step 4: Run them** — expect PASS.
- [ ] **Step 5: Commit.**

**Test-first: yes — the three `_display_model` tests fail until the method exists.**

---

### Task 4: Handler — per-model cache-read sum and display model in `get_sessions`

**Files:**
- Modify: `codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py:527-576` (the `asyncio.gather` call and the per-row loop in `get_sessions`).
- Test: `codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: `self._repo.get_session_model_costs(f)` (Task 1), `self._price_cache_reads` (existing), `self._display_model` (Task 3).

- [ ] **Step 1: Write the failing test** — add to `test_cli_analytics_handler.py`:
```python
@pytest.mark.asyncio
async def test_get_sessions_prices_cache_reads_per_model_and_shows_priciest():
    cost_facts = [{**BASE_COST_ROW, "model_name": "claude-haiku", "cache_read_tokens": 100_000, "cost_usd": 0.8}]
    model_costs = [
        {"session_id": "s1", "model_name": "claude-haiku", "cost_usd": 0.1, "cache_read_tokens": 70_000},
        {"session_id": "s1", "model_name": "claude-opus-5", "cost_usd": 0.7, "cache_read_tokens": 30_000},
    ]
    repo = MagicMock()
    repo.get_session_cost_facts = AsyncMock(return_value=cost_facts)
    repo.get_turns_by_session = AsyncMock(return_value=[])
    repo.get_tool_success_by_session = AsyncMock(return_value=[])
    repo.get_lines_by_session = AsyncMock(return_value=[])
    repo.get_model_breakdown = AsyncMock(return_value=[])
    repo.get_session_model_costs = AsyncMock(return_value=model_costs)
    repo.get_skill_names_by_session = AsyncMock(return_value=[])
    handler = LocalAnalyticsHandler(repo)

    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]

    old_bloat = _pct(cache_read_cost("claude-haiku", 100_000), 0.8)
    assert session["bloat_pct"] != old_bloat
    assert session["model_name"] == "claude-opus-5"
```
  (`_pct` and `cache_read_cost` are already imported in the test module's target module; import both at the top of the test file if not already present.)
- [ ] **Step 2: Run it** — `pytest .../test_cli_analytics_handler.py::test_get_sessions_prices_cache_reads_per_model_and_shows_priciest -v`; expect FAIL (model-level pricing not wired yet).
- [ ] **Step 3: Wire it in** —
  - In the `asyncio.gather(...)` at line 527, add `self._repo.get_session_model_costs(f)` and capture it as `session_model_rows`.
  - After line 541 (`_cost, unpriced = self._price_cache_reads(model_rows)`), build `model_rows_by_session: dict[str, list[dict]] = {}` by appending each row of `session_model_rows` under its `session_id`.
  - Inside the per-row loop (replacing the single line at 576), compute `rows = model_rows_by_session.get(sid, [])`, then `crc = self._price_cache_reads(rows)[0] if rows else cache_read_cost(model, cache_read)`, and set `"bloat_pct": _pct(crc, cost)`.
  - Set `"model_name": self._display_model(rows) if rows else model` instead of the bare `model` at line 567.
- [ ] **Step 4: Run it** — expect PASS.
- [ ] **Step 5: Run the whole handler test file** — `pytest codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`; expect PASS, including the pre-existing single-model fixtures (`BASE_COST_ROW`'s `model_name: "claude"`), since `_display_model` on a single-row list returns that row's model unchanged and `_price_cache_reads` on that same row reproduces today's number.
- [ ] **Step 6: Commit.**

**Test-first: yes — `test_get_sessions_prices_cache_reads_per_model_and_shows_priciest` fails until `get_sessions` sums per model and overrides the display model.**

---

### Task 5: Handler — per-model cache-read sum and display model in `get_session_detail`

**Files:**
- Modify: `codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py:616-716` (`get_session_detail`).
- Test: `codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: `self._repo.get_session_detail_model_costs(session_id)` (Task 2), `self._price_cache_reads`, `self._display_model` (Task 3).

- [ ] **Step 1: Write the failing test** — add to `test_cli_analytics_handler.py`:
```python
@pytest.mark.asyncio
async def test_get_session_detail_prices_cache_reads_per_model_and_shows_priciest():
    repo = MagicMock()
    repo.get_session_detail_meta = AsyncMock(return_value=[{"session_id": "s1"}])
    repo.get_session_detail_cost = AsyncMock(
        return_value=[{"model_name": "claude-haiku", "cache_read_tokens": 100_000, "cost_usd": 0.8,
                        "input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0}]
    )
    repo.get_session_detail_model_costs = AsyncMock(
        return_value=[
            {"model_name": "claude-haiku", "cost_usd": 0.1, "cache_read_tokens": 70_000},
            {"model_name": "claude-opus-5", "cost_usd": 0.7, "cache_read_tokens": 30_000},
        ]
    )
    repo.get_session_detail_scalars = AsyncMock(return_value=[{}])
    repo.get_session_detail_tools = AsyncMock(return_value=[])
    repo.get_session_detail_events = AsyncMock(return_value=[])
    repo.get_session_detail_dispatches = AsyncMock(return_value=[])
    repo.get_skill_names_by_session = AsyncMock(return_value=[{"skill_names": []}])
    handler = LocalAnalyticsHandler(repo)

    detail, _ = await handler.get_session_detail("s1")

    old_crc = cache_read_cost("claude-haiku", 100_000)
    assert detail["cache_read_cost_usd"] != old_crc
    assert detail["model_name"] == "claude-opus-5"
```
- [ ] **Step 2: Run it** — expect FAIL.
- [ ] **Step 3: Wire it in** —
  - Add `self._repo.get_session_detail_model_costs(session_id)` to the `asyncio.gather(...)` at line 617, captured as `model_cost_rows`.
  - Replace lines 635-636 (`crc = cache_read_cost(model, cache_read)` and the `unpriced` list) with: `crc, unpriced = self._price_cache_reads(model_cost_rows) if model_cost_rows else (cache_read_cost(model, cache_read), [model] if (cache_read > 0 and model and lookup_cost_config(model) is None) else [])`.
  - Set `display_model = self._display_model(model_cost_rows) if model_cost_rows else model`; use `display_model` for `"model_name"` in the `detail` dict (line 694) instead of `model`.
  - Leave `"cache_read_cost_usd": crc` and `"bloat_pct": _pct(crc, cost_usd)` as-is — they already read the (now per-model-summed) `crc`.
- [ ] **Step 4: Run it** — expect PASS.
- [ ] **Step 5: Run the whole handler test file** — expect PASS, including existing single-model `get_session_detail` fixtures.
- [ ] **Step 6: Commit.**

**Test-first: yes — `test_get_session_detail_prices_cache_reads_per_model_and_shows_priciest` fails until `get_session_detail` sums per model and overrides the display model.**

---

### Task 6: Diagnostic — confirm the live verification session's three models price non-zero

**Files:**
- Test: `codemie/tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py`

**Interfaces:** none new — exercises the existing `cache_read_cost` directly against the real `llm-dial-config.yaml`/litellm fallback, no mocks.

- [ ] **Step 1: Write the diagnostic test** — add to `test_coding_agent_pricing.py`:
```python
@pytest.mark.parametrize(
    "model",
    ["anthropic.claude-haiku-4-5-20251001-v1:0", "claude-opus-5", "claude-sonnet-5"],
)
def test_cache_read_cost_resolves_for_verification_session_models(model):
    """Live-verified models from session c9b8c2a5-268b-42de-adbd-92b17f1ec5f0 (2026-10-07).

    If this fails for a model, Tasks 4/5's per-model sum for that model silently
    contributes 0.0 — not a Task 4/5 bug, but a pricing-config gap. `claude-opus-5`
    appears in neither config/llms/llm-dial-config.yaml nor litellm_config.yaml today,
    and the haiku deployment_name there (`us.anthropic.claude-haiku-4-5-20251001-v1:0`)
    does not match the bare id `normalize_model` produces, so failure here is expected
    going in, not a regression.
    """
    assert cache_read_cost(model, 1000) > 0.0
```
- [ ] **Step 2: Run it** — `pytest .../test_coding_agent_pricing.py::test_cache_read_cost_resolves_for_verification_session_models -v`.
- [ ] **Step 3: Act on the result, per model.** For each model that passes: leave it. For each model that fails: do **not** invent a cache-read rate — mark that model's parametrized case `pytest.mark.xfail(strict=True, reason="<name the exact config gap found in Step 2>")` instead of deleting or weakening the assertion, so the gap stays visible rather than silently passing. Note in the commit message which models passed and which are `xfail`, so a human can decide whether to add the missing `llm-dial-config.yaml` entries in a follow-up — that pricing-data change is out of this plan's scope.
- [ ] **Step 4: Run the file once more** — expect every case to either PASS or XFAIL (none plain FAIL, none silently skipped).
- [ ] **Step 5: Commit.**

**Test-first: yes — the parametrized assertions are written before deciding, per model, whether a config gap needs flagging.**

---

## Negative-Constraints Pass

- "Do NOT change `_TOP_MODEL`'s selection or touch `get_users`" → honored: Tasks 1-2 only add new reader methods; no task edits `_TOP_MODEL`, `_GRAIN_CALLS`, `_cost_rows`, or `get_users`.
- "Do NOT touch `_build_dispatches` / `get_session_detail_dispatches`" → honored: no task's file list includes either; Task 5's edit range (616-716) excludes `_build_dispatches` (719+).
- "Must NOT make the pinned test `test_the_top_model_is_the_largest_row_of_the_original_cost_grain` fail" → honored: Tasks 1-2 add methods beside, not inside, `get_session_cost_facts`/`get_session_detail_cost`; Task 1 Step 6 and Task 2 Step 6 both re-run the full reader suite to confirm.
- "Must NOT duplicate 'most expensive' selection logic in the frontend" → honored: no frontend files appear in any task's file list; Tasks 4-5 compute `_display_model` once, backend-side, and return it through the existing `model_name` field.
- "Display-model override must leave single-model sessions showing the same model as today" → honored: `_display_model` on a one-row list returns that row's model (Task 3's third test); Task 4 Step 5 and Task 5 Step 5 both re-run the existing single-model fixtures to confirm no regression.
