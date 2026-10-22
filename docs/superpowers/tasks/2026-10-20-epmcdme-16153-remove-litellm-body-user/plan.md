# Remove LiteLLM Body `user` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** No code path puts `user` into a LiteLLM request body; spend attribution rides `x-litellm-customer-id` with the same values as before.

**Architecture:** Proxy and direct LangChain paths already build the header for project budgets; premium and platform direct paths gain it via `merged_headers`, set to the value previously sent as body `user`. The member ID is read from `provider_result.headers`, which makes `body_overrides`, the inject helper and the loader export dead.

**Spec:** `docs/superpowers/tasks/2026-10-20-epmcdme-16153-remove-litellm-body-user/spec.md`

Commit per task using the repository's existing convention. No code comments in any change. Add no new blank or whitespace filtering and no new guards, with one exception: the platform-budget path omits the header when `user_email is None`. Existing guards and `RuntimeError` behavior stay as they are. Rename `_create_body_stream_with_optional_injection` to `_resolve_budget_attribution_stream` and the `runtime_budget_user_injected` log event to `runtime_budget_customer_resolved` (Task 3). Do not touch `PROXY_HOP_BY_HOP_HEADERS` or the personal-key bypass branch (`llm_factory.py:557-567`).

## Review Focus

- Member-tracking mode with a provider result lacking the header: same `RuntimeError` on both paths (Tasks 2, 3).
- Personal-key bypass: neither header nor `user` (Task 2).
- Embeddings share `_configure_direct_runtime_overrides`: header must reach `create_litellm_embedding_model` params too (Task 2).
- Platform budget with `user_email` of `None`, `""` or whitespace: only `None` omits the header; strings pass through unchanged (Task 2).

## Task 1: Shared header-name constant

**Files:** Create `src/codemie/enterprise/litellm/customer_header.py`; modify `proxy_router.py:107`, `budget_provider_adapter.py:1365`.

`proxy_router` imports `llm_factory` (`proxy_router.py:91`) and the adapter imports neither, so the constant lives in a dependency-free module all three import.

Test-first: no — pure relocation; existing header tests (`tests/enterprise/litellm/test_proxy_router.py:1038-1060`) cover it.

- [ ] Create `customer_header.py` exporting only `LITELLM_CUSTOMER_ID_HEADER: Final[str] = "x-litellm-customer-id"`. Replace the definition at `proxy_router.py:107` with an import (keep the name importable from `proxy_router`). Use the constant for the literal key at `budget_provider_adapter.py:1365`.

## Task 2: Direct LangChain path uses the header (`llm_factory.py`)

**Files:** Modify `src/codemie/enterprise/litellm/llm_factory.py:379-404, 457-525, 547-644, 817-925`; tests in `tests/enterprise/litellm/test_llm_factory.py`.

**Interfaces:**
- `_resolve_direct_project_budget_runtime(...) -> tuple[dict[str, str], str | None, str | None, bool]` (runtime user element dropped).
- `_has_project_runtime_overrides(runtime_headers, runtime_api_key, runtime_base_url)` and `_apply_project_runtime_overrides(*, merged_headers, request_params, runtime_headers, runtime_api_key, runtime_base_url)` lose `runtime_user`; no `model_kwargs` is set.
- `_try_apply_premium_budget(..., merged_headers: dict[str, str])` sets `merged_headers[LITELLM_CUSTOMER_ID_HEADER] = premium_username` with no new guard, and no longer sets `model_kwargs`.
- `_apply_platform_budget(..., merged_headers: dict[str, str])` sets `merged_headers[LITELLM_CUSTOMER_ID_HEADER] = user_email` only when `user_email is not None`; strings including `""` and `"  "` pass through unchanged; `None` leaves the header absent. It no longer sets `model_kwargs`.

Test-first: yes — a parametrized platform-budget test over `user_email` values `None` (header absent, no `model_kwargs`), a normal email, `""` and `"  "` (header equals the exact string); premium test asserts `merged_headers["x-litellm-customer-id"]` equals the premium username and `"model_kwargs" not in request_params`; a project test asserts the provider customer header lands in `merged_headers` with no `model_kwargs`; the member-tracking test with headers lacking the customer header raises `RuntimeError`.

- [ ] Rewrite the assertions at `test_llm_factory.py:174-263, 444, 490-496, 613, 641-647, 796-846, 920, 942, 970, 1057, 1158, 1200, 1243, 1282` and the `_invoke` helpers (`:885`, `:1121`) to inspect `merged_headers`; fix the stale 4-tuple mock at `:808-833` to the new shape. Replace `body_overrides = {"user": ...}` setup with `headers={"x-litellm-customer-id": ...}` on the provider result. Bypass test asserts `merged_headers` is empty.
- [ ] In `_resolve_direct_project_budget_runtime`, read the ID with `provider_result.headers.get(LITELLM_CUSTOMER_ID_HEADER)` (replacing `:906`), keep the existing validation and `RuntimeError` at `:918-922` unchanged, keep `litellm_customer_key` in the log at `:916`, and return the 4-tuple (including early returns at `:836, :854, :898`).
- [ ] Thread `merged_headers` from `_configure_direct_runtime_overrides` into the two helpers (calls at `:628`, `:638`); update the destructuring and log at `:576-614`, dropping `body_user_present`. `_apply_project_runtime_overrides` keeps `merged_headers.update(runtime_headers)` as is. Budget logic and validation otherwise unchanged.

## Task 3: Proxy path passes body through (`proxy_router.py`, `loader.py`)

**Files:** Modify `src/codemie/enterprise/litellm/proxy_router.py:97, 525-553, 898-916, 958-962`; `src/codemie/enterprise/loader.py:104, 116, 254`; tests `test_proxy_router.py`, `test_premium_models_budget.py`.

Test-first: yes — `_create_project_runtime_body_stream` and `_create_global_budget_body_stream` return a stream of the unchanged `body_bytes` and set `request_info["litellm_customer_id"]` as before; the project path with headers lacking the customer header raises the same `RuntimeError`.

- [ ] In `_create_project_runtime_body_stream`, read the ID from `project_runtime.headers.get(LITELLM_CUSTOMER_ID_HEADER)` (replacing `:898`), keep the existing `RuntimeError` and `.strip()` guard, and `return _stream_body_bytes(body_bytes)`. Same return in `_create_global_budget_body_stream`. `_apply_budget_provider_headers` is unchanged.
- [ ] Delete `_inject_user_into_request_body_from_bytes` (`:525-553`) and the `inject_user_into_body` import (`:97`; keep `parse_usage_from_response`). Remove the import, `= None` fallback and `__all__` entry in `loader.py`.
- [ ] Rename `_create_body_stream_with_optional_injection` to `_resolve_budget_attribution_stream` (definition at `proxy_router.py:762`, its caller at `:1856`, its docstring, and every import and `patch(...)` target in `test_proxy_router.py` and `test_premium_models_budget.py`), rename the test class `TestCreateBodyStreamWithOptionalInjection` to `TestResolveBudgetAttributionStream`, and rename the `runtime_budget_user_injected` log event (`proxy_router.py:912`) to `runtime_budget_customer_resolved`. Behavior is unchanged.
- [ ] Tests: delete `TestInjectUserIntoRequestBody` (`test_proxy_router.py:1157-1187`); in `TestBudgetBodyStreamWhitespaceUsername` (`:246-338`), `TestResolveBudgetAttributionStream` (`:1190-1544`) and `TestProxyPremiumUsernameInjection` (`test_premium_models_budget.py:306-481`) remove injector patches and assert `request_info["litellm_customer_id"]` plus the streamed bytes. Replace `body_overrides = {"user": ...}` at `:260`, `:543` with `headers={"x-litellm-customer-id": ...}`.

## Task 4: Remove `body_overrides`

**Files:** Modify `src/codemie/service/budget/provider.py:92`, `enterprise/litellm/budget_provider_adapter.py:1359-1375`, `service/budget/budget_resolution_service.py:303, 341`, `enterprise/litellm/proxy_router.py:1028-1030`; adapter/registry/resolution tests.

Test-first: yes — a `_resolve_runtime_core` test in `tests/enterprise/litellm/test_budget_provider_adapter.py` asserts the result has headers `{"x-litellm-customer-id": ref}` and no `body_overrides` attribute.

- [ ] Delete the field, the `body_overrides={"user": ...}` argument, and every `body_overrides_applied=` log fragment; at `proxy_router.py:1030` source `litellm_customer_key` from `provider_result.headers.get(LITELLM_CUSTOMER_ID_HEADER)`. Fix any test constructing `BudgetRuntimeProviderResult(body_overrides=...)`.

## Task 5: Final stray-reference sweep

**Files:** Possibly `proxy_router.py` (beyond line 1060), `tests/enterprise/litellm/test_budget_helpers.py`, `tests/enterprise/litellm/test_embedding_wrapper.py`, `tests/codemie/core/test_build_embedding_llm_run.py`, `tests/codemie/service/budget/test_provider_registry.py`.

Test-first: no — grep-driven cleanup.

- [ ] Grep `src/` and `tests/` for `body_overrides`, `inject_user_into_body`, `_inject_user_into_request_body_from_bytes`, `body_user_present`, `body_overrides_applied`, `_create_body_stream_with_optional_injection`, `runtime_budget_user_injected`, `model_kwargs`, `extra_body` and `"user"` near LiteLLM request construction; read the unscanned remainder of `proxy_router.py` (1060 onward) for body `user`. Remove or convert every hit; legitimate non-LiteLLM `user` fields (auth, analytics, MCP) stay.

## Negative-constraint check

Spec non-goals are honored: no new blank or whitespace filtering or guards beyond the single platform-path `user_email is None` omission (existing `.strip()` guard and `RuntimeError` kept in Tasks 2, 3); the body-stream function and log event are renamed in Task 3 as the spec requires; no task edits hop-by-hop stripping, the closed `codemie_enterprise` package, switchyard or image generator, adds a compatibility shim, or adds comments; the bypass branch is untouched (Task 2).
