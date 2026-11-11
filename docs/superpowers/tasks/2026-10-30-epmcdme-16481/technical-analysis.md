# Technical Research

**Task**: analytics assistants usage cost
**Generated**: 2026-10-30
**Research path**: filesystem

---

## 1. Original Context

Summary: Enable retrieval of analytics for individual assistant runs via the CodeMie API

h2. Summary
Determine whether CodeMie provides an API-supported way to retrieve usage statistics for individual assistant runs, rather than only aggregates by assistant and date range. Provide API for Digital Platform to obtain AI analytics of their Assistants/Workfllows.

h2. Description
The {{/code-assistant-api/v1/analytics/assistants-chats}} endpoint, queried with {{start_date}} and {{end_date}}, returns aggregate statistics for each assistant, including chat counts, total tokens, total cost, errors, and average duration. This makes it possible to see, for example, that an assistant had 10 chats consuming approximately 1.6 million tokens during a period, but the response does not identify the usage attributable to each specific run.

The requester needs to know whether individual-run statistics can already be obtained through a supported API. If they cannot, identify the gap and recommend a follow-up approach. Do not assume that a new endpoint is required before checking existing capabilities.

Related issue: EPMCDME-15658 (detailed analytics for assistant usages); this task focuses on per-run token and cost retrieval through the API.

h2. Preconditions
* The requester has access to the CodeMie analytics API and can query assistant-level aggregates for a date range.
* Assistant chats or runs exist within the selected period.

h2. Scenarios of Use
# The requester queries assistant usage for a date range and identifies an assistant with multiple chats.
# The requester needs to distinguish those individual executions and inspect the tokens and cost attributable to each one.
# The CodeMie team identifies a supported API and explains how to correlate its records with assistant runs; or, if no such API exists, documents the limitation and a proposed way to address it.

h2. Affected Areas
* CodeMie analytics API and its documentation
* Assistant-run identification and usage reporting

h2. Acceptance Criteria
# Confirm whether a supported API currently provides statistics for individual assistant runs or chats, and distinguish a chat from a run if they are not equivalent.
# If supported, provide the endpoint, required permissions, relevant filters or identifiers, and an example of how to retrieve and correlate individual records.
# State which per-run fields are available, specifically tokens, cost, execution time, and errors, and identify any unavailable fields.
# If unsupported, document the current limitation and recommend a scoped follow-up for exposing the required data.
# Explain how any individual records relate to the totals returned by {{assistants-chats}}, including known differences in aggregation or attribution.

---

## 2. Codebase Findings

### Existing Implementations

**How `assistants-chats` is computed (Elasticsearch, not PostgreSQL)**
- Route: `GET /v1/analytics/assistants-chats`, `src/codemie/rest_api/routers/analytics/usage.py:130`. Takes `AnalyticsQueryParams` (`time_period` | `start_date`+`end_date`, `users`, `projects`, `page`, `per_page` ≤1000) and `client_source`.
- Handler: `AssistantHandler.get_assistants_chats`, `src/codemie/service/analytics/handlers/assistant_handler.py:49`. It runs an ES|QL query over `codemie_metrics_logs*` with `metric_name == "conversation_assistant_usage"` and aggregates in two stages:
  1. `STATS ... BY assistant_name.keyword, conversation_id.keyword`: `messages_per_conv=COUNT(*)`, `SUM(money_spent)`, `SUM(input_tokens + output_tokens)`, `errors_per_conv=SUM(status != "success")`, `AVG(execution_time)`.
  2. `STATS ... BY assistant_name.keyword`: `total_conversations=COUNT(*)`, `total_messages`, `total_cost`, `total_tokens`, `avg_execution_time=AVG(per-conv avg)`, `failed_conversations`, `unique_users=COUNT_DISTINCT(user_name)`. Then `LIMIT 1000`.
- The output columns are aggregates only: `total_chats`, `total_messages`, min/median/avg/max msg per chat, `success_ops`, `total_errors`, `error_rate_percent`, `total_tokens`, `total_cost_usd`, `cost_per_op_usd`, `avg_time_seconds`, `last_error_timestamp`. The response contains no `conversation_id` or `request_id`.

**Where the per-run record comes from**
- `ConversationMonitoringService.send_conversation_metric`, `src/codemie/service/monitoring/conversation_monitoring_service.py:49`, emits one `conversation_assistant_usage` document per completed assistant turn. Attributes:
  - who: `user_id`, `user_name`, `user_email`, `project`
  - which assistant: `assistant_id`, `assistant_name`
  - tokens: `input_tokens`, `output_tokens`, `cache_read_input_tokens`
  - cost: `money_spent`, `cached_tokens_money_spent`
  - run: `execution_time`, `llm_model` (the routed model), `conversation_id`, `status`, `client_source`, optional `request_id`
  - always added: `count`, `budget_fallback_from`
  - There is no error message or class, only `status`.
- `send_log_metric` (`src/codemie/service/monitoring/base_monitoring_service.py:55`) writes the metric as a JSON log line. Shipping that log to ES happens outside this repo.
- Single call site: `ConversationService._handle_turn_completion`, `src/codemie/service/conversation_service.py:378`, reached from `upsert_chat_history`.
- `request_id` is the `X-Request-ID` request header when the caller sends one; otherwise a uuid4 (`src/codemie/rest_api/main.py:1444`).
- **Skip path:** `AssistantRequestHandler.save_chat_history` (`src/codemie/rest_api/handlers/assistant_handlers.py:431`) returns before `upsert_chat_history` when `request.save_history` is False. Virtual assistants force `save_history=False` (`routers/assistant/execution.py:133`, `routers/assistant/_ask.py:213`). On that path neither the usage metric nor the history appears to be written.

**Per-message records in PostgreSQL**
- `Conversation.history: List[GeneratedMessage]` (`src/codemie/rest_api/models/conversation.py:248`).
  - The assistant message per turn stores `input_tokens`, `output_tokens`, `money_spent`, `response_time` (None while `in_progress`), `status`, `date`, `assistant_id` and `history_index` (`_build_chat_history_messages`, line 395).
  - `cache_creation/read_input_tokens` are declared on `GeneratedMessage`, but `ChatTurnData` does not carry them.
  - The message does not store `request_id`.
- `ConversationMetrics` table `conversation_metrics` (line 196): lifetime totals per conversation (tokens, money, avg response time, message count). It has no assistant column and no time bucketing.

**Workflows (have a per-run record and API)**
- `WorkflowMonitoringService.send_workflow_execution_metric` (`src/codemie/service/monitoring/workflow_monitoring_service.py:33`) emits `workflow_execution_total`:
  - When a request summary exists, it emits one document per LLM call, carrying `execution_id`, `request_id`, `llm_model`, the tokens, `money_spent`, `status`, and `execution_time`. `execution_time` is the whole execution's duration, repeated on every document.
  - Otherwise it emits one aggregated document per execution.
- `WorkflowHandler.get_workflows` (`handlers/workflow_handler.py:43`) groups by `workflow_name`. It counts runs as `cardinality(execution_id)`, which uses HyperLogLog and is approximate above 3000. It computes `avg_time` as an average over documents.
- `WorkflowExecution` table (`src/codemie/core/workflow_models/workflow_execution.py:149`): `execution_id`, `overall_status`, `tokens_usage: TokensUsage`, `date`, `update_date`, `conversation_id`, `created_by`, `project`.

### Architecture and Layers Affected
- API: `rest_api/routers/analytics/usage.py`, `routers/assistant/execution.py`, `routers/conversation/retrieval.py`, `routers/workflow_executions.py`.
- Service: `service/analytics/analytics_service.py` → `handlers/assistant_handler.py`, `handlers/workflow_handler.py`. Pipeline: `query_pipeline.py` (`execute_esql_query`, `execute_tabular_query`), `access_filter.py`.
- Monitoring/emission: `service/monitoring/conversation_monitoring_service.py`, `workflow_monitoring_service.py`, `run_ids.py`.
- Data: Elasticsearch `codemie_metrics_logs*` via `MetricsElasticRepository`. PostgreSQL tables `conversations`, `conversation_metrics`, `workflow_executions`.

### Integration Points
Existing APIs that return individual records:

| Endpoint | Granularity | Fields | Permission |
|---|---|---|---|
| `GET /v1/conversations/{id}` (`retrieval.py:228`) | per message (history) | tokens, money_spent, response_time, status, date | Ability `Conversation` READ = OWNED_BY, SHARED_WITH only (no admin/project-admin) (`core/ability.py`) |
| `GET /v1/conversations` (`crud.py:75`) | list of caller's own conversations | no token or cost fields | authenticated user, own only |
| `GET /v1/admin/conversations` (`history.py:157`) | cross-user list (`ConversationListItem`) | no token or cost fields | `admin_access_only` |
| `GET /v1/assistants/metrics/{conversation_id}` (`execution.py:425`) | per conversation totals | input/output tokens, money, classifier cost | **no auth dependency and no user param on this branch** (assistant router `dependencies=[]`) |
| `GET /v1/workflows/{wf}/executions` (`workflow_executions.py:174`) | per execution, paginated | as WorkflowExecution | `Ability.list(workflow)`; non-admin sees own, plus the project's when application admin and `filter_by_project` |
| `GET /v1/workflows/{wf}/executions/{exec}` (`:211`) | per execution | `tokens_usage`, `overall_status`, `date`/`update_date` (duration derivable) | WorkflowExecution READ = shared/owned/managed/admin |
| `GET /v1/analytics/routing/sessions` | per-session tabular rows | routing only | analytics access filter (precedent for row-level analytics) |
| `POST /v1/assistants/{id}/model` response `BaseModelResponse` (`core/models.py:857`) | the run itself | `time_elapsed`, `tokens_used` (single int), `success`, `agent_error`, `tool_errors`; no cost | caller |

- Analytics access model (`access_filter.py`): a plain user sees only their own documents in their projects, a project admin sees every user's documents in that project, and a super admin sees everything.
- The Python SDK (`../codemie-sdk/sdk/codemie-python/src/codemie_sdk/services/analytics.py`) has `get_assistants_chats` and `get_workflows`. `conversation.py` has `get_conversation`, and there is a `workflow_execution.py` service. The SDK has no wrapper for `/assistants/metrics/{id}`. The Node SDK was not checked.

### Patterns and Conventions
- Analytics handler pattern: a handler class wrapping `AnalyticsQueryPipeline`; `AnalyticsQueryFilters`; either ES|QL with a `result_parser` and `columns`, or a terms aggregation via `AggregationBuilder`; results returned as `TabularResponse` through `_create_response`; routes wrapped in `@handle_analytics_errors`.
- Metric names are centralised in `service/analytics/metric_names.py` (`MetricName` enum).
- `run_ids.py` decides which `assistant_id`/`workflow_id` a run's metrics are written under. Virtual assistants get the label `"virtual"`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- The `.ai-run/guides/` set has no guide specific to platform (web) analytics. `data/elasticsearch-integration.md` and `api/rest-api-patterns.md` cover the general patterns. `integration/cli-analytics-*.md` cover CLI analytics only (PostgreSQL), not assistant runs.
- Docs site: `../docs/docs/user-guide/analytics/index.md` describes the dashboard widgets ("Assistants & Workflows — top assistants by total messages"), not the REST contract. No page documents `/v1/analytics/assistants-chats` or `/assistants/metrics`. `../docs/docs/admin/configuration/codemie/api-configuration.md` lists the `codemie_metrics_logs*` index and `codemie_conversation_metrics`.

### Architectural Decisions
- The ES|QL two-stage aggregation in `assistant_handler.py:62` was chosen "for accurate error rate calculation and message counting".
- The workflow handler documents its use of approximate cardinality (`workflow_handler.py:82`).
- An unmerged fix on the `EPMCDME-13429*` branches (commit `4a1295f6a`) adds `Depends(authenticate)` and an `Ability READ` check to `conversation_metrics`, with `tests/.../test_conversation_metrics_access.py`. It is absent on this branch; only a stale `.pyc` remains.

### Derived Conventions
- Run = one HTTP request (`request_id`); chat = `conversation_id`. The analytics vocabulary uses "messages" for runs and "chats/conversations" for conversations.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/analytics/handlers/test_assistant_handler.py`: ES|QL query structure, result parsing, null handling.
- `tests/codemie/service/analytics/test_analytics_service.py`: delegation to the handlers.
- `tests/codemie/rest_api/routers/test_analytics.py`: route-level tests for `assistants-chats`.
- `tests/codemie/service/monitoring/test_conversation_monitoring_service.py`, `test_run_ids.py`.
- `tests/codemie/rest_api/router_guards/` (`baselines/openapi.json`, `route_precedence.json`): OpenAPI snapshot and route-precedence guards. Any new route changes the baseline.

### Testing Framework and Patterns
- pytest with `AsyncMock`/`MagicMock`. Tests mock the repository and pipeline and assert on the query structure; route handlers are called directly as functions.

### Coverage Gaps
- No test for `GET /v1/assistants/metrics/{conversation_id}` on this branch, including its missing auth.
- No `test_workflow_monitoring_service.py` found.
- No test asserts that `save_history=False` runs skip the usage metric.

---

## 5. Configuration and Environment

### Environment Variables
- `ELASTIC_METRICS_INDEX="codemie_metrics_logs*"` and `CONVERSATIONS_METRICS_INDEX="codemie_conversation_metrics"` (`configs/config.py:165,175`).
- `METRICS_ROTATION_ENABLED` (quarterly ES index rotation, `config.py:913`).
- `ANALYTICS_DEFAULT_PAGE_SIZE`.

### Configuration Files
- `src/codemie/configs/config.py`.
- The ES ingest of metric log lines is configured outside this repo.

### Feature Flags and Deployment Concerns
- Routing analytics is gated by `_ensure_routing_analytics_enabled`.
- No flags on assistant analytics.
- Results are capped by `LIMIT 1000` in ES|QL.

---

## 6. Risk Indicators

- **AC1 answer from code:** no supported endpoint returns per-run (per-turn) tokens and cost for assistants to anyone other than the conversation owner or a share recipient. `GET /v1/conversations/{id}` gives per-message values, but admins and project admins cannot read other users' conversations. Workflows do have a per-execution API.
- **Security:** `GET /v1/assistants/metrics/{conversation_id}` is unauthenticated on this branch (`execution.py:425`). Any recommendation that points Digital Platform at it must account for the fix pending on the EPMCDME-13429 branch.
- **Invisible runs:** with `save_history=False` and for virtual assistants, no `conversation_assistant_usage` metric is emitted, so those runs are absent from `assistants-chats` and from history.
- **Attribution mismatches (AC5):**
  - `assistants-chats` groups by `assistant_name`, not id, so renamed or same-named assistants merge.
  - `total_tokens` = input + output only; `cache_read_input_tokens` and `cached_tokens_money_spent` are recorded but not summed.
  - `avg_time` is an average of per-conversation averages, not a per-run mean.
  - `error_rate` counts failed conversations, not failed runs.
  - The date filter applies per run `@timestamp`, so a conversation can be split across ranges.
  - PostgreSQL `ConversationMetrics` and history are lifetime, all-assistant totals per conversation and do not reconcile with date-ranged, per-assistant ES totals.
- **Errors:** the per-run ES document carries only `status`; it has no error class or message. Workflow per-LLM-call documents repeat the whole-execution `execution_time`, which skews `/workflows` `avg_time`. Workflow `total_runs` is approximate above 3000.
- **Correlation:** the history message has no `request_id`. A caller can supply `X-Request-ID` to tag the ES document, but no API returns ES documents by `request_id`.
- Speculative: a scoped follow-up could reuse the `execute_esql_query` pipeline and access filter. It would be a tabular endpoint (for example `/v1/analytics/assistants-runs`, filtered by `assistant_id`/`conversation_id`) returning raw `conversation_assistant_usage` rows (timestamp, request_id, conversation_id, user, model, tokens, cost, execution_time, status). This would expose per-run data to project admins without opening conversation content.
- Speculative: SDK (`codemie-sdk` analytics service) and docs-site coverage would need updates for any new endpoint, and the OpenAPI baseline would need regenerating.

---

## 7. Summary for Complexity Assessment

`assistants-chats` is computed purely from Elasticsearch. A two-stage ES|QL query runs over `codemie_metrics_logs*` documents with `metric_name=conversation_assistant_usage`: it groups first by conversation, then by `assistant_name`. Each document is one assistant run (one HTTP request/turn, identified by `request_id` = `X-Request-ID` or a uuid4). It carries `conversation_id`, `assistant_id`, user, `project`, `llm_model`, input/output/cache-read tokens, `money_spent`, `cached_tokens_money_spent`, `execution_time`, `status` and `client_source`. A "chat" is a conversation (`total_chats` = distinct `conversation_id`) and a "run" is a message/turn (`total_messages`). The run-level data therefore already exists in ES, but no API returns those rows.

Per-message tokens, cost, response time and status are also stored in PostgreSQL `Conversation.history`. They are reachable through `GET /v1/conversations/{id}`, but only for the owner or a share recipient, so a platform or project admin cannot use that route. `GET /v1/assistants/metrics/{conversation_id}` returns conversation-level totals only, and on this branch it has no authentication (the fix sits on unmerged EPMCDME-13429 branches). Workflows are better served: `GET /v1/workflows/{id}/executions[/{execution_id}]` returns per-execution `tokens_usage`, status and timestamps with project-admin visibility. Runs made with `save_history=False` or through virtual assistants emit no usage metric at all.

Change surface for a follow-up is moderate and follows existing patterns. It would add one handler method using the ES|QL pipeline and access filter, one route in `analytics/usage.py`, tests mirroring `test_assistant_handler.py`/`test_analytics.py`, an OpenAPI baseline update, and possibly SDK and docs additions in sibling repos. The main risks are the auth gap, attribution differences between per-run rows and the aggregate (name-based grouping, averaging of averages, cache tokens excluded), the absence of `request_id` on history messages, and the missing coverage for non-persisted runs.

---

## 8. External References

None named by the task. Sibling repos consulted as related evidence: `../codemie-sdk/sdk/codemie-python/src/codemie_sdk/services/analytics.py` (SDK wraps `/v1/analytics/assistants-chats` and `/workflows`) and `../docs/docs/user-guide/analytics/index.md` (dashboard-level docs only; no REST contract for these endpoints).
