# Technical Research

**Task**: litellm end_user username identity budget logging-context datasource-indexing
**Generated**: 2026-10-22
**Research path**: codegraph

---

## 1. Original Context

User request: "Lets implement the fix requested in https://jiraeu.epam.com/browse/EPMCDME-16273. I would prefer to go with option c and avoid places where it is not pure rename but behavior change. This is just a suggestion, make sure that we cover everything what is written in Jira and follow criteria mentioned there. No need to do anything related to chargeback (it is a separate repo and will be handled separately). Proceed with dirty git state"

Jira EPMCDME-16273 (Bug): "LiteLLM customer id built from user email instead of username for datasource indexing and REST direct LLM/embedding calls (spend split, dropped by chargeback v3)".
Description: Every LiteLLM end_user should be built from user.username (plus category suffix) in all code paths, so spend for one account is tracked under a single LiteLLM customer id. Two code paths build it from user.email.
Root cause:
1. base_datasource_processor.py:287-291 (_setup_processing_context, called from process() in a background thread) sets set_logging_info(user_email=getattr(self.user,'email',None) or self.user.username or self.user.id). It overrides the username set in the constructor (line 102). get_elasticsearch -> get_embeddings_model then fixes the LiteLLM user to the email. Applies to all 13 BaseDatasourceProcessor subclasses. file_datasource_update_processor.py:198 already uses username.
2. authentication.py:162-166 sets user_email=user.email on every request (EPMCDME-12577). REST handlers that don't reset it (POST /index/<id>/search, POST /tools/<name>/invoke, chat naming, history compaction, generators) send the email via core/dependecies.py:61,289 into llm_factory.py:524, no suffix.
3. core/dependecies.py:225 (_resolve_user_email) prefers current_user.email over username.
4. (Related, NOT in acceptance criteria) worker threads creating LLM clients without context send user="unknown".
Suggested fix: (1) base_datasource_processor.py:287 username first; (2) authentication.py:166 pass user.username after checking consumers of current_user_email (logger.py:211, base_monitoring_service.py:82, proxy_router.py:1195) or resolve username explicitly in core/dependecies.py; (3) one helper builds the LiteLLM user string and the resolver prefers username; (4) propagate logging context into worker threads; (5) chargeback v3 follow-up (OUT OF SCOPE here).
Acceptance Criteria:
1. For a user with username different from email, indexing jobs (code, Confluence/Jira, file) produce spend logs with end_user = username.
2. The same holds for POST /index/<id>/search and POST /tools/<name>/invoke by a service account.
3. No new email-form end_user rows appear for such accounts.
4. Consumers of current_user_email (logs, metrics) are checked, with no dashboard regression.
5. Unit tests cover the identity in the processing context setup, get_embeddings_model and get_llm_by_credentials.
6. The chargeback v3 email-fallback follow-up and the October 2026 backfill are linked (Jira work, no code).

Decisions already made with the user (treat as requirements):
- Rename scope "option c": rename the user_email name to username across the LiteLLM identity chain ONLY: configs/logger.py (current_user_email contextvar, set_logging_info(user_email=) arg, LoggingContextSnapshot key), core/dependecies.py (_resolve_user_email), enterprise/litellm/llm_factory.py, enterprise/litellm/dependencies.py (check_user_budget, get_premium_username, get_proxy_username, get_premium_customer_spending, get_proxy_customer_spending), service/budget/budget_resolution_service.py, service/budget/provider.py (BudgetRuntimeContext.user_email), enterprise/litellm/budget_provider_adapter.py, enterprise/litellm/project_member_runtime_sync.py, plus all set_logging_info(user_email=...) callers and current_user_email.get() readers, and the tests touching them.
- Keep MetricsAttributes.USER_EMAIL / USER_NAME metric label names and constants unchanged (only the variable on the right-hand side is renamed).
- Do NOT rename genuine-email data: CLI analytics, leaderboard, analytics handlers, personal_project_service, registration_service, authentication_service, conversation_analysis_service, llm_service/utils.py personal_project=user.email / _unfunded_project, proxy_router.py:663.
- Avoid places where it is not a pure rename (behavior change) except the ticket-required ones (authentication.py:166, base_datasource_processor.py:291, _resolve_user_email preference). Known non-pure-rename spot: llm_factory.py:617-625 compares runtime_context.current_project != user_email, where current_project is the personal project named after user.email (llm_service/utils.py:150-151) — research whether it should compare against litellm_context.personal_project, and what changes for REST/datasource flows after the fix (it is currently masked because has_project_budget_scopes is False for personal projects).
- Out of scope: chargeback, worker-thread "unknown" propagation, the single-helper refactor (not in acceptance criteria).
- Docstrings/comments that still say "email" in the renamed chain (llm_factory.py:617-618, budget_categories.py derive_category_from_user_id, dependencies.py docstrings) must be updated.
Already verified by the requester: sibling repo ../codemie-enterprise has no user_email and does not call the renamed functions; get_or_create_customer_with_budget(user_id,...) and _get_cached_customer(user_id) there treat the value as an opaque id. Live repro on the local docker stack confirmed email-form end_user for indexing, /index/<id>/search and /tools/image_tool/invoke (user repro16273-user / repro16273@codemie.ai), proxy chat completions gave the username form.

Research focus: exact call sites and signatures to rename (kwarg callers and tests, ~120 occurrences in 9 test modules: tests/enterprise/litellm/test_llm_factory.py, test_proxy_router.py, test_project_member_runtime_sync.py, tests/codemie/core/test_core_dependencies.py, tests/codemie/configs/test_logger_pytest.py, tests/codemie/rest_api/handlers/test_hedged_handler.py, tests/codemie/service/analytics/handlers/test_budget_handler.py, tests/codemie/service/monitoring/test_project_monitoring_service.py, test_agent_monitoring_service.py), existing test patterns for base_datasource_processor / dependecies / llm_factory to extend for acceptance criterion 5, and any other readers of the context var.

---

## 2. Codebase Findings

Limits of this pass: the tools available had no text search, so reader/caller lists below come from codegraph blast-radius output plus targeted reads. Codegraph reports `set_logging_info` with 39 callers in about 16 files and `current_user_email` with 11 readers; only part of them are named here. The implementer must run a repo-wide search for `user_email=` and `current_user_email` to get the full rename list.

### Existing Implementations (identity chain, verified on disk)

Context variable and logging (`src/codemie/configs/logger.py`):
- L28-32 `LoggingContextSnapshot` TypedDict has key `user_email`.
- L99 `current_user_email = contextvars.ContextVar("user_email", default="unknown")` (the ContextVar's string name is also "user_email").
- L164-190 `set_logging_info(uuid, user_id, conversation_id, user_email="-")`; None is coerced to "-".
- L207-212 `copy_logging_context()` emits `"user_email": current_user_email.get("unknown")`; L220-225 `restore_logging_context()` reads `snapshot["user_email"]` and calls `set_logging_info(user_email=...)`.
- The log formats (LOCAL_LOG_FORMAT, LOG_FORMAT) and `record_factory` do NOT emit the value. Log lines carry only uuid, user_id, conversation_id, trace and span ids. The context var is therefore not a log-line field.

Producers of the value (writes):
- `src/codemie/rest_api/security/authentication.py:162-167` `set_logging_info(..., user_email=user.email)` on every authenticated request. Ticket-required change to username.
- `src/codemie/datasource/base_datasource_processor.py:102` (constructor) already `user_email=user.username`. L286-292 `_setup_processing_context` sets `user_email=getattr(self.user,'email',None) or self.user.username or self.user.id`. Ticket-required change.
- `src/codemie/datasource/file/file_datasource_update_processor.py:198` already `user_email=self.user.username`.
- `src/codemie/rest_api/handlers/hedged_handler.py:68` already `getattr(handler.user,"username",None) or getattr(handler.user,"email",None) or "-"`.
- Other `set_logging_info` callers reported by codegraph: `rest_api/routers/assistant/generation.py`, `routers/skill.py`, `routers/workflow_executions.py`, `agents/assistant_agent.py`, `agents/langgraph_agent.py`, and about 8 more. Each needs a look to see which identity it passes.

Readers of the value:
- `src/codemie/core/dependecies.py` L31 import; L61 (`get_embeddings_model`) and L289 (`get_llm_by_credentials`) both do `user_email = _resolve_user_email(current_user_email.get())` and pass `user_email=` to `get_litellm_embedding_model` / `get_litellm_chat_model`.
- `core/dependecies.py:219-227` `_resolve_user_email(user_email)`: when the value is falsy, "-" or "unknown", falls back to `get_current_user()` and returns `current_user.email or current_user.username or None`. Ticket-required change: prefer username.
- `src/codemie/service/monitoring/base_monitoring_service.py:82` (`emit_llm_token_metric`) and `src/codemie/enterprise/litellm/proxy_router.py:1195` (`emit_llm_error_log`) write `MetricsAttributes.USER_EMAIL: current_user_email.get("-")` into metrics. After the ticket change this label's value becomes the username for REST requests.
- Also readers (from codegraph callers): `service/assistant_generator_service.py`, `skill_generator_service.py`, `workflow_generator_service.py`, `workflow_execution/workflow_output_change_request_service.py`, plus about 6 more not enumerated.

LiteLLM chain signatures that carry `user_email` today (`src/codemie/enterprise/litellm/llm_factory.py`):
- `_try_apply_premium_budget(*, llm_model_details, user_email, user_id, request_params, has_project_context)` L457; calls `get_premium_username(user_email, base_name)` and `check_user_budget(user_email=premium_username, ...)`, sets `model_kwargs["user"]`.
- `_apply_platform_budget(*, llm_model_details, user_email, user_id, request_params)` L503; L521-524 logs `litellm_customer_key={user_email!r}`, `check_user_budget(user_email=user_email, ...)`, `request_params["model_kwargs"] = {"user": user_email}`. This is where the plain email reaches LiteLLM as end_user with no suffix.
- `_configure_direct_runtime_overrides(..., user_email, ...)` L547; `_resolve_direct_budget_category(*, user_email, llm_model, availability)` L771; `_resolve_direct_project_budget_runtime(..., user_email)` L817; `create_litellm_embedding_model(..., user_email, user_id)` L928; `create_litellm_chat_model` (kwarg `user_email`); public wrappers `get_litellm_chat_model(llm_model_details, litellm_context, user_email, user_id=None, ...)` L1121 and `get_litellm_embedding_model(embedding_model, llm_model_details, litellm_context, user_email, user_id=None)` L1180, with docstring "User email for budget tracking". Many log f-strings already print the value under the key `username=`.
- `src/codemie/enterprise/litellm/dependencies.py`: `check_user_budget(user_email, budget_id=None, user_id=None)` L440 (docstring "User email (used as LiteLLM username ...)"; passes the value to `litellm._get_cached_customer`, `get_or_create_customer_with_budget`, `_cache_customer`, `_report_hard_limit_exceeded`, `_report_soft_limit_exceeded`); `get_premium_username(user_email, model)` L604; `get_proxy_username(user_email)` L619; `get_premium_customer_spending(user_email, on_raise)` L633; `get_proxy_customer_spending(user_email, on_raise)` L657. Callers of `get_premium_username`: llm_factory.py, proxy_router.py, `enterprise/litellm/__init__.py`. `get_proxy_username` has 4 callers incl. `__init__.py`.
- `src/codemie/enterprise/litellm/budget_categories.py:26-58`: `build_user_id(user_identifier, category)` and `derive_category_from_user_id`; the docstring example is `alice@example.com_codemie_cli` and "plain email address". `build_user_id` already uses the neutral name `user_identifier`.
- `src/codemie/service/budget/provider.py:71-82` `BudgetRuntimeContext` has field `user_email: str | None`.
- `src/codemie/service/budget/budget_resolution_service.py`: `build_runtime_context(resolved, *, user_id, user_email, model=None)` L240 (static), `dispatch_runtime(...)`, `dispatch_runtime_sync(resolved, *, user_id, user_email, model)` L307; log keys already say `username=`.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` `_resolve_runtime_core` L1321 reads `context.user_email` only inside log f-strings (key `username=`).
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py` `ensure_project_member_runtime_ready(...)` and `ensure_project_member_runtime_ready_sync(*, user_id, user_email, project_name, budget_category)` L372.

Evidence the intended identity is already the username: `service/llm_service/utils.py:101-104` `_unfunded_project` passes `user_email=user.username` into `_resolve_direct_budget_category`; hedged_handler and the datasource constructors pass username; proxy chat completions gave the username form in the live repro.

### The non-pure-rename spot (`llm_factory.py:617-627`)
```
has_real_project_context = bool(runtime_context and runtime_context.current_project
    and runtime_context.current_project != user_email and has_project_budget_scopes)
```
- `service/llm_service/utils.py:148-153` builds `LiteLLMContext(current_project=user.email if fallback_from else effective_project, personal_project=user.email, ...)`. `_resolve_effective_project` (L34-58) returns `user.email` for a global asset the user is not a member of with no default project. Therefore `current_project` can be the personal project, which is named after the email.
- `LiteLLMContext` (`rest_api/models/settings.py:194-200`) already has `personal_project` ("independent of the provider's username/customer ID") and `_record_direct_budget_attribution` (llm_factory.py:528-544) already compares against `context.personal_project`.
- Today the comparison works only because the old `user_email` value equals the email. Once the context var carries the username, `current_project (email) != user_email (username)` is always True for a personal project, so the guard no longer filters it. It stays harmless only while `has_project_budget_scopes` is False. That flag comes from `_resolve_direct_project_budget_runtime`, which probes `_probe_direct_project_budget_scopes(current_project, user_id)`; a project named after an email has no budget assignments in the normal case.
- Precedent for the alternative: compare against `runtime_context.personal_project` (already populated by `set_llm_context`). For contexts built without `personal_project` (default None, e.g. tests that build `LiteLLMContext(current_project=...)`), a None comparison never equals, which differs from the old behaviour only when current_project equals the old email value.
- Whether the datasource and REST flows change after the fix: they call `set_llm_context(...)` first (datasource: `base_datasource_processor.py:287` and `file_datasource_update_processor.py:197`), so `personal_project` is set; REST handlers that never call `set_llm_context` have `litellm_context.get(None)` None, so `_resolve_direct_project_budget_runtime` returns the global fallback (`False`) and the guard is not reached.

### Architecture and Layers Affected
- Logging/context layer: `configs/logger.py` (ContextVar, snapshot).
- Security/auth layer: `rest_api/security/authentication.py`.
- Core dependency layer: `core/dependecies.py` (resolver, embeddings, chat factory entry points).
- Datasource layer: `datasource/base_datasource_processor.py` and its 13 subclasses.
- Enterprise LiteLLM adapter layer: `enterprise/litellm/{llm_factory,dependencies,budget_categories,budget_provider_adapter,project_member_runtime_sync,proxy_router}.py`; enterprise package `codemie_enterprise` is imported only through `codemie.enterprise.litellm` (per `llm_proxy_provider_adapter.py` module doc).
- Budget service layer: `service/budget/{provider,budget_resolution_service}.py`.
- Monitoring layer (metric emitters reading the context var).

### Integration Points
- `check_user_budget` -> `LiteLLMService.get_or_create_customer_with_budget(user_id, ...)` and `_get_cached_customer(user_id)` in the enterprise package, treated as opaque ids (stated by the requester, not re-verified here).
- LiteLLM end_user reaches the proxy through `request_params["model_kwargs"] = {"user": ...}` (llm_factory.py:524, 498) or project runtime body override `body_overrides["user"]` (budget_provider_adapter.py:1366).
- `core/dependecies.get_elasticsearch` -> `get_embeddings_model` is the datasource indexing path.

### Patterns and Conventions
- Lazy imports inside functions to avoid circulars; kwargs-only signatures in the budget chain, so a rename touches every keyword call site and every test that passes or asserts `user_email=`.
- Log messages use `budget_event=... username=...` structured f-strings already.
- Project rule from user memory: no explanatory code comments in code changes.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/development/logging-patterns.md` (no coverage of the context var), `.ai-run/guides/integration/llm-providers.md`, `.ai-run/guides/testing/testing-patterns.md` and `quality-gates.md` exist in the guide set. No guide describes the LiteLLM customer id composition or `current_user_email`.

### Architectural Decisions
EPMCDME-12577 (authentication.py sets the email on every request) is cited in the Jira text only. Stable category suffixes: `build_user_id` docstring says suffixes "must match existing LiteLLM customer entries".

### Derived Conventions
`_unfunded_project`, hedged_handler and the datasource constructors treat `username` as the LiteLLM identity; `build_user_id` takes a neutral `user_identifier`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/datasource/test_base_datasource_processor_llm_context.py`: class `TestBaseDatasourceProcessorLLMContext`; builds `processor = MagicMock()` with `.user = MagicMock()`, `.user.id`, `.request_uuid`, `.callbacks = []`, calls `BaseDatasourceProcessor._setup_processing_context(processor)` unbound, patches `codemie.service.llm_service.utils.set_llm_context`, `codemie.datasource.base_datasource_processor.set_logging_info` and `DatasourceMonitoringCallback`. Only asserts `set_llm_context` args; `mock_set_logging` is never asserted. This is the natural place for the processing-context identity test (set `processor.user.email` and `processor.user.username` to different values, assert `mock_set_logging.assert_called_once_with(uuid="req-uuid-1", user_id="user-abc", user_email/username=<username>)` under the final kwarg name). Note `MagicMock()` user makes `getattr(user,'email',None)` truthy by default, so fixtures must set both attributes explicitly.
- `tests/codemie/datasource/test_base_datasource_processor.py` also exists (not read).
- `tests/codemie/core/test_core_dependencies.py`: class `TestGetLLMByCredentialsUserFallback`; fixture `mock_dependencies` patches `codemie.core.dependecies.{llm_service, litellm_context, current_user_email, logging_user_id, logger}`, `codemie.enterprise.litellm.get_litellm_chat_model`, `codemie.rest_api.security.user_context.get_current_user`; helper `_make_current_user(email, username)`; assertions read `call_args[1]['user_email']`. Cases: valid value, fallback on "unknown", "-", "", None, `test_fallback_uses_username_when_email_absent`, no-user passthrough ("unknown", "-"). All use email-only users, so they stay valid after "prefer username, fall back to email". There is no case with both email and username set (the case that proves the fix) and no `get_embeddings_model` test in this file.
- `tests/enterprise/litellm/test_llm_factory.py`: plain pytest classes using nested `patch.object(config, ...)` and `patch("codemie.enterprise.litellm.dependencies.check_user_budget")`; `create_litellm_chat_model(llm_model_details=MagicMock, litellm_context=None, user_email="test@example.com")` style calls. Extension point for asserting `model_kwargs["user"]` equals the username (no suffix for platform) and that the premium path yields `{username}_codemie_premium_models`.
- Other test modules listed by the requester that pass or assert `user_email`: `tests/enterprise/litellm/test_proxy_router.py`, `test_project_member_runtime_sync.py`, `test_premium_models_budget.py` (covers `get_premium_username`, `get_proxy_username`), `tests/codemie/configs/test_logger_pytest.py` (`test_set_logging_info`, `test_copy_and_restore_logging_context`, `test_logging_context_propagates_to_new_thread`), `tests/codemie/rest_api/handlers/test_hedged_handler.py`, `tests/codemie/service/analytics/handlers/test_budget_handler.py`, `tests/codemie/service/monitoring/test_project_monitoring_service.py`, `test_agent_monitoring_service.py`. Other candidates found via codegraph: `tests/enterprise/litellm/test_budget_provider_adapter.py`, `tests/codemie/service/budget/test_provider_registry.py`, `tests/codemie/service/test_assistant_generator_service*.py` (25 test callers of `current_user_email` overall).
- `tests/codemie/datasource/file/test_file_datasource_update_processor.py` fixture `mock_user` uses `username = "user1@example.com"` (username in email form), so it cannot discriminate username from email.

### Testing Framework and Patterns
pytest with `unittest.mock` (`MagicMock`, `patch`); tests patch names at their import location (`codemie.core.dependecies.current_user_email`); class-grouped tests. Per AGENTS.md tests are written or run only when requested; here AC5 asks for them.

### Coverage Gaps
- No assertion on `set_logging_info` kwargs in `_setup_processing_context`.
- No test of `get_embeddings_model` identity (its two helper calls `get_litellm_embedding_model(user_email=...)` and `_resolve_user_email`).
- No `_resolve_user_email` test with both email and username present.
- No test that `authentication.authenticate` sets the context identity.
- No test around `llm_factory.py:617-627` `has_real_project_context` with a personal-project `current_project` and a username that differs from the email.

---

## 5. Configuration and Environment

### Environment Variables
Relevant to routing, not identity: `LLM_PROXY_MODE` (must equal `lite_llm`), `LLM_PROXY_ENABLED` (via `is_litellm_enabled`), `LLM_PROXY_EMBEDDINGS_DISABLED` (embeddings fall back to native providers, bypassing the identity path), `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `LITELLM_PREMIUM_MODELS_ALIASES` (drives the premium suffix), `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED`.

### Configuration Files
Predefined budgets (`budget_config.predefined_budgets`) decide `get_category_budget_id` for platform/cli/premium_models; `is_proxy_budget_enabled` and `is_premium_models_enabled` are `lru_cache`d.

### Feature Flags and Deployment Concerns
No flag guards the identity. The change takes effect on deploy; no config toggles. Existing email-form LiteLLM customers remain in LiteLLM (backfill and chargeback are out of scope, linked per AC6).

---

## 6. Risk Indicators

- Metrics value change (AC4): `base_monitoring_service.py:82` and `proxy_router.py:1195` emit `MetricsAttributes.USER_EMAIL` from the context var. After the change the `user_email` metric attribute carries usernames for REST requests (hedged and datasource paths already did). Dashboards filtering that attribute by email address would shift. Any other `current_user_email.get()` reader not enumerated above (codegraph reports 11) needs the same check.
- `llm_factory.py:625` guard `current_project != user_email` silently stops matching for personal projects once the value is a username; currently safe only because `has_project_budget_scopes` is False for them. A personal project that did have budget scopes would be treated as a real project context and skip the personal premium budget.
- Spend continuity: switching identity creates new username-form LiteLLM customers for users who previously accumulated spend under their email (platform: bare email; cli/premium: `{email}_codemie_*`). Budget caps and the 5-minute customer cache restart per new identity. Chargeback/backfill handled elsewhere.
- Users whose username equals their email (e.g. file processor fixtures, many local users) are unaffected; mixed-identity users were the bug.
- Wide mechanical rename: kwarg `user_email=` is keyword-only in the budget chain, so a missed call site raises TypeError at runtime, not at import. `LoggingContextSnapshot` key rename must change `copy_logging_context` and `restore_logging_context` together, and `test_logger_pytest.py` asserts dict keys.
- Public re-exports in `enterprise/litellm/__init__.py` (`get_premium_username`, `get_proxy_username`, `get_litellm_chat_model`, `get_litellm_embedding_model`) are part of the core/enterprise boundary; the sibling enterprise package was reported by the requester as not calling them by keyword (not re-verified here).
- Do-not-touch email sites share tokens with the rename (`personal_project=user.email` in `llm_service/utils.py`, `_unfunded_project`, `proxy_router.py:663`, analytics, registration); a blind search/replace would hit them.
- ContextVar string name `"user_email"` (logger.py:99) is an internal name only; renaming it is cosmetic.
- Speculative: the `ContextVar` default `"unknown"` and `"-"` placeholders still reach LiteLLM when no request user exists (worker threads); out of scope per decisions, and `_resolve_user_email` keeps passing them through unchanged.
- Speculative: `_resolve_user_email` should return `username or email` (the last fallback keeps the existing email-only tests green); returning only username for users with empty username would change that edge case.
- Speculative: the `has_real_project_context` comparison could use `runtime_context.personal_project` and fall back to the identity value when None.

---

## 7. Summary for Complexity Assessment

The change spans seven layers (logging context, auth, core dependencies, datasource base processor, enterprise LiteLLM factory and dependencies, budget service, metric emitters) but is predominantly a keyword/identifier rename of `user_email` to `username` across a keyword-only call chain, with exactly three ticket-required behaviour edits: `authentication.py:166` (pass `user.username`), `base_datasource_processor.py:291` (username first), and `_resolve_user_email` (prefer username). Production files in the chain: about 12 named by the decisions plus an unenumerated set of `set_logging_info(user_email=)` callers (about 16 files by codegraph) and `current_user_email` readers (11). Test surface is about 9 modules and roughly 120 occurrences. Nothing is novel: no new modules, schemas, or config.

Test posture: existing tests are mock-heavy and easy to extend. Direct test hooks exist for `_setup_processing_context` (existing llm_context test file, logging assertion missing), `get_llm_by_credentials` (fallback test class, no both-fields case), `llm_factory` (create_litellm_chat_model tests), but nothing covers `get_embeddings_model` identity or the `has_real_project_context` comparison. The main judgment call is `llm_factory.py:625`, currently masked by `has_project_budget_scopes`; the codebase already has a `personal_project` field and a precedent for comparing against it.

Key risks: a missed keyword call site fails at runtime; the `MetricsAttributes.USER_EMAIL` metric value shifts from email to username on the two emitters (AC4); personal-project guard drift; spend restarting under new customer ids. Reader and caller enumeration is incomplete in this document and needs a repo-wide text search before editing.

---

## 8. External References

- https://jiraeu.epam.com/browse/EPMCDME-16273: unresolved (a web URL this agent cannot open). The ticket description, root cause, suggested fix and acceptance criteria were supplied verbatim in `task_context` and are reproduced in Section 1.
- `../codemie-enterprise` (sibling repository): not opened by this analysis. The requester states it has no `user_email` and does not call the renamed functions, and that `get_or_create_customer_with_budget(user_id,...)` / `_get_cached_customer(user_id)` treat the value as an opaque id. Treat as unverified here.
