# Technical Research

**Task**: cli-analytics session-title entrypoint served-prompt
**Generated**: 2026-11-16
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-17317 — CLI Analytics: Claude Desktop auto-generated chat name shown as session prompt instead of first user prompt, for Code tab and Cowork sessions.

Scope: backend fix in codemie-dev/codemie (this repo).

ROOT CAUSE: `_served_prompt` in src/codemie/service/analytics/handlers/cli_analytics_handler.py:142 returns `title or first_prompt or repository or branch`. Desktop auto-names every chat and sends that as `session_title`, which the backend treats as an explicit user rename (EPMCDME-16528, Viktoriya Ponkratina, commit 0693a18bc9) and always serves over the first prompt. This is wrong for Desktop-hosted sessions (Code tab and Cowork) but correct for a real `--name`/`/rename`.

FIX: make `_served_prompt` ignore `session_title` when the session's entrypoint indicates a Desktop-hosted session, falling through to first_prompt, then repository, then branch.

VERIFIED (prior investigation this session) entrypoint values, not yet confirmed against live session_dims rows for the two repro session ids below:
- Code tab (native Claude Code run inside Desktop): entrypoint is `claude-desktop` or `claude-desktop-3p`, sourced from CLAUDE_CODE_ENTRYPOINT in the CLI repo (codemie-code src/agents/plugins/claude-code-otlp/claude-code-otlp.plugin.ts:315).
- Cowork: entrypoint is `local-agent`, read directly from Desktop's own transcript line field (codemie-code src/telemetry/runtime/cowork/cowork-events.ts:213), NOT from CLAUDE_CODE_ENTRYPOINT. Confirmed by two independent test fixtures: codemie-code src/telemetry/runtime/cowork/__tests__/cowork-events.test.ts:31, and this backend's own commit fc043cc186 (branch origin/EPMCDME-17128_cowork-cli-analytics-tests, tests/codemie/repository/cli_analytics/support/cowork_session.py:71).
- So the condition must be `entrypoint in ('claude-desktop', 'claude-desktop-3p', 'local-agent')`.

EXACT CHANGES believed needed (verify every line number against current disk state — this is prior analysis, not a substitute for your own check):
1. src/codemie/service/analytics/handlers/cli_analytics_handler.py:142 `_served_prompt(title, first_prompt, repository, branch)` — add an `entrypoint` parameter; when it's one of the Desktop values, ignore title.
2. Three call sites in the same file: ~:843 inside `_session_row` (feeds `get_sessions`), ~:1073 inside `get_session_detail`, ~:1308 inside `get_efficiency` (worst_session_prompt). Each currently calls `_served_prompt(row/meta.get("session_title"), ...)` and needs entrypoint passed too.
3. Also ~handler.py:975 inside `get_sessions`: `named_first_prompt` keys off `_s(r.get("session_title")).strip(BLANK_CHARS)` for search-by-title fallback — needs the same Desktop-entrypoint exclusion.
4. Three reader.py queries must also select `entrypoint` alongside `session_title`:
   - src/codemie/repository/cli_analytics/postgres/reader.py ~:426, `_sessions_cte`'s `sel` CTE (`coalesce(d.session_title, '') AS session_title`) — add entrypoint from `d.entrypoint`.
   - reader.py ~:872, `get_session_cost_facts` (`max(s.session_title) AS session_title`) — add `max(s.entrypoint) AS entrypoint`.
   - reader.py ~:1051, `get_session_detail_meta` (`nullif(d.session_title, '') AS session_title`) — add `nullif(d.entrypoint, '') AS entrypoint`.
   - entrypoint already exists as a plain session_dims column, part of `_COMMON_TEXT` in rollups.py:813 — no schema change needed.
5. Keep session_title stored and served as its own field unchanged — only the prompt label changes.

KNOWN TRADE-OFF (ticket accepts this): a deliberate rename inside Desktop is indistinguishable from the auto-name and will also be hidden.

NOT IN SCOPE: the CLI-side change in codemie-code (cowork-events.ts:340) — a different repo, handled separately.

REPRO SESSIONS (from the ticket):
- Code tab: f17aca9c-8d00-43c8-8682-a14ccd196bda
- Cowork: c9edfb02-e43f-4ac1-8122-720c3d9c24c5
- Control: a native claude session without --name, and codemie-claude, must still show first prompt unchanged.

TESTS expected: unit for _served_prompt (Desktop entrypoints ignore title; native entrypoint with title still shows title; decide empty-entrypoint case); integration for sessions list / session detail / efficiency worst_session_prompt; regression for native claude/codemie-claude sessions.

DOCS likely needing a one-sentence update: .ai-run/guides/integration/cli-analytics-events.md (session_title common-field row) and .ai-run/guides/integration/cli-analytics-storage.md (latest session name rule).

Your job: verify all of the above against the current on-disk code (line numbers may have drifted), confirm the call graph and blast radius of `_served_prompt` and the three reader queries, identify any additional callers/tests of these functions not listed above, and check whether `entrypoint` is already exposed anywhere as a filterable/typed field (e.g. existing enums or constants for entrypoint values) that the fix should reuse rather than hardcoding three string literals in three places.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose: decide what label a session's prompt/name shows**
- `_served_prompt(title, first_prompt, repository, branch)` — `src/codemie/service/analytics/handlers/cli_analytics_handler.py:142`. Exact current signature and body confirmed on disk (no drift from the task's analysis): `return _s(title).strip(BLANK_CHARS) or strip_pasted_content(_s(first_prompt)) or _s(repository) or _s(branch)`. Takes no `entrypoint` today.
- Exactly three callers, no more, confirmed by codegraph's call graph for `_served_prompt`:
  - `_session_row` (static method, feeds `get_sessions`) — `cli_analytics_handler.py:843-845`, called with `row.get("session_title")`.
  - `get_session_detail` — `cli_analytics_handler.py:1073-1075`, called with `meta.get("session_title")`.
  - `get_efficiency` (worst_session_prompt) — inside the `cost_facts` loop starting at `cli_analytics_handler.py:1300`; the call itself is further down in the gap-elided region (not shown verbatim above, but `get_efficiency → _served_prompt` is listed in the call graph and `cost_facts` rows come from `get_session_cost_facts`, which already carries `session_title`).
  - No other caller of `_served_prompt` exists anywhere in the indexed codebase.

**Purpose: let a named session also be found by searching its first prompt**
- `named_first_prompt` dict comprehension inside `get_sessions` — `cli_analytics_handler.py:972-976`: `{_s(r.get("session_id")): strip_pasted_content(_s(r.get("prompt"))) for r in cost_facts if _s(r.get("session_title")).strip(BLANK_CHARS)}`. Line number matches the task's "~:975" claim (the dict's guard condition is on line 975). Consumer: `_filter_sessions` (`cli_analytics_handler.py:865-884`), which also matches the free-text `search` term against each session's already-served `prompt` (`s["prompt"]`), its `named_first_prompt` entry, repository and branch.

**Purpose: carry `session_title` from storage into the handler layer**
- `_sessions_cte`'s `sel` CTE — `reader.py:412-434`. Confirmed: `coalesce(d.session_title, '') AS session_title` is exactly line 426. No `entrypoint` column is selected here today — confirmed by a direct grep of the file (only one `session_title` hit per CTE, zero `entrypoint` hits anywhere in reader.py).
- `get_session_cost_facts` — `reader.py:841-886`. Confirmed: `max(s.session_title) AS session_title` is exactly line 872, reading `s` = the `sel` CTE above (same alias used for `s.repository`, `s.branch` two lines up). Because `s.entrypoint` does not exist in `sel` today, adding `max(s.entrypoint) AS entrypoint` here requires the `_sessions_cte` change above first — the two edits are coupled, not independent.
- `get_session_detail_meta` — `reader.py:1016-1071`. Confirmed: `nullif(d.session_title, '') AS session_title` is exactly line 1051, where `d` is `session_dims` joined directly (not through `sel`/`_sessions_cte`). This query is independent of the other two.
- All three reader methods return untyped `Rows` (`list[dict]`) per the `CliAnalyticsReader` Protocol (`src/codemie/repository/cli_analytics/ports.py:301-342`) — adding an `entrypoint` key to a row is not a Protocol signature change.

**Purpose: know what "entrypoint" values a session can carry**
- `entrypoint` is one of the latest-value text columns in `_COMMON_TEXT`, `rollups.py:813`: `("platform", "entrypoint", "client_version", "codemie_cli_version", "team", "environment", "space_id", "space_source", "session_title")`. It is filled from any event's `entrypoint` attrs field (the merge logic for these columns is `_session_dims_set`, `rollups.py:1378-1411`, generic `coalesce(EXCLUDED.column, sd.column)` — no Desktop-specific handling).
- `entrypoint` already exists as a real `session_dims` column (migration `a1c1a0000006_cli_analytics_schema_extension.py:173`, part of the same batch that added `session_title` support) — no schema change needed, confirmed.
- No enum, constant list, or typed vocabulary of entrypoint values exists anywhere in this repository (searched `entrypoint` across `src/`: only 7 files match, none defining a value set) — the three Desktop literals (`claude-desktop`, `claude-desktop-3p`, `local-agent`) would be introduced as new string literals wherever the fix lands; there is no existing constant to reuse.
- In the sibling CLI repo (codemie-code, read directly: `src/agents/plugins/claude-code-otlp/claude-code-otlp.plugin.ts:315` and `src/telemetry/runtime/cowork/cowork-events.ts:213`), neither literal `claude-desktop` nor `claude-desktop-3p` appears anywhere in that repo either — `CLAUDE_CODE_ENTRYPOINT` is an environment variable Anthropic's own Claude Code/Desktop binary sets, read but never defined by codemie-code. `local-agent` is defined only as a test-fixture literal (`cowork-events.test.ts:31`), not as a named constant.

**Purpose: exercise `_served_prompt` and its callers in tests**
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:1805-1879` (`_served_sessions` helper and ~10 parametrized tests) cover named/unnamed/blank-title/markup/repository-fallback cases for `_session_row` → `_served_prompt`, all via `BASE_COST_ROW` fixture rows. None of these rows set an `entrypoint` key.
- `tests/codemie/repository/cli_analytics/support/hook_event_builders.py:39` builds synthetic ingest events with a default `"entrypoint": "cli"` — confirms the ingest/rollup test fixtures already carry an entrypoint field end to end, but it is never a Desktop value in any existing fixture.

### Decisions Already in the Code

- `_served_prompt`'s precedence order (title, then first prompt, then repository, then branch) was itself a prior deliberate choice (EPMCDME-16528, commit 0693a18bc9) to treat any non-blank `session_title` as an explicit rename that always wins — the convention this task must partially override, not replace: the fallthrough chain (first_prompt → repository → branch) is unaffected by this task; only the *entry condition* onto that chain changes.
- The merge rule for every `_COMMON_TEXT` column including `entrypoint` is "last non-null wins" (`_session_dims_set`, `rollups.py:1411`): a session's `entrypoint` can in principle change value across its own events (e.g., a resumed session). No alternative (e.g., "first seen wins") is recorded or implemented for this column.
- `session_title` is already established as a column separate from the AI-generated summary `title` (documented explicitly in `cli-analytics-storage.md:155`: "separate from the summary's AI-generated title, which is stored but not served"). The task does not disturb this: it changes only which stored value the prompt label is built from, not what is stored.
- `BLANK_CHARS` (`vocabulary.py:162`) is the single shared definition of "blank" used for session_title, prompts, platform and harness name; `_served_prompt` already uses it via `_s(title).strip(BLANK_CHARS)`. Any new entrypoint comparison should match on the raw field (entrypoint values are not free text and have no established blank-trimming convention of their own).

### Task Statements Checked Against the Code

- "`_served_prompt` returns `title or first_prompt or repository or branch`" — **holds**, verbatim at `cli_analytics_handler.py:148`.
- "Desktop auto-names every chat and sends that as `session_title`, treated as an explicit rename" — **unverified**: nothing in this repo or in codemie-code stores or asserts what Claude Desktop itself sends; this is a claim about Anthropic's own client behavior. What would confirm it: the live `session_dims` rows for the two repro session ids, or a recorded OTel sample from Desktop.
- "entrypoint is `claude-desktop` or `claude-desktop-3p` ... sourced from CLAUDE_CODE_ENTRYPOINT ... (claude-code-otlp.plugin.ts:315)" — the *mechanism* holds exactly as described (confirmed verbatim: `entrypoint: process.env.CLAUDE_CODE_ENTRYPOINT ?? ''`), but the specific literal values `claude-desktop`/`claude-desktop-3p` are **unverified**: that env var's possible values are set by the Claude Code/Desktop binary itself, not by any string in codemie-code or this repo. Nothing in reach confirms them; the ticket's own text already flags this ("not yet confirmed against live session_dims rows").
- "Cowork: entrypoint is `local-agent`, read directly from Desktop's own transcript line field ... NOT from CLAUDE_CODE_ENTRYPOINT" — **holds**: confirmed verbatim at `cowork-events.ts:213` (`if (typeof line.entrypoint === 'string' ...) state.entrypoint = line.entrypoint`), and the literal `'local-agent'` is confirmed in the test fixture `cowork-events.test.ts:31` (`entrypoint: 'local-agent'`).
- "this backend's own commit fc043cc186 (branch origin/EPMCDME-17128_cowork-cli-analytics-tests, tests/codemie/repository/cli_analytics/support/cowork_session.py:71)" — **unverified** from the current checkout: that file does not exist on the current branch (it lives only on the named unmerged branch); it was not read as part of this research since it would require checking out or diffing a different ref.
- "Three call sites... ~:843, ~:1073, ~:1308" — **holds** for the first two exactly (843 and 1073 confirmed verbatim); the third (`get_efficiency`) call is inside a code region elided by exploration but `get_efficiency → _served_prompt` is confirmed present in the call graph, consuming `cost_facts` rows from `get_session_cost_facts`, so the mechanism holds even though the exact current line (near but not confirmed to be exactly 1308) was not read character-for-character.
- "~handler.py:975 ... `named_first_prompt` ... needs the same Desktop-entrypoint exclusion" — **holds**, line 975 confirmed exactly as the dict comprehension's guard line.
- "Three reader.py queries ... ~:426, ~:872, ~:1051" — **holds exactly** for all three line numbers, confirmed verbatim.
- "entrypoint already exists as a plain session_dims column ... no schema change needed" — **holds**, confirmed via `_COMMON_TEXT` (`rollups.py:813`) and the originating migration (`a1c1a0000006_cli_analytics_schema_extension.py:173`).
- "Keep session_title stored and served as its own field unchanged — only the prompt label changes" — **keeps an existing mechanism in place**: `session_title` is served as its own response field independently of `_served_prompt` in all three call sites' surrounding dict literals (e.g. there is no `"session_title"` key in the response payloads at all today — it already isn't served as a separate field; see `_session_row`'s return dict, `cli_analytics_handler.py:837-862`, which has no `session_title` key). This statement is consistent with current behavior but describes a non-change, not something currently exposed that must be protected from a new regression.
- "DOCS likely needing a one-sentence update: cli-analytics-events.md (session_title common-field row) and cli-analytics-storage.md (latest session name rule)" — **holds** as a description of where the relevant prose lives: `cli-analytics-events.md:132` and `cli-analytics-storage.md:155` are exactly the rows describing `session_title`'s storage semantics today, and neither currently mentions `_served_prompt`'s display precedence or any Desktop-specific exception.
- "no existing enum/constant for entrypoint values to reuse" (the open question in task_context) — **confirmed**: none found in this repo or in codemie-code; three string literals will be new wherever they are introduced.

### Consumers of Changed Contracts

- `_served_prompt`'s signature changes (new `entrypoint` parameter): all three callers found above must be updated in the same change (`_session_row`, `get_session_detail`, `get_efficiency`) — no caller outside this file exists.
- `_sessions_cte`'s `sel` CTE output columns: consumed by every method that builds its FROM/CTE on `self._sessions_cte(f)` — found by searching reader.py structurally: `get_session_cost_facts` (`s` alias, line ~865-872) and `get_session_page`/`get_repository_sessions`/other list-style queries that reference `sel`/`s.*` elsewhere in the file (not all enumerated here; any such query would silently gain nothing extra since SQL `SELECT *`-style column addition only affects queries that explicitly project the new column). Only `get_session_cost_facts` is in this task's scope; other `sel` consumers are unaffected unless they later choose to project `entrypoint` too.
- `get_session_cost_facts`, `get_session_detail_meta` row shape (adding an `entrypoint` key to each returned dict): consumed only by the three `_served_prompt` call sites above; no other code reads these two readers' rows by key in a way that would break from an added key (dict rows tolerate extra keys silently in Python).
- `named_first_prompt`'s dict (keyed by whether `session_title` is non-blank): consumed only by `_filter_sessions` (search-by-title fallback) in the same file. No other reader of this dict exists.
- `entrypoint` as a `session_dims`/`_COMMON_TEXT` column: searched for `entrypoint` across `src/` — besides the write path (`rollups.py`) and the (currently absent) read path in `reader.py`, no other code in this repo reads `session_dims.entrypoint`; this task would be the first reader of that column.

### Architecture and Layers Affected

- **Service layer** (business logic): `src/codemie/service/analytics/handlers/cli_analytics_handler.py` — `_served_prompt`, its three callers, and `named_first_prompt`/`_filter_sessions`.
- **Repository layer** (PostgreSQL CLI Analytics reader): `src/codemie/repository/cli_analytics/postgres/reader.py` — `_sessions_cte`, `get_session_cost_facts`, `get_session_detail_meta`. The `CliAnalyticsReader` Protocol (`ports.py`) needs no signature change (rows are untyped dicts).
- **Documentation layer**: `.ai-run/guides/integration/cli-analytics-events.md` and `cli-analytics-storage.md`.
- Not touched: ingestion (`ingestor.py`), rollups write path (`rollups.py`), schema/migrations, REST response models (`cli_analytics.py` models — no new field is added to any response; this is purely which stored value feeds the existing `prompt` field).

### Integration Points

- No external service calls are involved; this is entirely internal (PostgreSQL CLI Analytics storage → service handler → existing REST responses).
- Cross-repo dependency: the *meaning* of the entrypoint literals this fix matches on is defined by the CLI/Desktop side (codemie-code, read directly above) and by Anthropic's own Claude Code/Desktop binary (outside both repos, unverifiable from source).

### Patterns and Conventions

- Pure functions over raw dict rows (`_s`, `_i`, `_ni`, `_served_prompt`, `_routing`, etc.) are the established style for deriving display fields from reader rows in this handler — a Desktop-entrypoint check would follow the same shape (a small pure helper or an added parameter), not a class or registry.
- SQL columns added to a reader query are added as additional projected expressions alongside the existing ones in the same `SELECT`, following the `coalesce(..., '')` / `nullif(..., '')` idioms already used for `session_title` at each of the three sites.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-events.md:128,132,373,395` — documents `entrypoint` and `session_title` as common fields/`session_dims` columns, including the exact trim/200-char-cut rule for `session_title`. Says nothing today about display precedence or Desktop auto-naming.
- `.ai-run/guides/integration/cli-analytics-storage.md:73,155,214,297,313` — documents the `session_title` migration, the "latest session name" merge rule, and that it is "separate from the summary's AI-generated `title`, which is stored but not served." Says nothing today about `_served_prompt`'s precedence or any entrypoint-based exception.
- No guide documents `_served_prompt` or its precedence rule at all; that logic is undocumented outside the function's own docstring (`cli_analytics_handler.py:143-147`).

### Architectural Decisions

- The only recorded decision bearing directly on this behavior is referenced by ticket number only (EPMCDME-16528, commit `0693a18bc9`): session_title always wins over first_prompt. No ADR file exists in this repo for it; the decision lives in the function's docstring and git history.

### Derived Conventions

- None beyond what is captured under "Patterns and Conventions" above — the fix is a narrow, local extension of an existing pure-function convention.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:1805-1879` — direct coverage of `_served_prompt`'s existing precedence via `_session_row`/`get_sessions` (named, unnamed, blank title variants, markup cleaning, repository/branch fallback). None of these set `entrypoint`, so none currently exercise the behavior this task adds.
- Session detail and efficiency worst-session-prompt tests exist per the codegraph blast-radius listing (`test_efficiency_worst_session_prompt_is_the_session_title_when_named`, `test_session_detail_serves_the_first_prompt_without_markup_when_unnamed`, etc. — all in the same test file) but were not individually re-read; they follow the same `BASE_COST_ROW`-based pattern with no `entrypoint` key.
- `tests/codemie/repository/cli_analytics/support/hook_event_builders.py:39` already threads a default `"entrypoint": "cli"` through the ingest-test fixtures, so live/integration-style tests for the reader layer have a ready hook to set a Desktop entrypoint value on a synthetic session.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio` and `pytest.mark.parametrize`; handler tests stub the repository with `MagicMock`/`AsyncMock` (`_efficiency_handler`) or build cost-fact row dicts directly (`_served_sessions`/`BASE_COST_ROW`). Reader-level tests appear to use live-Postgres fixtures (`test_live_*` naming pattern seen throughout the repository test tree).

### Coverage Gaps

- No existing test sets `entrypoint` to a Desktop value (`claude-desktop`, `claude-desktop-3p`, `local-agent`) anywhere in the `_served_prompt` test suite — every case needed by the task (Desktop ignores title; native entrypoint with title still shows title; the empty/blank entrypoint case) is currently unexercised.
- `named_first_prompt`'s search-by-title fallback (`cli_analytics_handler.py:972-976`) has no test setting `entrypoint`, so there is no coverage today for whether a Desktop-named session should still be findable by its first prompt under search.
- `get_session_cost_facts` and `get_session_detail_meta` have no test asserting an `entrypoint` key is present in their returned rows (reasonable, since the column is not selected today).

---

## 5. Configuration and Environment

### Environment Variables

- None in this repo. `CLAUDE_CODE_ENTRYPOINT` is read client-side only, inside codemie-code (`claude-code-otlp.plugin.ts:315`), never referenced in this backend.

### Configuration Files

- None specific to this feature beyond the two guide files listed above; no feature flag gates `_served_prompt`'s behavior (the surrounding `cliAnalytics` feature flag gates the whole CLI Analytics surface, not this specific rule).

### Feature Flags and Deployment Concerns

- No new flag is implied by anything found; the existing `cliAnalytics` feature flag (`customer_config.is_feature_enabled("cliAnalytics")`, checked in `cli_analytics_jobs.py:89` and `rest_api/routers/cli_analytics.py:155`) is unrelated to this specific rule and would not need to change.

---

## 6. Risk Indicators

- The Desktop entrypoint literals (`claude-desktop`, `claude-desktop-3p`) are **unverified** against any source in either repo — they come only from the ticket's own prior investigation of live data (two named repro session ids), not from any committed constant. A wrong or incomplete literal set silently leaves some Desktop sessions showing the auto-name.
- No existing enum/constant for entrypoint values exists to reuse (confirmed); the fix necessarily introduces new string literals, and the task explicitly asks whether to centralize them — this is a design choice for the spec, not a research finding to resolve here.
- `_sessions_cte`'s `sel` CTE change and `get_session_cost_facts`'s SELECT-list change are coupled: adding `entrypoint` only to one of the two leaves the other reading a nonexistent `s.entrypoint` or silently missing the column — both edits must land together.
- `get_efficiency`'s exact `_served_prompt` call site line was not read verbatim (elided by exploration's size cap); only the call-graph edge was confirmed. Low risk since the function and its three-argument call shape are otherwise fully confirmed, but the exact current line number should be re-checked before editing.
- `cowork_session.py:71` on branch `origin/EPMCDME-17128_cowork-cli-analytics-tests` could not be read from the current checkout (does not exist on this branch) — the task's claim about what it contains is unverified from this research; confirming it would require diffing or checking out that branch.
- Existing `_served_prompt` test suite (10+ cases) has zero coverage of the new Desktop-entrypoint branch — every case the task's own "TESTS expected" section calls for is currently unexercised, consistent with this being new behavior rather than a regression fix.
- Task statement "keep session_title stored and served as its own field unchanged" keeps an existing mechanism in place: `session_title` is not exposed as its own response field in any of the three call sites' response dicts today (see Task Statements Checked Against the Code), so there is nothing currently served that this change could accidentally expose or hide further.

---

## 7. Summary for Complexity Assessment

This task touches two layers only: the service handler (`cli_analytics_handler.py`, three call sites plus one search-fallback dict plus the `_served_prompt` function itself) and the PostgreSQL reader (`reader.py`, two coupled SQL edits plus one independent one), with a one-sentence update to two existing guide files. No schema migration, no new feature flag, and no REST response model or OpenAPI change: `entrypoint` already exists as a `session_dims` column from a prior migration, and the change only alters which already-stored value feeds an existing `prompt` field — nothing new is exposed in any API response. All line numbers the task's prior analysis predicted were independently reread from current disk state and confirmed exact for the reader.py edits (426, 872, 1051) and for two of the three handler call sites (843, 1073); the third (`get_efficiency`) was confirmed present by call graph but not re-read character-for-character.

The main technical risk is not structural but factual: the three "Desktop" entrypoint literals this fix must match on are not verifiable from either repository's source — they are asserted from a prior, out-of-band investigation of live data for two specific repro sessions, and nothing in reach (this repo or the sibling codemie-code repo) defines or constrains what Claude Desktop's native client actually sends for `CLAUDE_CODE_ENTRYPOINT`. There is also no existing enum/constant to centralize these literals in, so the fix will introduce new string literals somewhere, which the spec/plan stage should decide where to place.

Test coverage posture: the existing `_served_prompt` test suite is thorough for the current precedence rule (named/unnamed/blank/markup/fallback-chain cases, ~10 parametrized tests) but has no case at all involving `entrypoint`, so every behavior this task adds is net-new coverage, not a modification of an existing assertion. Given the small, well-isolated blast radius (exactly 3 callers of `_served_prompt`, exactly 3 reader queries, no protocol/schema/API changes), this reads as a small, contained change whose complexity is almost entirely in correctly naming the Desktop entrypoint values, not in the code structure around them.

---

## 8. External References

- `codemie-code/src/agents/plugins/claude-code-otlp/claude-code-otlp.plugin.ts` (resolved at `C:/Users/Yauheni_Hil/repos/codemie-code/src/agents/plugins/claude-code-otlp/claude-code-otlp.plugin.ts`) — resolved. Line 315 confirmed verbatim: `entrypoint: process.env.CLAUDE_CODE_ENTRYPOINT ?? ''` inside `resolveAgentFields()` (line 312), alongside `platform: 'claude-code'` (line 314). Confirms the *mechanism* (entrypoint comes live from the `CLAUDE_CODE_ENTRYPOINT` env var for native Claude Code runs including inside Desktop); does not and cannot confirm the specific values (`claude-desktop`/`claude-desktop-3p`) that env var takes, since those come from Anthropic's own binary, not this file.
- `codemie-code/src/telemetry/runtime/cowork/cowork-events.ts` (resolved) — resolved. Line 213 confirmed verbatim: `if (typeof line.entrypoint === 'string' && line.entrypoint) state.entrypoint = line.entrypoint;` inside `freshLines()`, reading `entrypoint` directly off Desktop's own transcript line object, independent of `CLAUDE_CODE_ENTRYPOINT`.
- `codemie-code/src/telemetry/runtime/cowork/__tests__/cowork-events.test.ts` (resolved) — resolved. Line 31 confirmed verbatim: `entrypoint: 'local-agent'` inside the fixture `ENVELOPE` object, used as the test's canonical Cowork entrypoint value — this is the strongest available confirmation of the literal `'local-agent'`.
- `tests/codemie/repository/cli_analytics/support/cowork_session.py:71` on branch `origin/EPMCDME-17128_cowork-cli-analytics-tests` — **unresolved** from the current checkout: the file does not exist on the current branch (`EPMCDME-16713_lines-null-vs-zero` / `main`); it exists only on the named unmerged branch and was not read (would require checking out or diffing that ref, out of scope for this research pass). The task's claim about commit `fc043cc186` containing a matching test fixture is therefore unverified from this research, though independently corroborated by the codemie-code test fixture above.
