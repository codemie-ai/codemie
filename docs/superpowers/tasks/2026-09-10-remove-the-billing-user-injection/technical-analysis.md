# Technical Research

**Task**: auth teams billing service-account
**Generated**: 2026-09-10T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Remove the billing_user injection for /model teams billing logic: when a teams header + service account are present, authenticate the user directly instead of injecting a separate billing user. The teams header + service account combination should become just another authentication method, not a special billing-user path.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/user/billing_user_resolver.py` — `BillingUserResolver` class and module-level singleton `billing_user_resolver`. `resolve(caller, sender_email)` swaps identity only when the caller is the allow-listed Teams service account (`caller.user_type == "service_account"` and `caller.id == config.TEAMS_SERVICE_ACCOUNT_ID`); every other caller is returned unchanged. It looks up a DB user by `sender_email` (`user_repository.get_by_email`), rejects inactive/deleted/service-account matches with a 404 `ExtendedHTTPException`, and rebuilds a `User` via `_to_user` (pulling `user_project_repository`, `user_kb_repository`). Module also defines `TEAMS_SERVICE_ACCOUNT_USER_TYPE = "service_account"` and `TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"`.
- `get_billing_user(raw_request, user=Depends(authenticate))` — FastAPI dependency in the same file. Short-circuits to `user` when the `teamsBotIntegration` feature flag (`TEAMS_BOT_INTEGRATION_FEATURE` from `settings_request_validator.py`) is off, or when the `X-Teams-Sender-Email` header is absent. Otherwise calls `billing_user_resolver.resolve(user, sender_email)`.
- `src/codemie/rest_api/routers/assistant.py` — three router endpoints depend on `get_billing_user` alongside `authenticate`: `ask_virtual_assistant` (line ~962), `ask_assistant_by_id` (line ~1050), `ask_assistant_by_slug` (line ~1183). Each passes both `user` (original caller) and `billing_user` (resolved) into `_ask_assistant` / `_ask_virtual_assistant`. `resume_tool_call`/`_ask_assistant_resume` do **not** take a billing_user — only `authenticate`.
- `_ask_assistant(assistant, raw_request, request, user, background_tasks, include_tool_errors, error_detail_level, billing_user=None)` (assistant.py ~line 2351) — defaults `billing_user = billing_user or user`; uses `billing_user` for `assistant_user_interaction_service.record_usage(...)`, `request_summary_manager.create_request_summary(...)`, and passes it into `get_request_handler(assistant, user, request_uuid, billing_user=billing_user)`. Access-control (`_check_user_can_access_assistant`) always uses the original `user`.
- `src/codemie/rest_api/handlers/assistant_handlers.py` — `AssistantRequestHandler.__init__(assistant, user, request_uuid, billing_user=None)` stores `self.billing_user = billing_user or user` with an explicit comment: "Drives budget-key selection / cost attribution only. Access control and conversation ownership must always evaluate self.user." `self.billing_user` is threaded into `AssistantService.build_agent(..., user=self.billing_user, ...)` in `_handle_stream`, `_handle_background`, `_handle_sync`, and into `set_llm_context(self.assistant, None, self.billing_user)` inside `save_chat_history`. `A2AAssistantHandler` and `HedgedAssistantHandler` (referenced in `get_request_handler`) also accept/propagate `billing_user`. `get_request_handler(assistant, user, request_uuid, billing_user=None)` is the single factory choosing `A2AAssistantHandler` / `HedgedAssistantHandler` / `StandardAssistantHandler`, forwarding `billing_user` to whichever is constructed.
- `src/codemie/rest_api/security/authentication.py` — `authenticate(request, internal_user_id=Depends(user_id_header), bind_key=Depends(bind_key_header))` is the single central auth dependency. It branches on `bind_key` (internal service-to-service HMAC-signed calls via `LocalIdp`) vs. the standard external flow: `get_user_provider()` selects `PersistentUserProvider` or `LegacyJwtUserProvider` (feature-flag `ENABLE_USER_MANAGEMENT`), then `IdpFactory.create()` supplies the IDP, and `provider.authenticate_and_load_user(request, idp)` returns the `User`. Result is stored via `request.state.user = user` and `set_current_user(user)`. This is the only place the codebase currently treats "authentication method" as a pluggable concept (bind-key vs. external IDP); the Teams-header substitution today sits entirely *outside* this function, as a second dependency layered on top of its output.
- `src/codemie/rest_api/security/user_providers/persistent.py` — `PersistentUserProvider.authenticate_and_load_user` shows the existing pattern for method branching inside the provider: dev header (`ENV=="local"`), local JWT, or external IDP token, each producing a `user_id` that is then coalesced/loaded from DB via `authentication_service.authenticate_persistent_user`.
- `src/codemie/rest_api/security/user_providers/legacy_jwt.py` — sibling `LegacyJwtUserProvider`, the ephemeral (no-DB) counterpart selected when `ENABLE_USER_MANAGEMENT` is off.
- `src/codemie/configs/config.py:216` — `TEAMS_SERVICE_ACCOUNT_ID: str = "codemie-teams-bot"` (the allow-listed service-account id).
- `src/codemie/service/settings/settings_request_validator.py:351,428-460` — `TEAMS_BOT_INTEGRATION_FEATURE = "teamsBotIntegration"` and `validate_ms_teams_request(...)`, which gates MS Teams integration *settings* writes behind the same feature flag (separate concern from the billing-header substitution, but shares the flag and the "MS Teams" domain).

### Architecture and Layers Affected

- **Security/auth layer**: `src/codemie/rest_api/security/authentication.py` (`authenticate`), `src/codemie/rest_api/security/user_providers/` (`PersistentUserProvider`, `LegacyJwtUserProvider`, `base.py` `UserProvider` protocol, `factory.py`). Folding the Teams-header substitution "into authentication as just another method" implies this layer, not `service/user/billing_user_resolver.py`, becomes the point of decision.
- **Service layer**: `src/codemie/service/user/billing_user_resolver.py` (`BillingUserResolver`, `get_billing_user`) — the component the task names for removal/absorption.
- **API/router layer**: `src/codemie/rest_api/routers/assistant.py` — three `/model`-family endpoints wire `Depends(authenticate)` + `Depends(get_billing_user)` and thread both `user`/`billing_user` through `_ask_assistant`/`_ask_virtual_assistant`.
- **Handler layer**: `src/codemie/rest_api/handlers/assistant_handlers.py` — `AssistantRequestHandler` (and `A2AAssistantHandler`, and `HedgedAssistantHandler` in `hedged_handler.py`, referenced but not read) carry a `self.billing_user` distinct from `self.user` through the entire request lifecycle including streaming, background tasks, and chat-history persistence (`set_llm_context`).

### Integration Points

- `get_billing_user` depends on `authenticate` (`Depends(authenticate)` inside its own signature) — the resolver is layered strictly after auth today, not part of it.
- `billing_user_resolver.resolve` opens its own `get_session()` (`codemie.clients.postgres`) and calls `user_repository.get_by_email`, `user_project_repository.get_by_user_id`, `user_kb_repository.get_by_user_id` to rebuild a full `User` object — duplicating logic already present in the persistent-auth-provider's DB-load path (`authentication_service.authenticate_persistent_user`, not read in full here).
- `assistant_user_interaction_service.record_usage`, `request_summary_manager.create_request_summary`, `set_llm_context` (in `codemie.service.llm_service.utils`), and `AssistantService.build_agent` are the four call sites that currently consume `billing_user` as distinct from `user`.
- `customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE)` gates the header lookup — same flag also gates `validate_ms_teams_request` in the settings layer (`config/customer/customer-config.yaml:259` declares `features:teamsBotIntegration`).

### Patterns and Conventions

- Header-derived auth constants follow a documented naming convention: `USER_ID_HEADER`/`BIND_KEY_HEADER` in `authentication.py`, mirrored by `TEAMS_SENDER_EMAIL_HEADER` in `billing_user_resolver.py` (the prior implementation task, per `docs/superpowers/tasks/2026-08-31-.../plan.md`, explicitly followed this convention).
- `authenticate`'s internal-vs-external branching (`bind_key is not None`) is the existing precedent for "another authentication method" selected by a header/credential rather than a body field.
- `UserProvider` is a `Protocol` (`user_providers/base.py`) with exactly one required method, `authenticate_and_load_user(request, idp) -> User`; `IdpFactory`/`get_user_provider()` are the existing factory/registry points for method selection.
- Handlers/`_ask_assistant` consistently comment that access-control and conversation ownership must evaluate the *original* caller, never the billing identity — this invariant is repeated at three code sites (`billing_user_resolver.py` docstring, `assistant_handlers.py` `__init__` comment, `_ask_assistant` docstring) and is treated as load-bearing by tests (`TestAskAssistantBillingUserSwap`).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/security-patterns.md` — states the convention "Use the central authentication dependency and role helpers" / "Reimplementing bearer or bind-key parsing in endpoints → Use `authenticate`", citing `authentication.py:59`. Does not mention Teams or billing-user substitution specifically.
- No dedicated guide file exists yet for the Teams billing/service-account flow; it is documented only in task-scoped docs under `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/` (see below), not in `.ai-run/guides/`.

### Architectural Decisions

- `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/plan.md` — the plan that introduced the current `resolve_billing_user`/`BillingUserResolver` design ("option 4b"): explicitly chose a **header**, not a body field, and explicitly designed the swap so "the *original* caller still drives access control, and only the *resolved* user drives usage recording, request-summary attribution, and `get_request_handler`."
- `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/teams-bot-handoff.md` — external-facing contract doc for the `codemie-teams-bot` service describing exactly what the header does/doesn't affect (billing only, not access control) and its error behavior (404 on unresolved/invalid sender email). This is the closest thing to a spec for current behavior that any redesign must either preserve or explicitly break, and the bot-service consumer of this header exists outside this repo.
- `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/lens-blind.md` — a prior review flagged a security concern directly relevant to this task: the header is trusted verbatim once the caller matches the allow-listed service account, with **no signature/HMAC binding it to the actual Teams message** — "if the bot's own token is ever reused outside the bot process..., any string can be placed in that header to attribute usage/billing to an arbitrary existing user." It also flagged that non-allow-listed callers sending the header get only a `logger.warning`, with no caller-visible error distinguishing "no header" from "header present but ignored."

### Derived Conventions

- The repo's existing "fold X into Y" precedent is commit `0b260a14c` ("Fold OAuth credential types into base integration types"), which absorbed a previously separate OAuth credential-type concept into the base integration-type model across `settings.py`, `settings_request_validator.py`, and a new `folded_credentials.py` — a directly analogous prior refactor (separate special-case path → part of the base/general mechanism) though in the integrations domain, not auth.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/user/test_billing_user_resolver.py` — unit tests for `BillingUserResolver.resolve` and `get_billing_user`: no-header passthrough, feature-flag-off passthrough, non-allowlisted-caller passthrough, 404 on unmatched/inactive/deleted/service-account sender email, and successful resolution rebuilding `User.project_names`/`admin_project_names` from repositories.
- `tests/codemie/rest_api/routers/test_assistant.py` — `TestAskAssistantBillingUserSwap::test_billing_user_drives_record_usage_while_access_check_uses_original_user` asserts `_ask_assistant` uses the original `mock_user` for `Ability(...)` (access control) and the resolved `billing_user` for `record_usage` and `get_request_handler(..., billing_user=billing_user)`. `TestAskVirtualAssistantBillingUserSwap::test_virtual_assistant_project_uses_caller_but_thread_gets_billing_user` asserts the virtual-assistant route passes the caller for project scoping but the billing_user into the background thread call.
- No test file was found under `tests/.../security/` that exercises `authenticate()` branching by Teams-header/service-account combination — that combination is currently tested only at the `billing_user_resolver`/router layer, never inside `authentication.py` or the `user_providers/` tests.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio` for async dependency/router functions; heavy use of `unittest.mock.patch`/`MagicMock(spec=User)` to isolate router logic from DB/session calls; `@patch(...config.TEAMS_SERVICE_ACCOUNT_ID, "codemie-teams-bot")` is the pattern used to pin the allow-listed id in tests.

### Coverage Gaps

- No test currently exercises `authenticate()` itself with a Teams-header + service-account combination — any refactor that moves this logic into `authenticate`/`UserProvider` will need new coverage there, since none exists.
- No test in `authentication.py`'s own test file (not located in this survey — search covered `billing_user_resolver` and `assistant.py` tests only) verifies interaction between `bind_key` internal auth and the Teams header; if the two paths must remain mutually exclusive or ordered under a merged design, this is currently unverified anywhere.

---

## 5. Configuration and Environment

### Environment Variables

- `TEAMS_SERVICE_ACCOUNT_ID` (config.py:216, default `"codemie-teams-bot"`) — the allow-listed service-account id; consumed via `config.TEAMS_SERVICE_ACCOUNT_ID` in `billing_user_resolver.py`.
- `ENABLE_USER_MANAGEMENT` — feature flag selecting `PersistentUserProvider` vs `LegacyJwtUserProvider` inside `get_user_provider()` (referenced in `authentication.py`'s docstring and `persistent.py`'s class docstring).
- `ENV` — `PersistentUserProvider` checks `config.ENV == "local"` to enable the dev-header bypass.
- `IDP_PROVIDER` — `PersistentUserProvider` branches to local-JWT validation vs. external IDP based on this value.

### Configuration Files

- `config/customer/customer-config.yaml:259` — declares `features:teamsBotIntegration` as a customer-config feature component, read via `customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE)`.

### Feature Flags and Deployment Concerns

- `teamsBotIntegration` (constant `TEAMS_BOT_INTEGRATION_FEATURE = "teamsBotIntegration"` in `settings_request_validator.py`) gates both: (a) `get_billing_user`'s header lookup, and (b) `validate_ms_teams_request`'s settings-write validation. Any redesign that removes `get_billing_user` must decide where this flag check now lives if the Teams-header/service-account path becomes part of `authenticate`.

---

## 6. Risk Indicators

- Speculative: Moving the Teams-header substitution into `authenticate()` (or into a `UserProvider`) changes where the 404-on-unresolved-sender-email error surfaces — today it is a service-layer `ExtendedHTTPException` raised from inside a FastAPI dependency (`get_billing_user`) that runs *after* `authenticate` has already succeeded; folding it into `authenticate` means auth failure and billing-identity-resolution failure become the same exception class/code path, which may change client-visible behavior for the `codemie-teams-bot` consumer described in `teams-bot-handoff.md`.
- The security concern already on record in `lens-blind.md` — the header is trusted verbatim with no HMAC/signature binding it to the real Teams message — is unresolved in the current implementation and is directly in-scope for a task that touches this exact code path; any redesign should address whether "just another authentication method" closes or perpetuates that gap.
- `self.billing_user` is threaded through `AssistantRequestHandler`, `A2AAssistantHandler`, and (referenced but unread) `HedgedAssistantHandler` in `hedged_handler.py`, plus `AssistantService.build_agent` and `set_llm_context` — removing the billing_user parameter is not confined to the router/resolver files; it is a distinct parameter across the handler class hierarchy and multiple call sites within each handler (stream/background/sync paths).
- `resume_tool_call`/`_ask_assistant_resume` never took a `billing_user` at all — confirms the existing design already treats access-control-vs-billing-identity as separable per endpoint, and any endpoint audit must check each of the three `/model` endpoints independently for whether "authenticate directly" changes their handler wiring identically or differently.
- No existing test exercises `authenticate()` with the Teams-header/service-account combination — a refactor collapsing this into `authenticate` starts with zero coverage at that layer and must build it fresh; the current test suite's assumptions (e.g. `TestAskAssistantBillingUserSwap`'s explicit assertion that `Ability(...)` uses `mock_user` while `record_usage` uses `billing_user`) will very likely need rewriting, not just extending, if `user` becomes the sole identity threaded through everywhere.
- `billing_user_resolver.py`'s DB rebuild of `User` (`_to_user`, using `user_project_repository`/`user_kb_repository`) duplicates logic that likely already exists in the persistent-auth-provider's user-loading path (`authentication_service.authenticate_persistent_user`, not fully read in this survey) — if the task folds Teams-header handling into `authenticate`, this duplication is a candidate for consolidation, but confirming the exact overlap requires reading `authentication_service.py`, which this survey did not cover in depth.

---

## 7. Summary for Complexity Assessment

This task touches the security/auth layer (`authentication.py`, `user_providers/`), a dedicated service module (`billing_user_resolver.py`), the API/router layer (three `/model` endpoints in `assistant.py`), and the handler layer (`AssistantRequestHandler` and its `A2A`/`Hedged` subclasses in `assistant_handlers.py` and `hedged_handler.py`). The `billing_user` parameter is not confined to one call site — it is threaded through `_ask_assistant`/`_ask_virtual_assistant`, `get_request_handler`, handler `__init__`, and multiple internal handler methods (stream/background/sync/save_chat_history), so removing it as a separate concept is a multi-file, multi-layer change rather than a localized fix.

Technical novelty is moderate: the codebase already has a working precedent for "authentication method" as a branch point (`authenticate`'s bind-key-vs-external branch, `UserProvider`'s dev-header/local-JWT/IDP branches inside `PersistentUserProvider`), so the target pattern exists in-repo, but no code currently unifies the Teams-header+service-account case with those branches, and no test coverage exists for that combination inside `authenticate()` itself. A prior review (`lens-blind.md`) already flagged an unresolved security gap in the current header-trust model (no signature binding the header to the real message) directly on this code path, which raises the stakes of getting the new design right rather than just relocating the existing logic.

Test-coverage posture is mixed: the current billing_user-swap behavior is well covered at the resolver and router layers (`test_billing_user_resolver.py`, `TestAskAssistantBillingUserSwap`, `TestAskVirtualAssistantBillingUserSwap`), and those tests encode the exact invariant this task changes (access-control-uses-original-caller vs. billing-uses-resolved-user) — meaning most existing assertions will need rewriting rather than only new tests being added at the auth layer where coverage is currently absent. There is also an external consumer of the current header contract (`codemie-teams-bot`, documented in `teams-bot-handoff.md`) whose behavior/error-handling expectations may be affected by relocating where authentication failure vs. billing-resolution failure surfaces.

---

## 8. External References

None named by the task.
