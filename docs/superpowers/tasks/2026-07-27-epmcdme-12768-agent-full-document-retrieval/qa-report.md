# QA Gate Report — epmcdme-12768-agent-full-document-retrieval

**Branch**: EPMCDME-12768_sharepoint-multipage-doc-indexing
**Runner**: poetry (guide-first, `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-27
**Status**: BLOCKED — by findings outside this change; detail below

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `make ruff` | format clean, `check --fix` and final `check` both pass |
| build | PASS | `make build` | sdist + wheel built |
| license | PASS | `make license-check` | 1955 files, 0 missing headers |
| secret-scan | FAIL | `make gitleaks` | 5 findings, none in the change — see below |
| unit | FAIL | `make test` | 47 failed, 13462 passed, 129 skipped — all 47 pre-existing |
| coverage | N/A | `make coverage` | Guide: skip unless requested |
| static-analysis | N/A | `make sonar-local` | Guide: skip if credentials unavailable |
| ui | SKIPPED | (n/a) | Backend-only diff, no UI surface touched |

## Failure detail

### secret-scan — 5 findings, none inside the change

The scan genuinely ran this time (47.28 MB in 22s). Earlier in the day it could not read
the mounted repository at all and reported `scanned ~0 bytes`; that was an environment
fault, since resolved by restarting the container VM.

| File | Rule | What it is |
|---|---|---|
| `.env:16` | generic-api-key | Local LLM key, pre-existing |
| `.env:55` | generic-api-key | `SHAREPOINT_OAUTH_CLIENT_ID` — a public OAuth application identifier, not a secret. False positive of the generic rule. |
| `.env.bak-llmmode-124709:16` | generic-api-key | Untracked local backup |
| `.env.pre-main-switch:16` | generic-api-key | Untracked local backup |
| `.elasticsearch_data/indices/.../_z.cfs` | github-pat | Binary segment of the local Elasticsearch index |

None of these reach the merge request: `git diff origin/main...HEAD --name-only` contains
no `.env` file and no Elasticsearch data. The `.env` entry at line 55 was added during this
session while configuring SharePoint OAuth locally; it is a client identifier, which OAuth
transmits in plain sight in the authorization URL.

### unit — 47 pre-existing failures

The failures cluster entirely in MCP-auth and local-auth, areas this change does not touch:

| Count | File |
|---|---|
| 30 | tests/enterprise/mcp_auth/test_post_auth_401_bridge.py |
| 4 | tests/enterprise/mcp_auth/test_discovery_probe_bridge.py |
| 3 | tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py |
| 3 | tests/enterprise/mcp_auth/test_client_metadata_bridge.py |
| 2 | tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py |
| 2 | tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py |
| 1 | tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py |
| 1 | tests/enterprise/mcp_auth/test_insufficient_scope_recovery_bridge.py |
| 1 | tests/codemie/rest_api/routers/test_local_auth_router.py |

**Verified pre-existing, not assumed.** Earlier in this session the changed source files
were reverted to their pre-change state and the same suites re-run, reproducing the same 47
failures with the same distribution. The count and clustering here are identical.

Areas this change does touch are green:

- `tests/codemie/agents/` + `tests/codemie/service/search_and_rerank/` + `tests/codemie/service/conversation/` — 776 passed, 4 skipped
- Passing total rose from 13456 to 13462, matching the tests added here

## Coverage of the spec by testing

Unit tests cover coverage declaration, the unverified-total statement, notice placement and
the anti-fabrication guardrail. The one acceptance criterion that unit tests cannot prove —
that a repeated request within a conversation obtains content the earlier response lacked —
has **no recorded end-to-end evidence for this run**. The spec's Verification section
describes how to obtain it and notes that the assistant used must not carry instructions
suppressing further tool use.

## Drift signal

no — implemented names and signatures match the spec and plan, with two deliberate
deviations already recorded in `code-review-check.json`: the replay block-boundary work
(plan Task 3) was reverted as a net regression and its spec criterion is therefore unmet,
and CR-004 (coverage derived from indexed rather than retrievable chunks) is deferred by
explicit decision.
