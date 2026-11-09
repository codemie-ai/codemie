# EPMCDME-16522 reproduction evidence

Run on branch `EPMCDME-16522_reproduce-bug` (from `origin/main`), 2026-11-05. No source changed.

Command: `poetry run python docs/superpowers/tasks/2026-11-05-epmcdme-16522/evidence.local/repro_unpriced.py`
Output: `repro_unpriced.out`. Catalog is patched via `llm_service.get_all_llm_model_info`, as in `test_coding_agent_pricing.py`.

| Case | catalog entry | `unpriced` | `cost_available` | cost |
|---|---|---|---|---|
| A | none | `[model]` | False | 0.0 |
| B | `cost=None` | `[model]` | False | 0.0 |
| C | in=0, out=0, cache_read=None | `[model]` | **True** | 0.0 |
| D | in=0, out=0, cache_read=0.0 | **`[]`** | **True** | 0.0 |
| E | real rates | `[]` | True | 0.0004011 |

## Conclusions
- Ticket symptom (`unpriced_models: []`, cost $0.00, priced) reproduces at service level for **D** (explicit zero rates). Zero passes the `is not None` gate (`coding_agent_pricing.py:119`, `session_money.py:73-76`).
- The ticket's stated mechanism (LiteLLM library fallback returning cache_read=None) does NOT produce `[]` on current main: case **C** lists the model in `unpriced` (but still `cost_available=True`). The library fallback was removed in 699b7c128.
- Not yet reproduced: handler-level, live Postgres, live stack, UI. UI side is by code reading only (store drops metadata, `SessionBody` has no `unpriced`/`cost_available` reference).

## Blast radius on the near-prod LiteLLM catalog (2026-11-05)

Source: `GET {LITE_LLM_URL}/model/info` (293 raw entries) built into the pricing catalog with the backend's own
`_map_raw_models` (enabled only, deduped by base_name, last entry wins): **71 chat models**, matching the live backend's
`/v1/llm_models` (73 base names, admin login). Priced by `CatalogPricer`. Scripts: `model_names_probe.py` (-> `.out`;
`--raw` skips the dedup), `litellm_catalog_audit.py` (raw, non-deduped entries), `session_blast_radius.py`.
Live session data: not available - the running backend answers 404 on `/v1/analytics/cli-analytics/*`
(`localAnalytics` feature flag disabled); its analytics DB `cli_analytics_16530` holds 1 session (claude-sonnet-5, PRICED).

- **Ticket's zero-rate shape: 0 chat models.** `gpt-5.5-2026-04-24` is priced here (5.5e-06 / 3.3e-05 / cr 5.5e-07).
  The ticket's symptom came from a catalog without it.
- **NOT_IN_CATALOG (API flags, UI shows $0.00, no marker)**: router aliases `claude-router-standard/premium`,
  `gpt-router-standard` (cost None in LiteLLM); names that match no entry: `gpt-5.5`, `gpt-5-codex`,
  `mai-code-1.1-flash`, `gemini-3.1-pro-preview`.
- **Ambiguous tariffs -> unpriced**: `claude-opus-4-6` (undated name) matches `claude-opus-4-6-20260205` (5.5e-06) and
  `claude-opus-4-6-vertex` (5e-06) at the normalized alias -> None. Claude Code sessions reporting that name show $0.00.
  (`claude-haiku-4-5-20251001` is PRICED after dedup: the raw-entry conflict is an artifact of the non-deduped probe;
  dedup keeps the last entry, the global 1e-06 tariff, which is order-dependent.)
- **PARTLY_PRICED (API flags, cost_available=true, understated, UI shows a number)**: no cache-read rate on
  gpt-oss-120b, qwen3-coder (30b, 480b, next), kimi-k2.5; `gpt-5.5-2026-04-24` has no cache-write rate (matters only
  if cache-creation tokens are reported).
- **Conclusion**: on this catalog the backend zero-rate gap is latent (no affected chat model). The impact everywhere
  is the UI: it never reads `unpriced_models` / `cost_available`, so every unpriced or partly priced model looks like
  a confirmed $0.00 or a confirmed number.

## Live reproduction on the local backend (2026-11-05, FEATURE_CLI_ANALYTICS=true, catalog_source=litellm)

`ingest_unpriced_sessions.py demo` sends Claude-Code-shaped OTLP logs (session start hook + api_request per model) to
`POST /v1/analytics/cli-analytics/logs` (project `demo`, `cli_analytics_enabled` switched on for this) and reads back:

| session | models | cost_usd | cost_available | metadata.unpriced_models |
|---|---|---|---|---|
| `epmcdme-16522-172028-unpriced` | mai-code-1.1-flash (cache_read 40,110) | 0.0 | false | `["mai-code-1.1-flash"]` |
| `epmcdme-16522-172028-mixed` | claude-sonnet-4-6 + mai-code-1.1-flash | 0.01617 (sonnet only) | true | `["mai-code-1.1-flash"]` |

List (`/sessions?time_period=last_7_days`) returns both rows with the same cost_usd / cost_available and
`metadata.unpriced_models: ["mai-code-1.1-flash"]`. Raw responses: `api_session_*.json`, `api_sessions_list.json`.

Backend: correct for a model with no catalog entry. UI (codemie-ui @ a577150fa): `git grep -i "unpriced|cost_available"`
under `pages/analytics/components/cli-analytics`, `store/cliAnalytics.ts`, `types` -> 0 matches. `SessionBody.tsx:110`
and `SessionsView.tsx:95` render `formatCliAnalyticsCost(cost_usd)` -> "$0.00" for the unpriced session and "$0.02"
(no marker, understated) for the mixed one; `fetchSessionDetail` (`store/cliAnalytics.ts:281-290`) keeps only `data`.
Other UI areas already render unpriced figures without a $0.00 (workflow execution popup, spend bar chart tests).
