# ADO Test Plan `limit` as a Tool Argument Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start clean from `main` (drop the local config-based work on branch `epmcdme-12071`), then let the model pass `limit` (-1 = all, or any integer >= 1) to `get_test_plan`, `get_test_suite`, `get_test_cases`; when omitted, the existing `AzureDevOpsTestPlanConfig.limit` applies, with its default raised from 5 to 100. Truncation notice never mentions integration settings.

**Architecture:** Input models get `limit: Optional[int] = Field(default=None, ...)` plus a shared `check_list_limit` validator. A new `BaseAzureDevOpsTestPlanTool._limit_results(items, kind, limit)` replaces main's silent `[: self.config.limit]` slicing: effective limit = explicit `limit` if given, else `self.config.limit` (precedent `work_item/tools.py:301-313`, `if not limit: limit = self.config.limit`, and `SearchWorkItemsInput.limit` where -1 = all). Config change is limited to the `limit` default and description.

**Tech Stack:** Python, pydantic, pytest, `unittest.mock`. Package `src/codemie_tools/azure_devops/test_plan/`, tests `tests/codemie_tools/azure_devops/test_plan/`.

**Spec:** none (inline requirements, EPMCDME-12071); analysis at `technical-analysis.md` in this directory.

Commit per task using the repository's existing convention (`.ai-run/guides/standards/git-workflow.md`, ticket prefix `EPMCDME-12071:`). Test commands: see `.ai-run/guides/quality-gates.md`; per task run `poetry run pytest tests/codemie_tools/azure_devops/test_plan -q`. Task 0 makes no commit.

## Assumptions (recorded)

1. `limit` argument semantics (user choice): `None`/omitted = use the integration default; `-1` = everything; any integer `>= 1` = cap; `0` and values below `-1` are rejected; no upper max (a hard max of 100 would make item 101+ unreachable within the first batch).
2. Default source (user's decision, work-item precedent): the existing `AzureDevOpsTestPlanConfig.limit`. A module-level constant default was considered and rejected. The config default changes from 5 to 100 (still `Optional[int]`); 100 is an unmeasured judgment call (5 silently hid items, the ticket asks for a higher default), documented in the field description. The model can override per call; `-1` = all. No payload-size or performance claim anywhere.
3. Invalid configured values: config `None` follows main's behaviour (`[:None]`, i.e. no cap, everything returned); config `-1` means all; config `0` or below `-1` is rejected by the same check inside `_limit_results` and surfaces as `ToolException`. The `Field(default=100)` default covers the normal case where stored credentials omit `limit` (the UI never sends it).
4. **SDK facts (verified in `azure/devops/released/test_plan/test_plan_client.py`) that every doc must state accurately:** `get_test_plans`, `get_test_suites_for_plan` and `get_test_case_list` have no page-size / top / limit parameter, so the SDK cannot be asked for "first N". They accept `continuation_token`, but these tools never pass it, so only the first batch the server returns is seen. Therefore `limit` is applied in the tool AFTER the whole first batch is fetched: it reduces what is returned to the model, not what is fetched from Azure DevOps (no API-cost saving), and items beyond the first batch are unreachable even with `limit=-1`. Wording must not claim the SDK "has no pagination"; say the SDK has no page-size parameter and the tools do not use `continuation_token`. Adding pagination is out of scope; the notice's "N of M" counts only the first batch, never a true server total.
5. Compatibility: stored credentials with `limit` and the existing `test_config_mapping.py` `"limit": 5` DB-format case must still pass. Existing tests passing `limit=5` to the config stay unchanged. The work_item config's own copy of the default 5 is out of scope.
6. The rejected design's commits and planning artifacts (`docs/superpowers/tasks/2026-10-23-epmcdme-12071/`) are dropped from the local, unpushed branch (user decision); the old directory must not be copied into new work.
7. Tool descriptions in `tools_vars.py` document the `limit` argument without stating a fixed default (config can change it).
8. Docs touched: `tools_vars.py` descriptions, the `limit` Field description in the three input models, `AzureDevOpsTestPlanConfig.limit` description, a comment in `_limit_results`. `.ai-run/guides` was grepped for "test plan" / "azure devops" (case-insensitive) and has no guide covering these tools, so no guide is edited and no new guide is created.

## Acceptance criteria

- [ ] `git log main..HEAD` contains no config-based-limit commits and `docs/superpowers/tasks/2026-10-23-epmcdme-12071/` is absent.
- [ ] `get_test_plan`, `get_test_suite`, `get_test_cases` accept `limit`: omitted/`None`, `-1`, and any integer >= 1 are accepted; `0` and `-2` are rejected by the schema and by direct calls.
- [ ] Omitting `limit` applies `config.limit`; that config default is 100 (was 5).
- [ ] An explicit `limit` overrides `config.limit`; `limit=-1` returns every retrieved item as a plain list with no notice, even when `config.limit` is small.
- [ ] A notice appears only when a limit actually cut items; it states how many were returned out of how many were retrieved, tells the model to pass `limit=-1` (or a larger limit), and does not mention the integration or its settings.
- [ ] In `AzureDevOpsTestPlanConfig` only the `limit` default (5 -> 100) and description changed versus `main`; the list tools read `config.limit` only as the fallback when `limit` is omitted.
- [ ] Tool descriptions list `limit`, say the integration default applies when omitted, `-1` = all, explain the truncation notice, and state that `limit` trims after fetching, Azure DevOps is not asked for fewer items, and the tools do not paginate (items beyond the first server batch are unreachable even with `limit=-1`).
- [ ] The `limit` Field description (input models), the `AzureDevOpsTestPlanConfig.limit` description and the `_limit_results` comment state the same SDK facts (no SDK page-size parameter; `continuation_token` exists but is not used; "N of M" counts only the first batch) without claiming the SDK has no pagination.

## Global Constraints

- No `Field(ge=-1)` alone (it lets 0 through): use the shared `check_list_limit` via `field_validator` (an `Annotated[int, AfterValidator(check_list_limit)]` alias is an acceptable equivalent) and call the same function inside `_limit_results` on the effective limit, because direct `execute()` and `script_callable` calls bypass `args_schema`.
- Edit `AzureDevOpsTestPlanConfig` only for the `limit` default and description; do not add other fields or change its type.
- Uncommitted work-tree files must survive Task 0: modified `Dockerfile`, `Makefile`, `config/llms/llm-azure-config.yaml`, untracked `scripts/dial-usage.sh`, and the new task dir `docs/superpowers/tasks/2026-10-23-epmcdme-12071-ado-test-plan-limit-tool-arg/`. Never commit them.

## Review Focus

- `limit=0` (the value `Field(ge=-1)` would wrongly accept) and `-2`: rejected in schema and in direct `execute()` / `_limit_results` calls, never an empty list. Pinned in Task 1.
- `limit=-1` with a small `config.limit` and more than 100 items: all retrieved items, plain list, no notice. Pinned in Task 1.
- `limit` equal to or above the item count: plain list, no notice. Pinned in Task 1.
- Explicit `limit` larger than `config.limit` (config 2, limit 5): 5 items, not 2. Pinned in Task 1.
- Single-item lookups (`plan_id` / `suite_id` given) ignore `limit` and still return a dict. Pinned in Task 1.
- Docs overstating `limit` (implying it reduces fetching, or that `-1` reaches everything on the server). Pinned in Task 2 description test.

## Negative-constraint pass

negative-constraints: (a) notice must not reference the integration config setting -> Task 1 notice text + test asserting no `"integration"`/`"setting"`; (b) 0 and below -1 rejected, no hard upper max -> Task 1 validator and tests; (c) pagination out of scope -> no task touches `continuation_token`, docs say so; (d) user: do not keep previous-run artifacts -> Task 0; (e) no measured-payload claim for 100 -> config description and Global Constraints; (f) no fixed default stated in tool descriptions -> Task 2; (g) no module-level default constant (rejected) -> Task 1 uses the config only; (h) docs must not claim the SDK "has no pagination" -> Task 1 and 2 wording rule; (i) no new guide created -> Assumption 8.

---

### Task 0: Drop the rejected local commits and old artifacts

**Files:** none edited by hand; repo state only (branch `epmcdme-12071`, local and unpushed per user).

**Method chosen:** `git reset --keep main`. It moves the branch pointer to `main` and rewrites tracked files changed by the dropped commits, but keeps uncommitted local modifications and leaves untracked files alone; if any dirty file overlaps a file touched by the dropped commits, it aborts without changing anything. `--hard` is not used. No stash is needed.

- [ ] **Step 1: Inspect.** Run `git status --short` and `git log --oneline main..HEAD`. Expected commits, newest first: `9f418f209` (planning artifacts), `b261e0f2b`, `eea8cd4e1`, `9021f12e3`, `086e51b53` (config-based limit). If any other commit appears, STOP and report it to the caller instead of continuing. Also run `git diff --stat main..HEAD` and confirm it lists only `test_plan/models.py`, `tools.py`, `tools_vars.py`, the two test files under `tests/codemie_tools/azure_devops/test_plan/`, and `docs/superpowers/tasks/2026-10-23-epmcdme-12071/*`.
- [ ] **Step 2: Reset.** Run `git reset --keep main`. If it aborts, STOP and report which file overlaps.
- [ ] **Step 3: Verify.**
  - `git log --oneline main..HEAD` prints nothing.
  - `git diff main --stat` lists only `Dockerfile`, `Makefile`, `config/llms/llm-azure-config.yaml`.
  - `git status --short` additionally shows untracked `scripts/dial-usage.sh` and the new `...-ado-test-plan-limit-tool-arg/` dir, and nothing else.
  - `test ! -e docs/superpowers/tasks/2026-10-23-epmcdme-12071` succeeds (old dir gone).
  - `git diff main -- src/codemie_tools/azure_devops tests/codemie_tools/azure_devops` prints nothing (clean starting point at main's state; the config changes only in Task 1).
  - Recovery if ever needed: `git reflog` still holds the old tip (`9f418f209`).

**Test-first: no — repo-state operation.**

### Task 1: `limit` argument on the three list tools; config default 100

**Files:**
- Modify: `src/codemie_tools/azure_devops/test_plan/models.py` (`AzureDevOpsTestPlanConfig.limit` default and description only; `GetTestPlanInput`, `GetTestSuiteInput`, `GetTestCasesInput`; validator and description constant next to `PROJECT_DESCRIPTION`)
- Modify: `src/codemie_tools/azure_devops/test_plan/tools.py` (new helper on `BaseAzureDevOpsTestPlanTool`; `execute()` of `GetTestPlanTool`, `GetTestSuiteTool`, `GetTestCasesTool`, which on main slice the SDK result with `[: self.config.limit]`)
- Test: `tests/codemie_tools/azure_devops/test_plan/test_tools.py` (append new block; leave existing tests and their `limit=5` config arguments untouched), `tests/codemie_tools/azure_devops/test_plan/test_config_mapping.py` (`test_limit_default_value` 5 -> 100; leave the `"limit": 5` DB-format case and `test_limit_custom_value` as they are)

**Interfaces:**
- Produces in `models.py`: `check_list_limit(value: Optional[int]) -> Optional[int]` (returns `value` if `None`, `-1`, or `>= 1`; otherwise raises `ValueError`); `LIMIT_DESCRIPTION`; on each of the three input models `limit: Optional[int] = Field(default=None, description=LIMIT_DESCRIPTION)` validated by `check_list_limit`. `LIMIT_DESCRIPTION` states: maximum number of items to return; -1 for all; omit to use the integration default; if more were retrieved the result includes a notice; `limit` trims the result after the Azure DevOps SDK has fetched it (the SDK list calls have no page-size parameter, so Azure DevOps is not asked for fewer items) and these tools do not use `continuation_token`, so items beyond the first batch the server returns cannot be reached. Config: `limit: Optional[int] = Field(default=100, description=...)` where the description says: default number of items returned by test plan / suite / test case listings when the model does not pass `limit`; the model can override per call and `-1` returns everything; 100 is an unmeasured judgment call (the previous default of 5 silently hid items); the limit is applied after the SDK has fetched the first batch (no SDK page-size parameter; the tools do not use `continuation_token`), so it does not reduce what is fetched from Azure DevOps and items beyond the first batch are unreachable.
- Produces in `tools.py`: `execute(..., limit: Optional[int] = None)` on the three tools; `BaseAzureDevOpsTestPlanTool._limit_results(self, items: list, kind: str, limit: Optional[int]) -> list[dict] | dict`, with a comment stating: the SDK list calls take no page-size/top/limit parameter and the tools never pass `continuation_token`, so the whole first server batch is fetched and `limit` is applied here, only to what is returned to the model; "N of M" counts only that first batch, not a server total.

- [ ] **Step 1: Write failing tests** (new block; define `_make_items(n, spec)` building `MagicMock(spec=spec)` items whose `as_dict()` returns `{"id": i}`, and `LIST_CASES` of `(tool_cls, client_method, kind, kwargs)` for `GetTestPlanTool`/`get_test_plans`/"test plans"/`{}`, `GetTestSuiteTool`/`get_test_suites_for_plan`/"test suites"/`{"plan_id": 1}`, `GetTestCasesTool`/`get_test_case_list`/"test cases"/`{"plan_id": 1, "suite_id": 1}`; a `_run(tool_cls, method, kwargs, count, config_limit=None, **extra)` helper builds the config as the existing tests do, adding `limit=config_limit` only when not `None` (so the config default applies otherwise), mocks the client and calls `tool.execute(**kwargs, **extra)`).
  - `test_config_mapping.py`: `test_limit_default_value` asserts `config.limit == 100`.
  - Schema, parametrized over the three input models (required ids supplied): omitted -> `None`; `limit=-1`, `1`, `1000` accepted; `limit=0` and `limit=-2` raise `pydantic.ValidationError`.
  - `TestListLimit`, parametrized over `LIST_CASES`:
    - `test_omitted_limit_uses_config_limit`: `config_limit=2`, 8 items -> `len(result["items"]) == 2` and notice present.
    - `test_default_config_limit_is_100`: no config limit, 150 items -> 100 items and `"100 of 150 " + kind in notice`.
    - `test_explicit_limit_overrides_config`: `config_limit=2`, `limit=5`, 8 items -> 5 items.
    - `test_minus_one_returns_all_without_notice`: `config_limit=2`, `limit=-1`, 150 items -> plain list of 150.
    - `test_limit_equal_or_above_count_is_plain_list`: 3 items with `limit=3` and `limit=10` -> `[{"id":0},{"id":1},{"id":2}]`, no notice.
    - `test_limit_below_count_returns_notice`: 5 items, `limit=3` -> 3 items, `"3 of 5 " + kind in notice`, `"limit=-1" in notice`.
    - `test_notice_does_not_mention_integration`: truncated notice contains neither `"integration"` nor `"setting"`.
    - `test_invalid_limit_direct_call_rejected`: `limit=0` and `limit=-2` via `execute()` -> `pytest.raises(ToolException)` (import from `tools.py`); `tool._limit_results([], "x", 0)` raises `ValueError`; a config with `limit=0` and omitted `limit` also raises `ToolException`.
  - Add to `TestGetTestPlanTool`: `execute(plan_id=1, limit=1)` still returns the `as_dict()` dict.
- [ ] **Step 2: Run** `poetry run pytest tests/codemie_tools/azure_devops/test_plan -q`. Expected: FAIL (unexpected `limit` kwarg, config default still 5).
- [ ] **Step 3: Implement.** Make the `models.py` changes above. In `tools.py`, add `limit` to the three `execute()` signatures; list branches call `self._limit_results(sdk_result, "<kind>", limit)` instead of the main-state slicing; single-item branches ignore `limit`. `_limit_results` picks `effective = limit if limit is not None else self.config.limit`, calls `check_list_limit(effective)` (a `ValueError` surfaces as `ToolException` because the call sits inside each tool's `try`), converts items via `as_dict()`, returns the plain list when `effective is None` (main's no-cap behaviour), `effective == -1`, or `len <= effective`, otherwise `{"items": result[:effective], "notice": ...}`. The notice contains `f"Showing {effective} of {n} {kind}"`, says `n` is the number retrieved from Azure DevOps (first batch only, not a server total), and tells the model to pass `limit=-1` (or a larger limit) to see the rest. No mention of integration or settings.
- [ ] **Step 4: Re-run the Step 2 command.** Expected: PASS. Then confirm `git diff main -- src/codemie_tools/azure_devops/test_plan/models.py` shows changes inside `AzureDevOpsTestPlanConfig` only on the `limit` default and description, and `rg "config\.limit" src/codemie_tools/azure_devops/test_plan/tools.py` finds only the fallback in `_limit_results`.
- [ ] **Step 5: Commit** (e.g. `EPMCDME-12071: Add limit tool argument to ADO test plan list tools`).

**Test-first: yes — `test_default_config_limit_is_100` (config default is 5 on main) and `GetTestPlanInput(limit=0)` raising `ValidationError` fail on main.**

### Task 2: Document `limit` in tool descriptions

**Files:**
- Modify: `src/codemie_tools/azure_devops/test_plan/tools_vars.py` (`GET_TEST_PLAN_TOOL`, `GET_TEST_SUITE_TOOL`, `GET_TEST_CASES_TOOL` descriptions)
- Test: `tests/codemie_tools/azure_devops/test_plan/test_tools.py`

**Interfaces:**
- Consumes: Task 1 `limit` semantics and notice.

- [ ] **Step 1: Write failing test** `test_list_tool_descriptions_document_limit`, parametrized over the three tool classes: `tool.description` contains `"limit (int, optional)"`, `"-1"`, `"integration default"`, `"notice"` and `"paginat"`, and does not contain `"default 100"` (the config can change it). Run it; expected FAIL.
- [ ] **Step 2: Implement.** Add to each "Arguments:" list `- limit (int, optional): Maximum number of items to return; any integer >= 1, or -1 for all. If omitted, the integration default applies`, plus these sentences per description (short, model-facing): "If more items were retrieved than the limit, the result is an object with 'items' and a 'notice' stating how many are shown; pass limit=-1 to get everything retrieved." and "limit trims the result after it is fetched; Azure DevOps is not asked for fewer items. This tool does not paginate, so items beyond the first batch the server returns cannot be reached, even with limit=-1." Do not say the SDK has no pagination. Run `poetry run pytest tests/codemie_tools/azure_devops -q`; expected PASS. Commit (e.g. `EPMCDME-12071: Document limit argument in ADO test plan tool descriptions`).

**Test-first: yes — description test fails while the Arguments lists lack `limit` and the no-pagination statement.**
