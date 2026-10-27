# Technical Research

**Task**: workflow truncation finish_reason litellm
**Generated**: 2026-09-03
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14145
Workflow state output can be silently truncated without finish reason or truncated flag

Summary: Workflow state output can be silently truncated without finish reason or truncated flag.

Description: Final assistant answers may be cut mid-generation and stored as successful state output because truncation detection runs only on tool-call messages. Users receive a 200 response with incomplete content and no caller-visible warning.

Preconditions:
- Workflow state produces long LLM output.
- Affected model path can stop due to token/output limits.

Steps to Reproduce:
1. Run a long-output workflow state on the affected model path.
2. Fetch the state output after completion.
3. Inspect whether the output ends mid-sentence or mid-table.

Expected Result: Output is complete, or the API exposes a {finish_reason}/{truncated} signal so consumers can detect incomplete output.

Actual Result: Output ends mid-sentence/table with status 200 and no warning.

Affected Areas: Workflow execution, LLM response handling, LiteLLM proxy integration, State output API

Acceptance Criteria:
- Truncation detector runs on terminal messages, not only tool-call messages.
- State output API exposes truncation signal or finish reason.
- LiteLLM proxy path has confirmed/intended {max_tokens} behavior.
- Regression tests cover final-answer {finish_reason=length}/{stop_reason=max_tokens}

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/agents/langgraph_agent.py` (lines ~1213-1350) — `LangGraphAgent` hosts the full truncation-detection machinery:
  - `is_finish_reason_stop(message)` / `is_finish_reason_tool_calls(message)` — classify an `AIMessage` by whether it terminated normally with tool calls or ended without them.
  - `_get_truncation_indicator(message)` — pure metadata inspector; normalizes across providers: OpenAI/LiteLLM `finish_reason in ["length", "max_tokens"]`, native Claude `stop_reason == "max_tokens"`, Bedrock camelCase `stopReason`.
  - `_check_for_truncated_response(message)` — raises `TokenLimitExceededException(message, model, truncation_reason)` when the indicator fires.
  - `_safe_check_for_truncation(message)` — wraps the check in try/except; re-raises only `TokenLimitExceededException`, **swallows/logs everything else** (line ~1246-1248).
  - `_log_incomplete_tool_calls(...)` — logging helper for the tool-call path.
  - This detector is correct and provider-aware, but is wired into the wrong code paths (see below).
- `src/codemie/agents/langgraph_event_adapter.py` — `LangGraphEventAdapter` / `LangGraphCallbackBridge` is the dispatcher between LangGraph stream chunks and the agent's callback methods. **This is the exact location of the bug**:
  - `parse_update_type()` line ~154-156: gated by `is_finish_reason_tool_calls(message)` → calls `self.agent._safe_check_for_truncation(message)`.
  - `handle_supervisor_tool_calls()` line ~218: same pattern, tool-call-gated.
  - `parse_message_type()` lines ~149-151: `elif self.agent.is_finish_reason_stop(message): ... self.agent._on_llm_end(...)` — **calls `_on_llm_end` directly, never calls `_safe_check_for_truncation`.**
  - `parse_supervisor_message_type()` lines ~182-183: identical gap for the supervisor/multi-agent final-answer path.
  - Net effect: a final assistant answer with `finish_reason=length`/`stop_reason=max_tokens` and no tool calls is treated exactly like a normal completion.
- `src/codemie/core/exceptions.py` (line 252) — `TokenLimitExceededException(ValueError)` carries `model` and `truncation_reason` attributes. Currently only ever raised from the tool-call-gated paths.
- `src/codemie/agents/assistant_agent.py` — `AIToolsAgent.invoke_task` (line 444) and `TaskResult` (line 96: `result`, `success`, `intermediate_steps`, `original_exc`; `from_agent_response` at line 106). `TaskResult` has no truncation/finish_reason field; `success=True` is set regardless of truncation for terminal answers, since no exception was ever raised for that path.
- `src/codemie/workflows/nodes/agent_node.py` — `AgentNode.execute` / `post_process_output` only raises `TaskException` when `output.success is False`. A truncated-but-"successful" `TaskResult` passes straight through unchanged.
- `src/codemie/workflows/nodes/base_node.py` — `_run_success_path` (lines 210-253): `execute()` → `post_process_output()` → `workflow_execution_service.finish_state(..., output=processed_output, status=SUCCEEDED)`. This is the orchestration point that persists the (possibly silently truncated) output as a successful state.
- `src/codemie/service/workflow_execution/workflow_execution_service.py` — `WorkflowExecutionService.finish_state` (line 372): `state.output = output; state.save()` — plain string assignment, no truncation metadata captured or persisted.
- `src/codemie/core/workflow_models/workflow_execution.py`:
  - `WorkflowExecutionState` (table model, line 119) — `output: Optional[str]` only.
  - `WorkflowExecutionStateOutput` (API response model, line 368) — `output: Optional[str]` only. **This is the exact API gap named in the acceptance criteria** — no `finish_reason`/`truncated` field exists anywhere in the model or the DB column.
- `src/codemie/rest_api/routers/workflow_executions.py` (line ~690-704) — `GET /workflows/{workflow_id}/executions/{execution_id}/states/{state_id}/output` (`get_workflow_execution_state_output`) returns `WorkflowExecutionStateOutput(output=...)` sourced directly from the DB `output` column, with HTTP 200 and no truncation signal — confirms the "State output API" gap end-to-end.
- `src/codemie/enterprise/litellm/llm_factory.py` — `create_litellm_chat_model`, `_filter_litellm_params` (line ~1037): `if llm_model_details.features.max_tokens is False: filtered['max_tokens'] = None` — this is the LiteLLM proxy path's `max_tokens` handling referenced by the acceptance criteria ("LiteLLM proxy path has confirmed/intended {max_tokens} behavior"). Behavior is per-model, driven by the `features.max_tokens` flag in `config/llms/*.yaml`.
- `src/codemie/enterprise/litellm/models.py` — `LiteLLMChatOpenAI` — proxy-backed `AzureChatOpenAI`-compatible wrapper; LiteLLM is accessed as an OpenAI-compatible HTTP endpoint, not via a direct `litellm` python import.
- Analogous existing pattern (different bug class, same shape): `src/codemie/workflows/utils/utils.py:631-654` — truncates *serialized state* exceeding `MAX_JSONB_SIZE_BYTES` and sets a `_truncated: True` / `_message` flag on the stored object. This is the precedent pattern the ticket implicitly wants mirrored for *LLM-generation* truncation, which currently has no equivalent flag.
- Prior related work: `docs/superpowers/tasks/2026-08-07-epmcdme-13459-fix-token-limit-message/` — reworked the *message text* shown to users for the tool-call truncation path (`TokenLimitExceededException` display string via `extended_error()`); did not touch the terminal-message gap this ticket addresses. Useful as a precedent for exception-message conventions.

### Architecture and Layers Affected

- **API / Router** — `src/codemie/rest_api/routers/workflow_executions.py` (`get_workflow_execution_state_output` endpoint) must expose the new signal.
- **Data / Model** — `src/codemie/core/workflow_models/workflow_execution.py` (`WorkflowExecutionState` table model, `WorkflowExecutionStateOutput` API model) need a new field (e.g. `truncated: bool` / `finish_reason: Optional[str]`); a DB column addition implies an Alembic migration.
- **Service / Business logic** — `src/codemie/service/workflow_execution/workflow_execution_service.py` (`finish_state`) needs to accept and persist truncation metadata.
- **Workflow orchestration (LangGraph nodes)** — `src/codemie/workflows/nodes/base_node.py`, `agent_node.py` — need to thread the truncation signal from `TaskResult` through to `finish_state`.
- **Agent / LLM integration** — `src/codemie/agents/assistant_agent.py` (`TaskResult`), `langgraph_agent.py` (detector), `langgraph_event_adapter.py` (dispatcher, **root cause site**) — the primary fix goes here: wire `_safe_check_for_truncation` (or an equivalent non-raising signal) into the `is_finish_reason_stop` branches of both `parse_message_type` and `parse_supervisor_message_type`.
- **External LLM integration** — `src/codemie/enterprise/litellm/llm_factory.py`, `models.py` — needs verification/confirmation (and likely a test) of `max_tokens` behavior when proxied through LiteLLM, per-model `features.max_tokens` flag in `config/llms/*.yaml`.

### Integration Points

- `codemie.agents.langgraph_event_adapter` → `codemie.agents.langgraph_agent` (`_safe_check_for_truncation`, `is_finish_reason_stop`, `is_finish_reason_tool_calls`).
- `codemie.agents.langgraph_agent` → `codemie.core.exceptions` (`TokenLimitExceededException`).
- `codemie.workflows.nodes.agent_node` → `codemie.agents.assistant_agent` (`AIToolsAgent`, `TaskResult`) and → `codemie.service.workflow_execution.WorkflowExecutionService`.
- `codemie.workflows.nodes.base_node` → `codemie.service.workflow_execution.workflow_execution_service.finish_state` and → `codemie.core.workflow_models.workflow_execution` (`WorkflowExecutionState`, `WorkflowExecutionStatusEnum`).
- `codemie.rest_api.routers.workflow_executions` → `codemie.core.workflow_models.workflow_execution` (`WorkflowExecutionState`, `WorkflowExecutionStateOutput`) and → `codemie.service.workflow_service.WorkflowService`.
- `codemie.enterprise.litellm.llm_factory` / `models` → `codemie.configs` (`LITE_LLM_URL`, retry config) and → `codemie.service.budget.*`; accessed by core LLM setup via an abstraction boundary (`LLM_PROXY_MODE=lite_llm`) not fully traced in this pass — worth a follow-up look at `codemie/configs/llm_config.py` if deeper litellm-model-selection tracing is needed during implementation.
- External: LiteLLM proxy is reached as an OpenAI-compatible HTTP endpoint (`LITE_LLM_URL`), not a direct `litellm` python SDK import; provider-specific finish/stop-reason shapes (OpenAI `finish_reason`, Claude `stop_reason`, Bedrock camelCase `stopReason`) must all be preserved by any fix.

### Patterns and Conventions

- **Detector-method pattern**: pure metadata check (`_get_truncation_indicator`) is separated from the raising check (`_check_for_truncated_response`) and the safe/wrapped invocation (`_safe_check_for_truncation`). Any fix should reuse this separation rather than inlining new logic.
- **Provider format normalization**: a single function branches on OpenAI/LiteLLM (`finish_reason`), native Claude (`stop_reason`), and Bedrock (camelCase `stopReason`) — this must be mirrored wherever the terminal-message check is added.
- **Exception-carries-metadata pattern**: `TokenLimitExceededException(message, model, truncation_reason)` — structured error propagation; `extended_error()` on `AbstractAgent` sanitizes the user-facing message (strips tool/model internals).
- **Event-adapter/dispatch pattern**: `LangGraphEventAdapter` centralizes branching on stream-chunk type (`messages` vs `updates`) and message shape before delegating to agent callback methods — the natural insertion point for the fix.
- **Result/Status pattern**: `TaskResult.success` is currently a strict boolean; there is no "succeeded-but-incomplete" state. Introducing a truncation signal likely requires extending `TaskResult` (and downstream `WorkflowExecutionState`/`WorkflowExecutionStateOutput`) rather than only raising an exception, since raising would change status to FAILED (a behavior/API-contract decision the fix must make explicitly — the ticket's Expected Result allows for either "complete output" or "expose a signal", implying non-raising is the preferred path for the terminal case, unlike the tool-call path which raises).
- **Config-driven per-model feature flags**: `features.max_tokens` in `config/llms/*.yaml` toggles whether the `max_tokens` param is even sent to a given provider/model — this is orthogonal to detection but relevant to acceptance criterion 3.
- **SQLModel-based persistence** with `BaseModelWithSQLSupport`/`CommonBaseModel` mixins for workflow execution tables — any new persisted field follows this convention and needs an Alembic migration.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — directs changes toward extending `WorkflowExecutor`/nodes/utilities rather than a separate execution path; the fix touching `LangGraphAgent`/`LangGraphEventAdapter` (used by the workflow executor) aligns with this guide.
- `.ai-run/guides/integration/llm-providers.md` — covers LiteLLM proxy gating and provider-specific response handling; relevant background for reconciling `finish_reason`/`stop_reason` differences across Anthropic/OpenAI/Bedrock-via-LiteLLM.
- `.ai-run/guides/development/error-handling.md` — "Accurate Error Details" principle (one message per distinct failure mode, no silent substitution) directly applies: silently accepting a truncated response as success violates this convention and supports fixing it.
- `.ai-run/guides/development/logging-patterns.md` — relevant to how a truncation-on-final-answer event should be logged/surfaced.
- `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md` — general REST conventions (response models, error handling); no truncation-specific content found, but response-model extension should follow these conventions.
- No dedicated guide exists for "truncation detection" or "finish_reason handling" as a standalone topic.

### Architectural Decisions

- No formal ADR files found in `docs/` for this domain.
- Prior work item `docs/superpowers/tasks/2026-08-07-epmcdme-13459-fix-token-limit-message/` documents the existing truncation-detection design this bug builds on (single raise site `_check_for_truncated_response`, wrapped by `_safe_check_for_truncation`, invoked via `_get_truncation_indicator`); its own tech-analysis explicitly notes "No guides found... No ADRs found in the codebase" for this domain, confirming conventions here are derived from code, not documentation.
- User-facing error strings for truncation follow the established convention of living as `AGENT_MSG_*`/`LITELLM_MSG_*` constants in `config.py`, referenced from `error_constants.py`, with `extended_error()` as the per-error-code display hook (`src/codemie/agents/tools/agent.py`).
- A placeholder task-tracking file (`.state.json`) exists at the current run directory from the same day, confirming this is a newly opened investigation with no prior spec/analysis captured before this research.

### Derived Conventions

- Truncation detection is a two-step pattern: classify the message (`is_finish_reason_tool_calls`/`is_finish_reason_stop`), then only the tool-calls branch currently invokes the checker — the terminal branch must be updated to match.
- `_safe_check_for_truncation` swallows/logs all exceptions except `TokenLimitExceededException` — detection failures inside the checker itself do not surface either; any new call site should be aware this failure mode is already accepted practice in this codebase, not a regression to fix separately.
- The state-storage-size truncation flag pattern (`_truncated: True` / `_message` in `workflows/utils/utils.py`) is the closest existing precedent for exposing a similar signal for LLM-generation truncation — the fix could mirror this flag shape for consistency, though it currently exists at the JSONB-serialization layer, not the state-output-model layer.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/agents/test_langgraph_truncation.py` — unit tests for `_check_for_truncated_response`, `_get_truncation_indicator`, `_safe_check_for_truncation`, `_log_incomplete_tool_calls`. Covers OpenAI (`finish_reason=length`), Claude (`stop_reason=max_tokens`), Bedrock camelCase (`stopReason`), normal completion (`stop`/`end_turn`), and `extended_error()` sanitization. **All tests call the detection methods directly on a constructed `AIMessage` — none exercise the actual event-parsing dispatch path (`LangGraphEventAdapter`).**
- `tests/codemie/agents/test_langgraph_event_adapter.py` — tests `parse_update_type`/`handle_supervisor_tool_calls`, but only for JSON/unicode serialization of tool args. No test exercises `parse_message_type`'s `is_finish_reason_stop` branch (the terminal/final-answer path) at all, and no test asserts `_safe_check_for_truncation` is (or is not) called from that branch.
- `tests/enterprise/litellm/test_llm_factory.py` — extensive coverage of LiteLLM proxy chat-model creation, budget resolution, headers, streaming-chunk cost injection; no test targets the `max_tokens` feature-filter branch (`filtered['max_tokens'] = None` when `features.max_tokens is False`) or confirms the actual `max_tokens` value forwarded through the proxy request.
- `tests/enterprise/litellm/test_models.py` — tests feature *detection* (does a model support `max_tokens`/`max_completion_tokens`), not runtime request behavior.
- `tests/codemie/core/test_errors.py`, `tests/codemie/core/workflow_models/test_workflow_execution.py`, `test_workflow_models.py` — general error/model tests; none cover `WorkflowExecutionStateOutput`'s field set or a truncation scenario.
- No test file exists for `get_workflow_execution_state_output` in `src/codemie/rest_api/routers/workflow_executions.py`.

### Testing Framework and Patterns

- pytest, Python 3.12, Poetry-managed.
- Manual object construction (`object.__new__(LangGraphAgent)` + attribute stubbing via a `create_mock_agent` helper) rather than full DI/factory fixtures.
- Heavy `unittest.mock.patch`/`MagicMock` with call-order assertions (`execution_order` lists) for LiteLLM factory internals.
- Class-based test grouping (`TestTruncationDetection`, `TestTruncationHelperMethods`, `TestExtendedError`) with BDD-flavored test names (`test_detect_truncation_...`).
- `pytest.raises(...)` combined with asserting exception attributes (`exc_info.value.truncation_reason`, `.model`).
- `@pytest.fixture(autouse=True)` for clearing LRU caches (e.g. `is_premium_model.cache_clear()`).
- `@pytest.mark.parametrize` for boundary/missing-field cases across providers.
- No factory-boy/faker usage; all fixtures are hand-rolled mocks.

### Coverage Gaps

- **Terminal-message truncation detection**: no test drives a terminal (non-tool-call) `AIMessage` with `finish_reason=length`/`stop_reason=max_tokens` through `parse_message_type`/`parse_supervisor_message_type` and asserts a truncation signal fires. This is the primary regression test the acceptance criteria require.
- **State output API truncation/finish_reason signal**: no test exists for `get_workflow_execution_state_output`; no test covers the currently-missing field on `WorkflowExecutionStateOutput`.
- **LiteLLM proxy `max_tokens` behavior**: no test verifies the actual `max_tokens` value forwarded through `create_litellm_chat_model`/`_filter_litellm_params` for models with `features.max_tokens: true` vs `false`.
- **Supervisor final-answer path**: `parse_supervisor_message_type`'s `is_finish_reason_stop` branch has the identical gap as the primary agent path and is equally untested.
- **End-to-end persistence check**: no test confirms that a truncated final answer, once detected, actually results in a distinguishable state (either a raised exception changing status, or a persisted truncation flag) rather than silent `SUCCEEDED` status.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_MODE` (`internal` | `lite_llm`) — selects whether LLM calls route through the LiteLLM proxy; directly relevant to confirming the proxy-path `max_tokens`/finish_reason behavior called out in the acceptance criteria. Both modes should be covered by regression tests since response metadata shape may differ.
- `LLM_PROXY_ENABLED`, `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `LITE_LLM_PROXY_APP_KEY`, `LITE_LLM_MASTER_KEY`, `LLM_PROXY_TIMEOUT` — LiteLLM proxy connection/auth configuration (`src/codemie/configs/config.py`, lines ~652-726).
- `HIDE_AGENT_STREAMING_EXCEPTIONS` (config.py line 905) — toggles whether raw/technical vs. classifier-friendly error text is shown to the caller for agent errors including token-limit truncation; affects presentation, not detection.
- `AGENT_MSG_TOKEN_LIMIT` (config.py line 894) — user-facing string used when `TokenLimitExceededException` is classified.
- Per-model `max_output_tokens` / `features.max_tokens` — declared in YAML (`config/llms/*.yaml`), not env vars, but loaded through the standard settings loader.

### Configuration Files

- `config/llms/llm-aws-config.yaml`, `llm-azure-config.yaml`, `llm-gcp-config.yaml`, `llm-dial-config.yaml` — LLM model registry; each entry sets `max_output_tokens` and a `features.max_tokens` boolean governing whether the `max_tokens` request parameter is sent to the provider at all for that model. Several models (gpt-5 family, o-series, some Claude/Gemini entries) have `features.max_tokens: false`; others (e.g. gpt-4.1-mini) have `true`. This per-model flag directly gates whether truncation via `max_tokens` can even occur for a given model/provider combination — central to acceptance criterion 3.
- `src/codemie/configs/config.py` — central settings including `LLM_PROXY_MODE`/`LLM_PROXY_ENABLED`/`LITE_LLM_*` (lines ~652-726). No explicit override of `max_tokens` behavior exists here — the endpoint list (`LITE_LLM_PROXY_ENDPOINTS`, incl. `/v1/chat/completions`, `/v1/messages`, `/v1/responses`) only declares routing, not truncation handling. This is the area needing the "confirmed/intended LiteLLM proxy max_tokens behavior" per the acceptance criteria — currently undocumented/unconfirmed in code and untested.

### Feature Flags and Deployment Concerns

- `features.max_tokens` (per model, `config/llms/*.yaml`) — toggles whether `max_tokens` is sent to the provider for that model; models with `false` may behave differently under the LiteLLM proxy vs. direct provider calls.
- `HIDE_AGENT_STREAMING_EXCEPTIONS` — toggles error-message presentation path only, not detection logic.
- No Docker/CI/Helm references to `finish_reason`, truncation, or `max_tokens` were found in `deploy-templates/`.
- Because `LLM_PROXY_MODE=lite_llm` changes the LLM invocation path, the fix and its regression tests should cover both `internal` and `lite_llm` proxy modes, since response metadata shape may differ between direct provider SDKs and LiteLLM-proxied OpenAI-compatible responses.

---

## 6. Risk Indicators

- No test coverage exists for the terminal-message truncation path (`is_finish_reason_stop` branches in `parse_message_type`/`parse_supervisor_message_type`) — this is the core of the bug and the core of the required regression tests; building it from zero rather than extending an existing test.
- No test coverage exists for the state output API endpoint (`get_workflow_execution_state_output`) at all — a new field/behavior there is untested surface area.
- Extending `WorkflowExecutionState`/`WorkflowExecutionStateOutput` with a `finish_reason`/`truncated` field touches a persisted SQLModel table — likely requires an Alembic migration; migration conventions were not explored in this pass and should be checked in `.ai-run/guides/data/database-patterns.md` before implementation.
- `TaskResult.success` is a strict boolean with no "succeeded-but-incomplete" state — introducing a truncation signal requires a design decision (raise an exception and mark FAILED, vs. add a non-raising signal field threaded through `TaskResult` → `agent_node` → `base_node` → `finish_state` → `WorkflowExecutionState` → API). This is a genuine architectural choice, not just a bug fix, and affects 5+ files across 4 layers.
- `_safe_check_for_truncation` already swallows/logs all exceptions other than `TokenLimitExceededException` — any new call site inherits this fail-open behavior; worth flagging explicitly rather than silently carrying it into the fixed path.
- LiteLLM proxy `max_tokens` behavior is currently unconfirmed and untested — acceptance criterion 3 is effectively a "verify and document" task layered on top of the detection fix, and could uncover its own follow-up bug (e.g. `max_tokens: null` being silently dropped for some models).
- Supervisor final-answer path (`parse_supervisor_message_type`) has the identical gap and must be fixed in lockstep with the primary agent path, doubling the surface area of the core fix.
- Provider-specific finish/stop-reason shape differences (OpenAI `finish_reason`, Claude `stop_reason`, Bedrock camelCase `stopReason`) must be preserved correctly in both the fix and its tests — a source of subtle regressions if not parametrized carefully (existing tests in `test_langgraph_truncation.py` already do this well for the tool-call path and should be mirrored).
- No ADRs or dedicated guides exist for "truncation detection" — conventions must be derived from the `_safe_check_for_truncation`/exception-metadata pattern already in code, increasing risk of inconsistent design choices during implementation.

---

## 7. Summary for Complexity Assessment

This task touches five architectural layers: Agent/LLM integration (root-cause fix in `langgraph_event_adapter.py` and possibly `langgraph_agent.py`), Workflow orchestration (`agent_node.py`, `base_node.py`), Service (`workflow_execution_service.py`), Data/Model (`workflow_execution.py` — `WorkflowExecutionState` and `WorkflowExecutionStateOutput`), and API (`workflow_executions.py` router). The core defect itself is narrow and precisely located — two `elif` branches in `LangGraphEventAdapter.parse_message_type` and `parse_supervisor_message_type` call `_on_llm_end` without ever invoking the already-correct `_safe_check_for_truncation` detector — but satisfying all four acceptance criteria requires threading a new signal end-to-end from LLM response metadata through `TaskResult`, workflow node post-processing, service persistence, and finally the REST response model. Realistic file-change surface is likely 6-10 source files plus a probable Alembic migration if the truncation flag is persisted as a new DB column, plus 3-4 test files (extending `test_langgraph_event_adapter.py` and `test_langgraph_truncation.py`, adding a new router test, and extending `test_llm_factory.py` for the LiteLLM `max_tokens` criterion).

Technical novelty is moderate: the detection logic itself already exists and is well-tested for the tool-call path, so the fix largely follows an established pattern (detector-method separation, provider-format normalization) rather than inventing one. The genuinely novel decision is architectural, not algorithmic — whether a detected terminal-truncation should raise `TokenLimitExceededException` (changing execution status to FAILED, consistent with the tool-call path) or introduce a new non-raising "succeeded but incomplete" signal threaded through `TaskResult`/`WorkflowExecutionState`/`WorkflowExecutionStateOutput` (better matching the ticket's "or the API exposes a signal" framing). This decision cascades through every layer named above and should be resolved explicitly during planning rather than left implicit.

Test coverage posture is mixed and instructive: the underlying detector (`_check_for_truncated_response`, `_get_truncation_indicator`) is thoroughly unit-tested across OpenAI/Claude/Bedrock shapes, but the dispatch layer that should invoke it for terminal messages has zero coverage of that branch, the state-output API endpoint has no tests at all, and the LiteLLM proxy `max_tokens` runtime behavior (as opposed to feature-detection) is untested. Combined with the multi-layer schema/API design decision and the need for both `internal` and `lite_llm` proxy-mode regression coverage, this pushes the task toward medium-to-high complexity despite the root cause being a small, well-understood code change.
