# EPMCDME-16481: Per-request assistant analytics API

## Problem

`GET /v1/analytics/assistants-chats` returns only per-assistant aggregates. No existing endpoint in codemie or codemie-enterprise returns usage for an individual assistant turn to an admin or project admin. `GET /v1/conversations/{id}` is owner/share-only, and `routing/decisions` covers routing data only. The per-turn data already exists in Elasticsearch as `conversation_assistant_usage` documents, emitted by `ConversationMonitoringService.send_conversation_metric` (`service/monitoring/conversation_monitoring_service.py:49`). These are the same documents `assistants-chats` aggregates, but no API returns them as rows.

## Terminology

- **Chat**: a conversation (`conversation_id`). `assistants-chats` `total_chats` counts distinct conversations.
- **Request**: one assistant turn, which is one HTTP request (`request_id` = `X-Request-ID` header or a uuid4, `rest_api/main.py:1444`). `assistants-chats` `total_messages` counts these documents. CodeMie reserves "runs" for workflows.

## Solution

Add a read-only `GET /v1/analytics/assistants-requests` in `rest_api/routers/analytics/usage.py`, next to `assistants-chats` (`usage.py:130-154`), using the same decorators and envelope. It returns one `TabularResponse` row per `conversation_assistant_usage` document, newest first.

Layers:
- **Route**: `Depends(authenticate)`, `AnalyticsQueryParams`, `client_source`, and an optional `assistant_id` query param.
- **Service**: `AnalyticsService.get_assistants_requests` delegates to `AssistantHandler.get_assistants_requests`.
- **Handler**: calls `AnalyticsQueryPipeline.execute_search_rows` (`query_pipeline.py:77`) with metric filter `conversation_assistant_usage`, a row parser and the column list. The pattern is the same as `RoutingHandler.get_decision_timeline` (`handlers/routing_handler.py:50-104`).
- **Pipeline/repository, backward-compatible**:
  - `AnalyticsQueryFilters` gains `assistant_id: str | None = None`. `_build_query` applies it via a new `SecureQueryBuilder.add_assistant_filter` (term on `attributes.assistant_id.keyword`), only when set.
  - `execute_search_rows` gains optional `sort` and `track_total_hits` params.
  - `MetricsElasticRepository.execute_search_query` (`repository/metrics_elastic_repository.py:228`) gains the same two optional params. It adds them to the ES body only when provided, so the `routing/decisions` request body stays byte-identical.

Row columns (attribute names as emitted; `timestamp` from `@timestamp`):

`request_id, timestamp, conversation_id, assistant_id, assistant_name, user_id, user_name, user_email, project, llm_model, client_source, input_tokens, output_tokens, total_tokens, cache_read_input_tokens, money_spent, cached_tokens_money_spent, execution_time, status`

- `total_tokens` = `input_tokens + output_tokens`, with a missing side counted as 0. This matches the `assistants-chats` definition.
- An attribute absent from a document is returned as `null`. Older documents may lack `request_id`.

Paging: `from = page*per_page`, `size = per_page`, sorted by `@timestamp` desc, exact `total_count` via `track_total_hits: true`. The ES `max_result_window` is 10,000. A request whose `(page+1)*per_page` exceeds 10,000 is rejected with HTTP 400 and a message telling the caller to narrow the date range. Without this check it would surface as an ES error.

Access scoping comes unchanged from `SecureQueryBuilder`/`AccessFilter`:
- a super admin sees all rows;
- a project admin sees all users' rows in their admin projects;
- other users see only their own rows.

## Where the investigation answers live

This spec's **Reconciliation** and **Field availability** sections are the source for the Jira write-up posted at handoff. A condensed version also goes into the route's OpenAPI `description`: chat vs request, the missing error detail, the 10,000-row paging bound, and the five reconciliation bullets. The OpenAPI baseline (`tests/codemie/rest_api/router_guards/baselines/openapi.json`, plus `route_precedence.json` if it changes) is regenerated via `regenerate_baselines.py`.

### Field availability

- **Available per request:** tokens (input, output, total, cache-read), cost (`money_spent`, `cached_tokens_money_spent`), `execution_time` (seconds, as `assistants-chats` treats it), `status`.
- **Unavailable:** error message and error class. These are not stored in the metric; only `status` is.
- **Absent requests:** with `save_history=False` and for virtual assistants, no usage document is emitted. Such requests are missing here, exactly as they are from `assistants-chats`.
- **Cost source:** `money_spent` comes from the LiteLLM spend ledger for requests made on or after the ledger's coverage start, and from the metric document for older requests and on deployments without the ledger. A cost the ledger has not recorded yet is `null` with `spend_unavailable: true`. If the ledger cannot be read, the document's value is returned with `spend_unavailable: true`. `cached_tokens_money_spent` is always the document's value.

### Reconciliation with `assistants-chats`

Totals match only for the same filters and date range. Differences:

1. `assistants-chats` groups by `assistant_id` and shows each assistant's current name. Filtering here by `assistant_id` lists the requests behind one of its rows.
2. `total_cost_usd` reads the same sources as `money_spent` here, so it equals their sum once every request's cost is recorded; rows with `spend_unavailable: true` account for a gap. Neither includes `cached_tokens_money_spent`. `total_tokens` excludes `cache_read_input_tokens`.
3. `avg_time_seconds` is an average of per-conversation averages, not the mean of request `execution_time`.
4. `error_rate_percent`/failed counts are per conversation (any non-success request fails the chat). Here, `status` is per request.
5. The date filter applies per request `@timestamp`, so one conversation can be split across ranges. `assistants-chats` counts a chat in every range containing at least one of its requests.

## Acceptance criteria

1. `GET /v1/analytics/assistants-requests` exists, is read-only, requires authentication, and returns `TabularResponse` with exactly the 19 columns above in that order.
2. Each row corresponds to one `conversation_assistant_usage` document. `total_tokens` = input + output, and missing attributes are `null`.
3. `time_period`/`start_date`/`end_date`, `users`, `projects`, `client_source`, `assistant_id`, `page` and `per_page` each narrow the ES query as for `assistants-chats`. `assistant_id` is an exact term match.
4. Rows are sorted by `@timestamp` descending, and `pagination.total_count` is exact (`track_total_hits: true`).
5. `(page+1)*per_page > 10000` returns HTTP 400 without querying ES.
6. Access scoping matches the other analytics endpoints: super admin all, project admin all users in admin projects, plain user own rows only.
7. The `routing/decisions` ES request body is unchanged, with no `sort` or `track_total_hits` keys. A test asserts this.
8. With identical filters, the count of rows equals the `assistants-chats` `total_messages` sum. For one assistant whose request costs are all recorded, the sums of `money_spent` and `total_tokens` equal `total_cost_usd` and `total_tokens` (covered by handler tests on shared fixtures).
9. The route's OpenAPI description covers chat vs request, unavailable error detail, the paging bound and the reconciliation notes. The OpenAPI baseline is regenerated and the router-guard tests pass.
10. Tests are added for the handler (query shape, parser, null handling), the pipeline/repository (optional params), the service delegation and the route, following `test_assistant_handler.py`, `test_analytics_service.py` and `test_analytics.py`.

## Non-goals

- No changes to metric emission. This includes `save_history=False`, virtual assistants and adding error detail to the metric.
- No changes to `assistants-chats` aggregation or its attribution quirks.
- No change to `GET /v1/assistants/metrics/{conversation_id}` or its auth (EPMCDME-13429).
- No `conversation_id` or `request_id` filter, no free-text search and no cursor/`search_after` paging.
- No workflow per-execution analytics. The existing `/v1/workflows/{id}/executions` already covers it.
- No SDK (`codemie-sdk`) or docs-site (`docs`) changes. These are follow-ups.
- No PostgreSQL reads (`conversations`, `conversation_metrics`) and no new index or mapping.
- No UI.
