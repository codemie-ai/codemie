# EPMCDME-15607 Tool Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task after approval.

**Goal:** Restore schema defaults and required-field semantics without masking validation failures.
**Architecture:** Fix the two shared service validators; keep execution dictionaries and attribute updates unchanged.
**Tech Stack:** Python, existing Pydantic/LangChain, pytest.
**Requirements source:** Inline EPMCDME-15607 requirements; research: `technical-analysis.md` beside this plan. No spec.
**Commit policy:** Commit per task using repository conventions (`EPMCDME-15607: Description`), staging only the three intended paths below; no artifact-only commit task.

## Acceptance criteria

1. Omitted defaulted fields retain declared defaults, including nullable non-None defaults.
2. Real `exchange_read` validation accepts `{}`; execution forwards `"Inbox", 0, 10`.
3. Required fields, including nullable required fields, cannot be omitted; explicit nullable `None` is accepted.
4. Invalid supplied values raise the intended `ValueError`, never a formatting `AttributeError`.
5. Both validators safely describe postponed/string and complex annotations.
6. Direct real-schema regressions and the Exchange seam do not patch `validate_tool_args`.

## Global constraints

- Await plan approval. Use existing branch `EPMCDME-15607_tool-validation-defaults`; no base refresh or worktree.
- Preserve Dockerfile's existing working-tree bytes; exclude it and unrelated paths from staging.
- No dependencies, configuration, exports, production caller changes, speculative helper abstraction, or reports regeneration.
- Preserve exception prefixes, returns, explicit null handling, attribute filtering, and original execution arguments. No execution normalization or unrelated schema/Python-default repairs.

## Review focus

Task 1 tests pin default factories/inherited fields, nullable omission versus explicit null, complex/postponed error formatting, validator returns/input preservation, and unknown attribute filtering.

### Task 1 — Restore shared tool validation semantics and regression coverage

**Test-first: yes —** Real-schema omitted defaults/required-nullable omission and invalid postponed/complex annotations expose current failures; the unpatched Exchange-defaults seam fails before the fix.

**Files:**
- Modify: `src/codemie/service/tools/tool_execution_service.py:71–105`.
- Test: `tests/codemie/service/tools/test_tool_execution_validate.py`.
- Test: `tests/codemie_tools/ms_exchange/test_tools.py:81–86`.

**Interfaces:** Keep `validate_tool_args`, `validate_tool_attributes`, and `update_tool_attributes` signatures/returns unchanged. All researched invocation callers already converge here; no caller, shared fixture, config, or export edits.

- [ ] Add real Pydantic schema/tool tests: omitted scalar and nullable non-None defaults, inherited fields/default factory, required and required-nullable omissions, explicit nullable null, invalid non-nullable null and invalid values. Observe effective defaults through a test-local after-validator without replacing validation. Exercise both error handlers with postponed/string and union/generic annotations; assert existing error prefixes and field descriptions. Check unchanged argument dictionaries, argument-validator tool identity, attribute-validator validated return, and existing attribute-update filtering.
- [ ] Extend the existing parametrized Exchange defaults test: validate a real `ReadTool` with `{}`, then call `ToolExecutionService.execute_tool` with the same empty dictionary. Keep only existing provider-boundary mocks; assert `"Inbox", 0, 10` reaches `list_messages`.
- [ ] Establish the red baseline using the focused command below; require behavioral assertion failures, not collection/environment failures.
- [ ] Remove blanket omitted-key `None` injection; construct the schema using only supplied arguments. In both error handlers describe class-level resolved `model_fields` annotations with `getattr(annotation, "__name__", str(annotation))`, using `type(tool).model_fields` for attributes. Retain catches, logging, error constants, returns and attribute-update code; do not introduce a helper.
- [ ] Repeat the focused command; require all selected tests to pass. Full quality gates/review belong to the calling flow, not another plan task.

**Focused command (future implementation only; no report flags):**
`poetry run pytest -n 0 tests/codemie/service/tools/test_tool_execution_validate.py tests/codemie_tools/ms_exchange/test_tools.py`

**AC:** Task 1 covers criteria 1–6.

**Negative-constraint pass:** Task 1 changes only the listed service/tests; no Dockerfile, reports, dependencies, config, exports, caller edits, normalization, unrelated default repairs, or speculative abstraction. Tests use real validation and mock only Exchange provider boundaries. Future staging must exclude unrelated changes; no commits, test execution, nested agents, memory, or worktrees occur during planning.

**Planning checks:** Read-only `cat`, `sed`, and `grep` commands inspected the analysis, local agent/skills, guides, validator/test excerpts and pytest options; no runtime validation ran.
**Skipped/risk:** No implementation, tests, git operations, or reports were produced. Restoring required-nullable semantics intentionally rejects previously accepted omissions; broader schema/Python default mismatches remain out of scope.
