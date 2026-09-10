## Summary

The Chrome extension shared the LLM proxy path with the CLI and sent the same
`X-CodeMie-CLI` marker header format (`<product>/<version>`, product =
`codemie-chrome-extension`). Extension traffic was misclassified as CLI
traffic for non-premium budget selection and the `cli_request` usage flag.

**This MR supersedes the previous implementation on this branch** (dual-signal
`is_chrome_extension_request` helper matching `X-CodeMie-Client` OR the
`X-CodeMie-CLI` marker product, a guard wrapping `_is_cli_request`, and
mismatch-detection logging in `_check_cli_version`). Code review flagged that
approach as complexity added to work around a header the extension shouldn't
have been sending at all — it made `_check_cli_version` reason about two
independent, unrelated headers in one function. That implementation has been
fully reverted (confirmed via `git diff` against its pre-existing baseline —
byte-identical) and replaced with a root-cause fix.

## Fix

The Chrome extension stops sending `X-CodeMie-CLI` entirely and sends a
dedicated identity header instead: **`X-CodeMie-Chrome-Extension: <version>`**.
`X-CodeMie-Client: codemie-chrome-extension` is unchanged.

With the CLI marker header gone from extension traffic, every
CLI-classification code path needs **zero** extension-awareness and works
unchanged:

- `_is_cli_request` (router and monitoring) naturally returns `False`.
- `_resolve_non_premium_tracking_identity` resolves to `BudgetCategory.PLATFORM`
  the same way it does for any other non-CLI client — verified even when a
  CLI budget is configured.
- `_check_cli_version` sees an empty `X-CodeMie-CLI` header and returns early
  — same as any non-CLI client. No guard, no helper, no conditional.
- `LLMProxyMonitoringService.track_usage` emits `cli_request: false`.

The only backend change to core classification logic is therefore a **revert**
back to the pre-guard implementation. Everything else is additive
observability wiring for the new header (see below) — nothing in it feeds
back into billing/classification decisions.

Companion change on `codemie-chrome-extension` (branch
`EPMCDME-14260_exclude-extension-cli-budget`): header swap in
`clientIdentity.ts`, `codemieClient.ts`, `providers.ts`, `embeddings.ts`.

## Type of change

- [x] Bug fix
- [ ] New feature
- [ ] Refactor
- [ ] Security fix
- [ ] Breaking change

## Changes

| File | What changed |
|---|---|
| `src/codemie/core/constants.py` | Added `CHROME_EXTENSION` (request_info key) and `HEADER_CODEMIE_CHROME_EXTENSION = "X-CodeMie-Chrome-Extension"`. |
| `src/codemie/core/utils.py` | Reverted to pre-guard baseline — `is_chrome_extension_request` helper removed. |
| `src/codemie/enterprise/litellm/proxy_router.py` | `_is_cli_request`, `_resolve_non_premium_tracking_identity`, `_check_cli_version` reverted to pre-guard baseline (unchanged from that state). `_extract_request_info` now captures the new header into `request_info["chrome_extension"]`. Unrelated: removed a redundant `json.JSONDecodeError` from an `except (json.JSONDecodeError, ValueError)` tuple flagged by SonarQube (`python:S5713` — `JSONDecodeError` is already a `ValueError` subclass). |
| `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` | Reverted to pre-guard baseline for classification. `track_usage` additionally tags `codemie_litellm_proxy_usage` with `chrome_extension_request` (derived bool) and the raw `chrome_extension` version string (same pass-through pattern as `codemie_cli`). |
| `src/codemie/rest_api/routers/metrics.py` | Generic `/v1/metrics` endpoint now also reads `X-CodeMie-Chrome-Extension` and tags `attributes.chrome_extension`, the same way it already tags `codemie_cli`/`codemie_client` — so extension feature-usage metrics keep carrying version info after the header swap. |
| `src/codemie/service/monitoring/metrics_constants.py` | Added `MetricsAttributes.CHROME_EXTENSION`. |
| `tests/enterprise/litellm/test_proxy_router.py` | Reverted the guard-specific test classes (`TestIsChromeExtensionRequest`'s dual-signal cases, `TestProxyBudgetAndUsage`, extended `TestCheckCliVersion` extension cases) back to baseline, then added targeted new tests: `_extract_request_info` header extraction, `_resolve_non_premium_tracking_identity` explicit PLATFORM-billing case for `client_type=codemie-chrome-extension`. |
| `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py` | New `chrome_extension_request_info` fixture; tests asserting `chrome_extension_request`/`chrome_extension` attributes on `track_usage`. |
| `tests/codemie/rest_api/routers/test_metrics.py` | New test asserting `/v1/metrics` tags `chrome_extension` from the header. |
| `docs/2026-09-07-cli-extension-budget-classification.md` | Rewritten to describe the final single-header, zero-classification-coupling design (supersedes the dual-signal design it previously documented). |

Preserved unchanged: both `_is_cli_request` predicates (byte-identical to
pre-guard baseline), premium-model precedence, personal-credential bypass,
and CLI/PLATFORM budget-availability fallbacks for all non-extension traffic.

**Excluded from this change:** `.ai-run/sdlc-factory/doctor.json` and
`.codemie/codemie-cli.config.json` were dirty in the working tree before this
work started — unrelated local machine state, not part of this diff.

## Spec

No formal spec/design doc — routed to `writing-plans` (low complexity, 11/36).
Design rationale: `docs/2026-09-07-cli-extension-budget-classification.md`.

## QA Guide

1. Send a proxy request (`/v1/chat/completions` or `/embeddings`) with headers
   `X-CodeMie-Client: codemie-chrome-extension` and
   `X-CodeMie-Chrome-Extension: <version>` (no `X-CodeMie-CLI`) → spend
   attributes to the PLATFORM budget,
   `codemie_litellm_proxy_usage.cli_request = false`,
   `.chrome_extension_request = true`, `.chrome_extension = "<version>"`.
2. Send the same request with a real CLI identity (e.g. `X-CodeMie-CLI:
   codemie-cli/<version>`, no chrome-extension header) → unchanged: CLI
   budget, `cli_request = true`.
3. With `CODEMIE_MIN_CLI_VERSION` set, send a request with only
   `X-CodeMie-Chrome-Extension` (no `X-CodeMie-CLI`) → not rejected (the
   version-check function returns early on the missing CLI header, same as
   any non-CLI client — no extension-specific exemption code exists or is
   needed).
4. `POST /v1/metrics` with `X-CodeMie-Client: codemie-chrome-extension` and
   `X-CodeMie-Chrome-Extension: <version>` → emitted metric's attributes
   include `chrome_extension: "<version>"` alongside `codemie_client`.

Full regression coverage: `tests/enterprise/litellm/test_proxy_router.py`,
`tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py`,
`tests/codemie/rest_api/routers/test_metrics.py`.

## Checklist

- [x] Self-reviewed (addressed two rounds of live review feedback on this
  branch: "complexity lives in the wrong place" → reverted the guard,
  "`_check_cli_version` should not mix two independent headers" → removed the
  header check from that function entirely, call site included)
- [x] Tests written/updated
- [x] Documentation updated — `docs/2026-09-07-cli-extension-budget-classification.md`
  rewritten to match the final design
- [x] No breaking changes — non-extension behavior provably unchanged
  (`_is_cli_request`, `_resolve_non_premium_tracking_identity`,
  `_check_cli_version` byte-identical to pre-guard baseline)

## Review history on this branch

1. **"Design concern: complexity lives in the wrong place."** The
   dual-signal `is_chrome_extension_request` helper, the `not is_extension`
   gate, and `_check_cli_version`'s mismatch-detection logging were flagged
   as workarounds for a header the extension shouldn't send. Resolution:
   extension stops sending `X-CodeMie-CLI`, sends
   `X-CodeMie-Chrome-Extension` instead; backend guard code fully reverted
   rather than kept for back-compat (explicit decision — no transition period
   for already-deployed extension builds).
2. **"`_check_cli_version` should not mix in itself two independent
   headers."** An intermediate version kept a one-line
   `X-CodeMie-Chrome-Extension` exemption check inside `_check_cli_version`.
   Removed — the function and its call site are now byte-identical to the
   pre-guard baseline; it needs zero awareness of the extension because the
   CLI marker header is simply absent from extension traffic.
3. **"This new header — is it properly wired? What about other extension
   metrics?"** Traced end-to-end (see QA Guide) and found the generic
   `/v1/metrics` endpoint would have silently lost the extension's version
   tag (previously piggybacked on `X-CodeMie-CLI`, read generically as
   `codemie_cli`) with no replacement. Fixed: `metrics.py` now reads
   `X-CodeMie-Chrome-Extension` the same way. Also found and fixed a
   same-key collision bug during implementation: `_sanitize_request_info`
   passes the raw `chrome_extension` request_info value through into the
   metric attributes dict, which was clobbering a same-named derived boolean
   — resolved by naming the derived attribute `chrome_extension_request`
   (mirrors why `cli_request` and `codemie_cli` are already separate keys).

**Known gap, explicitly out of scope:** no Elasticsearch aggregation/dashboard
exists for "extension cost" as its own bucket. Research for this ticket found
`codemie_litellm_proxy_usage` docs with `cli_request: false` (web app and
extension alike) aren't read by any existing ES aggregation today —
`get_users_platform_spending` reports off a separate metric family entirely.
The new `chrome_extension_request` attribute makes such a bucket buildable
(same shape as the CLI cost aggregations' `attributes.cli_request` filter)
but building it is separate, larger analytics work.

## Testing (exact commands run)

```
$ poetry run pytest tests/enterprise/litellm/test_proxy_router.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py tests/codemie/rest_api/routers/test_metrics.py -q --tb=short
117 passed, 1 warning in 131.77s (0:02:11)

$ poetry run ruff check src/codemie/core/constants.py src/codemie/core/utils.py src/codemie/enterprise/litellm/proxy_router.py src/codemie/rest_api/routers/metrics.py src/codemie/service/monitoring/llm_proxy_monitoring_service.py src/codemie/service/monitoring/metrics_constants.py tests/codemie/rest_api/routers/test_metrics.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py tests/enterprise/litellm/test_proxy_router.py
All checks passed!

$ poetry run ruff format --check <same files>
9 files already formatted

$ poetry run python scripts/license_headers/check_license_headers.py --check --quiet <same files>
Checked 9 files, 0 missing license headers

$ poetry build
Built codemie-0.8.0.tar.gz / codemie-0.8.0-py3-none-any.whl
```

```
$ git archive HEAD | tar -x -C /tmp/gitleaks-scan-14260 && cd /tmp/gitleaks-scan-14260
$ docker run --rm -v "$(pwd -W):/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 dir --no-banner --verbose --config=/workspace/.gitleaks.toml /workspace
scanned ~41006423 bytes (41.01 MB) in 1m58s
no leaks found
```

(Scanned an export of the committed tree via `git archive`, not the dev
working copy — the working copy's `.venv/` produces false positives
`.gitleaks.toml` doesn't allowlist, since CI's checkout never has a `.venv`
present.)

## Test harness

Run by the user directly against the local stack (Rancher Desktop: backend +
Postgres + Elasticsearch) with this branch's backend, via the `codemie-sdk`
monorepo checkout (`cd D:\Projects\codemie-sdk\test-harness && poetry run
pytest -n 8 -m "sanity" --reruns 2`):

```
======================== 189 passed, 4 skipped, 3 rerun in 2162.07s (0:36:02) ========================
```

## Code review

Independent multi-lens review of this diff (blind + edge-case lenses; no
story/spec artifact exists for this change, so acceptance was correctly
skipped and confidence is `low` per policy — no-spec, not a review failure).
One real, unambiguous finding in the final round:

- **CR-001** (major, patch): `HEADER_CODEMIE_CHROME_EXTENSION` was missing
  from `proxy_router.py`'s `PROXY_HOP_BY_HOP_HEADERS` set, so unlike every
  other CodeMie-specific identity header it was forwarded verbatim to the
  upstream LLM provider. **Fixed**: added to the set; regression coverage
  added in `TestPrepareProxyHeaders::test_prepare_headers_filters_hop_by_hop`.

Six other lens-raised concerns (no cross-check between `chrome_extension_request`
and `cli_request`; header-recognition logic present in 3 files; no length cap
on the version string; a test not using `assert_called_with`; the metric-name-prefix
logic not consulted; cross-repo claims in the design doc) were triaged to
`dismiss`/`defer` — either already handled elsewhere (generic attribute
truncation in `BaseMonitoringService.send_count_metric` and
`_sanitize_request_info`), consistent with a pre-existing codebase pattern,
unreachable given the real client's actual behavior, or explicitly rejected
by this exact ticket's documented design decision against cross-header
validation (see the review history above). Full reasoning:
`code-review-final.json` (request-changes, CR-001) → fix applied →
`code-review-check.json` (approve, CR-001 resolved).

## Artifacts

- Run directory: `docs/superpowers/runs/20260907-1425-EPMCDME-14260/`
- Design doc (rewritten for the final implementation): `docs/2026-09-07-cli-extension-budget-classification.md`
- Code review evidence for the final diff: `code-review-final.json` (request-changes, CR-001),
  `code-review-check.json` (approve, CR-001 resolved)
