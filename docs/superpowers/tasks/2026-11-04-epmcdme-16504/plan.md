# EPMCDME-16504 Save-time Contract API Surface — Implementation Plan

> Historical implementation plan. The accepted 2026-11-09 corrections in `../2026-11-09-epmcdme-16504-fix-cx/plan.md` supersede obsolete details below. Follow the current repository skill scope.

**Goal:** Expose the linker's `ContractLinkReport` through create, update, saved dry-run and a new `POST /v1/workflows/validate`. All four share one pipeline, use one issue shape, return coverage, and sit behind one flag.

**Architecture:** A static `Config` flag is read by one helper. A sync service pipeline `service/workflow/save_validation.py` (`prepare` / `validate`) takes over the router-private helpers and `validate_for_update`. Contract shaping lives in the new `workflows/validation/contract_issues.py`, not in `linker.py` (AR-7: the linker stays a leaf module). Routers keep the access, autonomous, guardrail, category and persistence steps.

**Spec:** `spec.md` (same dir) is the acceptance record. Arch-review brief findings AR-7..AR-16 and the six plan_notes are resolved in the tasks named below.

## Global Constraints
- Branch `epmcdme-16504`, based on completed 16503. Commit or publish only when requested.
- Follow `.ai-run/guides/architecture/layered-architecture.md`, `api/rest-api-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`.
- Additive only: every key sent today is still sent, `path` stays a string, `error_type` and the envelope keys are unchanged. Do not change `ExtendedHTTPException` or the global handler. Do not rename warning kinds (`tool_access_owner_integration` stays). No DB migration.
- Do not use `exclude_none` on save/validate routes, because it would alter `data`.
- One cap, `CONTRACT_WARNINGS_CAP = 100`, applied inside the shaping function for every format.
- One truncation key, `warnings_truncated` (the count cut by the cap). It is sent only when the count is above 0 and is absent otherwise: in the json rejection dict, in the string rejection text, and in `WorkflowSaveResponse` / `ValidateWorkflowResponse`. No other name or wording for it exists anywhere.
- Status codes stay as today: on create, a `SQLAlchemyError` raised during validation (categories, executor) or save is a 503 `Database error during workflow creation`, never a 400.

---

### Task 1: Platform flag, helper and dev-env enablement (AR-16, plan_note "one helper")

**Files:** Modify `src/codemie/configs/config.py` (next to `WORKFLOW_GENERATION_ENABLED`, `:962`), `.env.example`, `deploy-templates/values.yaml` (`extraEnv`, next to `TRIGGER_ENGINE_ENABLED` `:55`) and its README table. Create `src/codemie/service/workflow/save_validation.py`. Test `tests/codemie/service/workflow/test_save_validation.py` (new dir; add `__init__.py` if sibling test dirs have one).

**Produces:** `Config.WORKFLOW_CONTRACT_LINKING_ENABLED: bool = False`; `contract_linking_enabled() -> bool` in `save_validation.py`. This helper is the only place that reads the flag.

Test-first: yes — `test_contract_linking_enabled_follows_config` patches `config.WORKFLOW_CONTRACT_LINKING_ENABLED` True/False and asserts the helper's result. It fails with ImportError.

- [ ] Write the failing test, run it, see it fail.
- [ ] Add the Config field and the helper.
- [ ] Dev enablement: add `WORKFLOW_CONTRACT_LINKING_ENABLED=true` with a one-line comment to `.env.example`. It is the tracked development env template (`ENV=development`); `.env` is gitignored (see Notes, "Local setup"). Add `- name: WORKFLOW_CONTRACT_LINKING_ENABLED / value: "false"` to `values.yaml` `extraEnv`, which is the production-safe default. Regenerate the chart README per the `helm-docs` comment at `values.yaml:33`, or hand-edit the matching row.
- [ ] Run the test, see it pass.

### Task 2: `ContractRecord.field` carried through simulate and policy (AR-11, plan_note "safe_name")

**Files:** Modify `src/codemie/workflows/contracts/models.py:90-100`, `contracts/simulate.py:1057-1079` (`_add`) plus the mandatory-parameter (`:835`), contract-form note (`:972`) and convergence (`:977,986`) call sites, and `contracts/policy.py`: `_finish` `:371-397` (both branches), `_form_finding` `:490-499` and `_strict_candidates` `:502-529`. Test `tests/codemie/workflows/validation/test_contract_policy.py`, `test_contract_corpus.py`.

**Produces:** `ContractRecord.field: str | None = None`.
- `_add(..., field: str | None = None)` defaults to `requirement.name` when a requirement is given. Mandatory-parameter passes `name`, contract-form passes `note.name`, convergence passes `name`.
- State-strict findings: `_form_finding(owner, producer, path, reason, field: str | None = None)` sets `field` on its record. The consumer-strict branch of `_strict_candidates` (`:523-525`, record on the consumer at `record.path`) passes `requirement.name`, so its `path` is the context field name. The workflow-strict branch (`:518-522`) and `_declared_contract_candidates` (`:476-487`) put the record on the producer's whole `output_schema`, where no field applies, and keep `None`.
- `_finish` sets `field=safe_name(record.field)` in the `model_copy` update and passes the same value to the fallback constructor (`:390`). Graph-level, coverage and whole-output-contract records keep `None`.

Test-first: yes — corpus/policy tests assert `record.field` is the bare name for a declared input, a tool binding, a missing mandatory parameter, an `output_schema.<f>` note and a state-level strict consumer whose producer is not schema-form (`CONTRACT_FORM` record on the consumer). They also assert `None` for graph-level, coverage and workflow-strict producer `output_schema` records, and that a forced render failure (fallback branch) keeps `field`. They fail because there is no attribute.

- [ ] Write the failing tests, run them, see them fail.
- [ ] Implement as above.
- [ ] Run `tests/codemie/workflows/validation/test_contract_*.py` and see it pass. `to_dict` now includes `field`; update any exact-dict assertion in `test_contract_models_report.py`.

### Task 3: `contract_issues.py` — issue shaping, selection, coverage, cap (AR-7, AR-12, AR-14, plan_note "advisory per record")

**Files:** Modify `src/codemie/workflows/validation/models.py:37-65`. Create `src/codemie/workflows/validation/contract_issues.py`. Modify `workflows/validation/__init__.py` (export). Test `tests/codemie/workflows/validation/test_contract_issues.py` (new).

**Interfaces / Produces:**
```python
class ContractMeta(BaseModel):            # added to WorkflowValidationErrorDetail.meta Union
    label: str | None = None
# WorkflowValidationErrorDetail gains: config_path: str | None = None; outcome: str | None = None

CONTRACT_WARNINGS_CAP = 100
class ContractIssues(NamedTuple):
    errors: list[dict]          # WorkflowValidationErrorDetail.model_dump(exclude_none=True), path always present
    warnings: list[dict]        # same + "kind": "contract_validation", capped
    coverage: dict              # validated_states,total_states,uncovered_state_ids,analysed,cap_reached
    warnings_truncated: int     # count cut by the cap, 0 if none; emitted downstream only when > 0

def to_config_path(record_path: str) -> str | None: ...   # closed spec table; None outside it
def build_contract_issues(report: ContractLinkReport, line_finder: YamlLineFinderBase) -> ContractIssues: ...
```
Rules (spec "Issue shape", "Selection", "Coverage"):
- `path = record.field or ""`.
- `outcome`: blocks -> `ERROR`, warning -> `WARNING`, info -> `UNVERIFIED`.
- `message = record.message`.
- `details` is the provenance line, `producers: …; removed by: … (mechanism); overwritten by: …; sources checked: …`. It is omitted when provenance is empty.
- `meta = ContractMeta(label)` when a label is set.
- `id = str(uuid5(_NS, f"{state}|{record.path}|{kind}|{message}"))`.
- Errors are the records with `reported_as == blocks`. Warnings are the non-blocking records with `outcome != OK`.
- Records outside the table get no `config_path`. That covers convergence (`context.*`), the bare `tool_args` (schema unknown) and graph-level `""`. List them in the module docstring.
- Each record is shaped in its own try. On failure, log and degrade the entry to `id, state_id, path, outcome, message`. A failed `config_line` or `details` drops only that key.
- Coverage is built in its own try. On failure it is `analysed: False` with zero counts.
- The blocking-set decision stays outside every try.
- `config_line` comes from Task 4. In this task, call `line_finder` through a stub hook `_config_line(record, config_path, line_finder) -> int | None` that returns None.

Test-first: yes — table-driven tests over hand-built `ContractRecord`s cover these cases:
- every table row's `config_path`;
- `path` is `""` with no field;
- the outcome mapping, with strict UNVERIFIED arriving as `ERROR` in errors;
- no `ERROR` in warnings;
- OK records excluded;
- `details` is absent for empty provenance and never contains `message`;
- `meta.label`;
- ids are identical across two calls;
- 150 warnings give 100 entries and `warnings_truncated == 50`; 100 warnings give `warnings_truncated == 0`;
- `failure_report` coverage has `analysed: False`;
- a degraded record whose provenance raises still yields an entry.

They fail with ImportError.

- [ ] Write the failing tests, run them, see them fail.
- [ ] Implement. The module imports `contracts.models` and `validation.models` only. `contracts/linker.py` does not import it (AR-7).
- [ ] Add a one-line import-order check to the test module (`import codemie.workflows.contracts.linker` before `codemie.workflows.validation`). Run the tests and see them pass.

### Task 4: `config_line` lookup per table row (AR-10)

**Files:** Modify `contract_issues.py` (`_config_line`, plus a new `contract_line_finder(workflow_config) -> YamlLineFinderBase`). Test `test_contract_issues.py` with real YAML fixtures.

**Produces:** `contract_line_finder` builds `YamlLineFinder.from_yaml(text, yaml.safe_load(text))`. The text is `getattr(cfg, "_source_yaml_config", None) or cfg.yaml_config`, the client's own text, as the resource stage reads it (`validation/resources.py:790`). It returns `NullYamlLineFinder` on any failure.

`_config_line` translates `config_path` into the map's notation, `a.b.<i>` -> `a.b[<i>]`. It then picks the section per row:
- `config.mappings.*` and `config.input_key` -> `find_line_for_custom_node_field(state.custom_node_id, …)`. The state's `custom_node_id` comes from the parsed dict.
- every other row -> `find_line_for_state_field(state_id, …)`.

It tries the full path and then each shorter prefix, longest first, always with `allow_fallback=False` and never stopping at the bare section item. On a miss the key is omitted.

AC 10 restated per row: a declared input whose `input_schema` is a YAML mapping resolves to its `properties.<f>` line. When `input_schema` is JSON text (`workflow_models.py:390`) it resolves to the state's `input_schema:` line. Every other row resolves to its own key line. No row lands on the state's fallback line.

Test-first: yes — a fixture YAML with one state per table row asserts the exact 1-indexed line per row. The fixture's `assistants[].model` is a base name, so `_rewrite_yaml_config_from_objects` would re-dump it: set `_source_yaml_config` to the original text and `yaml_config` to a re-dumped one, then assert that lines follow the source. Further cases: an unknown path gives no `config_line`; an unparseable YAML gives `NullYamlLineFinder`. The tests fail because the stub returns None.

- [ ] Write the failing tests, run them, see them fail. Implement. Run them, see them pass.

### Task 5: Executor rejection envelope and leaf linker (AR-7)

**Files:** Modify `src/codemie/workflows/workflow.py:339-348`, `contracts/linker.py:69-110` (remove `build_contract_error`, `_error_detail`, `_error_line`), `validation/__init__.py:62-90` (re-export `build_contract_rejection` from `contract_issues`, drop the linker shaping names) and `contract_issues.py`. Test `tests/codemie/workflows/validation/test_contract_linker.py` (repoint `test_build_contract_error_*`).

**Produces:** `build_contract_rejection(report, line_finder) -> tuple[dict, str] | None`, built on `build_contract_issues`.
- json: `{error_type: "contract_validation", message: "Workflow contracts are not satisfied", errors, warnings, coverage}`, plus `warnings_truncated: <N>` only when N > 0.
- string: `<br>`-joined error lines, then warning lines prefixed `WARNING:`/`UNVERIFIED:`, then one line `warnings_truncated: <N>` only when N > 0, then one coverage line `Contracts checked on X of Y reachable states`. State ids and field names go through `safe_name`/`safe_path`.

The executor calls `build_contract_rejection(report, contract_line_finder(workflow_config))`. It keeps returning `(graph, report)`, a two-value return that five test sites unpack (AR-12).

Test-first: yes — a linked `validate_workflow_with_report` with two blocking records and one warning raises in json mode a dict carrying both errors, one `warnings` entry, `coverage` with `analysed: True`, `path` as the bare name, every key the old dict had, and no `warnings_truncated` key. In string mode the text contains the coverage line and no `warnings_truncated`. With 150 warnings, json has `warnings_truncated == 50` and string has the line `warnings_truncated: 50`. An earlier-stage failure (e.g. missing state) raises that stage's error and `link_workflow_contracts` is not called (AC 6, patch spy). The tests fail on the missing keys.

- [ ] Write the failing tests, run them, see them fail. Implement. Run `tests/codemie/workflows/validation/` and see it pass.

### Task 6: Save pipeline `prepare` / `validate`; delete `validate_for_update`; rollback unlinked (AR-8, AR-9)

**Files:** Modify `save_validation.py`. Move from `rest_api/routers/workflow.py:91-162,212-240`: `_get_project_for_workflow`, `_normalize_workflow_model_ids`, `_patch_model_ids_in_yaml`, `_rewrite_yaml_config_from_objects` and `_consumer_slot_warnings_sync` (as `collect_advisory_warnings`). No re-exports are left in the router (AR-13). Delete `WorkflowService.validate_for_update` (`service/workflow_service.py:249-263`). Modify `service/workflow/workflow_version_service.py:109`. Test `test_save_validation.py`, `tests/codemie/service/test_workflow_version_service.py`.

**Produces:**
```python
class SaveValidationResult(NamedTuple):
    warnings: list[dict[str, object]]
    coverage: dict | None            # None when unlinked
    warnings_truncated: int          # 0 when unlinked or nothing cut

def prepare(request_dump: dict, user: User, defer_parse_failure: bool) -> WorkflowConfig: ...
def validate(candidate: WorkflowConfig, request_dump: dict | None, user: User,
             error_format: WorkflowErrorFormat, treat_as_global: bool,
             stored_workflow: WorkflowConfig | None = None,
             link_contracts: bool | None = None) -> SaveValidationResult: ...
```
- `prepare` follows spec step 1. The non-deferred parse failure raises `ExtendedHTTPException(400, WORKFLOW_CONFIGURATION_ERROR, details=str(e).strip())` as today.
- `validate` follows steps (a)-(d). `link_contracts=None` means `contract_linking_enabled()`.
- (a) runs `validate_on_save` on `WorkflowConfig(**request_dump)` and on a freshly parsed copy. When `request_dump is None` it runs on the candidate only, which is today's `validate_and_strip_workflow_mcp_servers`. It then strips.
- (b) calls `_marketplace_service.validate` when `stored_workflow and stored_workflow.is_global`.
- (c) calls `WorkflowExecutor.validate_workflow_with_report(..., link_contracts=linked)`.
- (d) collects the advisory warnings from the candidate. When linked, `build_contract_issues(report, contract_line_finder(candidate))` follows in its own try, and a failure is logged and advisory. Step (d) is skipped when `request_dump is None` (rollback).
- MCP-gate `ValidationException`, marketplace exceptions and `SQLAlchemyError` propagate untouched. Executor `ValueError`s propagate unwrapped, so the routers wrap them.
- Rollback calls `validate(workflow, None, user, WorkflowErrorFormat.STRING, treat_as_global=bool(workflow.is_global), stored_workflow=workflow, link_contracts=False)`.

Test-first: yes — these tests fail with AttributeError/ImportError:
- `prepare(defer=True)` on bad YAML returns an unparsed config, and `defer=False` raises 400.
- Normalization rewrites `yaml_config` and keeps `_source_yaml_config`.
- Flag on gives `coverage` and contract warnings after `consumer_slot`; flag off gives `coverage is None`, `warnings_truncated == 0` and the linker is not called.
- A restricted-mode custom MCP server on the raw dump raises `ValidationException`.
- A stored global workflow triggers marketplace validation.
- A `SQLAlchemyError` from the executor leaves `validate` unwrapped.
- Rollback with the flag patched True never calls `link_workflow_contracts` (AR-8 acceptance).

- [ ] Write the failing tests, run them, see them fail. Implement and delete `validate_for_update`.
- [ ] Repoint the tests that call or patch the moved names (`test_workflow.py:345-346,439-471`, `WorkflowService.validate_for_update` patches) to `save_validation`. No response-body assertion changes. Run the touched test files and see them pass.

### Task 7: Wire create, update, saved dry-run; response `coverage` (AR-9, AR-13)

**Files:** Modify `src/codemie/rest_api/routers/workflow.py:164-209,487-496,499-614,623-712,715-758`. Test `tests/codemie/rest_api/routers/test_workflow.py`.

**Changes:**
- `WorkflowSaveResponse` and `ValidateWorkflowResponse` gain `coverage: dict | None = None` and `warnings_truncated: int | None = None`. A `@model_serializer(mode="wrap")` drops `coverage` when None and `warnings_truncated` when None or 0, and leaves `data` untouched. Routers pass `result.warnings_truncated or None`.
- Create: `prepare(dump, user, defer=True)`, autonomous 410, `project_access_check`, categories, then `validate(treat_as_global=False)`, save. Warnings come from the candidate result, not the saved config.
- Update and dry-run: load, access, `prepare(defer=False)`, autonomous 403, guardrail dry-run sync, then `validate(treat_as_global=bool(workflow.is_global), stored_workflow=workflow)`. The dry-run gains in-memory normalization.
- All three use one helper `_wrap_validation_errors(error_format, db_error_message: str | None = None)`:
  - It re-raises `ValidationException` and `NotFoundException`, so the create MCP refusal surfaces untouched, as today.
  - With `db_error_message` set, a `SQLAlchemyError` becomes `ExtendedHTTPException(503, db_error_message, details=str(e).strip())`. This is checked before the generic branch. Create passes `"Database error during workflow creation"` and keeps categories, `validate` and the save inside it, reproducing `:553-559`.
  - Otherwise it keeps each entry's rule. Update and dry-run (no `db_error_message`) re-raise `ExtendedHTTPException` and wrap everything else into a 400 with json-dict/str details, as `:199-209`. Create wraps the rest, `ExtendedHTTPException` included (details `e.message`), into the 400, as `:560-572`. Update's post-validation 503 at `:693-699` is unchanged.
- Async handlers call each pipeline step with `asyncio.to_thread`.

Test-first: yes — these fail because there is no `coverage` key and no pipeline call:
- Flag on: create with an unsatisfied input contract (json) returns 400 with `details.errors[0].config_path == "input_schema.properties.<f>"`, `details.coverage` and `details.warnings`, and no `details.warnings_truncated`.
- Flag on: a clean update returns `coverage` and a `contract_validation` warning, and no `warnings_truncated` key. With the shaping patched to cut 50, the body has `warnings_truncated == 50`.
- Flag off: the bodies have no `coverage` and no `warnings_truncated` key.
- Create in restricted MCP mode returns the governance `ValidationException` response, not "Workflow Configuration error".
- Create with `validate` patched to raise `SQLAlchemyError` returns 503 `Database error during workflow creation`, not 400.

- [ ] Write the failing tests, run them, see them fail. Implement. Run `test_workflow.py` and `test_workflow_versions.py` and see them pass with only patch-target edits (AC 1).

### Task 8: `POST /v1/workflows/validate` and parity (AR-15, plan_notes "access first", "MCP refusal test", "call pattern")

**Files:** Modify `src/codemie/rest_api/routers/workflow.py` (route registered before any `/workflows/{workflow_id}` POST). Add `ValidateNewWorkflowRequest` (spec block) to `core/workflow_models/workflow_models.py` next to `CreateWorkflowRequest`, and export it. Test `test_workflow.py` and `tests/codemie/workflows/validation/test_contract_parity.py` (new).

**Behavior:** sync `def`, query `error_format`.
1. `project_access_check(user, body.project)` and the autonomous 410 run before `prepare`, so a non-member triggers no lookups.
2. `prepare({**dump, "name": dump["name"] or "", "description": dump["description"] or ""}, defer=True)`, then `validate(treat_as_global=False)` inside `_wrap_validation_errors(error_format)`. Nothing is persisted.
3. Return `ValidateWorkflowResponse(message, warnings, coverage, warnings_truncated=result.warnings_truncated or None)`.

The docstring states the call pattern (explicit action or save-preview, not per-keystroke debounce: each call lists MCP tools and compiles the graph). It also states that a clean result guarantees no contract rejection on create in the same environment, and that category, guardrail, autonomous and access checks are not covered (AR-15).

Test-first: yes — these fail with 404/405 because the route does not exist:
- A body without name/description returns 200 with `coverage`, and the repository save is not called.
- A non-member gets 401/403 and `prepare` is not called.
- The autonomous mode returns 410.
- A restricted-mode raw-request custom MCP server is refused.
- Parity (non-global): one config sent through create, update, saved dry-run and the new endpoint gives identical contract entries (ids included), `consumer_slot` entries and coverage.
- Global case: a stored global workflow through update and the dry-run shows `tool_access_owner_integration` and calls marketplace validation.
- A config that is clean on `/validate` is not contract-rejected on create.

- [ ] Write the failing tests, run them, see them fail. Implement. Run them and see them pass.

---

## Notes
- **Local setup (before Stage 7 / local verification; not a task, nothing committed).** Add the line `WORKFLOW_CONTRACT_LINKING_ENABLED=true` to the repo-root `.env`. That file is gitignored: do not commit it. Then restart the backend so `Config` re-reads it (the flag needs a restart). Without this the verification checks run with the feature off. The `.env.example` change stays in Task 1.
- **Brief items handled outside a task.** Measuring the p95 latency and MCP connects of `/validate` belongs to the verification stage. The UI call pattern is handed off through the Task 8 docstring. Rollback, diagram, background schema and generator stay unlinked, with no follow-up owner named (spec Non-goals). `details` stays a provenance-only line per the user's decision; the repeat risk is accepted. A shared base for `ValidateNewWorkflowRequest` is not taken (low).
- **Hand-offs to EPMCDME-16505 (UI):**
  - `path` holds the context field name, not a config path. The editor navigates by `config_path` and registers the two added rows (`task`, `config.input_key`).
  - `warnings[]` uses `tool_access_owner_integration`, not the ticket's `script_step_owner_integration`.
  - Truncation arrives as `warnings_truncated`, only when above 0.
  - When the editor calls `/validate` (or the saved dry-run), it must clear the issues it is showing and replace them with the response's set. Otherwise issues the user has fixed stay on screen. Each response carries the complete current set, with deterministic ids.
- **Negative constraints:**
  - Shaping only, no outcome computation: Task 2 copies an existing name and computes nothing.
  - No `ExtendedHTTPException` or handler change: Tasks 5 and 7 use the existing `details` dict.
  - No warning-kind rename: Tasks 3 and 7.
  - No marketplace check on create and no guardrail dry-run on `/validate`: Tasks 7 and 8 pass `stored_workflow` only for update and the dry-run.
  - Parse-failure handling is not unified: `defer_parse_failure` keeps it per entry point.
  - The shared error wrapper does not turn a create DB error into a 400: Task 7 keeps the 503.
  - Rollback and the generator stay unlinked: Task 6 passes `link_contracts=False`, and the generator and diagram still call the executor default.
  - No `exclude_none` and no migration: Task 7 uses a field-scoped serializer, and nothing is persisted.

## Correction plan: strict-source metadata and safe responses

Preserve `meta.strict_source` through all four routes and recoverable formatter fallback. Set it only when workflow/state strict causes the block; workflow strict takes precedence. Cover label-free findings, strict contract-form findings, and graph failures. Do not add it to coverage findings that block in warn, settings-read failures, or final fallbacks that lost record metadata.

The correction plan replaces diagnostic-string path translation with typed locations, shares source metadata per request/text, removes raw database details from all four routes, and adds an independent final formatter fallback. It also adds real access, normalization, autonomous-order, mixed-rejection, and strict-source boundary tests. JSON names remain data; 16505 owns rendering fixes.
