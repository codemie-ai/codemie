# EPMCDME-16273: LiteLLM end_user from username, not email

## Problem

The LiteLLM `end_user` (customer id) is built from `user.email` on two paths, while proxy chat and the other paths use `user.username`. Spend for one account is therefore split across two customers, and chargeback v3 drops the email-form rows.

- Datasource indexing: `base_datasource_processor.py:287-291` (`_setup_processing_context`) sets the logging identity to `email or username or id`. This overrides the username set in the constructor (L102). `get_elasticsearch` then calls `get_embeddings_model`, which sends the email to LiteLLM. It affects all 13 `BaseDatasourceProcessor` subclasses.
- REST direct calls: `authentication.py:162-166` sets the identity to `user.email` on every request. Handlers that do not reset it (`POST /index/<id>/search`, `POST /tools/<name>/invoke`, chat naming, history compaction, generators) reach `llm_factory._apply_platform_budget` with the bare email. The path is `core/dependecies.py` L61 and L289, then `llm_factory.py:~524`.
- `_resolve_user_email` (`core/dependecies.py:219-227`) falls back to `email or username`.

## Approach ("option c")

1. Three behavior edits, all required by the ticket:
   - `authentication.py:166` passes `user.username`.
   - `base_datasource_processor.py:291` becomes `username or email or id`. The email stays as the second fallback so that a user with an empty username does not get their UUID as a third LiteLLM customer id.
   - `_resolve_user_email` returns `current_user.username or current_user.email or None`.
2. One deliberate non-pure-rename edit, the personal-project guard at `llm_factory.py:617-627`. It is a behavior change, not a rename. The guard compares `runtime_context.current_project` against the identity value, and `current_project` is the email-named personal project (`llm_service/utils.py:148-153`).
   - Agent and workflow paths (`assistant_agent`, `langgraph_agent`, workflow) already send the username today. For them the comparison already never matches a personal project, so the guard is already ineffective there. The edit fixes that drift too, not only the paths changed by this ticket.
   - New logic: `personal = runtime_context.personal_project or <identity>`, then `current_project != personal`. Use a falsy check, because `personal_project` can be an empty string `""` as well as None. `_record_direct_budget_attribution` already compares against `personal_project`.
   - Practical effect: a personal project (email-named) is again not treated as a real project context, on every path, as long as the guard is reached.
   - `main` also has `llm_factory._project_in_context` (EPMCDME-13429), which keeps a personal-scope `current_project` out of the spend-logs metadata by comparing it with `{identity, principal_email}`. It agrees with this guard (both treat the username and the email as the personal scope). This ticket only renames its identity parameter to `username` and leaves its logic unchanged.
3. Pure rename of `user_email` to `username` (names, kwargs, docstrings, comments, tests) across the LiteLLM identity chain:
   - `configs/logger.py`: `current_user_email` becomes `current_username`, the `set_logging_info` arg, and the `LoggingContextSnapshot` key. `copy_logging_context` and `restore_logging_context` change together, and so does the ContextVar's string name.
   - `core/dependecies.py`: `_resolve_user_email` and its callers.
   - `enterprise/litellm/llm_factory.py`, `dependencies.py` (`check_user_budget`, `get_premium_username`, `get_proxy_username`, `get_premium_customer_spending`, `get_proxy_customer_spending`), `budget_provider_adapter.py` and `project_member_runtime_sync.py`.
   - `service/budget/provider.py` (`BudgetRuntimeContext.user_email`) and `budget_resolution_service.py`.
   - Every `set_logging_info(user_email=...)` caller and every context-var reader. The implementer enumerates them with a repo-wide search for `user_email=` and `current_user_email`.
   - Stale docstrings and comments that say "email": the personal-project guard comment in `llm_factory.py`, `budget_categories.py` (`derive_category_from_user_id` example and "plain email address"), the `dependencies.py` docstrings, and the premium `end_user` wording (`{email}_{budget_name}`) in `proxy_router._build_premium_budget_error_body`.
   - The affected tests in the modules named below.
4. Admin users list spend: no code change in this ticket.
   - This ticket first planned to switch `user_repository.fetch_budget_assignments_map` from `lower(users.email)` to `lower(users.username)` when matching `project_spend_tracking.project_name`, because the admin list showed only the email-keyed slice of spend for users whose username differs from their email.
   - EPMCDME-13429 (`9c288b290f` on `main`) removed that join and the `collect_personal_spend` code with it. The list now asks the spend port for every figure (`user_management_service._current_spending_by_assignment`), and each holder carries `aliases_for_user(user.username, user.email)` (`service/spend/identity_bridge.py`).
   - A user whose username differs from their email is therefore matched under both identifiers. Email-keyed spend written before this ticket and username-keyed spend written after it are both counted, so nothing freezes or goes blank at deploy.
   - The planned join edit, its test and the docstring edit were dropped when the branch was rebased onto that change. Alias coverage of the list is already tested in `tests/codemie/service/user/test_user_management_service_list_users_spend.py`.
5. Metrics: `MetricsAttributes.USER_EMAIL` and `USER_NAME` constants and label names are unchanged. Only the right-hand variable is renamed. The context var is not part of any log format, so there is no log-line change.

Names: `username` for values, `current_username` for the ContextVar. Keyword-only signatures mean a missed call site raises TypeError at runtime, so a full-repo grep and a run of the affected test modules are part of done.

## Readers of the context var (AC4 inventory)

Value changes for plain REST requests (email to username), because `authentication.py` now sets the username:
- `service/monitoring/base_monitoring_service.py:111` (`emit_llm_token_metric`, label `USER_EMAIL`).
- `enterprise/litellm/proxy_router.py:1317` (`emit_llm_error_log`, label `USER_EMAIL`).
- `core/dependecies.py:169` (`get_project_for_metric`, the project label falls back to the identity when there is no project). The label value goes from email to username for such requests.
- `core/dependecies.py:64` and `:294` (`get_embeddings_model`, `get_llm_by_credentials`). This is the intended change.

Do not change value, or align with emitters that already send the username (their routers call `set_logging_info(username=user.username)` first, and where they run on a plain REST path they move from email to username as the existing emitters do):
- `service/assistant_generator_service.py` (6 `USER_NAME` sites), `skill_generator_service.py` (4), `workflow_generator_service.py` (3), `workflow_execution/workflow_output_change_request_service.py` (1), `skill_service.py:1740` (1), `marketplace_management/llm_assessment_service.py:243` (1, added on `main` by EPMCDME-14234 and renamed in this ticket).
- Pure propagation, no new value: `configs/logger.py:211` (`copy_logging_context`) and `agents/callbacks/agent_streaming_callback.py:477` (re-sets the current value).

## Acceptance criteria

- AC1: For a user whose username differs from their email, code, Confluence/Jira and file indexing jobs produce spend logs with `end_user=<username>` (plus category suffix where applicable).
- AC2: `POST /index/<id>/search` and `POST /tools/<name>/invoke` by a service account produce `end_user=<username>`.
- AC3: No new email-form `end_user` rows appear for such accounts. This is checked live with the repro at `/tmp/repro16273/repro.py` (user `repro16273-user` / `repro16273@codemie.ai`).
- AC4: Every reader in the inventory above is checked. The metric label names are unchanged, and the value changes are listed in the Jira comment. The claim of no dashboard regression covers in-repo analytics only (repo queries, handlers and dashboards defined in code); external dashboards cannot be checked from the code and are named as a rollout item.
- AC5: Unit tests cover:
  - `_setup_processing_context` calling `set_logging_info` with the username when email and username differ, with an empty username and an email present (the email is passed), and with neither (the id is passed). This extends `tests/codemie/datasource/test_base_datasource_processor_llm_context.py`.
  - `get_embeddings_model` passing the username to `get_litellm_embedding_model`.
  - `get_llm_by_credentials` and `_resolve_user_email` with both fields set (the username wins) and with an empty username (the email is returned).
  - The guard in `llm_factory`: a personal project (`current_project == personal_project`, differing from the username) is not a real project context; a real project still is; `personal_project=None` and `personal_project=""` both fall back to the identity value; the agent-like case where the identity is the username and `current_project` is the email-named personal project.
  - `authentication` setting the username in the logging context.
- AC5a: `GET /v1/admin/users` keeps returning `budget_assignments[].current_spending` for a user whose username differs from their email, counting spend recorded under either identifier. No code or test is added by this ticket; the behavior comes from the spend port's alias holder (see Approach 4) and is covered by `tests/codemie/service/user/test_user_management_service_list_users_spend.py`. Check live once on the local stack after the rebase.
- AC6: The chargeback v3 email-fallback follow-up and the October 2026 backfill are linked in Jira. This is Jira work only, with no code.
- Renamed tests pass. Affected modules: `tests/enterprise/litellm/` (`test_llm_factory`, `test_proxy_router`, `test_project_member_runtime_sync`, `test_premium_models_budget`, `test_budget_provider_adapter`, `test_spend_logs_metadata_transport`, `test_litellm_dependencies`), `tests/codemie/core/test_core_dependencies.py`, `tests/codemie/configs/test_logger_pytest.py`, `test_hedged_handler.py`, `test_budget_handler.py`, the project and agent monitoring service tests, `test_base_monitoring_service.py`, `tests/codemie/service/budget/test_provider_registry.py`, the assistant generator service tests, and the `marketplace_management` tests. `make ruff` is clean.

## Non-goals

- No change to genuine-email data: CLI analytics, leaderboard, analytics handlers, `personal_project_service`, registration and authentication services, `conversation_analysis_service`, `llm_service/utils.py` (`personal_project=user.email`, `_unfunded_project`), and `proxy_router.py:663`. A blind search-and-replace must not touch these.
- No change to the `MetricsAttributes` constants or label names.
- No chargeback changes (separate repo) and no backfill code.
- No propagation of logging context into worker threads (the "unknown" user). Placeholders `"unknown"` and `"-"` still pass through `_resolve_user_email` unchanged.
- No single LiteLLM-user helper refactor.
- No other behavior change beyond the three edits and the guard edit above.
- No change to the admin users list, `user_repository` or the spend port: EPMCDME-13429 owns how spend is read and attributed to a person.
- No code comments added (project convention).

## Risks and rollout

- Pre-deploy check (rollout step): for accounts that already have both an email-form and a username-form customer (including the `{identifier}_codemie_*` category forms), compare the sum of email-form plus username-form spend against the `max_budget` of the budget they will be assigned. After deploy, all of that spend counts against one username cap, so indexing and REST calls for those accounts can hit hard limits. Raise caps or reset beforehand where needed.
- Accounts that only ever indexed or used REST direct calls move to a new username-form customer with a fresh cap and a fresh 5-minute customer cache. Email-form customers remain in LiteLLM until the separate backfill.
- External dashboards or alerts that filter the `USER_EMAIL` label or the project-metric fallback by email address cannot be verified from the code. Name them as an owner check before deploy.
- A missed keyword call site fails at runtime, not at import. Mitigation is the repo-wide grep and the test runs above.
- Users whose username equals their email are unaffected.
- Admin users list: no behavior change from this ticket. The spend port counts both the username and the email alias, so email-keyed spend from before deploy stays in `current_spending`. No release-note entry is needed for it.
- The `../codemie-enterprise` claims (no `user_email` use, ids treated as opaque) come from the requester and are not re-verified here. The implementer should do a quick grep to confirm.
