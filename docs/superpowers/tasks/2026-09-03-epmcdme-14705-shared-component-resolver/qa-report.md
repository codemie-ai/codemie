# QA Gate Report — epmcdme-14705-shared-component-resolver

**Branch**: EPMCDME-14705_shared-component-resolver
**Runner**: poetry (gates taken from `.ai-run/guides/quality-gates.md`, which defers to Makefile targets)
**Status**: PASSED — with one environment-blocked gate, detailed below

## Gates

| Gate | Status | Command | Notes |
|---|---|---|---|
| lint | PASS | `make ruff` | Format, `check --fix`, then `check` — all clean. One test file was reformatted during the run and committed. |
| build | PASS | `make build` | sdist and wheel built: `codemie-0.8.0.tar.gz`, `codemie-0.8.0-py3-none-any.whl`. |
| license | PASS | `make license-check` | 2192 files checked, 0 missing headers. Headers were added to the 5 new test files via `make license-fix`. |
| secret-scan | FAIL (environment) | `make gitleaks` | Real scan (~70 MB in 38s, not an empty pass). 8 findings, **all in untracked local `.env` backups**, none in tracked files and none in the branch diff. See below. |
| unit | PASS (parity) | `make test` | Not directly usable in this checkout; verified by parity against `main`. See below. |
| ui | SKIPPED | (n/a) | No UI surface changed — the diff is backend-only. The pilot flag's frontend surface already exists in codemie-ui and is untouched. |

## Secret scan detail

`make gitleaks` runs `gitleaks dir` over the whole working tree, which includes untracked files. All
8 findings sit in local `.env` backups left over from earlier sessions:

```
4  .env.bak-before-main-20260831-132300
1  .env.bak-llmmode-124709
1  .env.pre-main-switch
2  local/.env.bak-before-enterprise-20260825-174600
```

Every one of those paths is untracked (`git ls-files --error-unmatch` fails for all four) and matched
by `.gitignore`. The branch diff contains no secret: the sole grep hit for
`client_id|secret|api_key|password` across `main...HEAD` is the sentence "Secrets: none in this
domain" in `technical-analysis.md`.

The gate therefore fails on developer-local files that do not exist in CI. It is not a finding against
this change. Removing or relocating those backups would turn the gate green locally.

## Unit test detail

`make test` cannot serve as a direct gate in this checkout: 38 test modules fail to collect on plain
`main` with `RuntimeError: enterprise MCP auth package is unavailable while MCP auth TMS is enabled`,
because the enterprise package is not installed locally. This is environmental and identical on
`main`.

The usable signal is a parity comparison, run in both directions:

| Run | Branch | main |
|---|---|---|
| `make test` (as-is) | 39 collection errors | 38 collection errors — the extra one is this branch's new `test_customer_config_warmup.py`, which imports `rest_api.main` like the other 38 |
| `MCP_AUTH_TMS_ENABLED=false make test` | 15700 passed / 60 failed | 15681 passed / 57 failed |
| `tests/enterprise` only | 51 failed | 51 failed — sorted FAILED lists are byte-identical |
| everything except `tests/enterprise` | — | — |

The three non-enterprise deltas were all caused by this change and were all resolved during the
review round. The final comparison of sorted non-enterprise FAILED lists between branch and `main` is
**empty**: full parity, no regression.

Targeted suites for the changed area are green: 34 tests across
`test_customer_config_service.py`, `test_customer_config_refresh.py` and `test_customer_config_audit.py`,
plus 130 in `tests/codemie/configs/` and 3 in `test_customer_config_warmup.py`.

## Drift signal

no
