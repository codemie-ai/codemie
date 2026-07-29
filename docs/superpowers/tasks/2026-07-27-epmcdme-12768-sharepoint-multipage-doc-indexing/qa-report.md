# QA Gate Report — epmcdme-12768-sharepoint-multipage-doc-indexing

**Branch**: EPMCDME-12768_sharepoint-multipage-doc-indexing
**Runner**: poetry (guide-first, `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-27
**Status**: BLOCKED — by pre-existing failures unrelated to this change; see below

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | ~35s | `make ruff` | format clean, `check --fix` and final `check` both pass |
| build | PASS | ~40s | `make build` | sdist + wheel built (codemie-0.8.0) |
| license | PASS | ~10s | `make license-check` | 1955 files checked, 0 missing headers |
| secret-scan | SKIPPED | — | `make gitleaks` | Environment block, reported explicitly below. NOT a pass. |
| unit | FAIL | 104s | `make test` | 47 failed, 13456 passed, 129 skipped. All 47 pre-existing on main. |
| coverage | N/A | — | `make coverage` | Guide: skip unless requested; not requested |
| static-analysis | N/A | — | `make sonar-local` | Guide: skip if config/credentials unavailable |
| ui | SKIPPED | — | (n/a) | No UI surface changed — backend-only diff |

## Failure detail

### secret-scan — environment block

Docker is running, but the gitleaks container cannot read the mounted repository:

```
docker run --rm -v $(pwd):/path zricethezav/gitleaks:v8.30.0 dir --no-banner --verbose /path
FTL unable to load gitleaks config, err: open /path/.gitleaks.toml: too many open files
```

Two mitigations were attempted and both failed:

1. Re-run with `--ulimit nofile=65535:65535` — same fatal error.
2. Scan only the changed files from an isolated small mount — the container reported
   `scanned ~0 bytes (0)`, i.e. it read nothing, so its `no leaks found` result is vacuous
   and is **not** counted as coverage.

The root cause is the colima/virtiofs bind mount, not the diff. This gate is recorded as
SKIPPED rather than PASS because no secret scanning actually ran. CI runs it on the MR.
By inspection the diff contains no credential-like material: it changes chunk numbering
logic and a deduplication key.

### unit — 47 pre-existing failures

The failures cluster entirely in MCP-auth and local-auth areas that this change does not touch:

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

**Verified pre-existing, not assumed.** Both changed source files were reverted to their
pre-change state (`git checkout <merge-base> -- ...`) and the same suites re-run: 44 failures
in `tests/enterprise/mcp_auth/` and the same 3 elsewhere reproduced identically — 47 in total,
matching this run exactly. The working tree was restored and confirmed clean afterwards.

The areas this change does touch are fully green:

- `tests/codemie/datasource/` — 1079 passed
- `tests/codemie/service/search_and_rerank/` — 101 passed

## Drift signal

no — the implemented method names and signatures match the spec and plan. The one deviation
found during planning (the spec's claim that the SharePoint `_process_chunk` test needed
updating) was corrected in `spec.md` before implementation, and the one found during review
(counter keyed on the storage key rather than the retrieval identity) was fixed in the
review fix-up round and is reflected in `code-review-check.json`.
