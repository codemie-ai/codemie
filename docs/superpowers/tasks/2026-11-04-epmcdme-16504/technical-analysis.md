# Technical Research

**Task**: workflow contract validation save dry-run
**Generated**: 2026-11-04
**Research path**: codegraph

---

## 1. Original Context

Jira sub-task EPMCDME-16504 "Save-time Contract API Surface: Issues, Coverage, Parity and the New Endpoint (codemie)". Full verbatim ticket text (Context, Story, Background, Out of Scope, Decisions D5-D8, Constraints, all Acceptance Criteria) lives in `docs/superpowers/tasks/2026-11-04-epmcdme-16504/ticket-source.md` and is the task description. Caller note: work from the current branch `epmcdme-16503` (it already holds the sibling linker commits); ignore the ticket's "branch from origin/main" constraint.

---

## 2. Codebase Findings

### Existing Implementations

**P1 - Switch the save-time linker on/off by a platform flag**
- `WorkflowExecutor.validate_workflow_with_report(..., link_contracts=False)` - `src/codemie/workflows/workflow.py:251-348`. The only switch: a per-call bool, default off. Off = no resolver, no linker, returns `ContractLinkReport()` (`analysed=False`). `validate_workflow` (`:230`) and `validate_workflow_and_draw` (`:351`) forward it. **No production caller passes `link_contracts=True`** (grep `link_contracts` in `src/`: only workflow.py). No config value exists for it.
- Platform flag mechanisms that exist: static `Config` bools in `src/codemie/configs/config.py` (e.g. `WORKFLOW_GENERATION_ENABLED: bool = False` `:962`, consumed at router assembly `routers/workflow.py:879`); `ENV: str = ENV_LOCAL` `:52` with `is_local()` `:1229`; `DynamicConfigService.get_bool_value_safe` runtime toggle (`.ai-run/guides/development/configuration-patterns.md:29`, table `dynamic_config`, `rest_api/models/dynamic_config.py:35`). No existing flag defaults differently in dev vs prod.
- Input-contract structural check (AC-39 "still runs in every mode"): `validate_state_input_contracts` from `workflows/contracts/input_contract_stage.py` is imported by the YAML-schema stage `workflows/validation/schema.py:56`, i.e. it runs before the linker and independently of `link_contracts`.

**P2 - Shape contract findings as issues (error type, path, config_path, line, outcome, details)**
- `build_contract_error(report)` - `workflows/contracts/linker.py:69-85`: only `blocking_records()`; `{"error_type": "contract_validation", "message": "Workflow contracts are not satisfied", "errors": [...]}` plus a `<br>` string. `_error_detail` `:96-102`: keys `id`, `message`, `path` (= `record.path`), `state_id` (omitted for graph-level), `details` (= `record.label`, e.g. certain-failure / not-guaranteed label or cause tag). No `config_path`, `config_line`, outcome or provenance emitted.
- `WorkflowErrorType.CONTRACT_VALIDATION = "contract_validation"` already exists - `workflows/constants.py:28`.
- `WorkflowValidationErrorDetail` - `workflows/validation/models.py:53-65`: `id, message, path ("leaf field name"), details, state_id, config_line (1-indexed), meta`. No `config_path`/outcome. Used by schema/resources/transformers builders; not used by the linker.
- `ContractRecord` - `contracts/models.py:90-100`: `kind, outcome (OK|ERROR|WARNING|UNVERIFIED), reported_as (blocks|warning|info), consumer_state_id, path, message, label, provenance(producers, sources_checked, overwritten_by, removed_by, mechanism)`. No bare field name on the record (the name is on `ContractRequirement.name`, `:71`).
- Record `path` values written by the linker (consumer-relative, dotted): `input_schema.<name>` (`derive.py:263`), `task` for placeholders (`:283`), `config.input_key` (`:383`), `config.mappings[<i>].source_path` (`:395`), `tool_args.<name>` (`:448,456`), `next.condition.expression` / `next.switch.cases[<i>].condition` / `next.iter_key` (`:498-510`), `output_schema[.<name>]` (`simulate.py:971`), coverage `input_schema` (`policy.py:108`), graph-level `""` (`policy.py:312`). `safe_path` sanitises (`policy.py:245`).
- Mode policy `_reported_as` - `policy.py:400-411`: OK -> info; strict -> blocks; ERROR -> blocks for `_REJECTING_KINDS` else warning; WARNING -> warning; UNVERIFIED (warn mode) -> **info**.
- YAML line lookup - `workflows/validation/line_lookup.py:156-279` `YamlLineFinder.find_line_for_state_field(state_id, field_path)`; keys built as `states[<i>].a.b[<j>]` (`_convert_path_to_string` `:109`), falls back to any line of the state. `NullYamlLineFinder` `:282`. Used by resources stage (`validation/resources.py:138-309`).

**P3 - Return every contract error, and the warnings, in one rejected response**
- Linker already collects all blocking records into one `ValueError` (`workflow.py:343-347`) after YAML/schema, parse, missing-states, resources and graph-compile stages, each of which raises first (`:274-337`).
- Rejected-save envelope: router catches and raises `ExtendedHTTPException(400, "Workflow Configuration error", details=e.args[0] if json-format dict else str)` - create `routers/workflow.py:560-572`, update pre-persist `:201-209`. `ExtendedHTTPException` fields: `code, message, details: str|dict, help` only (`core/exceptions.py:82`). With `error_format=string` details is a `<br>` string.
- The report (records incl. warnings/info and coverage) is discarded by `validate_workflow` (`workflow.py:248` returns `[0]`) and is never raised with the error.

**P4 - Coverage summary**
- `ContractLinkReport` - `contracts/models.py:103-125`: `analysed, cap_reached, validated_count, reachable_total, uncovered_state_ids, unreachable_state_ids, effective_modes`; `to_dict()` json dump. Filled by `_build_report` `policy.py:334-354`; `failure_report` `:299-317` leaves counts at 0, `analysed=False`. No response carries it today.

**P5 - `warnings[]` on save/dry-run, routed by `kind`**
- `_consumer_slot_warnings_sync` / `_consumer_slot_warnings` - `routers/workflow.py:212-240`: extends with `collect_consumer_slot_integration_warnings` (`validation/resources.py:520`, entries `{"kind":"consumer_slot","assistant_ref","tool_name","credential_type"}` `:541-548`) and `collect_tool_access_owner_integration_warnings` (`validation/tool_access.py:149`, entries `{"kind":"tool_access_owner_integration","step_id","tool_name","integration_alias","message"}` `:174-182`). Failures swallowed (advisory).
- Response models: `WorkflowSaveResponse(BaseResponseWithData)` `warnings: list[dict[str, object]]` `routers/workflow.py:487-490`; `ValidateWorkflowResponse(BaseResponse)` same `:493-496`; `rest_api/models/workflow_marketplace.py:43` also has `warnings: list[dict[str, object]]`.

**P6 - Same pipeline / parity across create, update, dry-run**
- Create `POST /v1/workflows` `routers/workflow.py:581-614` -> `_persist_and_finalize_new_workflow` `:499-572`: parse (failure deferred), normalize model ids + rewrite YAML only if parse succeeded (`:595-602`), MCP `validate_on_save` on raw and parsed copies, strip MCP, categories, `WorkflowExecutor.validate_workflow` directly (`:527`), save, warnings from saved config with `treat_as_global=is_global`.
- Update `PUT /v1/workflows/{id}` `:623-712`: parse failure -> immediate 400 with raw `str(e)` (`:652-658`), normalize + rewrite, `_run_pre_persist_update_validation` `:164-209` (guardrail dry-run sync, autonomous 403, `workflow_service.validate_for_update` `service/workflow_service.py:249-263`: MCP validate+strip, marketplace validate if stored workflow is global, `validate_workflow`). Warnings from `updated_config`, `treat_as_global=was_global or proposed`.
- Dry-run `POST /v1/workflows/{id}/validate` `:715-758`: requires saved workflow, `project_access_check`, `Action.WRITE`; parse; **no model-id normalization or YAML rewrite**; same `_run_pre_persist_update_validation`; returns `ValidateWorkflowResponse(message, warnings)`.
- Other `validate_workflow` entry points: `POST /workflows/diagram` `:789` (unsaved config, no project check, string errors, via `validate_workflow_and_draw`); `update_workflow_schema` background task `:861`; rollback `POST /workflows/{id}/versions/rollback` `:983` -> `WorkflowVersionService._validate_restored_workflow` (`service/workflow/workflow_version_service.py:108`, `validate_for_update`); `service/workflow_generator_service.py:115`; generator node `workflows/workflow_generator/nodes/validation.py:364` (json format).

**P7 - Validate a never-saved workflow with read access to the project**
- None found as a dedicated endpoint. Nearest: `POST /workflows/diagram` (unsaved config, full validation, but no project check and no warnings). `project_access_check(user, project)` `rest_api/security/authentication.py:378` checks `user.has_access_to_application` (membership, no read/write split).

### Decisions Already in the Code
- Linker is opt-in per call (`link_contracts=False` default), "report created per call and never kept" - `workflow.py:257-259`. Alternative (config-read inside executor) not taken; reason none recorded.
- Contract errors use the existing structured error dict shape (`error_type/message/errors[]`) - `linker.py:78-82`, same as resource/schema stages.
- `path` on contract errors is a dotted consumer-relative path, not a leaf name - `linker.py:97` vs `models.py:58` description "leaf field name".
- `details` on contract errors carries the label, not provenance - `linker.py:100-101`.
- UNVERIFIED in warn mode is classed `info`, not `warning` - `policy.py:411`. Records are stored by `reported_as`; the task needs them by outcome indicator (ERROR/WARNING/UNVERIFIED) - outcome is kept on the record (`models.py:94`), so both are available.
- Warnings are untyped `list[dict[str, object]]` keyed by `kind` - `routers/workflow.py:490,496`.
- Advisory warnings must never fail a save - `routers/workflow.py:217-219` (convention).
- Flags: convention is a static `Config` bool gated at assembly/service boundary (guide `configuration-patterns.md:14-23`).

### Task Statements Checked Against the Code
- "pipeline stops at the first failing validation layer" - holds (`workflow.py:274-337`).
- "warnings list is untyped, no severity" - holds (`routers/workflow.py:490,496`).
- "No save or dry-run response carries a coverage summary" - holds.
- "only existing dry-run needs a saved workflow and write permission" - holds (`routers/workflow.py:729-738`).
- "Create and update normalise model ids and rewrite config; dry-run validates as sent" - holds (`:601-602`, `:650-651`, absent in `:740-751`). Also: create normalizes only when parse succeeds, update 400s on parse failure - further divergence.
- "no existing mechanism has the two-flag dev-on/prod-off shape; platform setting is closer" - holds (no env-dependent default found); keeps static `Config`/`DynamicConfig` in place as the candidate mechanisms.
- D5 "one new contract-validation error type" - already exists: `contract_validation` (`constants.py:28`) added by the linker sibling; keeps it in place (consumer `linker.py:79`).
- AC "existing `path` field with its existing bare-field-name meaning" - contradicted by current linker output: `path` is dotted (`linker.py:97`, `derive.py:263,456,502`).
- AC `details` names producer/removing node/mechanism/sources - contradicted today: `details` = label (`linker.py:100`); provenance exists on the record (`models.py:78-87`).
- AC `config_path` table (`input_schema.properties.<field>`, `config.mappings.<index>.<field>`, ...) - differs from linker paths (`input_schema.<name>`, `config.mappings[<i>].source_path`); table has no entry for placeholder path `task` or `config.input_key` (`derive.py:283,383`).
- AC "existing `consumer_slot` and `script_step_owner_integration` entries" - contradicted: the kind string is `tool_access_owner_integration` (`validation/tool_access.py:175`).
- AC "UNVERIFIED notice" in `warnings[]` - UNVERIFIED records are `reported_as=info` in warn mode (`policy.py:411`); OK records are also info.
- "both dry-run shapes" - unverified: only one dry-run endpoint exists; the second may mean the new pre-save endpoint or the two `error_format`s.
- "No migration expected" - holds: report is per-call, nothing persisted.
- Constraint "versioning surface landed in the same router" - holds (`routers/workflow.py:964-999`); rollback validates via `validate_for_update`.

### Consumers of Changed Contracts
- `WorkflowExecutor.validate_workflow` / `validate_workflow_with_report` (13 callers): router create `:527`, `validate_for_update` (update, dry-run, rollback), diagram `:792`, `update_workflow_schema` `:862`, `workflow_generator_service.py:115`, generator node `validation.py:364`; tests `tests/codemie/workflows/validation/test_contract_linker.py` (incl. `test_flag_defaults_to_off`, `test_validate_workflow_returns_the_graph_element_of_the_report_call`), `test_contract_keys.py`, `test_workflow_mcp_servers.py`, `test_placeholder_no_rejection.py`. A flag read inside the executor would reach all of them, including the generator's json-error loop.
- `build_contract_error` / `_error_detail` shape: `workflow.py:344`, `validation/__init__.py` export; tests `test_contract_linker.py` (`test_build_contract_error_*`).
- `warnings[]` lists: `WorkflowSaveResponse`, `ValidateWorkflowResponse`; tests `tests/codemie/rest_api/routers/test_workflow.py:616,650` (`test_create_workflow_reports_consumer_slot_warnings`, script-step warning), `tests/codemie/workflows/test_workflow_missing_integration_user_slots.py` (`test_consumer_slot_warning_carries_kind_and_keeps_its_shape`). External consumers (UI, SDK, CLI) - outside repo.
- `WorkflowValidationErrorDetail`: schema/resources/transformers builders (no tests flagged).
- `ContractLinkReport`: `linker.py`, `policy.py`, `workflow.py`; tests `test_contract_models_report.py`, `test_contract_corpus.py`, `test_tool_schema_resolver.py`.

### Architecture and Layers Affected
- API layer: `src/codemie/rest_api/routers/workflow.py` (create/update/validate handlers, response models, envelope).
- Service layer: `service/workflow_service.py` (`validate_for_update`), `service/workflow/workflow_version_service.py`.
- Workflow validation/engine layer: `workflows/workflow.py` (stage sequence), `workflows/contracts/{linker,models,policy}.py`, `workflows/validation/{models,line_lookup}.py`, `workflows/constants.py`.
- Config: `configs/config.py`.

### Integration Points
- Linker -> `SaveTimeToolSchemaResolver` recorded during resource stage (`workflow.py:318-321,342`).
- Router -> `MCPAccessControlService`, `GuardrailService` (dry-run sync), `_marketplace_service.validate`, `llm_service.get_allowed_chat_models` (normalization, DB session via `application_repository`).
- Clients: visual/YAML editor, SDK, CLI (other repos).

### Patterns and Conventions
- Errors raised as `ValueError(dict|str)` via `_raise_validation_error` (`workflow.py:212`), mapped to `ExtendedHTTPException` in routers.
- Async handlers offload blocking work with `asyncio.to_thread` / `run_in_thread_pool`.
- Response models extend `BaseResponse`/`BaseResponseWithData`, `response_model_by_alias=True`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/configuration-patterns.md` - central `Config`, flags gated at assembly/service boundaries, `DynamicConfigService` runtime toggle example.
- `.ai-run/guides/api/rest-api-patterns.md`, `endpoint-conventions.md`, `workflows/langgraph-workflows.md` - present, not specific to contracts.

### Architectural Decisions
- Linker docstring (`linker.py:15-26`): per-call report, never raises, crash -> one graph-level record blocking only in strict.
- Models docstring (`models.py:15-20`): `outcome` vs `reported_as` kept apart on purpose.

### Derived Conventions
- Warning entries are dicts discriminated by `kind`; advisory collectors never fail a save.

---

## 4. Testing Landscape

### Existing Coverage
- Linker/report/policy: `tests/codemie/workflows/validation/test_contract_{linker,models_report,policy,corpus,keys,settings_reader,derive,simulate,effects,output_reader}.py`, `test_input_contract_check.py`, `test_tool_schema_resolver.py`.
- Router: `tests/codemie/rest_api/routers/test_workflow.py` (create `:319-650`, update `:708-1032`, diagram `:1186-1220`, validate `:1666-1753`), `test_workflow_versions.py`.
- Warnings: `tests/codemie/workflows/test_workflow_missing_integration_user_slots.py`.

### Testing Framework and Patterns
- pytest (async router tests, `request_header` fixture, `mock_get_wf`/`mock_ability` patches of service and `Ability`).

### Coverage Gaps
- `WorkflowService.create_workflow`, `validate_for_update`, router `create_workflow`/`update_workflow` flagged "no covering tests" by codegraph's call graph (router tests exist but patch around them).
- No test asserts parity of warnings between create/update/dry-run.
- `WorkflowValidationErrorDetail`, `WorkflowScope` - no covering tests.

---

## 5. Configuration and Environment

### Environment Variables
- `ENV` (`config.py:52`, default `local`); no contract-linker variable exists.

### Configuration Files
- `src/codemie/configs/config.py` (pydantic `Config`, env-loaded via `load_env_files` `:1259`).
- Workflow-level contract keys (`validation`, `require_input_contracts`) read from YAML by `contracts/settings.py` `read_contract_settings`.

### Feature Flags and Deployment Concerns
- Static bool flags (`*_ENABLED`) and `DynamicConfig` runtime keys are the two platform mechanisms. Runtime-checks flag (Story 3) out of scope.

---

## 6. Risk Indicators

- Linker `path`/`details` output contradicts the AC (dotted path vs bare name; label vs provenance) - see Task Statements; the shaping lives in sibling-owned `linker.py`.
- `config_path` AC table does not match linker record paths and omits `task` and `config.input_key` requirement paths (`derive.py:283,383`).
- Ticket names a warning kind `script_step_owner_integration`; code emits `tool_access_owner_integration` (`tool_access.py:175`).
- UNVERIFIED records are `info` in warn mode; OK records also `info` (`policy.py:403-411`) - selection for `warnings[]` must be by outcome, not `reported_as`.
- `ExtendedHTTPException` has no slot beyond `details`; with `error_format=string` details is a string, so warnings/coverage on rejection have no structured carrier there.
- `validate_workflow` drops the report (`workflow.py:248`); `validate_for_update` returns `None` - report does not reach routers today.
- 13 callers of `validate_workflow` (diagram, background schema, rollback, generator) - a flag read inside the executor changes all of them.
- Create/update/dry-run diverge in parse-failure handling, normalization and MCP checks (`routers/workflow.py:595-602,642-658,740-751`); create's warnings computed from the saved config, update's from `updated_config`.
- "Both dry-run shapes" undefined against code (only one dry-run endpoint).
- Coverage on rejected save: `failure_report` yields zero counts and `analysed=False`.
- Speculative: the new pre-save endpoint has no saved workflow, so `treat_as_global`, marketplace validation and guardrail dry-run (all keyed on a stored workflow) have no input there.

---

## 7. Summary for Complexity Assessment

The task touches the API layer (`rest_api/routers/workflow.py`: create, update, saved-workflow dry-run, response models, error envelope, a new endpoint), the service layer (`workflow_service.validate_for_update`, rollback's reuse of it), the workflow validation layer (`workflows/workflow.py` stage sequence, `contracts/linker.py` error shaping, `validation/models.py` issue model, `line_lookup.py` for config lines) and config (`configs/config.py`). The linker sibling already provides the error type `contract_validation`, all-errors-at-once aggregation, per-record outcome/provenance and coverage data on `ContractLinkReport`; but no production caller enables it, the report is discarded before reaching routers, and nothing returns coverage or contract warnings.

Novelty is moderate: the issue envelope, coverage object, flag and endpoint follow existing patterns (structured error dicts, `kind`-keyed warnings, static `Config` bools, `project_access_check`). Divergences between the ticket and the code are the main hazard: `path`/`details` semantics in the linker's current error detail, a `config_path` table that does not match the linker's record paths, a misnamed warning kind, UNVERIFIED being `info`, and the string error format having no structured carrier for warnings/coverage on rejection.

Test posture is good for the linker (ten contract test modules) and fair for routers (`test_workflow.py` mocks around validation); there is no parity test across create/update/dry-run, and the three entry points currently differ in parse-failure handling, model-id normalization and MCP checks. Thirteen callers of `validate_workflow` (diagram, background schema draw, rollback, workflow generator) are in reach of any change to the executor's default.

---

## 8. External References

- `docs/superpowers/tasks/2026-11-04-epmcdme-16504/ticket-source.md` - resolved (in run_dir). Facts used: error type is fixed by this child and mirrored by UI (D5); coverage returned on rejected saves (D6); outcome indicator additive and optional (D7); `config_path` table: `input_schema.properties.<field>`, `tool_args.<param>`, `config.mappings.<index>.<field>`, producer `output_schema`, `require_input_contracts` node `input_schema`, condition/switch/iteration key path inside `next`; contract warnings in `warnings[]` carry `kind` = contract-validation error type; coverage = reachable states checked, total reachable, uncovered ids; new endpoint takes configuration + project, needs only project read access; no migration.
- Parent attachments (story.md, technical-analysis.md, complexity-assessment.json) are named in the ticket but not present locally - unresolved.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Defined `src/codemie/core/exceptions.py:59-86`. `__init__(self, code: int, message: str, details: str | dict = None, help: str = None)`; stores `code`, `message`, `details`, `help` as plain attributes. Does not call `super().__init__`, so `e.args` is empty. The docstring describes `details` as `str, optional`; the signature admits `dict`.
- Single handler: `extended_http_exception_handler` `src/codemie/rest_api/main.py:1651-1679`. Response body `{"error": {"message", "details", "help"}}` with `details` passed through unchanged (str, dict or None). Exceptions: code 500 replaces `details` with `_internal_error_details(request)` (main.py:1580); code >=500 logs `exc.details`; code <500 logs it in a warning line. `str` and `dict` are not distinguished by the handler.
- Create: `_persist_and_finalize_new_workflow` (workflow.py:499-572). `WorkflowExecutor.validate_workflow(...)` is called without `link_contracts` (line 527). Its `ValueError` is caught by the generic `except Exception` (560). `details = e.args[0]` when `error_format == WorkflowErrorFormat.JSON` and `e.args[0]` is a dict, otherwise `str(e).strip()` (or `e.message` for an ExtendedHTTPException). Raised as `ExtendedHTTPException(code=400, message=WORKFLOW_CONFIGURATION_ERROR, details=details, help="")`. So in JSON mode the validation dict (`error_type`, `message`, `errors[]`) becomes `error.details`; in string mode `error.details` is the `<br>`-joined string.
- Update: same wrapping at workflow.py:~694-710. Pre-persist: `_run_pre_persist_update_validation` (164-209) wraps with the same rule (203-208); `validate_for_update` (`src/codemie/service/workflow_service.py:254-263`) calls `WorkflowExecutor.validate_workflow(... error_format=...)`, also without `link_contracts`.
- `link_contracts` is a parameter of `validate_workflow` / `validate_workflow_with_report` / `validate_workflow_and_draw` (`src/codemie/workflows/workflow.py:229-355`, default False); no router passes it today (grep of the router shows no `link_contracts`).
- A contract rejection is raised by `_raise_validation_error` (`workflow.py:212-227`) as `ValueError(dict)` in JSON mode, `ValueError(str)` otherwise. The dict comes from `build_contract_error` (`src/codemie/workflows/contracts/linker.py:69-85`): `{"error_type": "contract_validation", "message": "Workflow contracts are not satisfied", "errors": [...]}`. `WorkflowErrorType.CONTRACT_VALIDATION = "contract_validation"` exists at `src/codemie/workflows/constants.py:28`. It carries blocking records only (`report.blocking_records()`); warnings and UNVERIFIED are not in this dict.
- Other `details` shapes on this router: dict `{"error_type": "materialization_failed"}` (workflow.py:439-449), str elsewhere, `None` in some tests (test_workflow.py:2372, 2544).
- Consumers of the response `error.details`: tests in `tests/codemie/rest_api/routers/test_workflow.py` (lines 296, 1215, 1232, 1252, 1406, 1447 str; 2246, 2318, 2337 dict; 2372 None). Other in-repo readers of `exc.details`: `src/codemie/agents/tools/skill/skill_tool.py` (`error.code`, `error.message`, str(error)), the router's refine handler (`exc.details` of `WorkflowGenerationError`, workflow.py:906-953), and `main.py` handler. UI/SDK consumers are outside this repository (unverified).
- `ContractRecord` is defined `src/codemie/workflows/contracts/models.py:90-100`. Fields: `kind: RecordKind`, `outcome: Outcome`, `reported_as: ReportedAs | None`, `consumer_state_id: str`, `path: str`, `message: str = ""`, `label: str | None`, `provenance: Provenance`. **There is no attribute named `field`.** Not on `ContractRecord`, `ContractLinkReport` (models.py:103-125), `Finding` (`simulate.py:181-191`: `record`, `reason`, `requirement`, `detail`), nor the `ContractRequirement` model (models.py:59-75: `state_id`, `path`, `name`, `kind`, `origin`, `types`, `required`). Grep of `src/codemie/workflows/contracts` for `.field` / `field=` matched only `dataclasses.field(...)` in `effects.py:260,264`.
- The field name (the required field) lives on `ContractRequirement.name` and is not copied onto the record. `ContractRecord.path` carries the requirement path as a dotted string: declared input `input_schema.<name>` (`derive.py:263`), tool binding `tool_args.<param>` (`simulate.py:116,839`), graph-level `""`, producer contract form `output_schema` (`simulate.py:118`), coverage `input_schema` (`policy.py:108`). `Finding.requirement` keeps the `ContractRequirement` (so `.name`) inside the simulation but `_finish` (`policy.py:371-397`) returns only the `ContractRecord`, so the report does not carry the requirement.
- `ContractRecord` construction sites: `simulate.py:1070` (`_add`), `policy.py:308` (failure report), `policy.py:390` (fallback), `policy.py:491` (contract form), `policy.py:541` (coverage).
- What the record becomes today on the error path: `_error_detail(record)` (`linker.py:96-102`) builds `{"id": uuid4, "message": record.message, "path": record.path}` plus `state_id` when `consumer_state_id` is non-empty plus `details = record.label` when label set. `record.path` goes into the issue's `path` key as the full dotted path (e.g. `input_schema.x`), whereas `WorkflowValidationErrorDetail.path` is documented "Field path (leaf field name)" (`src/codemie/workflows/validation/models.py:53-62`; that model also has `details`, `state_id`, `config_line`, `meta`; no `config_path`, no outcome indicator). `outcome` and `reported_as` are not emitted in `_error_detail`.
- `label` values: `LABEL_RENDERS_EMPTY` for renders-empty origins with ERROR outcome (`simulate.py:853-855`), the failure cause for an analysis failure (`policy.py:314`), `_label(finding)` (`policy.py:414`) otherwise. Consumers of `label`: `linker.py:100-106`, `policy.py:381,416`.
- Error envelope for validation failures is the dict in `ExtendedHTTPException.details` under `error_format=json`; string mode flattens it to `<br>` text (`workflow.py:212-227`, router wrapping at 203-208, 561-566). Alternative none found.
- Contract finding `path` is stored as the full dotted requirement path, not the leaf name (`linker.py:97`), while the sibling issue model documents leaf names. Reason: none found.
- `label` is mapped to the issue `details` key (`linker.py:100-101`); no separate producer/removing-node text is built there (that sits in `record.provenance`, `models.py:78-87`, not emitted by `_error_detail`).
- `ExtendedHTTPException.details`: see above. Handler `main.py:1651`, router wrapping sites (workflow.py 187, 207, 567, 707, 798), and the tests listed above. Blast radius of the class: 1055 callers (codegraph).
- `_normalize_workflow_model_ids(workflow_config, user)` - `rest_api/routers/workflow.py:102`. Router-private, sync. Project = `None` if `workflow_config.is_global` else `_get_project_for_workflow(workflow_config.project)` (`:91`, opens `get_session()`, `application_repository.get_by_name`). Builds `base_name -> deployment_name` from `llm_service.get_allowed_chat_models(user, include_all=True, project=project)` (`llm_service.py:893`), mutates `assistant.model` and `custom_node.model` in place on the parsed objects when the value is a known base_name. Touches no other field; no return value.
- Callers: `create_workflow` `:601` (only if `parse_execution_config()` succeeded; on parse failure it logs a warning and skips both helpers), `update_workflow` `:650` (inside a try whose any exception becomes 400 `WORKFLOW_CONFIGURATION_ERROR` with `str(e)`), test `tests/codemie/rest_api/routers/test_workflow.py:345`. Not called by `validate_workflow` (`:721`, saved dry-run) or by `create_workflow_diagram`.
- `_patch_model_ids_in_yaml(config, section, items)` - `workflow.py:129`: helper of the rewrite; copies `item.model` into the parsed YAML dict entry with same `id` only when the entry already has a `model` key and value differs.
- `_rewrite_yaml_config_from_objects(workflow_config)` - `workflow.py:141`. `yaml.safe_load(yaml_config or "")`; non-dict -> returns, no change. Patches `assistants` and `custom_nodes` sections only. If any changed: sets `_source_yaml_config = yaml_config` (only if still None) and `yaml_config = yaml.safe_dump(original, sort_keys=False)`. When no model id changed, `yaml_config` is untouched (stored byte for byte).
- Callers: `create_workflow` `:602`, `update_workflow` `:651`. Tests: `test_workflow.py:346, 439, 447, 460, 471` (byte-for-byte preservation, only-model-ids changed, tolerates None/""/"- a").
- `WorkflowConfig._source_yaml_config` - `core/workflow_models/workflow_config.py:326`, `PrivateAttr(default=None)`, "never persisted". Only reader: `workflows/validation/resources.py:790` (`getattr(..., "_source_yaml_config", None) or yaml_config`) so line numbers in resource errors point at the client's text. Set in tests `test_tool_schema_resolver.py:537`, `test_config_resources_validation.py:1160`. The executor's schema stage reads `workflow_config.yaml_config` (the rewritten text): `workflow.py:275` `validate_workflow_execution_config_yaml(workflow_config.yaml_config)`; `workflow.py:296` `parse_execution_config()` re-parses it.
- `CreateWorkflowRequest` - `core/workflow_models/workflow_models.py:433-449` (plain pydantic `BaseModel`; exported from `core/workflow_models/__init__.py:17,94`). Fields: `id?`, `name: str` (required), `mode?=SEQUENTIAL`, `description: str` (required), `start_hint?`, `project: str` (required), `icon_url?`, `yaml_config?`, `shared=True`, `assistants?=[]`, `tools?=[]`, `states?=[]`, `supervisor_prompt?=""`, `meta_config?`, `guardrail_assignments?`, `categories` (max 3, each max 256). No `custom_nodes`, no `is_global` field.
- Consumers in `src`: `routers/workflow.py:47` import; `_persist_and_finalize_new_workflow(request, ...)` `:501` (`:512-513` builds two `WorkflowConfig(**request.model_dump())` copies for the MCP gate; `:524-526` reads `request.categories`/`guardrail_assignments`); `create_workflow` `:582,589`; `create_workflow_diagram` `:789`; `rest_api/models/workflow_generator.py:21,32` (`WorkflowGeneratorRequest.workflow_config`); `workflow_generator/nodes/validation.py:264,290` (builds it as generator output); `workflow_generator/state.py:34` (comment). Tests: `test_workflow.py` (113,127,2344,2437,2553), `test_workflow_generator.py`, `test_workflow_service.py`, `test_workflow_generator_service.py`, `test_workflow_generator_spend_origin.py`, `test_workflow_graph.py`.
- `WorkflowConfig` - `workflow_config.py:321`, `class WorkflowConfig(BaseModelWithSQLSupport, WorkflowConfigBase, table=True)`, `__tablename__="workflows"`. Fields come from `WorkflowConfigBase` (`:77`): `assistants`, `custom_nodes`, `tools`, `states`, `yaml_config: Optional[str]` (`:103`), `yaml_config_history`, `is_global: bool = False` (`:119`, indexed `:155`), `project` default `DEMO_PROJECT`, `name: str`, `description: str` (both required, no default), `categories`, etc.
- Reads `is_global` at `:316` (`is_shared_with`), `workflow.py:747`, `nodes/tool_node.py:204,458`, `nodes/base_node.py:151`, `nodes/agent_node.py:274,293`, `workflow_config_index_service.py:87,167,212`, `workflow_marketplace_service.py:40-41,116-120` (publish/unpublish set it).
- Direct consumers of `WorkflowConfig` in this path: executor `validate_workflow_with_report` (`workflow.py:251`), `link_workflow_contracts(config)` (`contracts/linker.py:48`, reads `config.states`, `custom_nodes`, `tools` via getattr and settings via `read_contract_settings`), `collect_consumer_slot_integration_warnings`, `collect_tool_access_owner_integration_warnings` (`routers/workflow.py:58-59`).
- `stored_workflow.is_global`: no variable named `stored_workflow` exists in `src` today; it is a spec name. Today's equivalents: `workflow.is_global` in `WorkflowService.validate_for_update` (`service/workflow_service.py:261`: `if workflow.is_global: _marketplace_service.validate(updated_config, user)` - between `validate_and_strip_workflow_mcp_servers(updated_config)` `:260` and `WorkflowExecutor.validate_workflow(...)` `:263`, no `link_contracts`), and `was_global = bool(workflow.is_global)` in `update_workflow` (`routers/workflow.py:662`, comment "the request carries no is_global; the stored state decides"), `bool(workflow.is_global) or bool(proposed.is_global)` in saved dry-run (`:756`). `updated_config.is_global` on a request-built config is the field default `False`.
- `WorkflowMarketplaceService.validate(workflow, user)` `workflow_marketplace_service.py:52`: collects assistants/skills via `_entities_collector.collect_for_workflow`; raises `ValidationException` if external (non-virtual) assistants/skills exist; collects inline credentials (returned in `WorkflowPublishValidationResponse`, not an error). Does not read `yaml_config`.
- Callers of `validate_for_update`: `routers/workflow.py:198` (`_run_pre_persist_update_validation`, used by update `:663` and saved dry-run `:753`) and `service/workflow/workflow_version_service.py:109` (rollback: `validate_for_update(workflow, workflow, user)`).
- Other `WorkflowExecutor.validate_workflow` callers (no link_contracts today): `routers/workflow.py:527` (create), `:792`, `:862` (diagram via `validate_workflow_and_draw`), `workflow_generator_service.py:115`, `workflow_generator/nodes/validation.py:364`, `workflow_service.py:263`.
- `safe_name(value)` - `workflows/contracts/policy.py:240`; thin wrapper over `_safe_name` imported from `contracts/input_contract.py:156` (`_UNSAFE_NAME_CHARS = [^A-Za-z0-9_.\-]` -> `_`, `str(name)[:40]`, `_MAX_NAME_LENGTH=40` `:30`). Docstring: names reach `<br>`-joined error markup. In `__all__` (`policy.py:617`).
- `safe_path` `policy.py:245`: `_UNSAFE_PATH_CHARS = [^A-Za-z0-9_.\-\[\]]`, `MAX_PATH_LENGTH=200` (`:106,110`).
- Callers (all in `policy.py`): `_quote` `:251`, `_finish` `:388` (log), `_clean_provenance` `:427-431` (producers, sources_checked, overwritten_by, removed_by, mechanism), `_fragments` `:573-577` (state, name, types, producer), `:586,596,597`. `safe_path` applied in `_finish` `:382,394`. Test: `tests/codemie/workflows/validation/test_contract_policy.py:43,821-825`. `linker.build_contract_error` relies on messages already sanitized by policy (`linker.py:72-74`) and joins them into `<br>` markup. `ContractRecord.consumer_state_id` is NOT passed through `safe_name` when stored (only inside rendered messages and log); `_error_detail` puts `record.consumer_state_id` raw into `state_id` (`linker.py:98-99`).
- `ContractRecord` - `contracts/models.py:90-100`: `kind`, `outcome`, `reported_as?`, `consumer_state_id`, `path`, `message=""`, `label?`, `provenance`. **No `field` attribute exists** (also none on `ContractLinkReport` `:103`). `ContractRequirement` `:59-75` is frozen and has `state_id, path, name, kind, origin, types, required`; `Finding` (`simulate.py:180-191`, frozen dataclass) carries `record`, `reason`, `requirement?`, `detail`.
- `contracts/simulate.py:1070` in `_add(...)` (the only generic factory; takes `requirement` as separate arg, builds `Finding(record=..., requirement=requirement)` at `:1079`; `_failure` `:1081` calls `_add` with `path=""`).
- `contracts/policy.py:308` `failure_report` (graph-level, `consumer_state_id=""`, `path=""`).
- `contracts/policy.py:390` `_finish` fallback after render failure (copies `kind, outcome, consumer_state_id, path, message, reported_as`; drops `label` and `provenance`).
- `contracts/policy.py:491` `_form_finding` (CONTRACT_FORM, no requirement).
- `contracts/policy.py:541` `_coverage_findings` (COVERAGE, `path=COVERAGE_PATH`, no requirement).
- `_finish` main path `policy.py:377` uses `record.model_copy(update={message, reported_as, label, path, provenance})` - not a constructor, retains any other attribute.
- Tests: `tests/codemie/workflows/validation/test_contract_models_report.py:63,100,110,117,124,141,171` (construct with `kind, outcome, consumer_state_id, path` keywords).
- Consumers of `ContractRecord`/report: `ContractLinkReport.to_dict()` = `model_dump(mode="json")` (`models.py:121`), `to_json` `:124` (any new attribute would appear in both); `blocking_records` `:118`; `linker._error_detail` `:96` (reads `id/message/path/consumer_state_id/label`), `_error_line` `:105`; `workflow.py:340-348` (returns report, builds error). No consumer found outside `workflows/contracts` and `workflow.py` in `src`.
- Response models: `WorkflowSaveResponse(BaseResponseWithData)` `routers/workflow.py:487` (`warnings: list[dict[str, object]] = []`), `ValidateWorkflowResponse(BaseResponse)` `:493` (same); `BaseResponse.message`, `BaseResponseWithData.data: Any = None` at `core/models.py:845-850`. Create/update/validate routes use `response_model_by_alias=True`, no `response_model_exclude_none` (`:575-580, 617-622, 715-720`). `exclude_none` is NOT set on any save/update/validate route.
- `model_dump(exclude_none=True)` today: `workflow_config.py:293-294` (retry policy merge), error-detail dumps in `workflows/validation/schema.py:142,613`, `validation/resources.py:190-311`, `validation/transformers.py:86`, `config_yaml_validation.py:110`, `config_resources_validation.py:90-123`, `workflow_generator/nodes/validation.py:95,268,280,281`. None on the save response path. Because `data` is `Any` holding a `WorkflowConfig`, a model-level `exclude_none` would drop every None field of the config in `data` (this is the existing meaning of the flag on the routes that use it; no test found asserting it for save).
- Normalization before validation, results written into the YAML text: `routers/workflow.py:595-602` comment: parse errors deferred to `validate_workflow` so the schema stage reports precise path-aware errors; otherwise a half-parsed config could be persisted. Alternative: raise on parse failure (what update does, `:652-658`). Reason recorded in the comment.
- YAML rewrite touches only `model` values, everything else stored as sent (`:141-150` docstring; tests `test_workflow.py:436-473`).
- `_source_yaml_config` keeps the client text for error line numbers (`workflow_config.py:324-326`, `resources.py:789`). Only the resource stage reads it; schema stage reads the rewritten text.
- Stored global state decides update-time marketplace check; the request has no `is_global` (`:662` comment; `workflow_service.py:261`).
- Message sanitization lives in policy at render time (`policy.py:240-251, 382, 427-431`); the stored `ContractRecord.path` is sanitized in `_finish`, `consumer_state_id` is not. Convention: `safe_name` for names (40 chars, no `[]`), `safe_path` for paths (200 chars, keeps `[]`).
- "module takes over `_normalize_workflow_model_ids`, `_rewrite_yaml_config_from_objects`, `_consumer_slot_warnings_sync` (`routers/workflow.py:102-162,212-232`)": holds for line ranges (`102-126`, `129-161` incl. `_patch_model_ids_in_yaml`, `212-232`). Consumers that import them by name: tests `test_workflow.py:345-346, 439-471` via `workflow_router.<name>`; `_consumer_slot_warnings` (`:235`) calls the sync one. `_patch_model_ids_in_yaml` and `_get_project_for_workflow` are also router-private dependencies of the two functions.
- "`validate_workflow_with_report` at `workflow.py:251-348`; executor re-parses at `:296`": holds. `link_contracts` default False in both `validate_workflow` and `..._with_report`.
- "`validate_for_update` becomes `validate(..., link_contracts=False)` and keeps rollback (`workflow_version_service.py:109`) unlinked": rollback calls `validate_for_update(workflow, workflow, user)` at `:109`; keeps `WorkflowService.validate_for_update` in place (consumers: routers `:198`, version service `:109`).
- "Marketplace validation when `stored_workflow.is_global`": holds as `workflow.is_global` at `workflow_service.py:261`, which runs after MCP strip and before executor validation.
- "`CreateWorkflowRequest` requires both name and description (`workflow_models.py:433-449`)": holds (`:435, :437`). "Missing values become `""` when the WorkflowConfig is built": no code does this today; `WorkflowConfigBase.name`/`description` are required `str` (`workflow_config.py:88, 92`). Unverified whether `table=True` SQLModel construction enforces them (not tested here).
- "`ContractRecord.field` known not to exist": holds (`models.py:90-100`).
- "`_finish` copies `Finding.requirement.name` (`policy.py:371-397`)": `_finish` uses `model_copy(update=...)` on `finding.record`; it does not read `finding.requirement` today (requirement is read in `_strict_candidates` `:509`). `Finding.requirement` is None for form, coverage, and failure findings.
- "`exclude_none` would also alter `data`": consistent with `data: Any` (`models.py:850`); no current save route uses it.
- `routers/workflow.py` helpers (if moved): tests `test_workflow.py:345-346,439-471` reference `workflow_router._normalize_workflow_model_ids` / `_rewrite_yaml_config_from_objects`; `create_workflow` `:601-602`, `update_workflow` `:650-651`.
- `ContractRecord` model (new attribute would flow to): `ContractLinkReport.to_dict/to_json` `models.py:121-125`; `model_copy` in `_finish`; fallback constructor `policy.py:390` (would not carry a new attribute unless added); tests `test_contract_models_report.py`.
