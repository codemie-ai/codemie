# LangGraph Workflows

## Workflow Executor

Use the existing executor and node model for workflow behavior.

| Avoid | Prefer |
|---|---|
| Creating a separate graph execution path for one workflow | Extend `WorkflowExecutor`, nodes, or validation utilities |
| Bypassing workflow config parsing | Use existing config parsing and validation |

Evidence: `WorkflowExecutor` imports `StateGraph` and workflow validation utilities at `src/codemie/workflows/workflow.py:29`.

## Nodes And State

Keep node behavior in workflow node packages and state transitions in workflow utilities/config.

| Avoid | Prefer |
|---|---|
| Embedding node-specific behavior in services | Add or extend workflow nodes |
| Manually evaluating transition expressions in feature code | Use workflow utility functions |

Evidence: workflow nodes are imported from `codemie.workflows.nodes` at `src/codemie/workflows/workflow.py:86`.

## Save-Time Validation Pipeline

Create, update, the saved-workflow dry-run (`POST /v1/workflows/{id}/validate`), the pre-save dry-run (`POST /v1/workflows/validate`) and version restore share one pipeline in `src/codemie/service/workflow/save_validation.py`: `prepare` parses the request dump and normalizes model ids, `validate` runs the gates (MCP, marketplace, `WorkflowExecutor.validate_workflow_with_report`, advisory warnings, contract issues). Routers keep only entry-specific steps (access, autonomous mode, guardrails, categories, persistence).

| Avoid | Prefer |
|---|---|
| A per-router copy of the parse, normalize and validate sequence, or a `WorkflowService.validate_for_update` method (removed) | `save_validation.prepare` then `save_validation.validate` |
| Reading `WORKFLOW_CONTRACT_LINKING_ENABLED` in routers, services or the executor (only the UI runtime component in `customer_config.py` reads it directly) | `contract_linking_enabled()`; pass `link_contracts` explicitly only to override (version restore passes `False`) |
| Letting a non-database advisory or issue-shaping failure fail a save | Catch, log and degrade. Database failures during warning-provider reads still return safe HTTP 503. |
| Calling the sync pipeline directly from an async handler | `asyncio.to_thread(...)` around each step |

Evidence: `src/codemie/service/workflow/save_validation.py::prepare`, `src/codemie/service/workflow/save_validation.py::validate`, `src/codemie/service/workflow/save_validation.py::contract_linking_enabled`; restore call at `src/codemie/service/workflow/workflow_version_service.py::WorkflowVersionService._validate_restored_workflow`; async offload in `src/codemie/rest_api/routers/workflow.py::update_workflow`.

## Contract Linker Package

`src/codemie/workflows/contracts/` is the save-time linker. `linker.link_workflow_contracts` reads the settings, runs `simulate.simulate_workflow` over the reachable graph and returns the `ContractLinkReport` from `policy.build_report`; it does not raise, and a crash becomes one graph-level "could not analyse" record that blocks only under workflow or state strict (a failed settings read assumes strict). Contract YAML keys: workflow-level `save_validation` (`warn` by default, or `strict`) and `require_input_contracts` (bool, every uncovered reachable state becomes a blocking coverage error in both modes); state-level `input_schema` (JSON text) and `save_validation`, which overrides the workflow mode for that state.

| Avoid | Prefer |
|---|---|
| Calling a service (tool discovery, MCP) from the linker | Answer through the `ToolSchemaResolver` protocol; `SaveTimeToolSchemaResolver` in `validation/tool_schemas.py` implements it and is created per save |
| Re-implementing a context-write rule in the linker, or changing it only inside a node | Change the pure function in `workflows/context_rules.py` (transform vocabulary: `workflows/transform_constants.py`); the nodes, the executor and `contracts/transitions.py` call the same functions |
| Adding `save_validation` or `require_input_contracts` as `WorkflowConfig` fields, or reading them elsewhere | `read_contract_settings` (`contracts/settings.py`) reads them from the `yaml_config` text; a bad value gives the default, and the YAML schema (`execution_config_schema.yaml`) rejects it on save |
| Making a stored workflow fail to load on a bad state `input_schema` or `save_validation` | Keep both lenient on `WorkflowState` (`input_schema` drops non-text); the schema stage rejects on save and `validate_state_input_contracts` checks the `input_schema` structure |

Evidence: `src/codemie/workflows/contracts/linker.py::link_workflow_contracts`, `src/codemie/workflows/contracts/settings.py::read_contract_settings`, `src/codemie/workflows/contracts/policy.py::resolve_state_modes`, `src/codemie/workflows/contracts/protocols.py::ToolSchemaResolver`, `src/codemie/workflows/validation/tool_schemas.py::SaveTimeToolSchemaResolver`, `src/codemie/workflows/contracts/input_contract_stage.py::validate_state_input_contracts`, `src/codemie/core/workflow_models/workflow_models.py::WorkflowState`.

## Contract Issues

When linking is on, `WorkflowExecutor.validate_workflow_with_report` returns the `ContractLinkReport` and `workflows/validation/contract_issues.py` shapes it. Blocking records (`reported_as == blocks`) become errors and raise one `contract_validation` error through `build_contract_rejection`; other non-OK records become warnings, capped at `CONTRACT_WARNINGS_CAP` with a `warnings_truncated` count. `coverage` counts checked and total reachable states. Entries are `WorkflowValidationErrorDetail` dumps carrying `config_path`, `config_section`, `outcome` (ERROR, WARNING, UNVERIFIED), `config_line` and `meta.label`. `meta.strict_source` (`workflow` or `state`) is set by the policy only when the issue blocks because of strict (it would not block in warn): on strict contract-form findings and on a graph-level failure that strict blocked, never on `require_input_contracts` coverage findings or on `CAUSE_SETTINGS_FAILED`, where strict is assumed.

| Avoid | Prefer |
|---|---|
| Importing `contract_issues` from `contracts/linker.py` | Keep the dependency one way: `contract_issues` imports `contracts.models` and `validation.*` only |
| Deriving `outcome` from the raw record outcome | Map from `reported_as` |
| Reconstructing ownership or list indices from a diagnostic string | Use `ContractRecord.location`; preserve string keys and integer indices as distinct tokens |
| Reporting lines of the re-dumped YAML | Pass the client's text as `line_source_yaml` (`_source_yaml_config`) |

`ContractRecord.field` holds the bare context-field or parameter name a record is about, or `None`. `coverage` and `warnings_truncated` are omitted from API responses when linking did not run or nothing was cut.

The public `config_path` uses the existing dot-separated notation. Internal line lookup uses exact typed tokens, so a numeric string key, a list index, and a key containing a dot or bracket remain distinct. `state_id` identifies the consumer; the location identifies the owner in `states`, `tools`, or `custom_nodes`. A workflow-root location has no owner. Missing locations do not trigger an ownership guess. A missing exact field can fall back through its known parent fields, but not to an unrelated item.

Build source metadata once per distinct source text within a request. Share it between schema validation and issue formatting. Keep original client text separate from normalized text. Do not store the metadata on the workflow or report, and do not share it between requests. Invalid or unavailable source uses a null lookup. Keep both MCP checks: raw request fields and parsed YAML.

If issue formatting fails, keep the classification selected by the caller. Try the minimal entry, then use a final fixed-text entry whose ID depends only on the known class and record index. The final fallback must not read the failing record. Preserve `strict_source` when it is recoverable; do not invent it after record data is lost.

Evidence: `src/codemie/workflows/validation/contract_issues.py::build_contract_issues`, `src/codemie/workflows/validation/contract_issues.py::build_contract_rejection`; `src/codemie/workflows/contracts/models.py::ContractLocation`, `src/codemie/workflows/contracts/models.py::ContractRecord`; `src/codemie/workflows/workflow.py::WorkflowExecutor.validate_workflow_with_report`.

## Response Text and Database Failures

Names in JSON are data. Remove the documented control characters and apply display bounds, but do not HTML-entity-encode semantic names. Preserve exact internal location tokens even when a display value is shortened. Clients must render messages, details, labels, and names as text. A client that renders HTML, including the legacy `<br>` error string, must sanitize at its render boundary. JSON escaping does not make a value safe HTML.

Create, update, new validation, saved validation, and version rollback return HTTP 503 for database failures. Use a fixed public message and safe details. Never include SQL, bind values, connection strings, or raw database exceptions in the response or diagnostic message. Preserve `SQLAlchemyError` through preparation, guardrails, resource checks, and advisory provider reads until the route mapper handles it. Only non-database advisory and issue-shaping failures degrade to omitted warnings. Keep access refusals (403/404), autonomous refusals (403/410), and structured contract errors (400) unchanged.

JSON contract rejections contain `details.errors`, `details.warnings`, and `details.coverage`. Legacy string rejections contain error lines, warning lines, and a coverage count summary. At most 100 contract warnings are returned; `warnings_truncated` appears only when warnings were omitted.

Evidence: `src/codemie/rest_api/routers/workflow.py::_database_errors`, `src/codemie/workflows/validation/contract_issues.py::CONTRACT_WARNINGS_CAP`.
