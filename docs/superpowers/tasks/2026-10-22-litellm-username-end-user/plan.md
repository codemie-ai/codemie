# EPMCDME-16273 LiteLLM end_user from username Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** LiteLLM end_user is built from `user.username` on datasource indexing and REST direct calls, and `user_email` is renamed to `username` across the LiteLLM identity chain.

**Architecture:** Rename first (tasks 1-3, each leaves the tree green by renaming a definition with all callers and tests), then three behavior edits and one guard edit (tasks 4-7) written test-first under the final names.

**Tech Stack:** Python, pytest, `unittest.mock`, poetry.

**Spec:** `docs/superpowers/tasks/2026-10-22-litellm-username-end-user/spec.md` (analysis: `technical-analysis.md` same dir)

## Global Constraints

- Commit per task using the repository's existing convention, ticket prefix `EPMCDME-16273` (`.ai-run/guides/standards/git-workflow.md`). Never stage `.codemie/codemie-cli.config.json` or `.serena/`.
- Run Python via poetry (`.ai-run/guides/development/setup-guide.md`, `quality-gates.md`). No code comments or new explanatory comments.
- Names: values `username`; ContextVar `current_username` (its string name too); `LoggingContextSnapshot` key `username`.
- `MetricsAttributes.USER_EMAIL` / `USER_NAME` constants and label names unchanged.
- Do not touch genuine-email sites: CLI analytics, leaderboard, analytics handlers, `personal_project_service`, registration/authentication services, `conversation_analysis_service`, `llm_service/utils.py` (`personal_project=user.email`, `_unfunded_project`), `proxy_router.py:663`.
- Keyword-only signatures: a missed call site is a runtime TypeError. In every rename task, run `grep -rn "user_email\|current_user_email" src tests` and fix every hit that belongs to the renamed symbol rather than trusting analysis lists; leave genuine-email hits.
- Out of scope: chargeback, worker-thread "unknown" propagation, single-helper refactor.
- Admin users list is out of scope: `user_repository.fetch_budget_assignments_map` no longer reads spend, and `user_management_service` gets every figure from the spend port, whose holder matches both `username` and `email` aliases (EPMCDME-13429). A rename grep must not touch `service/spend/` or the spend port's alias handling.

## Review Focus

- Empty-string username with email present: email is used (tasks 4, 5).
- Neither username nor email: user id used (task 4).
- `personal_project=""` or None: guard falls back to the identity (task 7).
- Placeholders `"unknown"` and `"-"` still pass through the resolver unchanged (task 5).
- Username equal to email: unchanged behavior (task 5).

### Task 1: Rename logging context identity

**Files:** Modify `src/codemie/configs/logger.py` (L28-32, L99, L164-190, L207-225); every `set_logging_info(user_email=...)` caller and `current_user_email` reader found by grep (incl. `rest_api/security/authentication.py`, `datasource/base_datasource_processor.py`, `file_datasource_update_processor.py`, `hedged_handler.py`, `core/dependecies.py` L31/L61/L164/L289, `base_monitoring_service.py:111`, `proxy_router.py:1317`, generator services, `workflow_output_change_request_service.py`, `skill_service.py`, `marketplace_management/llm_assessment_service.py`, `agent_streaming_callback.py`, agents, routers); tests in `test_logger_pytest.py`, `test_core_dependencies.py` patches, `test_hedged_handler.py`, monitoring and generator service tests.

**Interfaces:** Produces `current_username` ContextVar, `set_logging_info(uuid, user_id, conversation_id, username="-")`, snapshot key `username`.

Test-first: no — pure rename; existing tests renamed in this task keep passing.

- [ ] Rename `current_user_email` -> `current_username` (variable and ContextVar string name), the `set_logging_info` kwarg and the snapshot key; `copy_logging_context` and `restore_logging_context` change together. Update all callers, readers, patch targets and asserted dict keys in tests. Keep the `USER_EMAIL` label constants as the left-hand side at emitters.
- [ ] Run the repo-wide grep, then `poetry run pytest tests/codemie/configs tests/codemie/core tests/codemie/rest_api/handlers/test_hedged_handler.py tests/codemie/service/monitoring tests/codemie/service -k "logging or monitoring or generator or hedged or dependenc"`; all pass.

### Task 2: Rename budget service layer

**Files:** Modify `service/budget/provider.py:71-82` (`BudgetRuntimeContext.user_email` -> `username`), `service/budget/budget_resolution_service.py` (`build_runtime_context` L240, `dispatch_runtime`, `dispatch_runtime_sync` L307), `enterprise/litellm/budget_provider_adapter.py` (`_resolve_runtime_core` L1321 and other readers), `enterprise/litellm/project_member_runtime_sync.py` (`ensure_project_member_runtime_ready`, `..._sync` L372); their callers in `llm_factory.py` and elsewhere; tests `test_project_member_runtime_sync.py` (including the member-budget-override test added on `main`), `test_budget_provider_adapter.py`, `tests/codemie/service/budget/test_provider_registry.py`, `test_budget_handler.py`.

Test-first: no — pure rename; tests renamed in the same task.

- [ ] Rename the field and kwargs to `username`, update every caller (grep `user_email=` and `.user_email`) and test.
- [ ] Run `poetry run pytest tests/enterprise/litellm tests/codemie/service/budget tests/codemie/service/analytics/handlers/test_budget_handler.py`; all pass.

### Task 3: Rename LiteLLM factory and dependencies chain

**Files:** Modify `enterprise/litellm/dependencies.py` (`check_user_budget` L440, `get_premium_username` L604, `get_proxy_username` L619, `get_premium_customer_spending` L633, `get_proxy_customer_spending` L657, docstrings), `enterprise/litellm/llm_factory.py` (`_try_apply_premium_budget`, `_apply_platform_budget`, `_configure_direct_runtime_overrides`, `_resolve_direct_budget_category`, `_resolve_direct_project_budget_runtime`, `create_litellm_embedding_model`, `create_litellm_chat_model`, `get_litellm_chat_model`, `get_litellm_embedding_model`, docstring "User email for budget tracking"), `llm_factory.py` helpers added on `main` (`_project_in_context`, `_spend_attribution_values`, `_classify_direct_traffic`), `enterprise/litellm/__init__.py`, `proxy_router.py` callers and the premium `end_user` wording in `_build_premium_budget_error_body` (`{email}_{budget_name}` becomes `{username}_{budget_name}`), `enterprise/litellm/budget_categories.py:26-58` docstrings ("plain email address", `alice@example.com_codemie_cli` example), `core/dependecies.py` (`_resolve_user_email` -> `_resolve_username`, `user_email=` kwargs to `get_litellm_*`), `service/llm_service/utils.py:101-104` kwarg only; tests `test_llm_factory.py`, `test_proxy_router.py`, `test_premium_models_budget.py`, `test_spend_logs_metadata_transport.py`, `test_core_dependencies.py`.

Test-first: no — pure rename; tests renamed in the same task.

- [ ] Rename parameters, kwargs and docstrings to `username`. Leave the `llm_factory.py:617-627` guard logic unchanged (task 7), only rename its identifier.
- [ ] Quick check that `../codemie-enterprise` has no `user_email=` call into renamed functions (`grep -rn "user_email" ../codemie-enterprise`); report any hit.
- [ ] Run repo-wide grep, then `poetry run pytest tests/enterprise tests/codemie/core`; all pass.

### Task 4: Datasource processing context uses username first

**Files:** Modify `datasource/base_datasource_processor.py:286-292`; Test `tests/codemie/datasource/test_base_datasource_processor_llm_context.py`.

Test-first: yes — `_setup_processing_context` with email "a@x.com" and username "alice" asserts `set_logging_info` called with `username="alice"`.

- [ ] Add tests to `TestBaseDatasourceProcessorLLMContext` (set both `user.email` and `user.username` explicitly on the MagicMock): `mock_set_logging.assert_called_once_with(uuid="req-uuid-1", user_id="user-abc", username=<expected>)` for username differing from email (username), username `""` with email (email), and both empty (user id).
- [ ] Run `poetry run pytest tests/codemie/datasource/test_base_datasource_processor_llm_context.py -v`; the username case FAILS.
- [ ] Change the value to `username or email or id` (getattr-based as today).
- [ ] Rerun the file plus `tests/codemie/datasource`; PASS.

### Task 5: Resolver prefers username

**Files:** Modify `core/dependecies.py:219-227` `_resolve_username`; Test `tests/codemie/core/test_core_dependencies.py`.

Test-first: yes — `get_llm_by_credentials` with placeholder identity and a current user with email and different username passes the username to `get_litellm_chat_model`.

- [ ] Using `_make_current_user(email, username)` and the `mock_dependencies` fixture, add cases: both set (username wins), empty username (email returned), `"unknown"` and `"-"` with no current user pass through unchanged. Add a `get_embeddings_model` test (patch `codemie.enterprise.litellm.get_litellm_embedding_model`) asserting `call_args[1]["username"]` is the username.
- [ ] Run the file; both-set cases FAIL. Change the fallback to `current_user.username or current_user.email or None`. Rerun; PASS.

### Task 6: Authentication sets username

**Files:** Modify `rest_api/security/authentication.py:162-167`; Test the existing authentication test module under `tests/codemie/rest_api/security/` (locate by grep `authenticate`; create `test_authentication_logging_context.py` there if none fits).

Test-first: yes — authenticate a user with email "a@x.com" and username "alice", patched `set_logging_info` asserted called with `username="alice"`.

- [ ] Write the test, run it (FAILS with the email), pass `user.username`, rerun (PASS).

### Task 7: Personal-project guard uses personal_project

**Files:** Modify `enterprise/litellm/llm_factory.py:617-627` (`has_real_project_context`) and its stale comment/docstring wording at L617-621; Test `tests/enterprise/litellm/test_llm_factory.py`.

Test-first: yes — runtime context with `current_project="a@x.com"`, `personal_project="a@x.com"`, identity `"alice"` and project budget scopes True is not a real project context.

- [ ] Add tests: personal project not real; a different `current_project` still real; `personal_project=None` and `personal_project=""` fall back to the identity; agent-like case (identity `"alice"`, `current_project` equal to the email-named personal project, `personal_project` set). Use the existing nested `patch.object(config, ...)` style.
- [ ] Run; the personal-project cases FAIL.
- [ ] Compare `current_project != (runtime_context.personal_project or username)` (falsy check, not `is None`). Rerun `poetry run pytest tests/enterprise/litellm`; PASS.

## Negative constraints

- Genuine-email sites untouched: tasks 1-3 greps only touch identity-chain hits; `personal_project=user.email` stays.
- `MetricsAttributes` unchanged: task 1 renames only right-hand variables.
- No worker-thread propagation, no helper refactor, no chargeback code: no task adds them.
- Admin users list and `user_repository` untouched: spend is read through the spend port, which matches both username and email aliases (Global Constraints).
- Placeholders pass through unchanged: task 5 test.
- No code comments: Global Constraints.
