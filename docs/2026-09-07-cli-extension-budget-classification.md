# EPMCDME-14260: CLI and Chrome extension budget classification

## Problem

`_is_cli_request` (both the router's and the monitoring service's copies)
treats any non-empty `X-CodeMie-CLI` header as CLI traffic. The Chrome
extension sent `X-CodeMie-CLI: codemie-chrome-extension/<version>` on every
CodeMie call, so extension usage was billed to the CLI budget and tagged
`cli_request: true` in `codemie_litellm_proxy_usage`, instead of falling
through to the PLATFORM budget like other non-CLI clients.

An earlier attempt at this ticket fixed it backend-side only: a new
`is_chrome_extension_request` helper matched the extension's identity in
either `X-CodeMie-Client` or the `X-CodeMie-CLI` marker product, gating both
`_resolve_non_premium_tracking_identity` and the CLI minimum-version check.
That added a dual-signal OR helper, a guard wrapping the pre-existing
`_is_cli_request` predicate, and mismatch-detection logging re-deriving the
helper's own inputs — all to work around a header the extension should not
have been sending in the first place, and it made `_check_cli_version` reason
about two independent, unrelated headers (`X-CodeMie-CLI` and
`X-CodeMie-Client`) in one function.

## Fix

Root-cause fix, not a guard: the extension stops sending `X-CodeMie-CLI`
entirely and sends a dedicated identity header instead,
**`X-CodeMie-Chrome-Extension`** (value = extension version, e.g. `0.3.2`).
`X-CodeMie-Client: codemie-chrome-extension` is unchanged and still used for
general client-type metrics.

With the `X-CodeMie-CLI` marker gone from extension traffic, **every**
CLI-classification code path is unaffected by, and needs zero awareness of,
the extension — no guard, no helper, no conditional:

- `_is_cli_request` (router and monitoring, unchanged) naturally returns
  `False` for the extension.
- `_resolve_non_premium_tracking_identity` (unchanged) resolves extension
  traffic to **PLATFORM** the same way it does for any other non-CLI client
  — traced end-to-end in `TestResolveNonPremiumTrackingIdentity::test_chrome_extension_request_uses_platform_even_when_cli_budget_configured`
  (`tests/enterprise/litellm/test_proxy_router.py`): `client_type =
  codemie-chrome-extension`, no CLI marker → `BudgetCategory.PLATFORM`, even
  when a CLI budget is configured. This is the actual billing outcome, not
  just a metrics tag.
- [`_check_cli_version`](../src/codemie/enterprise/litellm/proxy_router.py)
  (unchanged) sees an empty `X-CodeMie-CLI` header for extension traffic and
  returns early — same as any other non-CLI client. It has no
  `X-CodeMie-Chrome-Extension` awareness and never needs any: the two headers
  are independent signals for independent purposes (CLI version vs. client
  identity), and mixing them back into one function was exactly what the
  prior attempt got wrong.

### Metrics / Elasticsearch

`_is_cli_request`/`_resolve_non_premium_tracking_identity` needing zero
extension-awareness doesn't mean the header is unused — it's read once, in
exactly one place, purely for observability:

- [`_extract_request_info`](../src/codemie/enterprise/litellm/proxy_router.py)
  extracts `X-CodeMie-Chrome-Extension` into `request_info[CHROME_EXTENSION]`
  (raw string, e.g. `"0.3.2"`, empty when absent) — same pattern as
  `BRANCH`/`REPOSITORY`/`CODEMIE_CLI`.
- [`LLMProxyMonitoringService.track_usage`](../src/codemie/service/monitoring/llm_proxy_monitoring_service.py)
  emits this on `codemie_litellm_proxy_usage` (ES index `codemie_metrics_logs*`):
  - `chrome_extension_request` (bool) — derived flag, `True` for extension
    traffic. Named differently from the raw `chrome_extension` request_info
    key so `_sanitize_request_info`'s generic key-copy-through (which also
    forwards the raw string) can't clobber it — mirrors why `cli_request`
    (derived) and `codemie_cli` (raw marker) are separate keys today.
  - `chrome_extension` (raw string, extension version) — passed through
    automatically via `_sanitize_request_info`, same as `codemie_cli` is for
    CLI requests.
  - `cli_request` stays `false` for the extension, as it always did once the
    marker header is gone.

  This makes extension traffic queryable/bucketable in ES independently of
  regular web-app traffic (`{"term": {"attributes.chrome_extension_request":
  true}}`), the same shape the existing CLI cost aggregations use for
  `attributes.cli_request` (see `aggregation_builder.py`'s `5-bucket`/
  `6-bucket`, `cli_cost_processor.py`). **No new ES aggregation/dashboard was
  built** — research for this ticket found that `codemie_litellm_proxy_usage`
  docs with `cli_request: false` (web app and extension alike) aren't read by
  any existing aggregation today; `get_users_platform_spending` reports off a
  wholly separate metric family (`MetricName.PLATFORM_METRICS`). Building an
  actual "extension cost" dashboard bucket is a separate, larger analytics
  feature and needs its own scoping — the attribute is now in place for
  whoever picks that up next.

No back-compat shim was added for already-deployed extension builds still
sending the old `X-CodeMie-CLI` marker: both repos ship this change together.
An extension build that predates this fix will (a) still be misclassified as
CLI for budget/usage, and (b) if `CODEMIE_MIN_CLI_VERSION` is configured, may
be rejected by the version gate (its extension version string gets parsed as
if it were a CLI version). Both are resolved by upgrading the extension.

## Regression coverage

`src/codemie/core/utils.py` is untouched (byte-identical to the state before
the prior, reverted guard commit). Changed: `constants.py` (+2 constants),
`proxy_router.py` (`_extract_request_info` only), `llm_proxy_monitoring_service.py`
(`track_usage` attributes only) — `_check_cli_version`, `_is_cli_request`, and
`_resolve_non_premium_tracking_identity` themselves are unchanged from that
baseline.

New tests: `TestExtractRequestInfo` (header present/absent),
`TestResolveNonPremiumTrackingIdentity::test_chrome_extension_request_uses_platform_...`
(billing outcome), `TestTrackUsage::test_chrome_extension_request_tags_attribute_and_is_not_cli`
/ `test_non_extension_request_tags_attribute_false` (metric attributes).

Validation commands:

```text
poetry run pytest tests/enterprise/litellm/test_proxy_router.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py -q --tb=short
poetry run ruff check src/codemie/core/constants.py src/codemie/enterprise/litellm/proxy_router.py src/codemie/service/monitoring/llm_proxy_monitoring_service.py tests/enterprise/litellm/test_proxy_router.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py
poetry run ruff format --check src/codemie/core/constants.py src/codemie/enterprise/litellm/proxy_router.py src/codemie/service/monitoring/llm_proxy_monitoring_service.py tests/enterprise/litellm/test_proxy_router.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py
```

`make` is unavailable in this Windows shell, so poetry is invoked directly.

## Rollout

The extension (header swap in `clientIdentity.ts`, `codemieClient.ts`,
`providers.ts`, `embeddings.ts`) and the backend (header-name constant +
`chrome_extension_request` metric attribute) ship on branch
`EPMCDME-14260_exclude-extension-cli-budget` in their respective repos. The
extension change alone fixes budget classification — deploying it is
necessary and sufficient for billing; the backend metric attribute is
additive observability, not required for correctness. Historical usage/budget
documents are not rewritten.
