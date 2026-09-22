# Technical Research

**Task**: rest_api security monitoring assistant-chat
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Add client-source attribution to the conversation_assistant_usage metric emitted by the assistant chat endpoint (POST /v1/assistants/{id}/model). Normalize the existing X-CodeMie-Client header into a closed set of three values: platform (default), teams, or other. Today attributes.codemie_client is written only for POST /v1/metrics, POST /v1/skills/events, and the LiteLLM passthrough proxy (proxy_router.py) — never for the assistant chat endpoint, which emits conversation_assistant_usage via ConversationMonitoringService.send_conversation_metric with no client field at all. Consequence: Teams bot traffic (once added) would be indistinguishable from web/desktop platform traffic in every analytics widget reading conversation_assistant_usage (summaries, users-spending, projects-spending, llms-usage, assistants-chats, users-activity). Detection must be header-only (no coupling to the Teams auth/impersonation layer, e.g. is_teams_bot_request() must NOT be used) — this was an explicit product decision. The propagation path needed: a request-scoped contextvar set once in middleware, snapshotted into handler instance state at construction time (because the streaming chat path loses contextvars set earlier — each generator iteration gets a fresh copy_context() snapshot), then threaded as an explicit parameter through save_chat_history -> ConversationService.upsert_chat_history -> ConversationMonitoringService.send_conversation_metric.

---

## 2. Codebase Findings

### Existing Implementations

**Critical finding: this task's implementation already exists on the current branch, committed at HEAD.**

The current branch is `EPMCDME-14793-teams-analytics`, and `git log -1` shows the tip commit is:

```
cb20fe61d EPMCDME-14793: Add client-source attribution to conversation_assistant_usage metric
```

Its own commit message: "Normalize X-CodeMie-Client header into platform/teams/other to distinguish MS Teams bot traffic from web/desktop in analytics. Implements client_context module for header parsing, integrates into conversation monitoring, and emits source value in usage metrics." Its diff stat: `assistant_handlers.py` (+5), `main.py` (+4/-1), `client_context.py` (new, 78 lines), `conversation_service.py` (+3), `conversation_monitoring_service.py` (+3), `metrics_constants.py` (+1). No test files were touched by this commit.

The full propagation path described in the task is present verbatim in the working tree:

- `src/codemie/rest_api/security/client_context.py` (new module) — defines `ClientSource(str, Enum)` with `PLATFORM = "platform"`, `TEAMS = "teams"`, `OTHER = "other"`; a module-level `ContextVar[ClientSource]` named `current_client_source` defaulting to `ClientSource.PLATFORM`; `normalize_client_source(raw: str | None) -> ClientSource` mapping `_TEAMS_CLIENT_VALUES = {"teams-bot", "teams", "msteams"}` and `_PLATFORM_CLIENT_VALUES = {"web", "webapp", "desktop", "ui"}` (case-insensitive, whitespace-stripped) to the enum, everything else (including CLI clients) falling into `OTHER`; and `set_client_source`, `get_client_source`, `clear_client_source` accessors, explicitly documented as "mirroring the pattern in `user_context.py`".
- `src/codemie/rest_api/main.py:1062` — inside the existing `configure_logging` HTTP middleware (registered via `@app.middleware("http")`), immediately after the existing `set_logging_info(...)` call: `set_client_source(normalize_client_source(request.headers.get(HEADER_CODEMIE_CLIENT)))`. This runs for every request, once, in middleware — matching the task's "contextvar set once in middleware" requirement.
- `src/codemie/rest_api/handlers/assistant_handlers.py:100-108` — `AssistantRequestHandler.__init__` snapshots the contextvar into instance state: `self.client_source = get_client_source()`, with an inline comment explaining why: "Snapshotted here (not read later at metric-emission time) because the streaming path loses contextvars set earlier — see save_chat_history."
- `src/codemie/rest_api/handlers/assistant_handlers.py:414-453` (`save_chat_history`) — passes `client_source=self.client_source` explicitly into `ConversationService.upsert_chat_history(...)`. An adjacent comment explains the same context-loss hazard for `set_llm_context` (anyio copies the async event-loop context per generator iteration in the streaming path), confirming the described root cause and establishing the precedent this fix follows.
- `src/codemie/service/conversation_service.py:234-308` (`ConversationService.upsert_chat_history`) — accepts `client_source: ClientSource | None = None` and forwards it unchanged into `ConversationMonitoringService.send_conversation_metric(..., client_source=client_source)`.
- `src/codemie/service/monitoring/conversation_monitoring_service.py:48-81` (`send_conversation_metric`) — accepts `client_source: ClientSource | None = None` and writes `MetricsAttributes.CLIENT_SOURCE: (client_source or ClientSource.PLATFORM).value` into the metric attributes dict, defaulting to `platform` when absent. The metric is emitted via `cls.send_count_metric(name=cls.CONVERSATION_BASE_METRIC + "_assistant_usage", ...)`, i.e. `conversation_assistant_usage`, matching the task's target metric exactly.
- `src/codemie/service/monitoring/metrics_constants.py:113-114` — `MetricsAttributes.CODEMIE_CLIENT = "codemie_client"` (pre-existing, used by `/v1/metrics`, `/v1/skills/events`, and `proxy_router.py`) sits directly above the newly added `MetricsAttributes.CLIENT_SOURCE = "client_source"` — a deliberately distinct attribute name from the legacy `codemie_client` field, not a reuse of it.

Pre-existing (unchanged by this feature) `codemie_client` usages, confirming the task's "today" baseline:
- `src/codemie/rest_api/routers/metrics.py:40,60-83` — `POST /v1/metrics` reads `X-CodeMie-Client` header, writes `attributes[MetricsAttributes.CODEMIE_CLIENT]`.
- `src/codemie/service/skill_event_service.py:112,134` — `POST /v1/skills/events` path, `client_type=x_codemie_client`.
- `src/codemie/enterprise/litellm/proxy_router.py:55,131,393` — LiteLLM passthrough proxy reads the header and writes `CLIENT_TYPE: headers.get(HEADER_CODEMIE_CLIENT, UNKNOWN)`.
- `src/codemie/service/analytics/handlers/cli/constants.py:50` and `cli/handler.py:397,417` — analytics CLI handler groups by `attributes.codemie_client.keyword`, the legacy raw-string field, unrelated to the new normalized `client_source`.

Teams auth/impersonation layer (explicitly excluded from this feature, confirmed not referenced by any of the above):
- `src/codemie/rest_api/security/teams_authentication_resolver.py:65` — `is_teams_bot_request(caller: User, raw_request: Request) -> bool`, a stateless predicate used only in `src/codemie/rest_api/security/authentication.py:144-149`. Grep across the whole `src/` tree shows zero references to `is_teams_bot_request` or `teams_authentication_resolver` from `client_context.py`, `main.py`, `assistant_handlers.py`, `conversation_service.py`, or `conversation_monitoring_service.py` — the header-only detection constraint is respected.

### Architecture and Layers Affected

- **Middleware / request lifecycle layer**: `configure_logging` in `main.py`, alongside existing `set_logging_info` — this is where header-derived, request-scoped state is conventionally established in this codebase.
- **Security/context layer**: new `client_context.py` module under `src/codemie/rest_api/security/`, following the sibling `user_context.py` pattern for ContextVar-based request-scoped state (not directly inspected in this pass, but cited by `client_context.py`'s own docstring as the pattern it mirrors).
- **Handler layer**: `AssistantRequestHandler` (ABC) in `assistant_handlers.py` — the abstract base class for assistant chat request handling; instance-state snapshotting happens in `__init__`, and propagation happens in `save_chat_history`.
- **Service layer**: `ConversationService.upsert_chat_history` (conversation_service.py) — orchestrates conversation persistence and triggers monitoring.
- **Monitoring layer**: `ConversationMonitoringService.send_conversation_metric` (conversation_monitoring_service.py), extending `BaseMonitoringService.send_count_metric`.
- **Metrics vocabulary layer**: `MetricsAttributes` enum in `metrics_constants.py`.

### Integration Points

- Internal: `client_context.get_client_source()` → `AssistantRequestHandler.__init__` → `save_chat_history` → `ConversationService.upsert_chat_history` → `ConversationMonitoringService.send_conversation_metric` → `BaseMonitoringService.send_count_metric` (OTel/metrics pipeline, not traced further in this pass).
- Analytics consumers of `conversation_assistant_usage` (read-side, unmodified by this feature): `src/codemie/service/analytics/handlers/summary_handler.py:133` (term filter on `metric_name.keyword`), `user_handler.py` and `project_handler.py` (grouped by `USER_ID_KEYWORD_FIELD` / `PROJECT_KEYWORD_FIELD` / `time`, no `client_source` grouping found), `assistant_handler.py` (grouped by `ASSISTANT_NAME_KEYWORD_FIELD` / `USER_ID_KEYWORD_FIELD`), `rest_api/routers/analytics.py:1198,1358` (users-activity, projects-activity endpoints), `leaderboard/collector.py:857` (term filter on the same metric name), and `dimension_queries.py:219,243` (raw SQL join against a `conversation_assistant_usage` table/view in the `codemie` schema, for the AI-adoption-framework dimension queries — a separate persistence path from the OTel metric emission).
- **None of the analytics read-side files above reference `MetricsAttributes.CLIENT_SOURCE`, `client_source`, or `ClientSource` anywhere.** The field is emitted but not yet consumed by any dashboard/query logic in this codebase.

### Patterns and Conventions

- Request-scoped state via module-level `ContextVar` with a default, paired with `set_*`/`get_*`/`clear_*` free functions — the same shape as the cited `user_context.py`.
- Snapshot-at-construction-time pattern for handler classes that later run inside a copy_context()'d generator: both `client_source` (this feature) and `set_llm_context` re-resolution in `save_chat_history` exist to work around the same anyio streaming-context-loss hazard; the inline comments in `assistant_handlers.py` cross-reference each other.
- Explicit parameter threading (not re-reading the ContextVar downstream) through service-layer calls — `client_source` is passed as an explicit keyword argument at every layer rather than re-fetched via `get_client_source()` inside `ConversationService` or `ConversationMonitoringService`.
- `MetricsAttributes` as a single flat enum of string constants shared across all monitoring services; new attributes are appended, not restructured.
- `BaseMonitoringService.send_count_metric(name=..., attributes=...)` as the common emission entry point across `ConversationMonitoringService`, and (per Step 2 grep) `hedging_monitoring_service.py`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/development/` contains `configuration-patterns.md`, `error-handling.md`, `local-testing.md`, `logging-patterns.md`, `performance-patterns.md`, `security-patterns.md`, `setup-guide.md`. None of these were grepped as containing `ContextVar`/`contextvar` matches for this domain in this pass — no guide was found describing the ContextVar-snapshot-at-construction pattern; it is documented only inline, via source comments, in `assistant_handlers.py` and `client_context.py`'s own docstring.

### Architectural Decisions

No ADR file was found for this feature. The product decision to keep detection header-only and independent from `is_teams_bot_request()` is recorded only in the task_context provided to this research, not (as far as this pass found) in a committed ADR or guide. The commit message for `cb20fe61d` records the "why" briefly but not the header-only constraint explicitly.

### Derived Conventions

Derived from code: normalize external free-text headers into closed enums at the boundary (middleware), store via ContextVar, and thread the resolved value explicitly through service calls rather than re-deriving it deeper in the stack — this mirrors how `user_context.py` (cited, not directly read) and `set_llm_context` are used elsewhere in `assistant_handlers.py`.

---

## 4. Testing Landscape

### Existing Coverage

None. `grep -rln "client_context\|ClientSource\|client_source"` across `tests/` returned zero matches. `find tests -iname '*client_context*' -o -iname '*conversation_monitoring*'` returned zero files. The `cb20fe61d` commit that introduced this feature touched no test files (confirmed via `git show --stat`).

### Testing Framework and Patterns

Not independently re-verified in this pass beyond the absence check above; the broader repo uses pytest per `pytest.ini` and `.ai-run/guides/testing/testing-patterns.md` (per AGENTS.md guide index).

### Coverage Gaps

- `normalize_client_source()` — no unit tests for any of the three branches (teams values, platform values, unrecognized/`other`), case-insensitivity, whitespace-stripping, or the `None`/empty-string default-to-platform paths.
- `set_client_source` / `get_client_source` / `clear_client_source` — no tests for the ContextVar plumbing itself (e.g. isolation between requests).
- `configure_logging` middleware — no test asserting the header is read and `set_client_source` is invoked with the normalized value for a live request.
- `AssistantRequestHandler.__init__` snapshot (`self.client_source = get_client_source()`) — no test confirming the snapshot happens at construction and survives into `save_chat_history` even when the ContextVar changes afterward (the exact regression this snapshot exists to prevent).
- `ConversationService.upsert_chat_history` — no test asserting `client_source` is forwarded to `send_conversation_metric` unchanged, or that the default (`None` → `platform`) path behaves correctly.
- `ConversationMonitoringService.send_conversation_metric` — no test asserting `MetricsAttributes.CLIENT_SOURCE` is present in the emitted attributes dict with the correct value, including the `client_source=None` → `"platform"` fallback.
- No test exists in `tests/codemie/rest_api/routers/test_assistant.py` (touched by other, unrelated commits per the earlier `git diff --stat`) or `tests/codemie/rest_api/handlers/test_assistant_handlers.py` covering the `X-CodeMie-Client` header end-to-end for the assistant chat endpoint.

---

## 5. Configuration and Environment

### Environment Variables

None found specific to this feature. `HEADER_CODEMIE_CLIENT = "X-CodeMie-Client"` in `src/codemie/core/constants.py:45` is a request-header name constant, not an environment variable.

### Configuration Files

None found governing this feature; behavior is entirely code-driven (header parsing + enum normalization), no config toggle located.

### Feature Flags and Deployment Concerns

No feature flag was found gating `client_source` emission — it is unconditional for every assistant-chat request via the middleware. No Dockerfile/CI reference to `X-CodeMie-Client` or `client_source` was found in this pass.

---

## 6. Risk Indicators

- **Zero test coverage** for every new component introduced by `cb20fe61d` (`client_context.py`, the middleware call, the handler snapshot, and the two threaded `client_source` parameters) — the highest-priority gap if any further work on this ticket is expected to include verification.
- Speculative: if the ticket's scope includes making the Teams/platform/other split visible in the analytics widgets named in the task (summaries, users-spending, projects-spending, llms-usage, assistants-chats, users-activity), that is a separate, currently-undone body of work — none of `summary_handler.py`, `user_handler.py`, `project_handler.py`, or `assistant_handler.py` group or filter by `client_source` today; the field is written but not read anywhere.
- The ContextVar-snapshot-at-construction pattern is subtle and undocumented outside inline code comments (no guide covers it); a reviewer or future maintainer unfamiliar with the anyio copy_context() streaming hazard could "simplify" the code by reading `get_client_source()` later at emission time and silently reintroduce the bug this fix works around.
- Two separate `conversation_assistant_usage` data paths exist: the OTel/metrics emission path (`ConversationMonitoringService.send_conversation_metric`) and a SQL-queryable `codemie.assistants` join referenced in `dimension_queries.py` for the "AI adoption framework." It is unverified in this pass whether the SQL-side table also needs a `client_source`/`codemie_client` column, or whether it is populated independently of the OTel metric — this is a candidate blind spot if downstream reporting relies on the SQL path rather than the OTel metric.
- The distinct-attribute-name decision (`client_source` vs. reusing `codemie_client`) means any consumer wanting a unified view of client provenance across `/v1/metrics`, `/v1/skills/events`, the LiteLLM proxy, and `conversation_assistant_usage` must reconcile two differently-shaped fields (`codemie_client` is free text; `client_source` is a closed three-value enum) — not necessarily a defect, but a documentation gap if no guide records the distinction.

---

## 7. Summary for Complexity Assessment

The task as described — add client-source attribution to `conversation_assistant_usage` via a header-normalized, middleware-set, construction-time-snapshotted, explicitly-threaded `ClientSource` value — is **already fully implemented** on the current branch (`EPMCDME-14793-teams-analytics`), committed at HEAD as `cb20fe61d`. Every mechanism named in the task_context (closed three-value enum, header-only detection with no coupling to `is_teams_bot_request()`, ContextVar set once in middleware, snapshot into handler instance state, explicit-parameter threading through `save_chat_history` → `upsert_chat_history` → `send_conversation_metric`) is present verbatim in six files: `client_context.py` (new), `main.py`, `assistant_handlers.py`, `conversation_service.py`, `conversation_monitoring_service.py`, and `metrics_constants.py`, matching the commit's own six-file diff stat exactly.

The layers touched are middleware/request-lifecycle, a new security/context module, the assistant-chat handler layer, the conversation service layer, and the monitoring layer — a narrow, well-contained vertical slice, and the implementation follows an existing precedent in the same file (`set_llm_context` re-resolution in `save_chat_history`) closely enough that the change required no new architectural pattern.

The primary open risk is not implementation correctness but verification and downstream consumption: there is zero test coverage for any of the six changed files or the new module, and none of the analytics read-side handlers (summaries, users-spending, projects-spending, llms-usage, assistants-chats, users-activity) currently reference the new `client_source` field — it is written but unread. If the actual remaining work under this ticket is "add tests" and/or "wire the new field into analytics widgets," that is a materially different and larger scope than the write-side plumbing already completed, and should be confirmed with the requester before planning proceeds.

---

## 8. External References

None named by the task. The task_context describes the required behavior and code path directly; it does not point to an external file, directory, or URL as a source of truth.
