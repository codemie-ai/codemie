# R2 verification: ES attributes.session_id vs Postgres session_dims.session_id

## Data check
- ES (localhost:9200): no metrics data. Indices with docs: codemie-codemie-onboarding (148), codemie-autotest-* (<=22 each), marketplace_assistants (3); no `codemie_metrics_logs*` index (count 0); `.internal.alerts-observability.metrics...` is empty. No `codemie_cli_session_total` / `codemie_cli_tool_usage_total` docs exist.
- Postgres (docker `postgres`, db postgres, schema codemie_analytics): `session_dims` has 3 rows, ids `demo-session-1..3` (user admin@codemie.ai, empty repo_remote/repository). Docker `pg-16528` has 2 rows. Neither is a real session id.
- Sample size from ES: 0. Overlap: not measurable. Result: **no data found** for a data-based overlap.

## Code evidence
- ES side: `F_SESSION = "attributes.session_id.keyword"` (usage_attribution/constants.py:39), queried for metrics `codemie_cli_tool_usage_total` / `codemie_cli_session_total` (queries.py `sessions_extra_body`). The value is the CLI's own session id sent in the metric attributes; the only ES writer in this repo is the LiteLLM proxy path, which takes it from header `X-CodeMie-Session-ID` verbatim (enterprise/litellm/proxy_router.py:466, `uuid4()` fallback).
- Postgres side: `session.id` (else `session_id`) from OTLP/hook event, passed through `_session_key` = `clean()` (drop NUL, repair surrogates) + cut to 256 bytes (otlp.py:443, 638, 683). No lower-casing, trimming, prefixing or hashing. Doc examples use plain UUIDs (cli-analytics-events.md).
- So both are the same raw CLI session UUID string; the only possible divergence is NUL/surrogate cleanup or >256-byte ids, neither realistic for UUIDs. Caveat: the CLI-side equality of the id in metrics vs OTLP events cannot be proven in this repo (CLI is external); `session_dims` is also only populated for sessions that sent hook/OTLP events, so coverage (not identity) may be partial and R2 must fall back to ES for sessions with no Postgres row.

## Seeded-match demonstration
Throwaway ES index `test-epmcdme-16435-throwaway` doc with `attributes.session_id=test-epmcdme-16435-s1` and `session_dims` row `test-epmcdme-16435-s1`. A terms aggregation on `attributes.session_id.keyword` returned key `test-epmcdme-16435-s1`; `session_dims.session_id = ANY(array[key])` matched it exactly. Both seeds deleted (session_dims back to 3 rows, index deleted). This only shows the join mechanics (string equality, `.keyword` subfield), not production equality.

Verdict: R2 proceed (by code evidence + seeded match; no production data to measure; keep ES fallback for sessions with no session_dims row)
