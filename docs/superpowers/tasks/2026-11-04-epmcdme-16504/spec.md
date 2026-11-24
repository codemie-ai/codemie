# Spec: EPMCDME-16504 Save-time Contract API Surface

Source: `ticket-source.md` (verbatim ticket), `technical-analysis.md` (TA). Branch: `epmcdme-16504`, based on the completed `epmcdme-16503` correction.

## Goal

Expose the linker's per-call `ContractLinkReport` through create, update, saved-workflow dry-run and a new pre-save endpoint. All four run one pipeline, report issues in one shape, return coverage, and are switched by one platform flag.

## Behavior

**Flag.** Static `Config` bool `WORKFLOW_CONTRACT_LINKING_ENABLED`, default `False`, set `true` in the development deployment's env file. It is read once, inside the pipeline's validate step. The executor default `link_contracts=False` (`workflow.py:251-348`) stays. Flag off: no linker, no contract issues, no `coverage` key. The input-contract structural check still runs because it is in the schema stage (`validation/schema.py:56`). Flipping it needs a restart.

**Shared pipeline.** It lives in a new service module `service/workflow/save_validation.py`. The module takes over the router-private `_normalize_workflow_model_ids`, `_rewrite_yaml_config_from_objects` and `_consumer_slot_warnings_sync` (`routers/workflow.py:102-162,212-232`). It is sync. Async handlers offload each step whole with `asyncio.to_thread`, and the new endpoint is a sync `def`. It has two steps.

1. `prepare(request_dump, user, defer_parse_failure)` parses the execution config. If the parse succeeds, it normalizes model ids and rewrites the in-memory `yaml_config`, because the executor re-parses that text (`workflow.py:296`). With `defer_parse_failure=True` (create, new endpoint), a parse failure is left for the executor's schema stage to report. With `False` (update, saved dry-run), it raises the raw-string 400 it raises today (`:652-658`, `:743-749`). The saved dry-run gains normalization and an in-memory rewrite. Nothing is persisted.
2. `validate(candidate, request_dump, user, error_format, treat_as_global, stored_workflow=None)` runs these steps in order:
   - (a) MCP gate. `validate_on_save` runs on the raw request candidate and on a freshly parsed copy, as create does today (`:512-521`). It then strips the servers from the candidate.
   - (b) Marketplace validation, when `stored_workflow.is_global`.
   - (c) `validate_workflow_with_report(link_contracts=<flag>)`.
   - (d) Advisory warnings (`consumer_slot`, `tool_access_owner_integration`) from the candidate, then the contract warnings, then coverage.

   It returns `SaveValidationResult(warnings, coverage, warnings_truncated)`. Database exceptions retain their type until the route maps them to a safe 503. Other validation errors retain their existing format.

Entry-specific steps stay in the routers:
- loading the stored workflow and checking write access (update, saved dry-run);
- `project_access_check` (all four);
- the autonomous-mode check: 410 for create and the new endpoint, 403 for update and the saved dry-run;
- the guardrail dry-run sync (update, saved dry-run), between `prepare` and `validate`;
- category validation (create);
- persisting (create, update).

`treat_as_global` is `False` for create and the new endpoint. For update and the saved dry-run it is the stored `is_global` (`:662,756`). `validate_for_update` becomes `validate(..., link_contracts=False)` and keeps rollback (`workflow_version_service.py:109`) unlinked.

**New endpoint.** `POST /v1/workflows/validate`, query `error_format`. Auth is `project_access_check(user, project)` (`security/authentication.py:378`), with no write check. It runs `prepare(defer=True)` and `validate(treat_as_global=False)`, persists nothing, and returns `ValidateWorkflowResponse`. The body is a new model in which `name` and `description` are optional. `CreateWorkflowRequest` requires both (`workflow_models.py:433-449`). Missing values become `""` when the `WorkflowConfig` is built.

```python
class ValidateNewWorkflowRequest(BaseModel):
    project: str
    name: str | None = None
    description: str | None = None
    mode: WorkflowMode | None = WorkflowMode.SEQUENTIAL
    yaml_config: str | None = None
    assistants: list[WorkflowAssistant] | None = []
    tools: list[WorkflowTool] | None = []
    states: list[WorkflowState] | None = []
    supervisor_prompt: str | None = ""
    meta_config: str | None = None
```

**One home for contract shaping.** `workflows/validation/contract_issues.py` owns `build_contract_issues(report, line_finder) -> ContractIssues`. It replaces `_error_detail` (`linker.py:96-102`) and feeds `build_contract_error`. The executor uses it twice:
- on rejection, for the raised dict (`workflow.py:343-347`);
- on success, by returning it with the report to `validate` (d).

Entries are `WorkflowValidationErrorDetail` (`validation/models.py:53-65`), which gains the optional fields `config_path` and `outcome`. Each entry's `id` is deterministic: uuid5 over state id, record path, kind and message.

**Issue shape.**
- `state_id`: unchanged.
- `path`: always sent. It holds the requirement's bare field name, taken from a new optional `ContractRecord.field` that `_finish` copies from `Finding.requirement.name` (`policy.py:371-397`). It is `""` when the record has no field (graph-level, whole output contract, coverage).
- `config_path`: one public formatter consumes typed `ContractRecord.location` tokens. The table below specifies the public syntax; it must not be used to infer ownership or parse diagnostic strings.
- `config_line`: exact typed location tokens select a line in the original submitted YAML. Numeric string keys remain distinct from list indices. Known parent fields are the only fallback. If no line is found, the key is omitted.
- `outcome` comes from `reported_as`: `blocks` maps to `ERROR`, `warning` to `WARNING`, `info` to `UNVERIFIED`. Raw `record.outcome` is not exposed. As a result, strict mode turns UNVERIFIED into `ERROR` in `errors[]`, and in warn mode a non-rejecting ERROR shows as `WARNING` in `warnings[]` (AC-18, AC-19).
- `message`: the linker's rendered title (`policy.py:320-328`). It names the producing node, the removing node or the unknown source.
- `details`: a provenance line built from `record.provenance`: producers, removed_by + mechanism, overwritten_by, sources_checked. It never repeats the `message` sentence. It is omitted when provenance is empty.
- `meta.label`: the label (e.g. certain runtime failure), kept so it stays visible.
- `meta.strict_source`: optional `workflow` or `state`, only when strict causes a block that warn would not cause. Workflow strict takes precedence. Label-free issues, strict contract-form findings, and graph failures can carry it. Coverage findings that block in every mode and `CAUSE_SETTINGS_FAILED` do not. Preserve it through recoverable formatter fallback; never invent it after record data is lost.

| Requirement | Linker path | `config_path` |
|---|---|---|
| declared input | `input_schema.<f>` | `input_schema.properties.<f>` |
| tool binding | `tool_args.<p>` | `tool_args.<p>` |
| transform mapping | `config.mappings[<i>].<f>` | `config.mappings.<i>.<f>` |
| producer output | `output_schema[.<f>]` | `output_schema` |
| `require_input_contracts` | `input_schema` | `input_schema` |
| condition / switch / iter | `next.condition.expression`, `next.switch.cases[<i>].condition`, `next.iter_key` | same, `[<i>]` -> `.<i>` |
| placeholder (added) | `task` | `task` |
| input key (added) | `config.input_key` | `config.input_key` |
| graph-level | `""` | omitted |

**Selection.**
- Errors: records with `reported_as == blocks`.
- Contract warnings: records with `reported_as != blocks` and `outcome != OK` (selected by outcome, because warn-mode UNVERIFIED is `info`, `policy.py:400-411`). Each gets `kind: "contract_validation"`. They are appended after the existing `consumer_slot` / `tool_access_owner_integration` entries, which are unchanged.
- On success, a non-database failure in shaping the warnings or coverage is advisory: it is logged, and the save still succeeds. Database failures in warning-provider reads propagate to the safe HTTP 503 mapper.

**Coverage.**

```python
{"validated_states": int, "total_states": int, "uncovered_state_ids": list[str],
 "analysed": bool, "cap_reached": bool}
```

The first three are the §7.4 keys, mapped from `validated_count`, `reachable_total` and `uncovered_state_ids`. The other two are additive, and they keep a crashed analysis from reading as "0 of 0" (`policy.py:299-317`). `coverage` is an optional top-level field on `WorkflowSaveResponse` and `ValidateWorkflowResponse`. When the flag is off, a serializer omits it; `exclude_none` would also alter `data`.

**Rejection envelope.** Contract errors are raised only after every earlier stage passes (`workflow.py:274-337`).
- `error_format=json`: `details` is `{error_type: "contract_validation", message, errors[], warnings[], coverage}`. `warnings[]` holds only the contract warnings, capped at 100.
- `error_format=string`: a `<br>` string with error lines, warning lines prefixed by outcome, the optional truncation count, and a coverage count summary. It does not expose structured issue objects. Names remain data; HTML consumers must sanitize at their rendering boundary.

## Acceptance criteria

1. Flag off: create, update, the saved dry-run and the new endpoint return no contract issues and no `coverage` key. Existing router tests pass unchanged. The input-contract structural check still rejects.
2. Flag on: every contract issue has `state_id`, `path` (field name, or `""`), `config_path` per the table, `config_line` when found, `outcome` (mapped from `reported_as`), `message`, and `details` that does not repeat `message`.
3. A client that ignores `config_path`, `outcome` and `coverage` parses every response unchanged. Every key sent today is still sent, `path` is still a string, and `error_type` and the envelope keys are unchanged.
4. A successful save or dry-run puts the non-OK, non-blocking contract records in `warnings[]` with `kind: "contract_validation"`. No entry in `warnings[]` has `outcome: "ERROR"`. Existing kinds are unchanged.
5. Several contract errors come back in one 400, together with the same run's contract warnings and coverage (json: `details.warnings`, `details.coverage`).
6. A workflow that fails an earlier layer is rejected by that layer exactly as today, and the linker does not run.
7. Coverage is returned on success and on contract rejection. A failed analysis returns `analysed: false`.
8. Parity:
   - Non-global case: one config, user and project sent through all four entry points give identical contract entries, `consumer_slot` entries and coverage.
   - Global case (separate test): a stored global workflow, through update and the saved dry-run, also shows the owner-integration warning and marketplace validation.
   - Results that depend on an MCP server match only when the server answers the same way (§7.5).
9. `POST /v1/workflows/validate` accepts a body without `name`/`description`, needs only project membership, persists nothing, and returns `ValidateWorkflowResponse` plus `coverage`.
10. For every row of the `config_path` table, a real YAML resolves `config_line` to that field's line, not to the state's fallback line.
11. Entry ids are stable across calls for the same config.

## Decisions

- Static `Config` bool over a `DynamicConfig` runtime key — which mechanism carries the flag (the configuration guide's convention for feature gates).
- Default `False`, set `true` in the dev env file, over an `ENV`-dependent default — how "on in dev, off in prod" is met (no such mechanism exists).
- Flag read once in the pipeline's validate step over reading it at four entry points — where the flag is read.
- A two-step sync service pipeline (`prepare` / `validate`) owning parse, normalize + rewrite, MCP gate on raw and parsed candidates, marketplace, executor and warnings, over point fixes per handler — what the shared pipeline owns (AR-1).
- Guardrail dry-run, autonomous check, categories, access checks and persistence stay in the routers over moving them into the pipeline — which steps depend on the entry point.
- Rollback, diagram, background schema and generator stay unlinked (`validate_for_update` passes `link_contracts=False`) over linking them — the ticket names only create, update and the dry-runs.
- "Both dry-run shapes" = the saved dry-run and the new pre-save endpoint, over the two `error_format`s — only one dry-run endpoint exists.
- `validation/contract_issues.py` is the one home for errors, warnings, and coverage. The linker has no REST dependency.
- Rejection carries warnings and coverage in the executor's raised dict over a new exception type or a handler change — how they reach the 400.
- Deterministic uuid5 entry id over the random uuid4 — so parity and UI card keys hold.
- `path` = bare field name via `ContractRecord.field`, `""` when there is none, over omitting the key — user.
- `outcome` mapped from `reported_as` over raw `record.outcome` — user.
- Label kept in `meta.label` — user.
- `message` = the linker's title, and `details` = a provenance line that never repeats it, over a new title template — user.
- Typed internal locations determine ownership and exact lookup. One formatter retains the existing public `config_path` syntax.
- `config_line` uses exact typed tokens from the report and a request-local source map.
- Table extended with `task` and `config.input_key` over omitting them — the linker emits those paths.
- Coverage keys `validated_states` / `total_states` / `uncovered_state_ids` (§7.4) over the report's field names — user.
- `analysed` and `cap_reached` added to coverage over leaving them out — AR-5.
- Kind name `tool_access_owner_integration` kept over renaming it to `script_step_owner_integration` — a rename breaks existing clients.
- Contract warnings selected by `outcome != OK` among non-blocking records over selecting by `reported_as` — warn-mode UNVERIFIED is `info`.
- New endpoint body `ValidateNewWorkflowRequest` with optional `name`/`description` over `CreateWorkflowRequest` — user.
- Parity scoped to contract entries, `consumer_slot` and coverage; global behavior pinned in a separate test — user, answers AR-3.
- New endpoint applies create's autonomous 410 over skipping it — it runs the create path.
- Warnings computed from the pre-persist candidate in every path over create's saved config — so a dry-run can reproduce them.

## Non-goals

- Computing outcomes, provenance or classification (linker sibling); only shaping changes.
- UI controls, issues panel, field-path registry (UI child); docs (docs child); SDK/CLI typing (Story 4).
- Runtime checks and the runtime flag (Story 3); engine behavior.
- Unifying parse-failure status and messages across entry points.
- Adding marketplace validation to create, or a guardrail dry-run to the new endpoint.
- Renaming existing warning kinds; changing `ExtendedHTTPException` or the global handler.
- Linking rollback or the generator loop.
- Any DB migration or persistence of issues or coverage.

## Risks

- Hand-off to EPMCDME-16505: `path` now holds the context field name, not a config path. The editor must navigate by `config_path` and register the two added rows.
- Hand-off to the UI/docs children: `warnings[]` uses `tool_access_owner_integration`, not the ticket's `script_step_owner_integration`.
- The default `error_format=string` carries warnings and coverage on rejection only as text. Callers need `json`.
- The new endpoint contacts declared MCP servers on every call. Its latency and connection count are unmeasured.
- Rollback can restore a version that a linked save would reject.

## 2026-11-09 correction requirements

The accepted correction plan is `../2026-11-09-epmcdme-16504-fix-cx/plan.md`. It supersedes obsolete implementation details above.

- Share source metadata within one request, once for each distinct text. Original and normalized YAML use separate entries. Do not cache metadata on a stored workflow or report. Preserve both raw-request and parsed-YAML MCP checks.
- Map database failures in preparation, model lookup, guardrails, shared validation, and persistence to a fixed safe 503 response on all four routes. Never expose SQL, bind values, connection strings, or exception text.
- A final issue fallback uses only known class and record index. It is bounded and serializable, preserves blocking/advisory status, and keeps unique response-local IDs.
- Compare create with new validation and update with saved validation under equal user/project/settings/resource state. A clean check does not guarantee later access, category, guardrail, or persistence success. Rollback remains unlinked.
- New validation requires project read access. Saved validation retains write access. Effective autonomous mode returns 410 for create/new validation and 403 for update/saved validation before guardrail sync.
- JSON names are data, with documented control removal and display bounds. Preserve ordinary Unicode and `<>&`, and exact internal source tokens. Render issue values as text in clients. Legacy HTML consumers must sanitize at their rendering boundary. The UI regression belongs to 16505.

Additional acceptance tests cover exact numeric/punctuation/Unicode keys, real model normalization with original line navigation, one source-map build per text/request, all route/database stages in both error formats, both formatter failures, real project access, mixed error/warning rejections, and strict-source exceptions.
