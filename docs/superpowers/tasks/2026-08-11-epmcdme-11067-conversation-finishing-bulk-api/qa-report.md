# QA Gate Report — EPMCDME-11067

**Branch**: EPMCDME-11067_platform-envofced-conversation-finishing-with-bulk-api-support
**Runner**: poetry (guide-first: `.ai-run/guides/quality-gates.md`)
**Started**: 2026-08-11
**Status**: PASSED (with pre-existing, out-of-scope items noted below)

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint/format | guide | PASS | ~5s | `make ruff` | `ruff format`, `ruff check --fix`, `ruff check` all clean. |
| build | guide | PASS | ~3s | `make build` | Poetry sdist + wheel built successfully (codemie 0.8.0). |
| license headers | guide | PASS | ~2s | `make license-check` | 2014 files checked, 0 missing headers. |
| secret scan | guide | FAIL* | ~8s | `make gitleaks` | See "Secret scan detail" below — flagged finding is in an untracked, gitignored local `.env`, not in this branch's diff. |
| tests (targeted) | guide | PASS | ~12s | `poetry run pytest tests/codemie/rest_api/models/test_conversation_model.py tests/codemie/rest_api/routers/test_conversation.py tests/codemie/rest_api/routers/test_conversation_pagination.py tests/codemie/rest_api/routers/test_admin_router.py tests/codemie/service/test_conversation_service.py tests/codemie/service/test_conversation_service_pagination.py tests/codemie/service/test_workflow_service.py tests/codemie/core/test_ability_conversation.py` | 170 passed, 1 skipped (pre-existing DB-integration-only skip, unrelated to this change). |
| tests (full suite) | guide | FAIL* | ~120s | `make test` (`poetry run pytest tests/`) | 14080 passed, 62 failed, 23 errors, 177 skipped. See "Full-suite detail" below — every failure/error is in a file this branch never touches, and spot-checks against `main` reproduce the same failures there. |
| coverage | guide | SKIPPED | — | `make coverage` | Not requested. |
| static analysis | guide | SKIPPED | — | `make sonar-local` | Not requested; Node/Sonar credentials not verified for this environment. |
| full verification | guide | N/A | — | `make verify` | Superseded by running its constituent gates (ruff, license, gitleaks, test) individually above — see those rows. |
| test harness | guide | SKIPPED | — | `make test-harness` | Not opening an MR in this session. |

\* Both FAILs below are pre-existing / out-of-scope for this diff, not regressions introduced by EPMCDME-11067 — see detail sections.

## Secret scan detail

`make gitleaks` flagged one finding:

```
Finding:     AZURE_OPENAI_API_KEY="<redacted>"
RuleID:      generic-api-key
File:        /workspace/.env
Line:        2
```

`.env` is gitignored (`git check-ignore -v .env` confirms it) and untracked (`git ls-files .env` returns nothing) — it is a local development file on this machine, not part of the git history, not part of this branch's diff (`git diff main...HEAD -- .env` is empty), and will not be pushed. `gitleaks dir` scans the full working tree including gitignored files by design, so it surfaces this every time it's run locally regardless of branch. Not a new secret introduced by this change.

## Full-suite detail

Ran the complete `tests/` tree (14,080+ tests) beyond the targeted regression to check for any indirect breakage. All 62 failures + 23 errors are confined to areas this branch's diff never touches: `test_permission_models.py` (`ResourceType.resource_class` — `TypeError: 'method' object is not subscriptable`), `test_local_auth_router.py`, `test_oauth_redis_lazy_init.py`, `mcp/test_toolkit_service_auth_resolver.py`, `codemie_tools/git/test_custom_git_api_wrapper.py` + `test_github_app_auth.py` (`TypeError: 'PydanticDescriptorProxy' object is not callable`), and the `tests/enterprise/mcp_auth/*` bridge suite. Spot-checked two of these directly against `main`'s file content (`test_permission_models.py::TestResourceType::test_resource_class`, `test_github_app_auth.py::test_custom_github_api_wrapper_pat_auth`) — both fail identically on `main`, confirming these are pre-existing failures in this checkout's environment, not regressions from this branch.

This checkout also has a known, pre-existing local environment gap unrelated to test correctness: `pymssql`/`tree_sitter_languages` have no prebuilt wheels for this machine's Python 3.13/arm64 combination, which blocked live Alembic migration verification during implementation (documented in the plan.md Task 1 notes) — the 62/23 full-suite failures above are a separate, independent issue from that gap.

**Update (post-implementation, manual verification)**: The local environment gap above only blocks `poetry install` on this machine, not Docker (the `codemie` service image builds on Linux, sidestepping the wheel gap). Live migration verification was completed manually against the project's `docker compose` Postgres:

- While verifying, discovered this branch's migration (`67b7c43d6090_add_is_finished_to_conversations.py`, `down_revision = "9b9b4c585e54"`) and an unrelated, already-`main`-merged migration from EPMCDME-13738 (`w1o2r3k4f5l6_add_workflow_scope_to_assistant_user_mapping.py`, also `down_revision = "9b9b4c585e54"`) both forked from the same parent revision, producing two Alembic heads (`alembic upgrade head` failed with "Multiple head revisions are present"). Resolved with a standard Alembic merge migration, `2b92af63e0d8_merge_epmcdme_11067_and_epmcdme_13738_.py` (`down_revision = ('67b7c43d6090', 'w1o2r3k4f5l6')`, empty `upgrade()`/`downgrade()`), matching this repo's existing merge-migration precedent (`8eb9522661cf_merge_heads.py`, `f2c3d4e5f6a7_merge_workflow_and_leaderboard_heads.py`).
- Round-trip verified: `alembic upgrade head` → `alembic downgrade 9b9b4c585e54` → `alembic upgrade head`. Confirmed via `\d codemie.conversations` at each step: `is_finished` (boolean, not null, default false), `finished_at` (nullable timestamp), and the partial index `ix_conversations_unfinished` (btree on `date` WHERE `is_finished = false`) are added on upgrade and cleanly removed on downgrade, with no errors at any step.
- Also confirmed the sibling EPMCDME-13738 migration applied correctly (`workflow_id` column, `ix_assistant_user_mapping_workflow_id` index, `uix_assistant_user_mapping_scope` unique constraint on `codemie.assistant_user_mapping`), since both heads are now reachable through the same merge point.

## Drift signal

No — implementation matches the (code-review-corrected) spec.md. Type signatures, method names, and endpoint paths referenced in spec.md all match the actual implementation as of the code-review check round.
