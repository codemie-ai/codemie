# EPMCDME-16117 Workflow chat history duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Prior executions' "User Input" in the workflow-chat history block is the raw user text, not the history-augmented prompt, so history stops growing recursively.

**Architecture:** `WorkflowService.create_workflow_execution` saves the augmented text as `WorkflowExecution.prompt` (workflow_service.py:291) and `_format_executions_as_markdown` re-reads it as each earlier "User Input" (formatter:158). Fix only the formatter's source: use the raw user message stored at `execution.history[0].message_raw` (written at workflow_service.py:265-273); file-only runs (empty `message_raw`) show a count placeholder; `prompt` is only a last-resort fallback.

**Tech Stack:** Python, pytest, `unittest.mock`.

## Acceptance criteria

- For every execution after the first, the context contains each earlier user input and its workflow output exactly once.
- No earlier execution's expanded prompt (`# Previous Workflow Executions`) is embedded in a later history block, including after file-only submissions.

## Global Constraints

- Assumptions (explicit): `WorkflowExecution.prompt` stays the augmented text (one history block by design); old rows with empty/None `history` fall back to `prompt` (may still duplicate, out of scope); the background (non-stream) branch not receiving history is existing behavior, out of scope; file-only runs (`message_raw` empty/None, `file_names` set) render a count placeholder such as `[1 file attached]` in the history block, file names/tokens are not shown (they are opaque workspace tokens and their decoder is private to `AgentWorkspaceService`).
- No new DB column or migration; `workflow_service.py` (`message_raw` stays as stored), the router (`workflow_executions.py:322`) and executor input are unchanged; the placeholder lives only in the formatter.
- Working tree has unrelated uncommitted changes (Dockerfile, Makefile, config/llms/llm-azure-config.yaml, scripts/dial-usage.sh): commits stage only task-owned files by explicit path, never these, and never `git stash`.
- Commit per task using the repository's existing convention (`EPMCDME-16117: Description`, per `.ai-run/guides/standards/git-workflow.md`).

## Review Focus

- File-only run (`message_raw` is `""` or `None`, `file_names` set; `prompt` is the augmented history block): must not fall through to `prompt`.
- `history` is `None` or `[]` (legacy rows) -> falls back to `prompt`.
- Everything empty (no raw input, no files, no prompt) -> `'N/A'`.

---

### Task 1: Formatter uses raw user input of prior executions

**Files:**
- Modify: `src/codemie/service/workflow_execution/workflow_execution_history_formatter.py:139-163`
- Test: `tests/codemie/service/workflow_execution/test_workflow_execution_history_formatter.py` (mocks at L345, 364, 369, 389, 434)

**Interfaces:**
- Produces: `_get_user_input(execution: WorkflowExecution) -> str` (module-private, next to `_get_workflow_output`). Order:
  1. `execution.history[0].message_raw` when `execution.history` is non-empty and `message_raw` is not None and `message_raw.strip() != ""`;
  2. else, when `execution.file_names` is truthy, `f"[{n} file(s) attached]"` with `n = len(execution.file_names)` (`"[1 file attached]"` for 1, `"[N files attached]"` otherwise);
  3. else `execution.prompt` (legacy rows with empty/None history);
  4. else `'N/A'`.
  The file check MUST precede the prompt fallback. `_format_executions_as_markdown` calls it in place of `execution.prompt or 'N/A'` at L158.

Test-first: yes — a 3-execution chain where each fake execution's `prompt` is the formatter output of the previous run (execution 2 file-only) fails today (earlier inputs appear 2x/4x) and passes after the fix.

- [ ] **Step 1: Write failing tests** in `TestFormatExecutionsAsMarkdown`:
  - `test_uses_raw_input_from_history_not_augmented_prompt`: mock with `history=[GeneratedMessage(role=USER, message_raw="raw q")]`, `file_names=[]`, `prompt="# Previous Workflow Executions ... raw q"`; assert `"**User Input:** raw q"` in result and `result.count("# Previous Workflow Executions") == 1`.
  - `test_file_only_execution_renders_placeholder`: `message_raw=""`, `file_names=["tok"]`, `prompt` = augmented history block; assert `"**User Input:** [1 file attached]"` in result and header count stays 1.
  - `test_file_only_with_none_message_raw_renders_placeholder`: same with `message_raw=None`.
  - `test_chain_of_three_executions_has_no_duplication`: loop building executions 1..3 where each `prompt = _format_executions_as_markdown(prior) + raw_i`, `history[0].message_raw = raw_i` (execution 2 file-only: `message_raw=""`, `file_names=["tok"]`), `output = out_i` (patch `_get_workflow_output` to return `execution.output`); format the 3 executions and assert `raw_1`, `out_1`, `out_2`, `raw_3` and `out_3` each have `result.count(...) == 1` and `"[1 file attached]"` is present.
  - `test_falls_back_to_prompt_when_history_empty` for `history=[]` and `history=None` with `file_names=[]`.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_history_formatter.py -v`; expect the new raw-input, file-only and chain tests FAIL.
- [ ] **Step 3:** Add `_get_user_input` and use it at L158. Update the existing mocks at L345, 364, 369, 389, 434 to set `.history = []` and `.file_names = []` (a bare `MagicMock(spec=...)` otherwise returns truthy `MagicMock`s for both).
- [ ] **Step 4:** Re-run the file plus `tests/codemie/service/test_workflow_service.py`; expect all PASS.
- [ ] **Step 5:** Commit only the two files above by explicit path, message `EPMCDME-16117: Use raw user input in workflow execution history`.

---

negative-constraints: honored by Task 1 — no new column/migration (formatter-only change), `prompt`/`message_raw`/router/executor untouched, no recursive embedding (raw input or file placeholder, never the augmented prompt when raw history exists), no decoding or printing of file tokens, background branch and legacy rows left alone, no stash and no staging of unrelated files (Global Constraints).
