# EPMCDME-15787 Forward Output-Token Limit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this run, `sdlc-standard` executes it inline with superpowers:test-driven-development.

**Goal:** On the LiteLLM proxy path, send the model's configured `max_output_tokens` as `max_tokens` so the proxy stops applying its 4096 fallback (Opus 5.5). Log the effective limit for each request. For text-only responses that are truncated, finalize the thought and log a WARNING.

**Architecture:**
- The CodeMie-side change is confined to the LiteLLM client factory (`enterprise/litellm/llm_factory.py`), plus one config kill switch.
- Truncation diagnostics live in `LangGraphEventAdapter`, behind a pure helper `get_truncation_indicator` that `LangGraphAgent._get_truncation_indicator` now delegates to.
- No response-schema changes. Tool-call truncation behaviour does not change.

**Tech Stack:** Python 3.12, FastAPI backend, langchain-openai `AzureChatOpenAI` (serializes `max_tokens` as `max_completion_tokens`), LangGraph, pytest 8 + unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-10-12-epmcdme-15787-opus-5-5-max-output-tokens/spec.md`

## Global Constraints

- The kill switch config field is exactly `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS: bool = True`. It goes in the `# LiteLLM args` block of `src/codemie/configs/config.py`, right after `LLM_PROXY_TRACK_USAGE`.
- Forward a value only when `max_output_tokens` is an `int`, is not a `bool`, and is `> 0`. Otherwise the `max_tokens` key must be **absent** from the base args.
- `_filter_litellm_params` stays unchanged and runs after the base args. If `features.max_tokens is False`, it sets `max_tokens=None`.
- The INFO log line format is exactly `llm_output_limit model=<base_name> max_tokens=<n|unset> source=<model_config|proxy_default|disabled_by_feature|disabled_by_config>`.
- The WARNING log line format is exactly `truncated_response model=<llm_model> indicator=<indicator> output_tokens=<n|unknown> action=kept`.
- Logs carry no user content and no secrets.
- Tool-call truncation still raises `TokenLimitExceededException` through `_safe_check_for_truncation`. The success/error outcome does not change in any mode.
- Native factories in `src/codemie/core/dependecies.py` are not modified.
- Run tests with `poetry run pytest <path> -n 0 -q` (pytest.ini sets `-n 3` and `--import-mode=importlib`; `-n 0` keeps targeted runs serial).
- Lint: `make ruff`.
- Commit messages are ticket-first: `EPMCDME-15787: <summary>`. Only stage the task's files (`docker-compose.yml` and the older untracked docs stay out).

## Review Focus

1. `MagicMock` model details: existing tests pass a `MagicMock` whose `max_output_tokens` is itself a `MagicMock`. The int guard must treat it as unset, or every existing factory test starts sending a `MagicMock` as `max_tokens`. Task 1 pins this with a dedicated test.
2. `max_output_tokens=True` (a bool is an int subclass) must not be forwarded as `1`. Task 1 pins this.
3. A supervisor handoff-back synthetic `AIMessage` has `METADATA_KEY_HANDOFF_BACK` in metadata and no finish reason. It must not be treated as truncated, and the existing handoff-back branch must still run. Task 3 pins this.
4. A truncated text-only message must be finalized exactly once, and the stop branch must not fire for it. `is_finish_reason_stop` returns False for `length`/`max_tokens`, so this holds; Task 3 asserts a single `_on_llm_end` call.
5. A truncated message with no `usage_metadata` must log `output_tokens=unknown` without raising. Task 3 pins this.

---

### Task 1: Forward `max_output_tokens` as `max_tokens` (with kill switch)

**Files:**
- Modify: `src/codemie/configs/config.py` (LiteLLM args block, after `LLM_PROXY_TRACK_USAGE: bool = True`, ~line 714)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py:304-327` (`_build_chat_model_base_args`) and add the helper `_resolve_max_output_tokens` directly above it
- Test: `tests/enterprise/litellm/test_llm_factory.py` (new class `TestMaxOutputTokensForwarding` at end of file)

**Interfaces:**
- Produces: `_resolve_max_output_tokens(llm_model_details: "LLMModel") -> int | None` and `config.LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS: bool`. Task 2 uses both.

Test-first: yes — `create_litellm_chat_model` with `max_output_tokens=128000` passes `max_tokens=128000` to `LiteLLMChatOpenAI`. This currently fails with KeyError/None because the key is never built.

- [ ] **Step 1: Write the failing tests**

Append to `tests/enterprise/litellm/test_llm_factory.py`:

```python
class TestMaxOutputTokensForwarding:
    """max_output_tokens from model config is forwarded as max_tokens on the LiteLLM path (EPMCDME-15787)."""

    @staticmethod
    def _model_details(max_output_tokens, max_tokens_feature=True):
        details = MagicMock()
        details.base_name = "claude-opus-5-5"
        details.configuration = None
        details.api_version = None
        details.max_output_tokens = max_output_tokens
        details.features.streaming = True
        details.features.temperature = True
        details.features.parallel_tool_calls = True
        details.features.max_tokens = max_tokens_feature
        details.features.top_p = True
        return details

    @staticmethod
    def _create(details, forward_enabled=True):
        from codemie.configs.config import config
        from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials

        context = LiteLLMContext(credentials=LiteLLMCredentials(api_key="user-key", url="http://test"))
        with (
            patch.object(config, "LITE_LLM_URL", "http://test:4000"),
            patch.object(config, "LITE_LLM_APP_KEY", "test-key"),
            patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"),
            patch.object(config, "OPENAI_API_TYPE", "azure"),
            patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3),
            patch.object(config, "LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS", forward_enabled),
            patch("codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI") as mock_model_cls,
        ):
            from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

            create_litellm_chat_model(
                llm_model_details=details,
                litellm_context=context,
                user_email="test@example.com",
            )
        return mock_model_cls.call_args.kwargs

    def test_forwards_positive_max_output_tokens(self):
        kwargs = self._create(self._model_details(128000))

        assert kwargs["max_tokens"] == 128000

    @pytest.mark.parametrize("value", [None, 0, -1, True, "128000"])
    def test_omits_max_tokens_for_unusable_values(self, value):
        kwargs = self._create(self._model_details(value))

        assert "max_tokens" not in kwargs

    def test_omits_max_tokens_for_mock_attribute(self):
        details = self._model_details(None)
        details.max_output_tokens = MagicMock()

        kwargs = self._create(details)

        assert "max_tokens" not in kwargs

    def test_feature_flag_false_sends_none(self):
        kwargs = self._create(self._model_details(128000, max_tokens_feature=False))

        assert kwargs["max_tokens"] is None

    def test_kill_switch_off_omits_max_tokens(self):
        kwargs = self._create(self._model_details(128000), forward_enabled=False)

        assert "max_tokens" not in kwargs
```

> If `create_litellm_chat_model` with user credentials still calls a budget or runtime-override helper that hits the network, add `patch("codemie.enterprise.litellm.llm_factory._configure_direct_runtime_overrides")` to the `with` block. Mirror the patches the neighbouring `TestCreateLiteLLMChatModel` credential tests use.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestMaxOutputTokensForwarding -n 0 -q`
Expected:
- `test_forwards_positive_max_output_tokens` FAILS with `KeyError: 'max_tokens'`.
- `test_kill_switch_off_omits_max_tokens` errors with `AttributeError` (config has no `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS`).
- The parametrized `test_omits_*` cases also error on the missing config attribute.

- [ ] **Step 3: Add config field**

In `src/codemie/configs/config.py`, after `LLM_PROXY_TRACK_USAGE: bool = True`:

```python
    # Send model max_output_tokens as max_tokens to LiteLLM proxy; False falls back to proxy defaults
    LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS: bool = True
```

- [ ] **Step 4: Implement forwarding**

In `src/codemie/enterprise/litellm/llm_factory.py`, add above `_build_chat_model_base_args`:

```python
def _resolve_max_output_tokens(llm_model_details: "LLMModel") -> Optional[int]:
    """Return the model's max_output_tokens when it is a positive integer, otherwise None."""
    value = getattr(llm_model_details, "max_output_tokens", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
```

Replace the body of `_build_chat_model_base_args` with:

```python
    from codemie.configs import config

    base_args = {
        'azure_endpoint': config.LITE_LLM_URL,
        'openai_api_version': llm_model_details.api_version or config.OPENAI_API_VERSION,
        'openai_api_key': creds.api_key if creds else config.LITE_LLM_APP_KEY,
        'openai_api_type': config.OPENAI_API_TYPE,
        'deployment_name': llm_model_details.base_name,
        'model_name': llm_model_details.base_name,
        'streaming': streaming,
        'max_retries': config.AZURE_OPENAI_MAX_RETRIES,
        'temperature': temperature,
        'top_p': top_p,
        'include_response_headers': config.LLM_PROXY_TRACK_USAGE,
    }

    # Without an explicit limit the proxy falls back to LiteLLM defaults (4096 for unmapped models)
    max_output_tokens = _resolve_max_output_tokens(llm_model_details)
    if config.LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS and max_output_tokens is not None:
        base_args['max_tokens'] = max_output_tokens

    return base_args
```

- [ ] **Step 5: Run tests to verify they pass, plus the full factory module**

Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py -n 0 -q`
Expected: all PASS, including the pre-existing `TestCreateLiteLLMChatModel` tests (MagicMock `max_output_tokens` → key absent).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/configs/config.py src/codemie/enterprise/litellm/llm_factory.py tests/enterprise/litellm/test_llm_factory.py
git commit -m "EPMCDME-15787: Forward max_output_tokens as max_tokens on LiteLLM path"
```

---

### Task 2: Log the effective output-token limit per client build (AC6)

**Files:**
- Modify: `src/codemie/enterprise/litellm/llm_factory.py`. Add `_log_output_limit` next to `_filter_litellm_params`, and call it in `create_litellm_chat_model` right after `filtered_args = _filter_litellm_params(...)`.
- Test: `tests/enterprise/litellm/test_llm_factory.py` (extend `TestMaxOutputTokensForwarding`)

**Interfaces:**
- Consumes: `config.LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` from Task 1, and `filtered_args['max_tokens']` as produced by Tasks 1 and 2.
- Produces: `_log_output_limit(llm_model_details: "LLMModel", filtered_args: dict[str, Any]) -> None`.

Test-first: yes — for `max_output_tokens=128000`, `logger.info` is called with `llm_output_limit model=claude-opus-5-5 max_tokens=128000 source=model_config`. This currently fails because no such call happens.

- [ ] **Step 1: Write the failing tests**

Add to `TestMaxOutputTokensForwarding`. Note that `_create` needs the logger patched, so add a `_create_logged` variant:

```python
    def _create_logged(self, details, forward_enabled=True):
        with patch("codemie.enterprise.litellm.llm_factory.logger") as mock_logger:
            self._create(details, forward_enabled=forward_enabled)
        return [c.args[0] for c in mock_logger.info.call_args_list if c.args and "llm_output_limit" in c.args[0]]

    @pytest.mark.parametrize(
        ("max_output_tokens", "feature", "forward_enabled", "expected"),
        [
            (128000, True, True, "llm_output_limit model=claude-opus-5-5 max_tokens=128000 source=model_config"),
            (None, True, True, "llm_output_limit model=claude-opus-5-5 max_tokens=unset source=proxy_default"),
            (128000, False, True, "llm_output_limit model=claude-opus-5-5 max_tokens=unset source=disabled_by_feature"),
            (128000, True, False, "llm_output_limit model=claude-opus-5-5 max_tokens=unset source=disabled_by_config"),
        ],
    )
    def test_logs_effective_output_limit(self, max_output_tokens, feature, forward_enabled, expected):
        lines = self._create_logged(
            self._model_details(max_output_tokens, max_tokens_feature=feature), forward_enabled=forward_enabled
        )

        assert lines == [expected]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestMaxOutputTokensForwarding::test_logs_effective_output_limit -n 0 -q`
Expected: 4 FAIL with `assert [] == ['llm_output_limit ...']`.

- [ ] **Step 3: Implement**

In `src/codemie/enterprise/litellm/llm_factory.py`, add after `_filter_litellm_params`:

```python
def _log_output_limit(llm_model_details: "LLMModel", filtered_args: dict[str, Any]) -> None:
    """Log the output-token limit sent to the proxy so truncation can be diagnosed per request."""
    from codemie.configs import config

    max_tokens = filtered_args.get('max_tokens')
    if max_tokens:
        source = "model_config"
    elif llm_model_details.features.max_tokens is False:
        source = "disabled_by_feature"
    elif not config.LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS:
        source = "disabled_by_config"
    else:
        source = "proxy_default"
    logger.info(
        f"llm_output_limit model={llm_model_details.base_name} max_tokens={max_tokens or 'unset'} source={source}"
    )
```

In `create_litellm_chat_model`, directly after `filtered_args = _filter_litellm_params(base_args, llm_model_details)`:

```python
    _log_output_limit(llm_model_details, filtered_args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/enterprise/litellm/test_llm_factory.py -n 0 -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/litellm/llm_factory.py tests/enterprise/litellm/test_llm_factory.py
git commit -m "EPMCDME-15787: Log effective output-token limit for LiteLLM requests"
```

---

### Task 3: Finalize and warn on truncated text-only responses (B-lite)

**Files:**
- Modify: `src/codemie/agents/langgraph_event_adapter.py`
  - Add the module-level `get_truncation_indicator` after the imports.
  - Add the method `_finalize_truncated_text` on `LangGraphEventAdapter`.
  - Wire it into `parse_update_type` (~lines 182-203) and `parse_supervisor_update_type` (~lines 228-256).
- Modify: `src/codemie/agents/langgraph_agent.py:1322-1342`. `_get_truncation_indicator` now delegates to `get_truncation_indicator`; the docstring is kept.
- Test: `tests/codemie/agents/test_langgraph_event_adapter.py` (new class `TestTruncatedTextResponses`)
- Regression: `tests/codemie/agents/test_langgraph_truncation.py`, `tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py`

**Interfaces:**
- Produces:
  - `get_truncation_indicator(response_metadata: dict[str, Any] | None) -> str | None` returns `"finish_reason=<v>"` for `length`/`max_tokens`, `"stop_reason=max_tokens"` for `stop_reason`/`stopReason == "max_tokens"`, and otherwise `None`.
  - `LangGraphEventAdapter._finalize_truncated_text(message: Any, author: str | None = None) -> bool`.

Test-first: yes — for a text-only `AIMessage` with `response_metadata={"finish_reason": "length"}`, `parse_update_type` calls `_on_llm_end` once and `logger.warning` once. This currently fails because neither branch matches and no call happens.

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/agents/test_langgraph_event_adapter.py`:

```python
class TestTruncatedTextResponses:
    """Text-only responses cut at max_tokens are finalized and logged (EPMCDME-15787)."""

    @pytest.fixture
    def agent(self):
        agent = MagicMock()
        agent.llm_model = "claude-opus-5-5"
        agent.is_finish_reason_tool_calls.side_effect = lambda m: bool(getattr(m, "tool_calls", None))
        agent.is_finish_reason_stop.return_value = False
        agent._on_llm_end = MagicMock()
        agent._safe_check_for_truncation = MagicMock()
        return agent

    @pytest.fixture
    def mock_logger(self):
        with patch("codemie.agents.langgraph_event_adapter.logger") as mock_logger:
            yield mock_logger

    @pytest.mark.parametrize(
        ("metadata", "indicator"),
        [
            ({"finish_reason": "length"}, "finish_reason=length"),
            ({"stopReason": "max_tokens"}, "stop_reason=max_tokens"),
        ],
    )
    def test_agent_text_truncation_finalizes_and_warns(self, agent, mock_logger, metadata, indicator):
        message = AIMessage(content="1\n2\n3", id="msg-1", response_metadata=metadata)
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 4096, "total_tokens": 4106}

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-1", author=None)
        agent._safe_check_for_truncation.assert_not_called()
        mock_logger.warning.assert_called_once_with(
            f"truncated_response model=claude-opus-5-5 indicator={indicator} output_tokens=4096 action=kept"
        )

    def test_missing_usage_metadata_logs_unknown(self, agent, mock_logger):
        message = AIMessage(content="partial", id="msg-2", response_metadata={"finish_reason": "length"})

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        mock_logger.warning.assert_called_once_with(
            "truncated_response model=claude-opus-5-5 indicator=finish_reason=length output_tokens=unknown action=kept"
        )

    def test_normal_stop_not_treated_as_truncated(self, agent, mock_logger):
        agent.is_finish_reason_stop.return_value = True
        message = AIMessage(content="done", id="msg-3", response_metadata={"finish_reason": "stop"})

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-3")
        mock_logger.warning.assert_not_called()

    def test_tool_call_truncation_still_uses_existing_check(self, agent, mock_logger):
        message = AIMessage(content="", id="msg-4", response_metadata={"finish_reason": "length"})
        message.tool_calls = [{"name": "search", "args": {"q": "x"}, "id": "call-1"}]
        agent._safe_check_for_truncation.side_effect = RuntimeError("token limit")

        with pytest.raises(RuntimeError, match="token limit"):
            LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        mock_logger.warning.assert_not_called()

    def test_supervisor_text_truncation_finalizes_and_warns(self, agent, mock_logger):
        agent._supervisor_coordinator.binding_for.return_value = None
        message = AIMessage(content="final", id="msg-5", response_metadata={"finish_reason": "length"})

        LangGraphEventAdapter(agent).parse_supervisor_update_type({"supervisor": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-5", author=None)
        mock_logger.warning.assert_called_once()

    def test_supervisor_subassistant_truncation_finalizes_and_still_reports_result(self, agent, mock_logger):
        binding = (uuid4(), "supervisor")
        agent._supervisor_coordinator.binding_for.return_value = binding
        message = AIMessage(content="partial", id="msg-6", response_metadata={"stop_reason": "max_tokens"})

        LangGraphEventAdapter(agent).parse_supervisor_update_type(
            {"analyst": {"messages": [message]}}, author="analyst"
        )

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-6", author="analyst")
        agent._LangGraphAgent__handle_supervisor_subassistant_result.assert_called_once_with(
            [message], run_id=binding, author="analyst"
        )

    def test_supervisor_handoff_back_not_treated_as_truncated(self, agent, mock_logger):
        from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK

        message = AIMessage(content="back", id="msg-7", response_metadata={METADATA_KEY_HANDOFF_BACK: True})

        LangGraphEventAdapter(agent).parse_supervisor_update_type({"analyst": {"messages": [message]}})

        agent._on_llm_end.assert_not_called()
        mock_logger.warning.assert_not_called()
        agent._LangGraphAgent__handle_supervisor_handoff_back.assert_called_once()


class TestGetTruncationIndicator:
    @pytest.mark.parametrize(
        ("metadata", "expected"),
        [
            ({"finish_reason": "length"}, "finish_reason=length"),
            ({"finish_reason": "max_tokens"}, "finish_reason=max_tokens"),
            ({"stop_reason": "max_tokens"}, "stop_reason=max_tokens"),
            ({"stopReason": "max_tokens"}, "stop_reason=max_tokens"),
            ({"finish_reason": "stop"}, None),
            ({}, None),
            (None, None),
        ],
    )
    def test_indicator(self, metadata, expected):
        from codemie.agents.langgraph_event_adapter import get_truncation_indicator

        assert get_truncation_indicator(metadata) == expected
```

Add `from unittest.mock import MagicMock, patch` to the import line at the top (it currently imports only `MagicMock`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_event_adapter.py -n 0 -q`
Expected: the `TestTruncatedTextResponses` cases FAIL. For example, `test_agent_text_truncation_finalizes_and_warns` fails with `Expected '_on_llm_end' to be called once. Called 0 times.`. `TestGetTruncationIndicator` errors with `ImportError: cannot import name 'get_truncation_indicator'`. The normal-stop, tool-call and handoff-back tests may already pass; they are regression guards.

- [ ] **Step 3: Implement the pure indicator and agent delegation**

In `src/codemie/agents/langgraph_event_adapter.py`, after the imports:

```python
def get_truncation_indicator(response_metadata: dict[str, Any] | None) -> str | None:
    """Return a truncation indicator when the response was cut at the output-token limit."""
    if not response_metadata:
        return None
    # LiteLLM proxy / OpenAI format
    finish_reason = response_metadata.get("finish_reason")
    if finish_reason in ("length", "max_tokens"):
        return f"finish_reason={finish_reason}"
    # Native Claude format (Bedrock uses 'stopReason' camelCase)
    stop_reason = response_metadata.get("stop_reason", response_metadata.get("stopReason"))
    if stop_reason == "max_tokens":
        return f"stop_reason={stop_reason}"
    return None
```

In `src/codemie/agents/langgraph_agent.py`, change the import line to
`from codemie.agents.langgraph_event_adapter import LangGraphCallbackBridge, LangGraphEventAdapter, get_truncation_indicator`
and replace the body of `_get_truncation_indicator` (keep its docstring) with:

```python
        return get_truncation_indicator(response_metadata)
```

- [ ] **Step 4: Implement adapter finalize helper and wiring**

Add a method to `LangGraphEventAdapter`, placed before `parse_update_type`:

```python
    def _finalize_truncated_text(self, message: Any, author: str | None = None) -> bool:
        """Finalize a text-only response cut at the output-token limit and log it; tool calls keep raising."""
        if not isinstance(message, AIMessage) or self.agent.is_finish_reason_tool_calls(message):
            return False
        indicator = get_truncation_indicator(message.response_metadata)
        if not indicator:
            return False
        self._restore_response_headers(message)
        self.agent._on_llm_end(response=message, run_id=message.id, author=author)
        usage = message.usage_metadata or {}
        logger.warning(
            f"truncated_response model={self.agent.llm_model} indicator={indicator} "
            f"output_tokens={usage.get('output_tokens', 'unknown')} action=kept"
        )
        return True
```

In `parse_update_type`, extend the `"agent"` branch's if/elif chain. Add a final branch after the existing `elif self.agent.is_finish_reason_stop(last_message): ...` block:

```python
            else:
                # Text-only response cut at max_tokens: no stop reason, so finalize it here.
                self._finalize_truncated_text(last_message)
```

In `parse_supervisor_update_type`, directly after `last_message = messages[-1]` and before the existing `if isinstance(last_message, AIMessage) and self.agent.is_finish_reason_tool_calls(...)`:

```python
        # Finalize and log a truncated text-only answer; the branches below still route it.
        self._finalize_truncated_text(last_message, author=author)
```

- [ ] **Step 5: Run the new tests and the regression suites**

Run: `poetry run pytest tests/codemie/agents/test_langgraph_event_adapter.py tests/codemie/agents/test_langgraph_truncation.py tests/codemie/agents/test_langgraph_multi_assistant_supervisor.py tests/codemie/service/assistant/test_langgraph_assistant.py -n 0 -q`
Expected: all PASS. If the `test_langgraph_multi_assistant_supervisor.py` fixtures use a real agent whose messages have no truncation metadata, `_finalize_truncated_text` returns False and nothing changes.

- [ ] **Step 6: Lint and commit**

Run: `make ruff`. Expected: format and check pass.

```bash
git add src/codemie/agents/langgraph_event_adapter.py src/codemie/agents/langgraph_agent.py tests/codemie/agents/test_langgraph_event_adapter.py
git commit -m "EPMCDME-15787: Finalize and log truncated text-only responses"
```

---

## Self-Review

- **Spec coverage:**
  - §3.1 → Task 1. §3.2 → Task 2. §3.3 → Task 3.
  - §3.4 matrix → Task 3 tests (normal stop, tool-call truncation, text-only truncation).
  - §5 tests → Tasks 1–3.
  - §7 rollout is post-deploy and has no code task.
- **Placeholders:** none. The one conditional note (an extra patch in Task 1 Step 1) names the exact target.
- **Type consistency:**
  - `_resolve_max_output_tokens -> Optional[int]` is used only in Task 1.
  - `_log_output_limit(llm_model_details, filtered_args)` is used only in Task 2.
  - `get_truncation_indicator(dict | None) -> str | None` is used in Task 3 by the adapter and agent.
  - `_finalize_truncated_text(message, author=None) -> bool` is used in Task 3.
- **Review Focus:** each of the 5 lines has a pinned test: Task 1 (MagicMock, bool) and Task 3 (handoff-back, single finalize, missing usage).
