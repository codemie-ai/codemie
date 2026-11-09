# Technical Research

**Task**: named_first_prompt session_binding.py
**Generated**: 2026-11-04
**Research path**: filesystem (top-up, facts only)

---

## 1. Original Context

EPMCDME-16427: CLI Analytics Sessions list performance — move pagination/search/sort to DB level (select page session IDs first, then load details only for those), add index supporting session_dims.started_at window (safe migration, verify with EXPLAIN), reduce concurrent DB operations per Sessions request to avoid starving ingestion. Top-up research - record facts only, for the names the spec relies on that the analysis does not cover: named_first_prompt, session_binding.py. For each: where it is defined, what it does today on the path this task changes, and who consumes it.

---

## 2. Codebase Findings

### Existing Implementations

**`named_first_prompt`** (a local variable, not a module symbol)
- Built in `CliAnalyticsHandler.get_sessions`, `src/codemie/service/analytics/handlers/cli_analytics_handler.py:944-951`. It is built only when `not in_sql`. `in_sql = sort_by in SESSION_PAGE_SORTS and not framework` (`:905`), and `SESSION_PAGE_SORTS = ("start_time", "ctx_per_call")` (`repository/cli_analytics/ports.py:36`). So it is built for `sort_by="cost_usd"` or for any request with a `framework`.
- Content: `{session_id: strip_pasted_content(prompt)}` taken from `cost_facts` rows, **only for sessions whose `session_title` is non-blank** (after `.strip(BLANK_CHARS)`). Comment at `:945`: "A named session is also found by its first prompt; an unnamed one is served as that prompt already."
- Single consumer: the parameter of `_filter_sessions` (`:838-858`). Python search matches when the lower-cased term is a substring of any of these: `s["prompt"]` (the served prompt), `named_first_prompt[trace_id]`, `repository`, or `branch`. A `framework` filter is applied after that (`delivery_framework == framework`).
- The served `s["prompt"]` comes from `_served_prompt(title, first_prompt, repository, branch)` (`:142-148`). It returns the first non-empty value of: the title, `strip_pasted_content(first_prompt)`, the repository, the branch. So for a named session the Python path matches the **name and the cleaned first prompt**. For an unnamed session it matches the cleaned first prompt only.
- `strip_pasted_content` is in `src/codemie/repository/cli_analytics/vocabulary.py:190`. It removes pasted-content tags, keeps the enclosed text and trims the ends.
- Data source: `get_session_cost_facts` selects `max(s.first_prompt) AS prompt, max(s.session_title) AS session_title` (`postgres/reader.py:853-854`). Both come from `_sessions_cte` (`reader.py:414-415`, `coalesce(d.first_prompt,'')`, `coalesce(d.session_title,'')` from `session_dims d`).

**SQL search today (`get_session_page`, `reader.py:870-933`)**: the condition is `s.first_prompt ILIKE … OR s.repository ILIKE … OR s.branch ILIKE …` (`:893-896`). The term is lower-cased and LIKE-escaped, and a NUL short-circuits to `(0, [])`. It does **not** reference `session_title`. So on the in-SQL path (start_time / ctx_per_call, no framework) a named session is not found by its name. On the Python path (cost_usd or framework) it is. The SQL match runs on stored `first_prompt` as-is. It does not run `strip_pasted_content` (the comment at `:145` says older rows can still carry the markup).

**`session_binding.py`** (`src/codemie/repository/cli_analytics/postgres/session_binding.py`, 354 lines; EPMCDME-16440)
- Purpose (docstring `:15-21`): the first accepted evidence request of a session binds it (owner, destination) in `session_dims`. This happens in the same transaction and under the same per-session advisory lock as its rows. Every later write is checked against that binding. The module is pure functions plus SQL; the ingestor owns the transaction.
- SQL that touches `session_dims`:
  - `SESSION_LOCKS_SQL` (`:53`): `pg_advisory_xact_lock` per session key, sorted.
  - `BINDINGS_SQL` (`:58-68`): `unnest($1) LEFT JOIN session_dims d ON d.session_id = s`, plus `hook_events` subqueries for the legacy project and emails.
  - `_BIND_SQL` (`:72-78`): `INSERT INTO session_dims … ON CONFLICT (session_id) DO UPDATE … WHERE sd.owner_email IS NULL`.
  - `_TOUCH_BOUND_SQL` (`:79`): `UPDATE session_dims SET updated_at = now() WHERE session_id = ANY($1)`.
- Functions: `lock_and_read` (`:334-338`; takes the locks, then runs BINDINGS_SQL) and `bind` (`:341-354`; runs `_BIND_SQL` for `to_bind` and `_TOUCH_BOUND_SQL` for `bound`). Guard helpers: `check_boundary`, `check_session_count` (`MAX_SESSIONS_PER_REQUEST = 100`, `:140`), `check_owner`, `guard`, `validate_effective`, `stamped`, `hook_repo_remote`, `log_discard`. Also the `Sender` dataclass, the `SessionDiscard` exception and the `SESSION_DISCARDS` counter.
- It is not on the Sessions read path. It is on the **ingest write path** that shares `session_dims`.

### Other touched code
- Sole production importer: `postgres/ingestor.py:82` (it imports `RECORD_KINDS`, `SESSION_DISCARDS`, `Sender`, `SessionDiscard`, `batch_sessions`, `bind`, `check_*`, `guard`, `hook_repo_remote`, `lock_and_read`, `log_discard`, …).
- `Ingestor._store_once` (`ingestor.py:540-576`) calls these in order inside one `_transaction()`: `lock_and_read`, `check_owner`, optional `sender.resolve`, `guard`, `validate_effective`, `stamped`, `bind`. Ledger and row inserts follow.
- `_transaction()` (`ingestor.py:516-538`) takes a connection with `settings.ingest_acquire_timeout_s` and sets `SET LOCAL statement_timeout = ingest_statement_timeout_ms`. On a `TimeoutError` (no free connection, or a client-side statement timeout) and on `asyncpg.PostgresError`/`InterfaceError`/`OSError`, it raises `TelemetryStorageUnavailableError`, which surfaces as 503 (`ingestor.py:25,119,489`).
- Other `session_dims` writer: the rollups refresher (`postgres/rollups.py:828`, `:1974`, insert of `first_prompt`, `developer_name`, … ).

### Decisions Already in the Code
- Named-session search on the first prompt was a deliberate behaviour (comment `handler:945`), and it is pinned by a test (see Section 4). It is implemented only in Python.
- The Python path exists because "cost_usd sorts by catalog prices and framework filters by customer regex config: neither is expressible in SQL" (`handler:903-904`).
- Lock order is sorted, which prevents deadlocks (`session_binding.py:52`, `ingestor.py:580-582`).
- Binding is write-once: `WHERE sd.owner_email IS NULL` (`:78`).

### Task Statements Checked Against the Code
- Spec says the retired Python branch includes `named_first_prompt`: holds. It exists only in the `not in_sql` branch. Retiring it removes the name and first-prompt match for named sessions unless the SQL search adds `session_title`. Today's SQL search has no `session_title` term (`reader.py:893-896`).
- Spec risk bullet `session_binding.py:73-79` (the ingest binding blocked by a SHARE lock during a plain index build, answering 503): holds. Lines 72-79 are `_BIND_SQL` (INSERT/UPDATE on `session_dims`) and `_TOUCH_BOUND_SQL` (UPDATE). Both are writes that a SHARE lock blocks. A blocked statement ends in the ingest `statement_timeout`, which `_transaction` maps to `TelemetryStorageUnavailableError` (503). `BINDINGS_SQL` (SELECT) is not blocked by SHARE.

### Consumers of Changed Contracts
- `named_first_prompt` → `_filter_sessions` only. `_filter_sessions` is called only from `get_sessions:951`. Found by grepping `named_first_prompt|_filter_sessions` in `src` and `tests`.
- `session_binding` symbols → `ingestor.py` only in `src`. In tests: `test_session_count_cap.py`, `test_ingestor.py`, `test_session_binding_owner.py`, `test_live_session_binding.py`, and `tests/codemie/service/analytics/test_cli_analytics_session_resolution.py`. Matches for `session_binding_hash` in `enterprise/mcp_auth/*` are an unrelated MCP-auth concept.

### Architecture and Layers Affected
Service: `cli_analytics_handler.get_sessions/_filter_sessions`. Repository: `postgres/reader.get_session_page`, `postgres/session_binding` and `ingestor` (they share the `session_dims` table).

### Integration Points
`session_dims` is read by the Sessions reader and written by the ingest binding and the rollups refresher.

### Patterns and Conventions
Pure SQL constants plus small async functions taking an `asyncpg.Connection`. The ingestor owns the transactions.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/cli-analytics-storage.md` and `cli-analytics-events.md` cover the domain. They were not re-read in this top-up.

### Architectural Decisions
EPMCDME-16440 spec 6/6b/6c, cited in the `session_binding.py` docstring.

### Derived Conventions
Comments explain the intent at the call sites (`handler:903-904`, `:945`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:1783-1787` `_served_sessions` uses `sort_by="cost_usd"` (comment: "cost_usd is paged in Python"). `:1860-1883` `test_a_named_session_is_found_by_name_prompt_repository_and_branch` checks a match on the name ("release"), on the first prompt of a named session ("smoke"), on the repository and on the branch, and that "pasted_content" gives 0 matches. `:1886` checks that the markup name finds no unnamed session.
- `tests/codemie/repository/cli_analytics/postgres/test_live_session_page.py:181-201` checks SQL-vs-Python equivalence over search terms. The seed sets no `session_title`, so named sessions are not compared.
- Session binding: `test_live_session_binding.py`, `test_session_binding_owner.py`, `test_session_count_cap.py`, `test_ingestor.py`.

### Testing Framework and Patterns
pytest + pytest-asyncio. Handler tests use `MagicMock`/`AsyncMock` repos. `test_live_*` run against a live PostgreSQL.

### Coverage Gaps
- No test checks the SQL search against `session_title`, or against markup-carrying `first_prompt` rows.
- No test covers ingest binding while a lock is held on `session_dims` (index build).

---

## 5. Configuration and Environment

### Environment Variables
`ingest_acquire_timeout_s` and `ingest_statement_timeout_ms` (settings) bound how long a blocked binding write waits before a 503.

### Configuration Files
Not inspected in this top-up.

### Feature Flags and Deployment Concerns
None found for these names.

---

## 6. Risk Indicators

- The handler test at `:1860-1883` runs on the Python path (`cost_usd`). It checks that a named session is found by name and by first prompt. The SQL search (`reader.py:893-896`) matches neither `session_title` nor the cleaned prompt. Retiring `named_first_prompt` therefore changes search results for named sessions unless the SQL condition covers them.
- Speculative: the equivalence test `test_live_session_page.py` seeds no titles, so it would not catch that divergence.
- `_BIND_SQL`/`_TOUCH_BOUND_SQL` (`session_binding.py:72-79`) write to `session_dims` under per-session advisory locks inside the ingest transaction. A table lock that blocks writes holds those locks and a pool connection until `ingest_statement_timeout_ms`, and the request then gets a 503.

---

## 7. Summary for Complexity Assessment

`named_first_prompt` is a local dict in `CliAnalyticsHandler.get_sessions` (`cli_analytics_handler.py:944-951`). It is built only on the Python paging path (cost_usd sort or framework filter). It maps each named session to its paste-stripped first prompt, and its only consumer is `_filter_sessions`. That lets a named session be found by its name (through the served prompt) and by its first prompt. The SQL page query `get_session_page` (`reader.py:893-896`) searches only `first_prompt`, `repository` and `branch`. It does not search `session_title` and does not strip markup. So the two paths already differ for named sessions, and the test pinning that behaviour runs on the Python path only.

`session_binding.py` (`repository/cli_analytics/postgres/`) is the ingest-side session guard from EPMCDME-16440, imported only by `ingestor.py`. Inside the ingest transaction it takes sorted per-session advisory locks and reads the bindings from `session_dims`. It then runs `_BIND_SQL` (INSERT … ON CONFLICT DO UPDATE) or `_TOUCH_BOUND_SQL` (UPDATE) on `session_dims` (`:72-79`). It is not on the Sessions read path. It matters to this task as a `session_dims` writer that a write-blocking lock (such as a non-concurrent index build) would stall. The ingestor maps the resulting timeout or DB errors to a 503.

---

## 8. External References

None named by the task.
