# EPMCDME-16153: Move LiteLLM customer identity from the request body to the header

## Problem

CodeMie sets the OpenAI `user` field in LiteLLM request bodies to the LiteLLM customer ID. For project budgets with member tracking that ID is `codemie:project:<project>:category:<category>:user:<uuid>`, longer than 64 characters once the project name is added. LiteLLM forwards body `user` to Azure, which rejects values over 64 characters. The same ID is already sent as `x-litellm-customer-id`, which LiteLLM 1.80.17+ (prod runs 1.99.2) reads first and never forwards to the provider.

## Goal

No CodeMie code path puts a `user` field into a LiteLLM request body. Every path that attributed spend through that field attributes it through `x-litellm-customer-id` with the same value as before.

## Behavior

All paths are under `src/codemie/enterprise/litellm/` unless noted.

1. **Proxy (`proxy_router.py`)**: `_create_project_runtime_body_stream` and `_create_global_budget_body_stream` pass the buffered body through unchanged. They keep setting `request_info["litellm_customer_id"]` under the existing non-blank guards; `_apply_budget_provider_headers` turns it into the header as today. The member ID is read from `project_runtime.headers[x-litellm-customer-id]` instead of `body_overrides["user"]`; the existing `RuntimeError` for a missing or empty ID in member-tracking mode is kept.
2. **Direct LangChain path (`llm_factory.py`, chat and embeddings via `_configure_direct_runtime_overrides`)**:
   - Project budget: no `model_kwargs`; the header already arrives through `runtime_headers`.
   - Premium budget: no `model_kwargs`; `merged_headers` is threaded into `_try_apply_premium_budget` and gets `x-litellm-customer-id` set to the premium username, the value previously sent as `user`, with no new guard.
   - Platform budget: no `model_kwargs`; `merged_headers` is threaded into `_apply_platform_budget`. The header is set to `user_email` only when `user_email is not None`. When it is `None` the header is omitted. Every string is passed through unchanged, including `""` and whitespace-only strings; there is no stripping or truthiness check.
   - Personal-key bypass is untouched: it returns before any header or `user` is set.
   - Existing budget logic and validation are unchanged.
3. **Member ID source**: `_resolve_direct_project_budget_runtime` reads the ID from `provider_result.headers`, validates it in member-tracking mode (same `RuntimeError`), and no longer returns it as a separate tuple element. `_has_project_runtime_overrides` and `_apply_project_runtime_overrides` lose their `runtime_user` parameter.
4. **Dead code removed**:
   - `_inject_user_into_request_body_from_bytes` and the `inject_user_into_body` import in `proxy_router.py`.
   - `inject_user_into_body` import, ImportError fallback and `__all__` entry in `enterprise/loader.py`.
   - `BudgetRuntimeProviderResult.body_overrides` (`service/budget/provider.py`) and its population in `budget_provider_adapter.py` `_resolve_runtime_core`.
   - Log fields that describe body overrides (`body_overrides_applied` in the adapter, `budget_resolution_service.py` and `proxy_router.py`; `body_user_present` in `llm_factory.py`). `litellm_customer_key` log fields stay, sourced from the header value.
5. **Names that implied injection are renamed**: `_create_body_stream_with_optional_injection` becomes `_resolve_budget_attribution_stream` (with its test class `TestCreateBodyStreamWithOptionalInjection` becoming `TestResolveBudgetAttributionStream`), and the `runtime_budget_user_injected` log event becomes `runtime_budget_customer_resolved`. All call sites, patch targets and docstrings follow; behavior is unchanged.

## Acceptance criteria

1. A grep of `src/` finds no code that sets `user` in a LiteLLM request body, `model_kwargs`, `extra_body` or `body_overrides`.
2. Proxy requests (project-member and global/personal budget) reach LiteLLM with the original body bytes and with `x-litellm-customer-id` equal to the value used before this change.
3. Direct chat and embedding models for project, premium and platform budgets carry `x-litellm-customer-id` in their default headers with the same value previously sent as `user`, and have no `model_kwargs["user"]`.
4. On the direct platform-budget path, `user_email=None` yields no `x-litellm-customer-id` header and no `model_kwargs["user"]`; any string, including `""` and `"  "`, yields a header equal to that exact string.
5. Personal-key bypass requests carry neither the header nor `user`.
6. Member-tracking mode with a provider result lacking the header still raises `RuntimeError` on both paths.
7. `body_overrides`, the inject helper and the loader export no longer exist; no reference remains in source or tests. No reference to `_create_body_stream_with_optional_injection` or `runtime_budget_user_injected` remains in `src/` or `tests/`.
8. Existing tests that asserted body `user` or patched the injector now assert the header instead; ruff and the affected test modules pass.

## Non-goals

- No change to which customer ID is chosen, budget resolution, or category logic.
- No new guards, blank or whitespace filtering, or error behavior anywhere, with one exception: the direct platform-budget path omits the header when `user_email is None`. Existing guards stay as they are.
- No change to `PROXY_HOP_BY_HOP_HEADERS` or client-supplied header stripping.
- No customer header added to paths that never had one (switchyard classifier, image generator).
- No change to the closed `codemie_enterprise` package.
- No compatibility shim for LiteLLM older than 1.80.17.
- No explanatory code comments.

## Test impact

Rewrite assertions and patch sites in `tests/enterprise/litellm/test_llm_factory.py`, `test_proxy_router.py` and `test_premium_models_budget.py`; delete tests of the removed helper. Add header assertions on the direct premium and platform paths, where none exist today. Add an automated test for `_apply_platform_budget` covering `None` (header absent, no `model_kwargs["user"]`), a normal email, `""` and `"  "` (header equals the string). No other tests for blank-value handling.
