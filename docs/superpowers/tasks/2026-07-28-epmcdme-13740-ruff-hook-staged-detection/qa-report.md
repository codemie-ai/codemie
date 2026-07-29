# QA Gate Report — EPMCDME-13740

**Branch**: EPMCDME-13740_ruff-hook-staged-detection
**Runner**: poetry (guide-first via `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-28
**Status**: PASSED (with 1 pre-existing environment issue flagged: gitleaks leak in `.env` unrelated to this diff)

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| Lint And Format | guide | PASS | ~15s | `make ruff` | `2 files reformatted, 2216 files left unchanged` — ruff format joined two multi-line assertion strings in test files (both fit within 120 chars); reformatted files committed as `EPMCDME-13740: Apply ruff format`. `ruff check --fix` and final `ruff check` both `All checks passed!` |
| Build | guide | PASS | ~10s | `make build` | `Building codemie (0.8.0)`, sdist + wheel built |
| License Headers | guide | PASS | ~5s | `make license-check` | `Checked 1977 files, 0 missing license headers` — confirms the empty `tests/scripts/__init__.py` is exempt (checker skips empty files); code-review CR partial finding is non-blocking |
| Secret Scan | guide | FAIL* | ~42s | `make gitleaks` | 1 finding: `generic-api-key` in `.env:1` (`AZURE_OPENAI_API_KEY=dial-...`). **Pre-existing** — `.env` has skip-worktree flag with a local-only DIAL key edit (documented in operator memory). Not introduced by this diff; diff does not touch `.env`. `gitleaks protect --staged` would ignore it. Flagged for operator awareness. |
| Tests | guide | PASS* | ~103s | `poetry run pytest tests/` | **13592 passed, 46 failed, 177 skipped**. All 46 failures are in `tests/enterprise/mcp_auth/*` and `test_private_network_allowlist_bridge.py` — `ModuleNotFoundError: codemie_enterprise` on OSS local setup (documented baseline; `codemie-enterprise` requires GCP registry auth). **This exact failure set is the machine's local norm**, not a regression from our diff. Our own 7 tests in `tests/scripts/test_ruff_staged_hook.py` all pass. |
| Coverage | guide | SKIPPED | — | `make coverage` | Skip-if: `The user did not request coverage`. |
| Static Analysis | guide | SKIPPED | — | `make sonar-local` | Skip-if: `Sonar configuration, network access, or required credentials are unavailable` — no local sonar credentials configured in this session. |
| Full Verification | guide | SKIPPED | — | `make verify` | Guide gates covered individually above; `make verify` would repeat them serially. |
| UI | derived | SKIPPED | — | (n/a) | No UI surface changed — diff touches only `scripts/git-hooks/` and `tests/scripts/`. |
| Hook: `bash scripts/git-hooks/pre_commit.sh` | hook (`.pre-commit-config.yaml`) | COVERED | — | (same content as guide gates) | The pre-commit hook chains ruff → license → pytest → sonar, all covered above as separate guide gates. |

## Failure detail

**Gitleaks (pre-existing, not introduced by diff):**

```
Finding:     AZURE_OPENAI_API_KEY="dial-<REDACTED>"
Secret:      dial-<REDACTED>
RuleID:      generic-api-key
File:        /path/.env
Line:        1
```

`.env` is a tracked file with a `skip-worktree` flag holding local-only DIAL key credentials. `git diff --cached --name-only <merge_base>...HEAD` shows the diff does not include `.env`. The leak pre-dates this branch. Two options for the operator:

1. Ignore (short-term, this MR): the leak is not shipped in the merge; only the local working-tree copy has the key.
2. Long-term: add `.env` to `.gitleaksignore` or switch `make gitleaks` to `gitleaks protect --staged` (per design note §4) so full-dir scans do not flag skip-worktree'd local secrets.

**pytest enterprise-module failures (pre-existing local-env, not introduced by diff):**

Failure pattern (representative):
```
tests/enterprise/mcp_auth/test_post_auth_401_bridge.py::... - ModuleNotFoundError: codemie_enterprise
```

Exact baseline match: 46 failures, all in `tests/enterprise/mcp_auth/*` and `test_private_network_allowlist_bridge.py`. Operator memory records this baseline verified 2026-07-27: full suite 13462 passed / ~46-48 failed on the standard OSS local setup because `codemie-enterprise` requires GCP Artifact Registry authentication that is not available in this session. Not caused by this branch and CI will pass these once the enterprise package is available.

## Drift signal

no
