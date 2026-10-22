# Technical Research

**Task**: litellm proxy_router llm_factory budget customer-id
**Generated**: 2026-10-20
**Research path**: codegraph

---

## 1. Original Context

Review https://jiraeu.epam.com/browse/EPMCDME-16153 and implement the fix. Details are available at docs/2026-10-20-epmcdme-16153-remove-litellm-body-user.md (read this file; it will be removed later so do not link it anywhere in artifacts). Make sure that we removed all places where this user field was set in the request body. Make sure that all these places are using the header value (x-litellm-customer-id) instead. If after our adjustments there are some constructions that are no longer needed - remove them.
Beyond the doc's listed locations, hunt for ANY other place that sets the OpenAI `user` field in a LiteLLM/LLM request body (model_kwargs user, body_overrides user, extra_body user, inject_user_into_body, `"user":` in payloads, embeddings, image/other clients, tests that assert on it) and list each with file:line.

---

## 2. Codebase Findings

### Existing Implementations
All paths under `src/codemie/`. Line numbers are current on disk (branch EPMCDME-16153, unmodified).

**Body `user` sinks (the bug):**
- `enterprise/litellm/proxy_router.py:525-553` `_inject_user_into_request_body_from_bytes` wraps enterprise `inject_user_into_body(body_stream, content_type, username, session_id, request_id)`; falls back to passthrough when it is None.
- `proxy_router.py:912-916` (`_create_project_runtime_body_stream`) and `:958-962` (`_create_global_budget_body_stream`) call it. Both set `request_info["litellm_customer_id"]` first (`:910-911`, `:954-956`), guarded by `.strip()`; that becomes the `x-litellm-customer-id` header in `_apply_budget_provider_headers` (`:329-331`), called from `_prepare_proxy_headers` (`:1056`).
- `enterprise/litellm/llm_factory.py:404` `_apply_project_runtime_overrides`: `request_params["model_kwargs"] = {"user": runtime_user}` (headers already merged at `:399`).
- `llm_factory.py:498` `_try_apply_premium_budget`: `model_kwargs={"user": premium_username}`; function has no `merged_headers` parameter (params: llm_model_details, user_email, user_id, request_params, has_project_context).
- `llm_factory.py:524` `_apply_platform_budget`: `model_kwargs={"user": user_email}`; also no `merged_headers` parameter.
- Both are called from `_configure_direct_runtime_overrides` (`:547`, calls at `:628` and `:638`), which has `merged_headers`. It serves chat (`create_litellm_chat_model` `:282`) and embeddings (`create_litellm_embedding_model` `:960`, `request_params=embedding_params`).

**Internal carriers of the member ID (not sent on wire, but named "body"):**
- `budget_provider_adapter.py:1361-1367` `_resolve_runtime_core` returns `headers={"x-litellm-customer-id": ref}` AND `body_overrides={"user": ref}`; log string `:1359` says `body_overrides_applied=true`.
- `service/budget/provider.py:92` `BudgetRuntimeProviderResult.body_overrides: dict[str, Any]` (field; only the LiteLLM adapter populates it; `provider_registry.py:88` noop result leaves default).
- Readers of `body_overrides["user"]`: `proxy_router.py:898` (hard dependency), `proxy_router.py:1028`, `:1030` (log only), `llm_factory.py:906` (hard dependency, feeds the 5-tuple's first element), logs at `llm_factory.py:612` (`body_user_present`), `:916`.
- `llm_factory.py:817-925` `_resolve_direct_project_budget_runtime` returns `(runtime_user, headers, api_key, base_url, has_scopes)`; `runtime_user` is used by `_has_project_runtime_overrides` (`:379`) and `_apply_project_runtime_overrides` (`:388`) and is None in non-member-tracking mode.

**Dead-after-fix candidates:** `_inject_user_into_request_body_from_bytes`; `inject_user_into_body` import at `proxy_router.py:97`; `enterprise/loader.py:104` (import), `:116` (ImportError fallback `= None`), `:254` (`__all__`); `runtime_user` params/tuple slot; `BudgetRuntimeProviderResult.body_overrides`; `_create_body_stream_with_optional_injection` (`proxy_router.py:793`) is now misnamed (called at `:847`, `:860`, and by `_proxy_to_llm_proxy`; referenced in tests).

### Architecture and Layers Affected
Enterprise integration layer (`enterprise/litellm/{proxy_router,llm_factory,budget_provider_adapter}.py`, `enterprise/loader.py`); core budget service layer (`service/budget/provider.py`, `provider_registry.py`, `budget_resolution_service.py`: `dispatch_runtime`/`dispatch_runtime_sync`); core LLM wiring (`core/dependecies.py:64` embeddings, `:292` chat) which calls the factory.

### Integration Points
- External package `codemie_enterprise.litellm.proxy_utils.inject_user_into_body` (outside repo; not inspectable here).
- LiteLLM proxy 1.99.2 in prod reads `x-litellm-customer-id` first (per task doc).
- `PROXY_HOP_BY_HOP_HEADERS` (`proxy_router.py:142`) strips any client-supplied `x-litellm-customer-id` so only server-set values reach LiteLLM.

### Patterns and Conventions
- Budget logs use `budget_event=... component=...` key=value style.
- Memory note: user wants no explanatory comments added.
- `AGENTS.md`: tests only when explicitly requested; follow `.ai-run/guides/quality-gates.md` for commands.

### Other places checked and NOT setting body `user` (negative findings)
- `enterprise/switchyard/llm_clients.py:145-152` `litellm.acompletion(model, messages, max_tokens, api_base, api_key, response_format)`: no `user`, no customer header (classifier call, app key).
- `codemie_tools/data_management/file_system/image_generator.py:95-135` (`LiteLLMImageGenerator.generate/edit`): request has model/prompt/n/size/output_format/extra_body only; `extra_body` holds `response_format` from `workspace/generate_image_tool_v2.py:190`. No `user`. (No customer header either; pre-existing, out of scope.)
- `llm_factory.py:335-347` `_build_chat_model_base_args` and `:365-376` `_build_embedding_model_params`: no `user`.
- `llm_factory.py:97-117` `LiteLLMAzureOpenAIEmbeddings`/`EmbeddingClientWrapper`: forwards kwargs to `embeddings.with_raw_response.create`; no user.
- `proxy_router.py` lines 1-1060 and the `_create_proxy_endpoint` area were not fully read beyond 1060; remaining ~1000 lines (response/usage/streaming) were not scanned line by line.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/llm-providers.md` ("Outbound Request Tagging") says route header changes through the shared header-building helper (`_generate_litellm_headers`) and not at call sites; relevant to adding the customer-id header. No guide mentions body `user`.

### Architectural Decisions
Inline comment `llm_factory.py:563-566` (personal-key bypass never injects member customer; EPMCDME-11961, EPMCDME-13264). That bypass branch returns before any `user` or header is set and must stay that way.

### Derived Conventions
Customer identity = `x-litellm-customer-id`; `x-litellm-tags` is set by `generate_litellm_headers_from_context`.

---

## 4. Testing Landscape

### Existing Coverage (assertions on body `user` that must change)
`tests/enterprise/litellm/test_llm_factory.py`:
- `:174-219` `test_skips_user_injection_when_has_personal_credentials` asserts `model_kwargs.get("user") is None` (stays true; wording stale).
- `:221-263` `test_sets_user_injection_when_no_personal_credentials`: asserts `model_kwargs["user"] == "member-user-123"` at `:263`; resolver mock returns `("member-user-123", {}, None, None, True)` with empty headers.
- `:444`, `:613` `provider_result.body_overrides = {"user": ...}` in `TestResolveDirectProjectBudgetRuntime`; expected tuples at `:490-496`, `:641-647` start with the runtime user.
- `:796-846` bypass tests assert `"model_kwargs" not in request_params` (mock returns a stale 4-tuple; resolver is not called in bypass).
- `:920`, `:942`, `:970`, `:1057`, `:1158`, `:1200`, `:1243`, `:1282` assert `params["model_kwargs"]["user"]` for premium/platform paths; `_invoke` helpers pass `merged_headers={}` (`:885`, `:1121`) and return only `request_params`.

`tests/enterprise/litellm/test_proxy_router.py`:
- `:246-338` `TestBudgetBodyStreamWhitespaceUsername`: three tests patch `_inject_user_into_request_body_from_bytes` and assert `mock_inject.call_args.kwargs["user_id"]`; `:260`, `:543` set `body_overrides = {"user": ...}`.
- `:1157-1187` `TestInjectUserIntoRequestBody` tests the function being deleted.
- `:1190-1544` `TestCreateBodyStreamWithOptionalInjection`: patches the injector at `:1210`, `:1261`, `:1295`, `:1343`, `:1398`, `:1462`, `:1490`, `:1531`; asserts `mock_inject.assert_called_once_with(...)` at `:1226`, `:1277`, `:1359`, `:1414`, `:1503`, `:1542`; `mock_inject.return_value = "stream"` results asserted at `:1220`, `:1271`, `:1305`, `:1353`, `:1408`, `:1471`.
- `:1038-1060` already asserts the customer header is stripped from client input (keep).
- Lines 2100-end of this file not read.

`tests/enterprise/litellm/test_premium_models_budget.py:306-481` `TestProxyPremiumUsernameInjection`: five tests patch `_inject_user_into_request_body_from_bytes` with `fake_inject(body_bytes, user_id, request_info)` and assert captured usernames (`:324`, `:360`, `:395`, `:428`, `:462`).

No test seen asserting `body_overrides` content of `_resolve_runtime_core` in `tests/enterprise/litellm/test_budget_provider_adapter.py` (read ~lines 36-320 partially and 320-970); `tests/codemie/service/budget/test_provider_registry.py` and `test_budget_resolution_service.py` not matched for body_overrides (resolution test read fully, no hit). Not read: `tests/enterprise/litellm/test_budget_helpers.py`, `test_embedding_wrapper.py`, `tests/codemie/core/test_build_embedding_llm_run.py`, `test_provider_registry.py` body.

### Testing Framework and Patterns
pytest + `unittest.mock.patch`/`MagicMock`/`AsyncMock`, `pytest.mark.asyncio`; heavy patching by dotted path (`codemie.enterprise.litellm.llm_factory.*`, `...proxy_router.*`).

### Coverage Gaps
No test asserts the header value on the direct path (`merged_headers` is passed as `{}` and never inspected). No test of `_apply_budget_provider_headers` customer header from a runtime-created `request_info` end-to-end.

---

## 5. Configuration and Environment

### Environment Variables
None specific. Related: `LLM_PROXY_MODE`, `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `LITE_LLM_PROXY_APP_KEY`, `LITE_LLM_TAGS_HEADER_VALUE`, `LITE_LLM_PROJECTS_TO_TAGS_LIST`.

### Configuration Files
`config/` budgets YAML (premium/platform categories). Premium customer ID format `<email>_codemie_premium_models` via `get_premium_username`; CLI `<email>_codemie_cli`; project member ref `codemie:project:<p>:category:<c>:user:<uuid>` (member ref built in enterprise package, not in repo).

### Feature Flags and Deployment Concerns
Prod LiteLLM is 1.99.2 (header-first customer lookup since 1.80.17). Older LiteLLM deployments would lose attribution once body `user` is removed.

---

## 6. Risk Indicators

- `inject_user_into_body` lives in closed `codemie_enterprise` package; its other behavior (session_id/request_id side effects) is unknown. Check if it also did something besides setting `user` (e.g. metadata) that proxy path relied on.
- Premium and platform direct paths currently have NO customer header (`llm_factory.py:498`, `:524`); removal without adding header loses spend attribution (task doc point). Speculative: `_try_apply_premium_budget` and `_apply_platform_budget` will need `merged_headers` threaded in (neither has it today).
- Speculative: customer-id header must stay conditional on non-blank value on the proxy path (existing `.strip()` guards); the direct path has no blank guard today.
- `llm_factory.py:404` project path: header is delivered only if `provider_result.headers` contains `x-litellm-customer-id`; `budget_provider_adapter.py:1365` does set it. Non-LiteLLM/noop providers return no member ref, and `llm_factory.py:918-922` / `proxy_router.py:898-902` raise `RuntimeError` when `body_overrides["user"]` is absent; Speculative: moving this check to the header keeps behavior.
- Test blast radius is large: ~8 injector patch sites in `test_proxy_router.py`, 5 in `test_premium_models_budget.py`, ~15 `model_kwargs["user"]` assertions in `test_llm_factory.py`.
- Stale test at `test_llm_factory.py:808-833` returns a 4-tuple from a mocked 5-tuple function (vacuous in bypass mode); update if tuple shape changes.
- Cleanup chain: removing `body_overrides` touches `BudgetRuntimeProviderResult` (`service/budget/provider.py:92`) and any plugin/enterprise provider constructing it; not verified outside this repo.
- Log fields referencing body (`body_user_present`, `body_overrides_applied`, `litellm_customer_key=...body_overrides.get('user')`) become misleading; sites: `proxy_router.py:1028,1030`, `llm_factory.py:612,906,916`, `budget_provider_adapter.py:1359`.
- `docs/2026-10-20-epmcdme-16153-remove-litellm-body-user.md` is transient: do not reference it in artifacts, commits, comments.
- Codegraph is semantic and no text grep was available: unscanned areas (`proxy_router.py` 1060-2060, test files listed as "not read") could hold additional `"user"` hits.

---

## 7. Summary for Complexity Assessment

The change is confined to the enterprise LiteLLM integration layer (`proxy_router.py`, `llm_factory.py`, `budget_provider_adapter.py`, `loader.py`) plus the shared `BudgetRuntimeProviderResult` model. Production edits: 2 proxy call sites, 3 llm_factory sites (one pure deletion, two needing a header write and a `merged_headers` parameter), and optional cleanup (member-ID read from header, drop `body_overrides`, delete `_inject_user_into_request_body_from_bytes`, the loader import/fallback/export, and stale log fields). Roughly 4-6 source files and 3-4 test files change; no schema, config or migration.

Technical novelty is low (header already built for proxy path and project path; no new patterns). The main non-trivial piece is correctness of attribution on the premium/platform direct paths, where no header existed, and keeping the personal-key bypass untouched. The closed enterprise package behind `inject_user_into_body` is an unverified dependency but is only being unplugged.

Test coverage exists and is extensive but tightly coupled to the body-injection shape: dozens of assertions and patch sites must be rewritten to assert headers (`merged_headers`, `request_info["litellm_customer_id"]`) instead. No existing test checks the header on the direct path. Key risks: lost attribution if a path is missed, and incomplete discovery since only the listed negative findings (switchyard classifier, image generator, base args) were checked and parts of `proxy_router.py` and a few tests were not read line by line.

---

## 8. External References

`docs/2026-10-20-epmcdme-16153-remove-litellm-body-user.md` — resolved (read). Facts: prod LiteLLM 1.99.2; LiteLLM >= 1.80.17 reads customer from `x-litellm-customer-id` first and never forwards it to the provider; Azure rejects `user` > 64 chars. Required: (1) proxy_router: `_create_project_runtime_body_stream` (~912) and `_create_global_budget_body_stream` (~958) return `_stream_body_bytes(body_bytes)`, keep `request_info["litellm_customer_id"]`; (2) llm_factory: delete `model_kwargs` at 404; at 498 set `merged_headers["x-litellm-customer-id"] = premium_username`; at 524 set `merged_headers["x-litellm-customer-id"] = user_email`; embeddings are covered via `_configure_direct_runtime_overrides`; (3) preferably read member ID from `provider_result.headers["x-litellm-customer-id"]` (proxy_router:898, llm_factory:906) and drop `body_overrides={"user": ...}` in `budget_provider_adapter.py:1366`; remove `_inject_user_into_request_body_from_bytes` (proxy_router:525) and `inject_user_into_body` import/export in `enterprise/loader.py` (104, 116, 254). Jira URL https://jiraeu.epam.com/browse/EPMCDME-16153 was not opened (not reachable by available tools).
