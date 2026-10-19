# Technical Research

**Task**: llm max_output_tokens bedrock model-config
**Generated**: 2026-10-12T11:45:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15787 Bug — Bedrock Claude Opus 5.5 (claude-opus-5-5) advertised max_output_tokens: 128000 via GET /v1/llm_models, but responses capped at exactly 4,096 output tokens in Production and ~8,100 in Preview (multi-agent fails "The configured output token limit was reached"). Opus 4.6 same request completes 23,004 tokens. AC: requests use output-token limit consistent with configured max_output_tokens; truncation exposes stop_reason max_tokens instead of unqualified success; effective output-token limit diagnosable from logs/telemetry.

Full ticket body: `work-item.md` in this directory (verbatim description, steps, expected/actual, AC1–AC6).

---

## 2. Codebase Findings

### Existing Implementations

**Root cause (Prod 4,096 cap): LiteLLM proxy path never sends `max_tokens`.**
- `src/codemie/enterprise/litellm/llm_factory.py:304-327` `_build_chat_model_base_args` builds the `LiteLLMChatOpenAI` (AzureChatOpenAI subclass) kwargs with no `max_tokens` key. This path is active whenever `LLM_PROXY_ENABLED=True` and `LLM_PROXY_MODE="lite_llm"`, i.e. Prod/Preview.
- `llm_factory.py:1035-1073` `_filter_litellm_params` only handles `features.max_tokens is False → max_tokens=None`. It never sets a positive value.
- `llm_factory.py:224-302` `create_litellm_chat_model` → `LiteLLMChatOpenAI(llm_model_details=..., **filtered_args)`. Because `max_tokens` is None, langchain_openai leaves `max_completion_tokens` out of the payload.
- The proxy therefore applies its own default. In LiteLLM, the Anthropic/Bedrock transformation calls `get_max_tokens(model)` from LiteLLM's built-in model map and falls back to `DEFAULT_MAX_TOKENS` / `DEFAULT_ANTHROPIC_CHAT_MAX_TOKENS = 4096` when the model is unknown. The map has `us.anthropic.claude-opus-4-6-v1` at 128000 and has no opus-5 entry. That explains Opus 4.6 = 23,004 OK and Opus 5.5 = exactly 4,096.
- The Bedrock Converse thinking path sets `maxTokens = budget_tokens + 4096` when max_tokens is absent. HIGH budget 4096 + 4096 = 8192, which plausibly explains Preview's ~8,100 (hypothesis; depends on proxy-side thinking defaults and version).
- `git log -S max_tokens -- llm_factory.py` shows only the initial commit. Forwarding was never implemented. EPMCDME-15572 (commit db9c687e7) exposed `max_output_tokens` only in the catalog mapping.

**Native (non-proxy) paths already forward it:** `src/codemie/core/dependecies.py`
- Azure raw path line 455: `max_tokens: llm_model_details.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS_4K`
- `get_vertex_llm` 483, `get_bedrock_llm` 527 (ChatBedrockConverse), `get_anthropic_llm` 551: `... or DEFAULT_MAX_OUTPUT_TOKENS_8K`
- `filter_params` 376-391: features.max_tokens False → None.
- `get_llm_by_credentials` 271-353 tries `get_litellm_chat_model` first (292-307).
- `src/codemie/core/constants.py:57-58` — `DEFAULT_MAX_OUTPUT_TOKENS_4K=4096`, `_8K=8192`.

**Catalog source of advertised 128000:** `src/codemie/enterprise/litellm/models.py:40-184` `map_litellm_to_llm_model` — line 100 sets the `features.max_tokens` flag from supported params, line 162 reads `max_output_tokens = model_info.get("max_output_tokens")`, line 181 passes it into LLMModel. There is no validation.

**Truncation handling (AC5 gap):**
- `src/codemie/agents/langgraph_event_adapter.py:182-203` `parse_update_type`. The truncation check `_safe_check_for_truncation` runs only in the `is_finish_reason_tool_calls` branch. The `elif is_finish_reason_stop` branch finalizes. A text-only message with `finish_reason="length"` / `stop_reason="max_tokens"` matches neither branch, so there is no error and the run is reported as success (the Prod symptom). Supervisor path at 260-261 checks only tool-call messages. In multi-agent runs, handoff tool-calls trigger `TokenLimitExceededException` (the Preview symptom).
- `src/codemie/agents/langgraph_agent.py:1285-1295` `is_finish_reason_stop`, 1300-1320 `_safe_check_for_truncation`, 1322-1342 `_get_truncation_indicator` (`finish_reason in ["length","max_tokens"]` or `stop_reason == "max_tokens"`), 1372-1421 `_check_for_truncated_response` → `TokenLimitExceededException`.
- User-facing string: `src/codemie/configs/config.py:946-948` `AGENT_MSG_TOKEN_LIMIT`, routed via `core/error_constants.py:81,286`, `core/errors.py:656-663` `_try_token_limit`, `agents/tools/agent.py:67-68` `extended_error`.
- `src/codemie/core/exceptions.py:304-329` `TokenLimitExceededException(message, model, truncation_reason)`.
- Response models lack truncation fields: `chains/base.py:61-84` `GenerationResult`, `123-150` `StreamedGenerationResult`; `core/models.py:856-878` `BaseModelResponse`.

**Telemetry (AC6 gap):** no log records effective max_tokens. The only LiteLLM factory log is `llm_factory.py:258` (`Creating LiteLLM chat model: <name>`). `agents/callbacks/tokens_callback.py:470-492` `on_chat_model_start` ignores invocation params. `service/request_summary_manager.py:28-38` `LLMRun` has no max_tokens or finish_reason field.

**Other:** `enterprise/switchyard/llm_clients.py:145-152` has a hardcoded `max_tokens=4096` for the routing classifier only, which is irrelevant. `core/router_chat_model.py:297-312` builds router candidates through `get_llm_by_credentials`, so the fix covers it automatically.

### Architecture and Layers Affected
- **Client factory (enterprise/litellm)**: `llm_factory.py` — primary fix (forward max_tokens, log it).
- **Agent runtime**: `langgraph_event_adapter.py`, `langgraph_agent.py` — text-only truncation detection (AC5).
- **Telemetry/logging**: llm_factory log line and/or tokens callback (AC6).
- **Config (out of repo)**: deployed LiteLLM proxy `model_info` / thinking defaults for claude-opus-5-5.

### Integration Points
- LiteLLM proxy (OpenAI-compatible Azure endpoint) → Bedrock. `max_completion_tokens` from AzureChatOpenAI is mapped by LiteLLM to Anthropic `max_tokens` / Converse `maxTokens`.
- ~35 callers of `get_llm_by_credentials` (assistants, workflows supervisor node, conversation service, chains) get the change transparently.
- Switchyard router candidates go through the same factory.

### Patterns and Conventions
- `value or DEFAULT_*` fallback for max_tokens on native factories; `LLMFeatures` flags filter params (`features.max_tokens is False → None`).
- Enterprise boundary: `get_litellm_chat_model` returns None to fall back to native.
- Logging: `logger.debug/info` key=value style (`budget_event=... component=litellm_llm_factory`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/llm-providers.md` (generic provider/LiteLLM; no max_tokens).
- `.ai-run/guides/development/logging-patterns.md` (AC6), `.ai-run/guides/agents/langchain-agent-patterns.md`, `.ai-run/guides/testing/testing-patterns.md`.

### Architectural Decisions
- `docs/superpowers/tasks/2026-10-05-expose-litellm-max-output-tokens/` (EPMCDME-15572): exposed max_output_tokens in the catalog only, with "no router/service changes". The deferred review flagged that `or DEFAULT` treats 0 as unset and that non-positive values go unchecked.
- `docs/superpowers/tasks/2026-08-07-epmcdme-13459-fix-token-limit-message/`: wired AGENT_MSG_TOKEN_LIMIT into the streaming error path.
- `docs/superpowers/tasks/2026-09-29-epmcdme-14763-expose-max-input-tokens/`: sibling.
- EPMCDME-14145 (truncation without finish reason): no repo artifacts.

### Derived Conventions
Config values come from YAML or LiteLLM model_info via `llm_service.get_model_details`, never hardcoded per model. Tests patch the model constructor and assert `call_args.kwargs`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_llm_factory.py` — create_litellm_chat_model budget/creds/user injection; patches `LiteLLMChatOpenAI`, inspects kwargs. No max_tokens assertion.
- `tests/enterprise/litellm/test_models.py` — max_output_tokens mapping present/absent (65-98), features.max_tokens flag.
- `tests/codemie/agents/test_langgraph_truncation.py` — `_check_for_truncated_response`, `_get_truncation_indicator`, `_safe_check_for_truncation`, extended_error.
- `tests/codemie/agents/test_langgraph_event_adapter.py` (mocks `_safe_check_for_truncation`), `test_langgraph_multi_assistant_supervisor.py`, `tests/codemie/service/assistant/test_langgraph_assistant.py`.
- `tests/codemie/core/test_errors.py`, `tests/codemie/core/test_dependencies_tagging_headers.py`.

### Testing Framework and Patterns
pytest 8 + pytest-asyncio, pytest-mock, xdist (`--import-mode=importlib -n 3`, `pythonpath=src`). MagicMock model details with explicit `features.*`; `patch.object(config, ...)`; AIMessage with `response_metadata` for truncation.

### Coverage Gaps
1. No test asserts max_tokens forwarded on the LiteLLM path.
2. No test for native factory max_tokens fallbacks.
3. No test for text-only `finish_reason=length` in `parse_update_type` / supervisor.
4. No truncation fields on response models.
5. No test or log for effective max_tokens.

---

## 5. Configuration and Environment

### Environment Variables
`LLM_PROXY_ENABLED`, `LLM_PROXY_MODE` (select the LiteLLM path), `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `MODELS_ENV`, `AWS_BEDROCK_*`, `HIDE_AGENT_STREAMING_EXCEPTIONS`. Proxy-side (not in repo): `DEFAULT_MAX_TOKENS`, `DEFAULT_ANTHROPIC_CHAT_MAX_TOKENS` (both 4096), thinking budget defaults.

### Configuration Files
- `config/llms/llm-aws-config.yaml:72-83` Opus 4.6 `max_output_tokens: 128000`; there is no opus-5-5 entry. `llm-dial-config.yaml:220-250` is similar.
- `litellm_config.yaml` (local proxy) has opus 4.5/4.6/4.7 and sonnet-5, no opus-5-5, and sets no max_tokens or thinking params.
- `src/codemie/configs/config.py` (LLM_PROXY_* 700-714, AGENT_MSG_TOKEN_LIMIT 946).

### Feature Flags and Deployment Concerns
- `LLMFeatures.max_tokens` (from LiteLLM supported_openai_params). Models with `max_tokens: False` must keep sending nothing.
- Prod/Preview proxy config for opus-5-5 lives outside the repo. The locked litellm is 1.99.2 but the local .venv has 1.84.0 (stale).
- Helm `deploy-templates/values.yaml` has no token settings.

---

## 6. Risk Indicators

- **Blast radius**: forwarding max_tokens on the LiteLLM path changes the request for every proxied model. A model whose catalog `max_output_tokens` exceeds what the provider accepts (stale or incorrect model_info) would start returning 400s where it previously used the proxy default. Mitigation: forward only when `max_output_tokens` is a positive int and `features.max_tokens` is not False.
- **Reasoning models**: OpenAI o-series/gpt-5 count reasoning tokens against `max_completion_tokens`. Sending the full max is the correct ceiling, but it is a behavior change.
- **Thinking interplay**: Anthropic requires `max_tokens > budget_tokens`. Sending the full max_output_tokens satisfies this; the risk is low.
- **AC5 behavior change**: raising an error or flagging text-only truncation (finish_reason=length) changes what users see for responses that previously "succeeded". It needs a careful choice between hard error and soft flag.
- **Out-of-repo dependency**: the Preview ~8.1k pattern may also involve proxy-side thinking defaults. Code alone cannot verify AC2–AC4 in Prod/Preview without deployment.
- **Stale local LiteLLM** (1.84.0 vs lock 1.99.2): LiteLLM internals quoted above may differ in the deployed version.
- `or DEFAULT` treats `0` as unset (prior deferred finding).

---

## 7. Summary for Complexity Assessment

The core defect is localized: the LiteLLM-proxy client factory (`enterprise/litellm/llm_factory.py`) never forwards the model's `max_output_tokens` as `max_tokens`, so the proxy falls back to LiteLLM's built-in model map, or to 4096 for models it doesn't know (Opus 5.5). Native factories in `core/dependecies.py` already forward it. The primary fix is one parameter in `_build_chat_model_base_args` plus a feature-flag guard, with unit tests following the existing `test_llm_factory.py` kwargs-assertion pattern.

Secondary ACs widen the surface. AC5 needs truncation detection for text-only responses in `langgraph_event_adapter.parse_update_type` (and possibly the supervisor path) and a decision on how truncation is surfaced (error vs flag on the response models). AC6 needs a debug/info log of the effective max_tokens per request (factory creation or `on_chat_model_start`). Existing truncation helpers (`_get_truncation_indicator`, `_check_for_truncated_response`) are reusable, so no new patterns are needed.

Test coverage around the touched code is decent but lacks assertions on max_tokens and on text-only truncation. Key risks: the cross-model behavior change from now sending max_tokens for every proxied model, user-visible behavior change for truncated plain responses, and the AC2–AC4 production verification that depends on deployment and on out-of-repo proxy config. Estimated 3–5 source files plus 3–4 test files across 2–3 layers (client factory, agent runtime, logging).
