# Technical Research

**Task**: azure devops test plan tools limit
**Generated**: 2026-10-23
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-12071. I have implemented it, but I think I did it wrong. You can check commits for this branch on how I did it.
/superpowers:brainstorming The problem with this implementation in my opinion: BaseAzureDevOpsTestPlanTool has config field of type AzureDevOpsTestPlanConfig. AzureDevOpsTestPlanConfig has field limit. Config is read from DB, right? And it is read from integration user create. But the problem is that ui never passes limit when creating AzureDevOps integration. But that's not the problem, because we can make UI pass it, right? That's not the bid deal. But. I don't think we should do this. Azure DevOps integration is used not only for TestPlan tools. It is used, for example, when creating datasource from Azure DevOps Wiki. So we can't just add, because it is used by some tools, but isn't used by others. I see the solution this way: we should let the model pass limit when calling tool with some validation: min = 1, max = 100. Model can choose how many items from 100 it should see. In case if there are > 100 test plans / suites we should display message notifying user about that.

Notes for research: the branch `epmcdme-12071` (vs `main`) already contains commits implementing a config-based limit (see `git log main..HEAD` and `git diff main...HEAD`). Document what they changed (config field, tools, truncation notice, tests, docs) so the plan can say what to revert/move to a tool-argument design.

---

## 2. Codebase Findings

### Existing Implementations

Package `src/codemie_tools/azure_devops/test_plan/` (all paths below relative to repo root):
- `models.py` - `AzureDevOpsTestPlanConfig(CodeMieToolConfig)` plus one pydantic input model per tool (`GetTestPlanInput`, `GetTestSuiteInput`, `GetTestCasesInput`, ...). Only `project` (and ids) are in the input models today; none has `limit`.
- `tools.py` - `BaseAzureDevOpsTestPlanTool(CodeMieTool)` (`config: AzureDevOpsTestPlanConfig`, lazy `_client`), and 9 tool classes. Three list tools: `GetTestPlanTool` (`execute(plan_id=None, project=None)`), `GetTestSuiteTool` (`execute(plan_id, suite_id=None, project=None)`), `GetTestCasesTool` (`execute(plan_id, suite_id, project=None)`).
- `tools_vars.py` - `ToolMetadata` per tool; `description` carries the "Arguments:" list shown to the LLM; `config_class=AzureDevOpsTestPlanConfig`.
- `toolkit.py` - `AzureDevOpsTestPlanToolkitUI` (`settings_config: True`), unaffected by this task.

### What branch `epmcdme-12071` changed vs `main` (4 code commits + 1 planning-artifacts commit)

Baseline on `main`: `AzureDevOpsTestPlanConfig.limit: Optional[int] = Field(default=5, description="Default number of items to return in queries")`; the three list tools slice `[: self.config.limit]` silently (tools.py main lines ~153, ~225, ~313). Input models have no `limit`.

Commits (oldest to newest):
1. `086e51b53` "Raise ADO test plan default limit to 100 and document it" - `models.py`: config `limit` now `Field(default=100, ge=1, description=<long text about truncation + "increase in integration settings">, json_schema_extra={"help": ...})`. `tests/.../test_config_mapping.py::test_limit_default_value` assertion 5 -> 100.
2. `9021f12e3` "Add truncation notice..." - `tools.py`: new `BaseAzureDevOpsTestPlanTool._limit_results(items, kind)` reading `self.config.limit`; returns bare `list[dict]` when `limit is None or len <= limit`, else `{"items": [...limit], "notice": "Showing {limit} of {n} {kind}. Result truncated by the 'limit' setting of the Azure DevOps Test Plan integration; increase 'limit' there to retrieve more."}`. The three list tools call `self._limit_results(..., "test plans"|"test suites"|"test cases")`. `tools_vars.py`: one added line in each of `GET_TEST_PLAN_TOOL`, `GET_TEST_SUITE_TOOL`, `GET_TEST_CASES_TOOL` descriptions: "Results are capped by the integration's configured limit; a notice is returned when truncated." (no `limit` argument is listed). `tests/.../test_tools.py`: `_make_items`, `LIST_CASES`, `_run`, parametrized `TestListLimit` (4 tests x 3 tools: 8 items not truncated at limit 100; exactly-limit returns bare list; above-limit returns notice dict; `limit=None` returns all).
3. `eea8cd4e1` "Reject non-positive ADO test plan limit" - `models.py` (adds `ge=1`; net 5-line change), `test_tools.py::test_non_positive_limit_rejected` (0, -2 -> ValueError).
4. `b261e0f2b` ruff format of that test.
5. `9f418f209` planning artifacts only under `docs/superpowers/tasks/2026-10-23-epmcdme-12071/` (plan.md, technical-analysis.md, code-review.md, decisions.jsonl, gate-run.json, actual-complexity.json). Decision log: plan approved as "default limit 100, per-integration, truncation notice" - i.e. the config-based design the requester now rejects. These artifacts are a superseded design record; the new run dir is `docs/superpowers/tasks/2026-10-23-epmcdme-12071-ado-test-plan-limit-tool-arg/`.

Net source diff vs main: 4 src/test files (`models.py`, `tools.py`, `tools_vars.py`, plus tests `test_config_mapping.py`, `test_tools.py`); no docs outside `tools_vars.py`/field descriptions, no CHANGELOG entry (CHANGELOG has no Azure mention).

### Architecture and Layers Affected
Tool layer only (`codemie_tools` library): input schema (`args_schema` pydantic model) -> `execute()` on `CodeMieTool` subclass -> Azure DevOps SDK `TestPlanClient`. Config layer: `AzureDevOpsTestPlanConfig`. LLM-facing prompt layer: `ToolMetadata.description` in `tools_vars.py`.

### Integration Points
- `src/codemie/service/settings/settings.py:33,232` maps `AzureDevOpsTestPlanConfig` to `CredentialTypes.AZURE_DEVOPS`. The same credential type is shared by `AzureDevOpsGitConfig`, `AzureDevOpsWikiConfig`, `AzureDevOpsWorkItemConfig` (lines 229-232), which is the requester's argument against putting `limit` in the integration. `AzureDevOpsWikiConfig` has no `limit`; `AzureDevOpsWorkItemConfig.limit` (default 5) exists.
- SDK: `get_test_plans`, `get_test_suites_for_plan`, `get_test_case_list` in `.venv/.../azure/devops/v7_0/test_plan/test_plan_client.py` all accept `continuation_token`; the tools never pass it, so each call returns only the first server batch. Whether the SDK page size can exceed 100 / what total count is knowable was not verified. "Showing N of M" therefore reports M = length of the first batch returned, not the true total.

### Patterns and Conventions
- Precedent for model-passed limit in the same package family: `src/codemie_tools/azure_devops/work_item/models.py:83-89` `SearchWorkItemsInput.limit: Optional[int] = Field(default=None, description="Number of items to return. IMPORTANT: Tool returns all items if limit=-1 ... else taken from tool configuration")`, consumed in `work_item/tools.py:301-313` (`if not limit: limit = self.config.limit`). Config default 5 plus arg override.
- Precedent for bounded arg validation: `src/codemie_tools/ms_exchange/tools.py:57` `count: int = Field(default=10, ge=1, le=MAX_PAGE, description=f"Number of messages (max {MAX_PAGE})")` (constant + `ge`/`le` on args_schema field).
- Tools declare arguments twice: pydantic input model (`models.py`) and the "Arguments:" text in `tools_vars.py` description; `execute()` signature must match the schema fields. All test-plan tools set `script_callable = True`, so `execute()` can also be called directly (bypassing `args_schema` validation) by workspace scripts and unit tests; `execute_structured` path exists in `src/codemie_tools/base/codemie_tool.py:192`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists. Relevant: `agents/agent-tools.md`, `agents/custom-tool-creation.md`, `agents/tool-overview.md`, `development/configuration-patterns.md`, `testing/testing-patterns.md`, `standards/code-quality.md` (not read in depth; the two repo precedents above were taken from source). No ADO test-plan-specific guide or `docs/` page; no CHANGELOG entry for the area.

### Architectural Decisions
Only `decisions.jsonl` of the earlier run (plan.approved: per-integration limit, default 100, truncation notice) - to be superseded.

### Derived Conventions
Optional list args default to `None` with descriptive `Field(description=...)`; bounds via `ge`/`le` in `Field`; user-visible tool contract documented in `tools_vars.py`.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie_tools/azure_devops/test_plan/`: `test_tools.py` (per-tool success tests built with `MagicMock(spec=...)` client assigned to `tool._client`, plus branch-added `TestListLimit`), `test_config_mapping.py` (config field mapping; `test_limit_default_value`, `test_limit_custom_value`, and a `"limit": 5` DB-format case at ~line 94), `test_azure_devops_toolkit.py`.

### Testing Framework and Patterns
pytest, parametrization, `unittest.mock.MagicMock`; tools built with `Tool(config=AzureDevOpsTestPlanConfig(...))` and `tool.execute(**kwargs)` called directly. Branch tests construct configs with `limit=...` (e.g. existing tests pass `limit=5` to config at several sites) - these break/are moot if the field leaves the config class, unless config keeps tolerating an unknown `limit` key (not checked whether `CodeMieToolConfig` forbids extras).

### Coverage Gaps
No test for `args_schema` bounds (min 1 / max 100) since schemas have no `limit`; branch's `TestListLimit` and `test_non_positive_limit_rejected` test the config-based design and call `execute()` directly (no schema validation path exercised). No test that `limit` appears in the tool description.

---

## 5. Configuration and Environment

### Environment Variables
Only `AZURE_DEVOPS_CACHE_DIR` set at import in `tools.py`. None related to limit.

### Configuration Files
`AzureDevOpsTestPlanConfig` in `models.py` (currently `limit` default 100, `ge=1`, `Optional[int]`). On `main` the field is `default=5`. The settings UI renders config fields from the class (`json_schema_extra` carries `help`/`placeholder`); the requester states the UI never sends `limit` on integration create, so stored DB credentials generally lack it and the default applies.

### Feature Flags and Deployment Concerns
None. Persisted integrations that did include `limit` (e.g. `"limit": 5` DB-format test case) are a compatibility concern if the field is removed.

---

## 6. Risk Indicators

- Speculative: removing `limit` from `AzureDevOpsTestPlanConfig` could break loading of stored credentials/tests that still pass `limit` (check `CodeMieToolConfig` extra handling; existing tests at `test_tools.py` and `test_config_mapping.py:94` pass `limit=`). Alternative is to leave the field (main default 5) unused, or revert it to main's state.
- Speculative: `limit` becomes the model-facing contract; default when omitted is a design choice (100 vs 5 as on main vs work_item's config fallback). The requester's text implies max 100, min 1, and notice when more than 100.
- Result shape change: truncation returns a dict (`items` + `notice`) for the same tool that otherwise returns a list; consumers/scripts (`script_callable = True`) see a type union. Notice text currently references the integration setting and must be reworded.
- Direct `execute()` calls bypass `args_schema` bounds; if the bound must hold for scripts too, `execute()` needs its own check (work_item precedent does none).
- SDK pagination: first batch only (no `continuation_token` handling), so "more than 100" may be undetectable beyond the first server page; count in the notice may under-report.
- Stale artifacts: `docs/superpowers/tasks/2026-10-23-epmcdme-12071/` records the rejected design and is committed on the branch.
- Test churn: `TestListLimit` (helper `_run` builds config with `limit=`), `test_non_positive_limit_rejected`, `test_limit_default_value`/`custom_value` all target the config field.

---

## 7. Summary for Complexity Assessment

The change is confined to the tool layer of one package (`src/codemie_tools/azure_devops/test_plan/`): three list tools (`get_test_plan`, `get_test_suite`, `get_test_cases`), their three input models in `models.py`, their three descriptions in `tools_vars.py`, and the helper `_limit_results` in `tools.py`. The branch currently implements a config-based limit (`AzureDevOpsTestPlanConfig.limit`, default 100, `ge=1`, truncation notice naming the integration setting). Moving to a tool-argument design means adding a bounded `limit` (1..100) to those three input models and `execute()` signatures, rewriting the helper to take the argument and the notice text, updating the tool descriptions, and reverting or neutralising the config field (back to main's state or removal) and its tests. Roughly 4 source/test files plus possibly reverting commit artifacts; no DB, API, or migration changes found.

Technical novelty is low: both pieces already exist in the repo (`work_item` `SearchWorkItemsInput.limit` for a model-passed limit with config fallback, `ms_exchange` `Field(ge=1, le=MAX_PAGE)` for bounds). Test posture is decent: a parametrized suite already covers the three list tools, but it is written against the config design and needs rewriting; schema-bound validation has no tests yet.

Key risks: compatibility of stored configs that still carry `limit` if the field is removed; return-type union (list vs dict with notice); `execute()` bypassing schema bounds for scripts; SDK returns only first server batch (no continuation handling), so the "more than 100" notice is only as accurate as the batch returned.

---

## 8. External References

None named by the task. (Task referenced the branch git history, which was read via `git log main..HEAD` and `git diff main...HEAD`; findings are in Section 2.)
