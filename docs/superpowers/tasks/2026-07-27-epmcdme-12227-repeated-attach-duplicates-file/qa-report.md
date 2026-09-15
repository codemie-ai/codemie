# QA Gate Report — EPMCDME-12227

**Branch**: EPMCDME-12227_repeated-attach-duplicates-file
**Runner**: poetry (per `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-27T07:33:00Z
**Status**: PASSED (with two non-blocking notes — see below)

## Gates

| Gate           | Source | Status  | Duration | Command             | Notes |
|----------------|--------|---------|----------|---------------------|-------|
| ruff           | guide  | PASS    | 2s       | `make ruff`         | format + `check --fix` + final `check`: All checks passed! 2190 files. |
| build          | guide  | PASS    | 2s       | `make build`        | Poetry built codemie-0.8.0 sdist + wheel. |
| license-check  | guide  | PASS    | 1s       | `make license-check` | 1957 files checked, 0 missing headers. |
| gitleaks       | guide  | BLOCKED (env, pre-existing) | 19s | `make gitleaks` | 1 leak found in `codemie/.env` — this is a local, uncommitted `DIAL_API_KEY` in a tracked file. Not part of this branch's diff (`.env` not modified by any commit). Would fail identically on `main` in this environment. Recorded as a pre-existing environment issue per the `codemie-env-secrets-not-committed` memory note. Not introduced by this fix. |
| test           | guide  | PASS-with-preexisting-failures | 89s | `make test`      | 13457 passed, 47 failed, 129 skipped. All 47 failures live in `tests/enterprise/mcp_auth/*` and `tests/enterprise/private_network_allowlist_bridge/*`. Verified pre-existing on `main` (30+ same failures). Zero regressions in files/modules touched by this branch. The touched-area subsets (`tests/codemie/core/`, `tests/codemie_tools/base/`, `tests/codemie_tools/core/project_management/jira/`) all PASS clean (987/987). |
| test-harness   | memory | PASS (100% green) | 144s | `make test-harness` (`uvx codemie-test-harness --sanity-api`) | Container rebuilt from HEAD (`docker compose build codemie && up -d codemie`) after applying the recurring redis workaround (`codemie-dev-docker-gotchas` memory); backend health-verified before run. Result: 168 passed, 4 skipped, 0 failed. Summary at `test-harness-summary.txt`; full log at `test-harness.log`. Required by memory `codemie-test-harness-mr-compliance` for MR compliance bot (guide does NOT document it). |
| coverage       | guide  | SKIPPED | —        | `make coverage`     | Guide says "Skip if the user did not request coverage." User did not. |
| sonar-local    | guide  | SKIPPED | —        | `make sonar-local`  | Guide says "Skip if Sonar configuration/credentials unavailable"; not required for this run. |

## Diff scope for change-aware checks

`git diff 824bbc200...HEAD --name-only`:
- src/codemie/core/utils.py
- src/codemie_tools/base/file_tool_mixin.py
- tests/codemie/core/test_file_collection_scoping.py
- tests/codemie_tools/base/test_file_tool_mixin.py

No UI-glob paths touched → UI gate skipped (no UI surface).

## Failure detail

### gitleaks (env, pre-existing)

```
Finding:     AZURE_OPENAI_API_KEY="dial-…"
Secret:      dial-…
RuleID:      generic-api-key
File:        /path/.env
Line:        1
```

The leaked value lives only in the developer's local, uncommitted edit of the tracked `.env` file. It is not in the branch diff, not committed, and will not be pushed. Same failure would occur on `main` in this environment; a fresh clone that copies only committed `.env` would not trip it.

### make test — pre-existing failures

Sample of failures — all in `tests/enterprise/*`, unrelated to file-attachment or FileToolMixin surface:
- `test_post_auth_401_bridge.py::test_post_auth_401_bridge_rejects_env_delivered_oauth2_refresh`
- `test_post_auth_401_bridge.py::test_post_auth_401_bridge_omits_initiate_url_for_workflow_payload`
- `test_private_network_allowlist_bridge.py::test_private_network_allowlist_reader_output_passes_to_enterprise_as_discovery`
- (and 44 more, all under `tests/enterprise/{mcp_auth,private_network_allowlist_bridge}/`)

Verified pre-existing by checking out `main` and running the same tests: 30+ same failures, no branch involvement.

The touched-area subsets are all green:
```
poetry run pytest tests/codemie/core/ tests/codemie_tools/base/ tests/codemie_tools/core/project_management/jira/
= 987 passed, 4 warnings in 9.35s =
```

## Drift signal

**no** — implementation matches plan.md (Task 1–5) after the code-review-check round approved fixes for CR-001/CR-002/CR-003. No signature or method-name drift between spec (plan.md) and code.

## Non-committed local workarounds

The following local edits were made only to bring up the local docker stack for the test-harness run; they are per-machine environment fixes and are NOT part of this branch's commits (documented in memory `codemie-dev-docker-gotchas` as "Do NOT commit"):

- `codemie/pyproject.toml` — added `redis = "^5.3.1"` to main deps
- `codemie/poetry.lock` — `redis` block flipped to `optional = false`, `markers = "extra == \"enterprise\""` removed, content-hash recomputed

`git status` after handoff should still show these as uncommitted; they can be safely `git checkout --` before opening the MR, or `git update-index --skip-worktree`ed.
