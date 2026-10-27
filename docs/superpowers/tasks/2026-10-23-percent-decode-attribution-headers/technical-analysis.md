# Technical Research

**Task**: cli-analytics attribution headers decode
**Generated**: 2026-10-23
**Research path**: filesystem

---

## 1. Original Context

Backend handoff: decode percent-encoded CLI attribution headers (EPMCDME-16260). The CLI always percent-encodes the values of X-CodeMie-Repository, X-CodeMie-Branch and X-CodeMie-Project, per '/'-separated segment with encodeURIComponent ('/' left as-is). The backend must decodeURIComponent-equivalent every value of these headers before storing or attributing (e.g. repository shows as clients/репо-тест in CLI Analytics instead of the percent-encoded form). Same applies to the metrics upload and skills-metrics endpoints, which carry the same headers. Old CLIs keep sending unencoded values, so the backend must handle both encoded and raw values during the transition (decode should be a no-op / fall back to raw value when the string isn't actually percent-encoded). Decode must not raise/fail on malformed escapes from older CLI versions (for example a raw '100%' containing a bare '%' not followed by two hex digits): catch the decode error and fall back to the raw value. This must cover every receiving path that reads these three headers: the CLI Analytics proxy/ingestion endpoint, the metrics upload endpoint, the skills-metrics endpoint, and the OTLP spool forwarder (event-hooks, logs, metrics, traces) if it also forwards/stores these headers. This is a wire-contract change to X-CodeMie-Repository / X-CodeMie-Branch / X-CodeMie-Project header values — security-sensitive, so identify anywhere these header values are logged, stored, or used in filtering/queries so review knows the blast radius.

---

## 2. Codebase Findings

### Existing Implementations

Header name constants (`src/codemie/core/constants.py`):
- `HEADER_CODEMIE_CLI_BRANCH = "X-CodeMie-Branch"`
- `HEADER_CODEMIE_CLI_REPOSITORY = "X-CodeMie-Repository"`
- `HEADER_CODEMIE_CLI_PROJECT = "X-CodeMie-Project"` (also aliased as `HEADER_CODEMIE_TAGGING_PROJECT`, same string, used for an *outbound* header the backend sets when calling the LLM — not an inbound CLI header, out of scope for decoding)

Confirmed places that read one or more of these three headers from an incoming request:

1. **LiteLLM proxy router** — `src/codemie/enterprise/litellm/proxy_router.py`
   - `_validate_project_header()` (~line 412): `headers.get(HEADER_CODEMIE_CLI_PROJECT)` — used only for presence/emptiness check before `require_valid_project()`.
   - `_extract_request_info()` (~line 453-477): reads all three raw —
     ```python
     BRANCH: headers.get(HEADER_CODEMIE_CLI_BRANCH, ""),
     REPOSITORY: headers.get(HEADER_CODEMIE_CLI_REPOSITORY, ""),
     PROJECT: project,  # from HEADER_CODEMIE_CLI_PROJECT or user.default_project or user.username
     ```
     This is the single natural decode point for the LLM-proxy path — decoding here propagates to every consumer of `request_info[BRANCH/REPOSITORY/PROJECT]`.
   - Called from the main proxy handler at line ~1829: `request_info = _extract_request_info(request.headers, user)`.
   - `PROXY_HOP_BY_HOP_HEADERS` (~line 118-143) strips all `HEADER_CODEMIE_*` headers (including these three, lower-cased) before forwarding to the upstream LLM provider — so decoding here never needs to re-encode for forwarding; the raw header never leaves this process.

2. **LLM proxy monitoring / metrics** — `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` (~line 494-496): consumes `request_info.get(BRANCH, "")` / `.get(REPOSITORY, "")` / `get_current_project(fallback=request_info.get(PROJECT))` and puts them into the metric `attributes` dict sent via OTel (`send_log_metric`/similar). **This is a logging/metrics sink** — whatever is in `request_info` at this point (encoded or decoded) is what ends up in OTel metric attributes.

3. **CLI Analytics router** — `src/codemie/rest_api/routers/cli_analytics.py`
   - `_project_gate()` (line 150-152): `request.headers.get(HEADER_CODEMIE_CLI_PROJECT)` passed straight into `build_ingest_gate()`. This is the only one of the three headers read directly in this router; Branch/Repository are **not** read here as headers — they arrive as typed *body* fields (`git_branch`, `repo_remote`, `cwd` in hook events / OTLP attributes per `.ai-run/guides/integration/cli-analytics-events.md`), which are a different wire contract and are **out of scope** for this header-decode task (confirm with the ticket owner if they are meant to be included).
   - Covers 4 ingest routes: `POST /v1/analytics/cli-analytics/{logs,metrics,traces,event-hooks}` — all funnel through `_project_gate(request)`.

4. **Project gate** — `src/codemie/service/analytics/cli_analytics_project_gate.py`
   - `_ProjectIngestGate.__call__()` (line 52-81) uses the raw `header_project` string for: blank check, case-insensitive conflict comparison against body-supplied project values (`project_context_conflict`), and `application_repository.aget_active_by_name_case_insensitive(session, project)` — a **DB lookup/query** keyed directly on this string. An undecoded value here would fail to match the stored (decoded) application name and incorrectly return `project_context_required`, discarding the whole batch (HTTP 200 per the "discard" contract) — a functional, not just display, bug.

5. **Metrics upload endpoint** — `src/codemie/rest_api/routers/metrics.py` (`POST /v1/metrics`, `send_metric`). Currently reads only `X-CodeMie-CLI`, `X-CodeMie-Client`, `X-CodeMie-Chrome-Extension` via `Header(alias=...)` params. **It does not currently read `X-CodeMie-Repository`/`X-CodeMie-Branch`/`X-CodeMie-Project` at all** — confirmed by grep across `src/`. If the CLI sends them here, the backend presently ignores them; no decode point exists yet in this file for these three headers.

6. **Skill events endpoint** — `src/codemie/rest_api/routers/skill_events.py` (`POST /v1/skills/events`, likely the "skills-metrics" endpoint the ticket refers to). Reads only `X-CodeMie-CLI`/`X-CodeMie-Client` as headers. `repository`/`branch`/`project` exist on `SkillEventRequest`/`SkillEvent` (`src/codemie/rest_api/models/skill_event.py`, lines ~108-110, ~180-182) but as **JSON body fields**, not headers. No header-based repository/branch/project reading exists in this router today.

7. **OTLP spool forwarder**: no distinct "forwarder" module was found under `src/codemie/repository/cli_analytics/postgres/otlp.py` or elsewhere that re-sends these three headers; the only header consumed on the OTLP ingest paths (`/logs`, `/metrics`, `/traces`) is `X-CodeMie-Project` via `_project_gate` (see #3/#4). Branch/Repository travel in the OTLP payload body (resource/record attributes), not headers, on these routes.

### Architecture and Layers Affected
- **API/router layer**: `proxy_router.py` (LLM proxy), `cli_analytics.py` (OTel ingestion), `metrics.py`, `skill_events.py` — these are the points where `Headers`/`Header(alias=...)` values first become Python strings.
- **Service layer**: `cli_analytics_project_gate.py` (DB-backed project lookup keyed on the header value), `llm_proxy_monitoring_service.py` (metrics/log attribution).
- **No existing dedicated header-decoding utility module** was found (`encode_header_value` in `src/codemie/core/routing_info.py` and `_encode` in `src/codemie/enterprise/litellm/litellm_custom_callbacks.py` are **encoders**, for the opposite/outbound direction — backend-to-CLI or backend-to-litellm header values — not decoders for inbound CLI headers). A new small decode helper (e.g. in `codemie/core/constants.py`'s neighborhood, or a new `codemie/core/header_decoding.py`) is the natural place to centralize this, reused by `proxy_router.py`, `cli_analytics.py`, and (if confirmed in scope) `metrics.py`/`skill_events.py`.

### Integration Points
- `request_info` dict (proxy_router) → `llm_proxy_monitoring_service.py` metric attributes → OTel exporter (external sink, outside this repo's control once emitted).
- `header_project` (cli_analytics) → `application_repository.aget_active_by_name_case_insensitive()` → Postgres query (`codemie_analytics`/application tables) — a decode bug here changes *query results*, not just display.
- CIMD/litellm client_id generation (`generate_litellm_headers_from_context`, recent commits 81615eede/8e34af8b2) also touches project/header handling in the same file family — worth a quick look during implementation to confirm it does not also need decoding, but no direct read of these three *incoming* headers was found there in this pass.

### Patterns and Conventions
- Existing outbound-encode pattern (`src/codemie/core/routing_info.py:63-69`, `src/codemie/enterprise/litellm/litellm_custom_callbacks.py:48-53`): percent-encode non-ASCII/unsafe header chars with `urllib.parse.quote(raw, safe=_HEADER_SAFE_CHARS)`, documented as "fully reversible via `urllib.parse.unquote`". The new decode helper is the mirror of this existing convention, but for a different direction and a different sender (the CLI, not this backend).
- `urllib.parse.unquote` (stdlib) already segment-decodes `%XX` and leaves non-percent text untouched; by default it does not raise on isolated `%` not followed by two hex digits (it leaves such sequences intact) — but passing `errors="strict"` can raise `UnicodeDecodeError` on a percent-decoded byte sequence that is not valid UTF-8, so whatever helper is written must catch exceptions explicitly, not merely rely on `unquote`'s default leniency, since a non-UTF-8 percent sequence is still a realistic malformed-escape case (not just a bare `%`).
- Existing regression precedent: `tests/enterprise/litellm/test_proxy_router.py::test_codemie_branch_header_with_unicode_is_stripped` (EPMCDME-12571) — a prior bug where a non-ASCII branch header broke httpx (header must stay latin-1); that is presumably *why* the CLI now percent-encodes these headers rather than sending raw UTF-8.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-events.md` — documents the CLI Analytics wire contract: field names, project-gate rules (`X-CodeMie-Project`, case-insensitive match, discard reasons `project_context_required`/`project_context_conflict`/`cli_analytics_disabled`), and that repository/branch travel as typed body fields (`repo_remote`, `git_branch`, `cwd`) on this set of routes, not headers.
- `.ai-run/guides/integration/cli-analytics-storage.md` — storage/recalculation side, not directly about header decoding but relevant for confirming what "attribution" means downstream.
- No guide file specifically documents the three `X-CodeMie-*` attribution headers' encoding contract; this ticket is introducing that contract into the guides as a byproduct.

### Architectural Decisions
- No ADR found for this specific header-encoding question. The `test_codemie_branch_header_with_unicode_is_stripped` test (EPMCDME-12571) is the closest recorded decision: non-ASCII header values are unsafe and must not reach outbound HTTP calls as raw UTF-8 — consistent with the CLI now percent-encoding instead.

### Derived Conventions
- Decode should be a small, pure, reusable function: `decode_attribution_header(raw: str) -> str` — `unquote` with a try/except around the whole call, falling back to `raw` on any exception, called once per header value as close to the `headers.get(...)` call as possible (in `_extract_request_info`, in `_project_gate`, and in any metrics/skill-events code path added for these headers), never re-applied a second time downstream.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_proxy_router.py` — extensive coverage of `_prepare_proxy_headers`, header stripping (`test_all_codemie_internal_headers_are_stripped`), and the unicode-branch regression test; good place to add encoded/raw/malformed-escape cases for `_extract_request_info`.
- `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py` and `test_cli_analytics_ingest.py` — cover the `X-CodeMie-Project` gate behavior (case, disabled project, conflict reasons) at lines ~125/413/482/490; natural place to add percent-encoded-project-header cases.
- `tests/codemie/core/test_dependencies_tagging_headers.py` — covers the *outbound* `X-CodeMie-Project` header set by `dependecies.py` (unrelated direction, do not confuse with this task).
- No tests found for `metrics.py` or `skill_events.py` touching these three headers (consistent with those endpoints not currently reading them).

### Testing Framework and Patterns
- `pytest`, `unittest.mock.patch`/`MagicMock`, Starlette `Headers(...)` fixtures constructing header dicts directly — same pattern usable for percent-encoded test values (encode with `urllib.parse.quote` in the test, or hardcode the `%xx` string).

### Coverage Gaps
- No test currently exercises a percent-encoded or malformed-escape value for any of the three headers anywhere in the repo.
- No test covers `metrics.py` or `skill_events.py` receiving these headers at all (because they don't read them yet) — if the ticket intends to add header-reading there, that is new behavior requiring new tests, not just a decode fix.
- No test covers the project-gate DB lookup with an encoded `X-CodeMie-Project` value failing to match a decoded stored project name (the functional-correctness risk noted in §2.4).

---

## 5. Configuration and Environment

### Environment Variables
None specific to header decoding found. `config.ANALYTICS_INGEST_MAX_BODY_BYTES` governs ingest body size, unrelated.

### Configuration Files
None relevant; this is pure request-parsing logic, not config-driven.

### Feature Flags and Deployment Concerns
- `customer_config.is_feature_enabled("cliAnalytics")` gates the analytics ingest routes but is unrelated to header decoding.
- No deployment manifest references these header names.

---

## 6. Risk Indicators

- **Scope ambiguity on "metrics upload and skills-metrics endpoints"**: `src/codemie/rest_api/routers/metrics.py` and `skill_events.py` do **not currently read** `X-CodeMie-Repository`/`X-CodeMie-Branch`/`X-CodeMie-Project` as headers at all (repository/branch/project exist only as body fields on skill events). If the task intends new header-reading behavior there, that's a larger change than "add decoding to an existing read" — confirm with the ticket before scoping the plan.
- **Functional correctness, not just display**: in `cli_analytics_project_gate.py`, an undecoded `X-CodeMie-Project` value is used directly in a case-insensitive DB lookup (`aget_active_by_name_case_insensitive`) and in a conflict comparison against body values. A missed decode point here silently discards whole batches (HTTP 200, per the documented discard contract) rather than just mis-displaying a name — harder to notice in production.
- **Decode-exception surface is stdlib-subtle**: `urllib.parse.unquote`'s default leniency handles a bare `%` without raising, but a percent-decoded sequence that is not valid UTF-8 can still raise under `errors="strict"`; the implementation must explicitly catch and fall back rather than assume `unquote` alone satisfies the "never raise" requirement in every branch.
- **Multiple independent read sites, no shared decode helper today**: `_extract_request_info` (proxy_router.py) and `_project_gate` (cli_analytics.py) are two separate call sites; a third/fourth may be needed in `metrics.py`/`skill_events.py` depending on the scope question above. Risk of inconsistent decoding (or double-decoding if a shared helper is called twice on the same value as it's threaded through `request_info`) if not centralized in one small utility.
- **Security/blast-radius surface for review**: decoded values flow into (a) `llm_proxy_monitoring_service.py` OTel metric attributes (external telemetry sink), (b) the `application_repository` DB query in the project gate, (c) presumably CLI Analytics storage tables (repository/branch columns reached via body fields, project via the gate's `canonical_project`). No SQL string-formatting of these values was found (parameterized queries via repository methods), which limits injection risk, but the values do reach OTel export and Postgres filtering — reviewers should check both.
- **No existing inbound-decode utility**: the two existing `quote`/`_encode` helpers in `routing_info.py` and `litellm_custom_callbacks.py` are encoders for the opposite direction; there is nothing to extend, a new function is needed, and nothing enforces it is applied at every header-read site (risk of a missed site, e.g. if a new attribution header read is added elsewhere in future).

---

## 7. Summary for Complexity Assessment

The change touches the API layer at 2-4 call sites (`_extract_request_info` in `proxy_router.py`, `_project_gate`'s `header_project` in `cli_analytics.py`/`cli_analytics_project_gate.py`, and possibly new reads in `metrics.py`/`skill_events.py` if those endpoints are meant to gain header-reading they don't have today) plus a new small, shared decode utility with no existing equivalent to extend (only outbound encoders exist as a style reference). The core logic itself is a few lines (`unquote` + try/except + fallback-to-raw), but correctness requires per-segment decoding only where the CLI actually percent-encodes (the ticket specifies decodeURIComponent-equivalent applied per '/'-separated segment, '/' left raw) and must be safe against both old unencoded CLIs and malformed escapes.

The main complexity driver is scope resolution, not code volume: the ticket names "metrics upload and skills-metrics endpoints" as carrying these headers, but neither currently reads them — so the plan must either confirm this is new header-reading (larger, with new tests) or that the ticket's description is imprecise and only the two confirmed paths (LLM proxy, CLI Analytics ingestion) are in scope. Test coverage for the target behavior (encoded/raw/malformed cases) is currently zero across all paths, and the project-gate DB lookup makes this a functional-correctness fix, not merely cosmetic, which raises the review bar given the explicit security-sensitivity flag in the ticket (OTel metric export and Postgres project-name lookups are the confirmed blast radius).

---

## 8. External References

None named by the task.
