# Technical Research

**Task**: workflow chat conversation history prompt
**Generated**: 2026-10-26
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16117. Don't stash changes untracked / not staged for commit.

Ticket EPMCDME-16117 (Bug): Workflow chat recursively duplicates previous execution history

Summary: Each new execution in the same workflow conversation includes prior executions' expanded prompts, which already contain earlier history. This recursively duplicates user inputs and workflow outputs instead of maintaining a flat conversation history.

Description: When multiple messages are sent in the same workflow chat, the saved prompt and initial graph input for later executions appear to include previous executions' expanded prompts. As a result, earlier inputs and outputs are embedded repeatedly.

Preconditions:
- The user can access and open a workflow chat.
- A workflow conversation is available for multiple consecutive executions.

Steps to Reproduce:
1. Open a workflow chat and complete one execution.
2. Send another message in the same conversation and wait for completion.
3. Send a third message.
4. Inspect the third execution's saved prompt and initial graph input.

Expected Result: Each prior execution's original user input and workflow output appears once, without recursively embedding expanded prompts.

Actual Result: The third execution contains the first execution's input/output twice: directly and again inside the second execution's stored prompt. The fourth execution contains four copies.

Affected Areas: Workflow chat conversation history; Saved prompts for workflow executions; Initial graph input.

Acceptance Criteria: For each execution after the first, the conversation context contains each earlier user input and corresponding workflow output exactly once, without duplication.

Notes: Backend repo (Python/FastAPI/LangGraph). Load the relevant .ai-run/guides (workflows/langgraph-workflows.md, architecture/service-layer-patterns.md) as needed. Do not modify any files other than writing technical-analysis.md.

---

## 2. Codebase Findings

### Existing Implementations
Root cause is a feedback loop between two places (traced end to end):

1. `src/codemie/service/workflow_service.py` `WorkflowService.create_workflow_execution` (~L230-300): calls `_augment_user_input_with_history(user_input, conversation_id, workflow_config.id)` (L835-868), which prepends `format_execution_history(...)` output to the raw input. The augmented string is saved as `WorkflowExecution.prompt` (`prompt=augmented_input`, L291). The raw `user_input` is stored separately in `execution_history[0]` (`message`/`message_raw`, history_index=0) and in the conversation's user `GeneratedMessage`.
2. `src/codemie/service/workflow_execution/workflow_execution_history_formatter.py` `_format_executions_as_markdown` (L158): renders `**User Input:** {execution.prompt or 'N/A'}` for every earlier execution. Because `execution.prompt` is the already-augmented string (contains `# Previous Workflow Executions ...`), execution N embeds the full expanded prompt of N-1, giving the recursive growth (1, 2, 4 copies).
3. `src/codemie/rest_api/routers/workflow_executions.py` L322 (streaming branch): `WorkflowExecutor.create_executor(user_input=execution.prompt, ...)` — the augmented prompt becomes the executor `user_input`. `src/codemie/workflows/workflow.py` `on_workflow_start` (L1186-1221) turns it into `HumanMessage(content=self.user_input)` and `inputs[USER_INPUT]` (the "initial graph input"). Here the single history block is intended; it is wrong only because the stored `prompt` is recursive.
4. Non-streaming/background branch of the same router passes `request.user_input` (raw), so it never receives history (existing behavior, outside the ticket's scope unless the spec says otherwise).

Where the raw input is available (no schema change needed): `WorkflowExecution.history[0].message_raw` (role USER) set at creation; also the conversation `GeneratedMessage` user entries (`message_raw`) paired by `history_index` with the assistant ref carrying `execution_id`. Legacy rows already have this raw copy, so reading it would also fix existing conversations on the next run.

Other `create_workflow_execution` callers: `workflow.py:1223` (no conversation_id, no history) and `nodes/sub_workflow_node.py:115` (no conversation_id). `execution.prompt` is read only at the formatter (L158) and router (L322) in `src/`; it is also exposed via `WorkflowExecutionResponse.prompt` (`core/workflow_models/workflow_execution.py` L293).

### Architecture and Layers Affected
- Service layer: `WorkflowService` (workflow_service.py), `service/workflow_execution/workflow_execution_history_formatter.py`.
- Model: `WorkflowExecution` (`core/workflow_models/workflow_execution.py` L148-172; `prompt`, `history`, `conversation_id` columns).
- API router: `rest_api/routers/workflow_executions.py` (consumer of `execution.prompt`).
- Workflow runtime: `workflows/workflow.py` `WorkflowExecutor.on_workflow_start` (consumer only).

### Integration Points
- Formatter -> `Conversation.find_by_id`, `WorkflowExecution.get_by_workflow_id`, `WorkflowExecutionState.get_all_by_fields` (fallback output from last state when `execution.output` empty).
- Router -> `WorkflowExecutor.create_executor` -> graph inputs.

### Patterns and Conventions
Private module-level helper functions in the formatter, broad try/except returning `""` on failure; static methods on `WorkflowService`. Guides: layered architecture; langgraph-workflows guide says extend existing executor/service, not a new path.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/workflows/langgraph-workflows.md` (generic: use `WorkflowExecutor`/nodes) and `.ai-run/guides/architecture/service-layer-patterns.md` exist; neither covers workflow chat history. No docs/ADR on the history formatter.

### Architectural Decisions
Only inline comments: workflow_service.py L291 "Use augmented input with history for workflow execution" and router L322 "Use augmented prompt from execution (includes history)" — these record the intent that `prompt` holds the augmented text, which is what the formatter then misuses.

### Derived Conventions
Raw user text lives in `history[*].message_raw`; `prompt` is the executed (augmented) text.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/workflow_execution/test_workflow_execution_history_formatter.py` — formatter unit tests; mocks executions as `MagicMock` with `.prompt` set (L345, 364, 369, 389, 434). Any change to which field the formatter reads breaks/needs updating these mocks.
- `tests/codemie/service/test_workflow_service.py` — `create_workflow_execution` tests; `_augment_user_input_with_history` is patched to return `"input"` (L841, 1608, 1677, 1720, 1733), so no test covers augmentation or the multi-execution recursion.
- `tests/codemie/rest_api/routers/test_workflow_executions*.py` — router tests.

### Testing Framework and Patterns
pytest with `unittest.mock` (`patch`, `MagicMock`); formatter tests patch `Conversation.find_by_id`, `WorkflowExecution.get_by_workflow_id`, `WorkflowExecutionState.get_all_by_fields` by full module path.

### Coverage Gaps
No test chains 3+ executions (formatter output fed back as `prompt`); no test of `_augment_user_input_with_history` itself.

---

## 5. Configuration and Environment

### Environment Variables
None relevant found.

### Configuration Files
None governing this path. No Alembic migration needed unless the model gains a column (Alembic lives in `src/external/alembic/versions`).

### Feature Flags and Deployment Concerns
No flags. Working tree has unrelated uncommitted changes (Dockerfile, Makefile, config/llms/llm-azure-config.yaml, scripts/dial-usage.sh); the task says not to stash them.

---

## 6. Risk Indicators

- Speculative: the smallest fix is in the formatter (read raw input from `execution.history[0].message_raw`, falling back to `prompt`), leaving `prompt` and the executor input unchanged; alternative is storing raw input in a new column (needs migration) — heavier.
- Legacy rows: existing executions have augmented `prompt` but a raw `history[0]`; a fallback on `prompt` for rows lacking `history` could still reintroduce duplication for those.
- `history` is `Optional` and may be empty/None for old rows; MagicMock-based formatter tests use `.prompt` only and will need updating.
- `WorkflowExecutionResponse.prompt` exposes the augmented text to the UI/API; ticket's "saved prompt" expectation could be read as wanting `prompt` itself to stay flat (it will contain one history block, by design) — spec should clarify.
- Background (non-stream) branch ignores history (router uses `request.user_input`); inconsistent but pre-existing.
- Formatter swallows all exceptions and returns `""`, so regressions degrade silently into "no history".

---

## 7. Summary for Complexity Assessment

The bug is a small, well-localized feedback loop in the service layer: `create_workflow_execution` saves the history-augmented text as `WorkflowExecution.prompt`, and `workflow_execution_history_formatter._format_executions_as_markdown` reads that same `prompt` as the "User Input" of each earlier execution, so every run re-embeds the previous run's whole expanded prompt. The runtime (`WorkflowExecutor.on_workflow_start`) and router simply forward `execution.prompt` and are not themselves faulty.

Change surface is likely one source file (the formatter, possibly `workflow_service.py`) plus test updates; no new dependencies, config or (probably) migrations. The raw user input is already persisted in `WorkflowExecution.history[0].message_raw` and conversation history, so no novel pattern is needed.

Test posture: formatter has solid unit tests (mock-based, will need adjusting), but nothing exercises multi-execution chaining or the augmentation function; a regression test over 3-4 chained executions is a gap. Main risks are legacy rows, optional `history`, and ambiguity over what the "saved prompt" should contain.

---

## 8. External References

None named by the task.
