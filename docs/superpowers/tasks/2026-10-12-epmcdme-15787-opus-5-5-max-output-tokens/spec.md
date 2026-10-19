# Spec — EPMCDME-15787: Forward configured output-token limit on the LiteLLM path

**Ticket**: EPMCDME-15787 (Bug, Major) · **Related**: EPMCDME-14145, EPMCDME-15572
**Size**: M (18/36) · **Branch**: `EPMCDME-15787_opus-5-5-max-output-tokens`

## 1. Problem

On the LiteLLM-proxy path (`LLM_PROXY_ENABLED=True`, `LLM_PROXY_MODE=lite_llm`, which is how Prod and Preview run), CodeMie never sends `max_tokens` with a request. `GET /v1/llm_models` advertises `max_output_tokens` from the proxy's `model_info` (128000 for `claude-opus-5-5`). The request itself has no limit, so the proxy falls back to LiteLLM internals: the built-in model map, then `DEFAULT_ANTHROPIC_CHAT_MAX_TOKENS=4096`.

Verified on LiteLLM **1.84.0** (local `.venv`) and **1.99.2** (`poetry.lock` and the `docker-compose` proxy image):

| Model id (Bedrock profile) | Model-map lookup | Fallback when `max_tokens` is absent |
|---|---|---|
| `us.anthropic.claude-opus-4-6-v1` | 128000 | 128000 |
| `us.anthropic.claude-opus-5-5-v1` | not found | **4096** |

This matches Prod exactly: Opus 5.5 stops at 4,096 and Opus 4.6 reaches 23,004. Preview's ~8.1k most likely comes from the thinking path (`budget + 4096` when `max_tokens` is absent). The same fix covers it, because once the limit is explicit, LiteLLM keeps `maxTokens: 128000` with adaptive thinking on (verified on 1.99.2).

Secondary gaps:
- A plain-text response (no tool calls) ending with `finish_reason=length` / `stop_reason=max_tokens` matches neither branch in `LangGraphEventAdapter.parse_update_type`. Nobody is told the answer was cut, and the streamed thought is never finalized.
- The effective output-token limit of a request is not logged anywhere.

## 2. Goals / Non-goals

**Goals**
- G1 (AC1, AC4): LiteLLM-path requests for **Claude models** carry `max_tokens = model.max_output_tokens` when it is a positive integer, matching the native factories.
- G2 (AC6): every LiteLLM client build logs the effective limit and where it came from.
- G3 (AC5, diagnostic part): a text-only truncated response finalizes its thought and logs a WARNING with model, indicator and output tokens. The success/error outcome does not change.
- G4: no dependency on LiteLLM internals. CodeMie relies only on the proxy's OpenAI-compatible `max_completion_tokens` contract.

**Non-goals**
- Hard-failing truncated text-only responses, and adding truncation fields to response schemas (`GenerationResult` and friends). Both move to EPMCDME-14145, which owns truncation reporting. AC5's user-facing part is deferred there.
- Changes to the deployed LiteLLM proxy config or version.
- Changes to the native (non-proxy) factories in `core/dependecies.py`. They already forward the limit.
- Prod/Preview reproduction (AC2, AC3). That is verified after deploy (§7).

## 3. Design

### 3.1 Forward the limit — `src/codemie/enterprise/litellm/llm_factory.py`

- New config field `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS: bool = True` in `src/codemie/configs/config.py`, `# LiteLLM args` block. It is a kill switch: set it to `False` to return to proxy defaults without a code rollback.
- New helper `_resolve_max_output_tokens(llm_model_details) -> int | None` returns `max_output_tokens` only when the model is a Claude model and the value is an `int` (not a `bool`) and `> 0`. Otherwise it returns `None`. Any positive value is forwarded verbatim (4096, 128000, 1000000, …); CodeMie does not clamp it.
- A model counts as Claude when `"claude"` is in `base_name` (case-insensitive) **or** `LLMModel.claude_family is True`. `map_litellm_to_llm_model` sets `claude_family` from the proxy entry's underlying `litellm_params.model` (contains `anthropic` or `claude`) or its `litellm_provider` (`anthropic`, `vertex_ai-anthropic_models`). This covers a Claude deployment registered under an alias such as `opus-latest`. Provider alone (`bedrock`) is not enough, because Bedrock also serves qwen, grok and kimi. The field is declared `Field(default=False, exclude=True)`, so `GET /v1/llm_models` never exposes the underlying deployment id.
- **Claude-only scope (code review CR-002):** LiteLLM sets a default `max_tokens` only for Anthropic/Bedrock, and for profiles missing from its model map that default is 4096. OpenAI/Azure/Gemini models get no limit today. Forwarding their `max_output_tokens` would make OpenAI-family providers reject long prompts (prompt + max_tokens > context window) and would reach Azure TPM limits sooner. Those models therefore keep sending nothing (`source=proxy_default`).
- `_build_chat_model_base_args` adds `'max_tokens': <resolved>` only when the kill switch is on and the resolved value is not `None`. Otherwise the key is absent, as it is today.
- `_filter_litellm_params` is unchanged and still runs after the base args are built. When `features.max_tokens is False` it sets `max_tokens=None`, so models that reject the param still get nothing.
- `LiteLLMChatOpenAI` (an `AzureChatOpenAI` subclass) serializes `max_tokens` as `max_completion_tokens`. LiteLLM maps that to Bedrock Converse `maxTokens` and Bedrock Invoke / Anthropic `max_tokens` (verified on 1.84.0 and 1.99.2).

### 3.2 Effective-limit log (AC6) — same file

`create_litellm_chat_model` emits one INFO line after the filtered args are final:

```
llm_output_limit model=<base_name> max_tokens=<n|unset> source=<model_config|proxy_default|disabled_by_feature|disabled_by_config>
```

Source values:
- `model_config`: a positive value was forwarded.
- `disabled_by_feature`: `features.max_tokens is False`.
- `disabled_by_config`: the kill switch is off.
- `proxy_default`: no usable `max_output_tokens`.

The line carries no user content and no secrets (per `.ai-run/guides/development/logging-patterns.md`).

### 3.3 Text-only truncation: finalize and warn (B-lite) — `langgraph_event_adapter.py`, `langgraph_agent.py`

- New adapter helper `LangGraphEventAdapter._finalize_truncated_text(message: AIMessage, author: str | None = None) -> bool`:
  - Returns `False` when the message has tool calls or `agent._get_truncation_indicator(message.response_metadata or {})` returns `None`.
  - If the truncated message has `invalid_tool_calls`, meaning the arguments were cut mid-JSON (code review CR-001), it finalizes the thought and then calls `agent._safe_check_for_truncation(message)`, which raises `TokenLimitExceededException` the same way a truncated tool call does.
  - Otherwise it calls `_restore_response_headers(message)` and then `agent._on_llm_end(response=message, run_id=message.id, author=author)`. This is the same finalize path the stop branch uses, so the thought closes with the partial text and routing metadata.
  - It then logs a WARNING and returns `True`: `truncated_response model=<llm_model> indicator=<...> output_tokens=<n|unknown> action=kept`. `output_tokens` comes from `message.usage_metadata` when present.
- Wiring:
  - `parse_update_type`, "agent" branch: add `elif self._finalize_truncated_text(last_message): pass` after the tool-call and stop branches. Existing branches are untouched.
  - `parse_supervisor_update_type`: before the sub-assistant-result / handoff branches, finalize and warn for a text-only truncated `AIMessage` without returning early, so the existing handling still runs. The supervisor's final answer and sub-assistant results are covered.
- Tool-call truncation is unchanged: `_safe_check_for_truncation` still raises `TokenLimitExceededException` before tools run.
- The response's success/error status is unchanged in every mode (`invoke`, `generate`, `stream`, `invoke_task`).

### 3.4 Behavior matrix

| Case | Before | After |
|---|---|---|
| Normal `stop` / `end_turn` | finalized, success | unchanged |
| Tool-call response truncated | error (thought left open) | unchanged |
| Tool call truncated mid-arguments (`invalid_tool_calls` only) | success, thought left open, no signal | thought finalized, then error |
| Text-only response truncated | success, thought left open, no signal | success, thought finalized, WARNING with model/indicator/output_tokens |

## 4. Blast radius

- §3.1 affects only proxied **Claude** models. For Claude models already in LiteLLM's map, the explicit value equals the proxy's existing default, so behavior effectively does not change. It changes only where the proxy's `model_info` overrides the map or the profile is unknown, which is the Opus 5.5 case. Non-Claude models do not change.
  - Risk: a catalog value above the provider's real cap (for example 1000000 output tokens where the model allows 128000) now returns a 400 instead of being silently capped. Mitigations: the kill switch, the positive-int guard, and `features.max_tokens`.
  - **Bedrock quota**: Bedrock deducts `max_tokens` from tokens-per-minute quota when a request starts. Opus 5.5 goes from 4k to 128k reserved per request, which is the same as Opus 4.6 today. If quota is tight, ops lower `model_info.max_output_tokens`. That is the documented lower per-model limit AC1 allows.
- §3.2 adds one INFO line per client build.
- §3.3 changes no outcomes. It only finalizes a thought that was left open and adds a WARNING.

## 5. Testing (TDD)

- `tests/enterprise/litellm/test_llm_factory.py`:
  - a positive `max_output_tokens` is forwarded as the `max_tokens` kwarg;
  - `None`, `0`, a negative value and `True` leave the key absent;
  - `features.max_tokens=False` sets it to `None`;
  - with the kill switch off, the key is absent;
  - any positive value (1 … 1000000) is forwarded verbatim;
  - an alias with `claude_family=True` is forwarded, an alias without it is not, and a non-bool `claude_family` is ignored;
  - the INFO log reports the correct `source` for each case.
- `tests/enterprise/litellm/test_models.py`: `claude_family` is set for Bedrock/Converse Claude ids and the anthropic providers, not for Bedrock qwen/grok/kimi, Azure or Gemini, and is absent from `model_dump()` / `model_dump_json()`.
- `tests/codemie/agents/test_langgraph_event_adapter.py`:
  - text-only `finish_reason=length` and Bedrock `stopReason=max_tokens` call `_on_llm_end` once, raise no exception, and log a WARNING;
  - normal stop is unaffected;
  - tool-call truncation still raises, through the existing path;
  - the supervisor final text and sub-assistant result cases finalize and warn, and the existing branch still runs.

## 6. Acceptance mapping

| AC | Covered by |
|---|---|
| AC1: limit consistent with `max_output_tokens` | §3.1 |
| AC2/AC3: repro in Prod/Preview | §7, post-deploy |
| AC4: consistent with Opus 4.6 | §3.1 (both send an explicit limit) |
| AC5: truncation not an unqualified success | Partial: §3.3 WARNING and finalized thought. The user-facing state / `stop_reason` exposure is deferred to EPMCDME-14145. |
| AC6: effective limit diagnosable | §3.2 log, plus `output_tokens` in the §3.3 WARNING |

## 7. Rollout and verification

1. Deploy to Preview. Confirm the log shows `llm_output_limit model=claude-opus-5-5 max_tokens=128000 source=model_config`. Run the ticket repro (print 1..8000; expect ~23k output tokens) and the multi-agent repro (expect no token-limit error).
2. Repeat in Prod (AC2) and compare against the Opus 4.6 control (AC4).
3. Ops checks: the deployed proxy's LiteLLM version, and that its `claude-opus-5-5` entry has `model_info.max_output_tokens: 128000` with no `litellm_params.max_tokens` override lowering it.
4. Rollback for §3.1 without redeploy: set `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS=False`.
5. Comment on EPMCDME-14145 with the deferred AC5 scope: hard error and response-schema truncation fields.
