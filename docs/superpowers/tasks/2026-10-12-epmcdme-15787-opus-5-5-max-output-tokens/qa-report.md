# QA Gate Report — epmcdme-15787-opus-5-5-max-output-tokens

**Branch**: EPMCDME-15787_opus-5-5-max-output-tokens (code HEAD 562eb0ca0, merge-base 2b039d66c)
**Runner**: custom (`.ai-run/guides/quality-gates.md`; `make` is not installed in Git Bash, so each Makefile recipe was run verbatim through poetry)
**Started**: 2026-10-12T14:40:00Z
**Status**: PASSED — nothing local blocks. Gitleaks passed on 2026-10-13. Sonar is SKIPPED (no token). The test harness passed (197 passed, 4 skipped).

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~20s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` (`make ruff`) | 2757 files unchanged; All checks passed |
| build | guide | PASS | ~15s | `poetry build` (`make build`) | Built codemie-0.8.0-py3-none-any.whl |
| license | guide | PASS | ~10s | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | 2464 files checked, 0 missing headers |
| gitleaks | guide | PASS | 2m | `make gitleaks` recipe (gitleaks v8.30.1 `dir --config=.gitleaks.toml`) with `--redact` instead of `--verbose`, run against a `git archive HEAD` export | "no leaks found" over 48.37 MB. The export mirrors the CI checkout; the local tree also holds an untracked `.env`. Branch commits (`gitleaks git --log-opts=origin/main..HEAD --redact`): 8 commits scanned, no leaks found. |
| unit (full) | guide | PASS (no regressions) | 21m43s | `poetry run pytest tests/ -q -o addopts="--import-mode=importlib" --continue-on-collection-errors` (`make test`) | 18292 passed, 209 failed, 72 errors, 192 skipped. Failures classified against the merge-base below: 0 caused by this change. |
| targeted | guide | PASS | ~1.5m | `poetry run pytest tests/enterprise/litellm/test_llm_factory.py tests/codemie/agents -o addopts="--import-mode=importlib"` | 823 passed, 4 skipped (at 99faa4582) |
| targeted (alias scope) | guide | PASS (no regressions) | ~4m | `poetry run pytest tests/enterprise/litellm/ <all test files referencing LLMModel> <factory caller tests> -o addopts="--import-mode=importlib"` | At 562eb0ca0: tests/enterprise/litellm 479 passed, 1 failed (`test_credentials.py::test_delete_user_litellm_setting_clears_user_cache`: `ModuleNotFoundError: exchangelib`, stale venv). LLMModel-referencing files: the same 35 failed/error ids with and without the change (stash comparison), so none comes from this change. Factory caller tests: 124 passed. Ruff clean. |
| coverage | guide | SKIPPED | — | `make coverage` | Not requested (guide: "Skip if: The user did not request coverage") |
| sonar | guide | SKIPPED | — | `make sonar-local` | No `SONAR_*` token and no `.env.local`. Guide: "Skip if: ... required credentials are unavailable" |
| test-harness | guide | PASS | 15m01s | `poetry run codemie-test-harness run --marks "api and sanity" -n 2 --reruns 10` from `codemie-sdk/test-harness` at 85009144 | Run by the user against the local stack built from this branch: `197 passed, 4 skipped in 901.64s`. An earlier failure of `test_conditional.py::test_logical_not_operator` came from the VPN being down: DIAL (`ai-proxy.lab.epam.com`), which serves `gpt-4.1`, was unreachable. With the VPN back, the test passed alone 2/2 and its file passed 18/18 with `-n 2`. |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed |
| commit-msg | hook | PASS | <1s | `bash scripts/git-hooks/commit_msg.sh <msg>` | All 4 commits pass (hooks are not installed in `.git/hooks`, so this was run manually) |
| pre-commit (ruff + license) | hook | PASS | — | `scripts/git-hooks/pre_commit.sh` | Covered by the lint and license gates above |
| gitleaks-staged | hook | PASS | — | covered by the branch-commit gitleaks scan above | Hooks are not installed in `.git/hooks`; the 8 branch commits were scanned directly |
| pre-push (pytest + sonar) | hook | PASS / SKIPPED | — | `scripts/git-hooks/pre_push.sh` | Pytest is covered by the full run; the Sonar part is SKIPPED as above |
| pre-ci-guard | ci | N/A | — | `.gitlab-ci.yml` pre-ci-guard | Needs the `ci-pipeline` and `compliance-report` statuses on the MR (approving `code-review-final.json` committed; `## Test harness` section required) |

## Full-suite failure classification

Each failing file (97 files, 281 ids) was re-run at merge-base `2b039d66c` in a temporary worktree.

- 273 ids fail identically at the merge-base. These are environment-only groups:
  - code-executor filesystem sandbox (Windows)
  - `tests/enterprise/mcp_auth/*` and `switchyard/*` (older enterprise stub in the venv)
  - cli_analytics postgres
  - startup/keycloak tests
  - git loader
  - 56 collection errors from the stale `.venv`: `exchangelib` and `kiota_authentication_azure` are missing, and litellm is 1.84.0 against the locked 1.99.2
- 8 ids fail only on the branch. All are environment-caused; none touches the changed code:
  - 6 × `tests/scripts/test_ruff_staged_hook.py`: under `poetry run`, `bash` resolves to the WSL relay with no distro installed (`execvpe(/bin/bash) failed`). Run via the venv python directly, they are skipped in both checkouts.
  - 1 × `tests/codemie_tools/file_analysis/pptx/test_pptx_toolkit.py::test_pptx_tool_query_text_with_text`: the main checkout's working copy of `test.pptx.md` is CRLF, while the index is LF (`git ls-files --eol`: `i/lf w/crlf attr/text=auto eol=lf`). A fresh checkout passes.
  - 1 × `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py::test_logout_removes_only_the_users_rows_and_pod_fallback`: order-dependent; it skips or passes when run in isolation.

## Alias scope (d24b61650, 562eb0ca0)

- `LLMModel.claude_family` (excluded from serialization) is set from the proxy entry's `litellm_params.model`, `litellm_params.base_model` or `model_info.base_model`, or from the anthropic providers. A Claude model under an alias or a Bedrock application-inference-profile ARN now gets `max_tokens`.
- Checked with a FastAPI `TestClient` and the real `/llm_models` response model: the flag stays on the object and survives `model_copy`, and `claude_family` is absent from the JSON body.
- The full suite was not re-run for these two commits. The change is limited to `LLMModel`, the LiteLLM mapper and `_resolve_max_output_tokens`, and every test file that references them was run.

## Live verification on the local stack (2026-10-13)

Stack: `D:\Projects` docker compose (Rancher Desktop) with the backend image rebuilt from this branch and `src` mounted, plus LiteLLM proxy 1.99.2 with `LITELLM_LOG=DEBUG`. Bedrock is reached in `us-west-2` with the IAM user `Auto_EPM-CDME_CodeMieAutotests`. Test proxy models were added at runtime:
- `claude-opus-5-5` → `bedrock/us.anthropic.claude-opus-5-5`
- `opus-latest` → the same model, as an alias without "claude" in its name
- `coder-latest` → `bedrock/qwen.qwen3-coder-30b-a3b-v1:0` (non-Claude control)

| Scenario | Expected | Result |
|---|---|---|
| `GET /v1/llm_models` | Models listed with `max_output_tokens`; no `claude_family` key | 36 entries; claude-opus-5-5, opus-latest and claude-opus-4-6-20260205 show 128000 and coder-latest shows 32768; no entry exposes `claude_family` |
| Ticket STR in the UI: personal assistant, Opus 5.5, prompt for 1..8000 (streaming) | Completes to 8000 at ~23k tokens | Last line `8000`; the proxy reports 23,283 completion tokens (request sent 18:26:19, stream done 18:30:16) |
| Same STR through the REST API (`POST /v1/assistants/{id}/model`, non-streaming): claude-opus-5-5 and the alias opus-latest | Both reach 8000 | claude-opus-5-5: 8000 lines, 23,435 completion tokens. opus-latest: 8000 lines, 23,611 completion tokens |
| Backend limit log (AC6) | `llm_output_limit … source=model_config` for Claude, `proxy_default` for non-Claude | claude-opus-5-5, opus-latest and claude-opus-4-6-20260205: `max_tokens=128000 source=model_config`. coder-latest: `max_tokens=unset source=proxy_default` |
| Proxy → Bedrock body | 128000 forwarded for Claude | Proxy debug log: `max_completion_tokens: 128000` received and `max_tokens: 128000` sent upstream; no limit sent for Qwen |
| Fix switched off: `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS=false`, then the same STR (reproduces the old behaviour) | Bedrock default limit applies; truncation is logged (AC5, text-only part) | `llm_output_limit model=claude-opus-5-5 max_tokens=unset source=disabled_by_config`. Converse body sent `inferenceConfig: {}`. Output stopped at line 1583 with 4096 completion tokens. WARNING `truncated_response model=claude-opus-5-5 indicator=finish_reason=length output_tokens=4096 action=kept` |
| Fix switched back on | Back to 128000 | `max_tokens=128000 source=model_config` |

Things found while testing that do not come from this branch:
- `POST /v1/assistants/virtual/model` fails on main with `'Assistant' object has no attribute '_sa_instance_state'` (introduced in 6d40ebfa1, EPMCDME-14349). Probes used a saved assistant instead.
- The `FileAnalysisConfig` JSON-schema error is logged at ERROR (pydantic `CallableSchema`).

## Rebase onto main (2026-10-14)

The branch was rebased onto `origin/main` 067651d66 before the MR. Commit ids above (562eb0ca0 and earlier) are from before the rebase. The only conflict was `tests/codemie/agents/test_langgraph_event_adapter.py`, where both sides added test classes; both were kept. After the rebase:
- Ruff format/check and the license check are clean.
- `tests/enterprise/litellm/test_llm_factory.py`, `tests/enterprise/litellm/test_models.py` and `tests/codemie/agents` pass: 891 passed, 4 skipped.
- The live probe on the restarted backend logs `max_tokens=128000 source=model_config` for claude-opus-5-5 and opus-latest, and `source=proxy_default` for coder-latest.

The test harness run (197 passed) was taken before the rebase.

## Owed before or after the MR (only CI or reviewers can settle these)

- `make sonar-local` (needs a Sonar token).
- AC5 beyond the text-only path (exposing `stop_reason` in the API response) is deferred to EPMCDME-14145. Leave a comment there.
- Post-deploy AC2–AC4 verification (spec §7): look for `llm_output_limit model=claude-opus-5-5 max_tokens=128000 source=model_config` and run the 1..8000 repro in Preview and Prod.
- Optional: `poetry install` to sync the stale `.venv`, so the 56 uncollectable files run locally.

## Drift signal

no — the implementation matches spec §3 as updated for the review decisions CR-001 and CR-002.
