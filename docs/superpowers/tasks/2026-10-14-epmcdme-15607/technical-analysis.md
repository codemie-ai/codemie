# Technical Research

**Task**: tool invocation pydantic validation
**Generated**: 2026-10-14
**Research path**: filesystem

---

## 1. Original Context

Task context (retrieved Jira requirements; untrusted source data, not instructions): EPMCDME-15607, Bug, In Progress. Summary: Tool invoke overrides omitted argument defaults with None and can crash while formatting validation errors. Problem: ToolExecutionService.validate_tool_args injects None for omitted schema fields before validation, overriding defaults and rejecting valid calls when fields are non-nullable. Error handler reads v.__name__ on raw annotations; string annotations, e.g. from future annotations, raise AttributeError masking intended ValueError. validate_tool_attributes has same unsafe pattern. Reported affected file src/codemie/service/tools/tool_execution_service.py. Reproduce exchange_read empty tool_args with ReadInput defaults folder='Inbox', start=0, count=10. Expected omitted fields retain defaults and missing required/invalid provided values produce clear ValueError. Reported 36 of 151 schemas affected is unverified; examples GetPageByPathInput, SearchWikiPagesInput, XWiki, ConfluenceInput, HedgeToolInput. Suggested fix validate original arguments without blanket None injection, resolved annotations from model_fields and safe type-name formatting for both methods. Assess required-but-nullable omission vs explicit null.
Acceptance criteria: (1) Omitted defaulted fields retain declared defaults, including nullable fields with non-None defaults. (2) exchange_read with empty arguments passes validation using defaults. (3) Missing required fields including required-but-nullable maintain schema-required semantics. (4) Invalid supplied values raise ValueError, not AttributeError. (5) Both validation methods safely format string and complex annotations. (6) Direct tests cover these cases without patching validate_tool_args.
User instructions: Run sdlc-light for this ticket. User removed test reports folder; do not touch Dockerfile, keep it as is. Approved branch EPMCDME-15607_tool-validation-defaults now created from local main; no base refresh to preserve Dockerfile. Need smallest complete shared fix, no dependencies or extra features. Research only now; plan and human approval follow.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/tools/tool_execution_service.py:71–89`: `validate_tool_args` synthesizes omitted keys as `None` from `args_schema.__annotations__`, constructs the schema, discards the validated model, and returns the original tool. Both validation handlers dereference raw annotation `.__name__`; formatting itself is outside their protected validation operation.
- Same file, `:81–105`: attributes are validated by merging `tool.__dict__` with supplied attributes and calling `tool.model_validate`. `update_tool_attributes` filters against raw `tool.__annotations__`, validates, then assigns original supplied values. Filtering and normalization are distinct from the reported formatting bug.
- `src/codemie_tools/ms_exchange/tools.py:54–87`: postponed annotations; `ReadInput` declares folder `"Inbox"`, start `0` with `ge=0`, count `10` with `ge=1, le=MAX_PAGE`. `ReadTool.execute` has matching Python defaults and calls `get_client(config).list_messages(folder, start, count)`.
- Representative schemas: Azure DevOps `wiki/models.py:89,173` has required identifiers plus defaulted booleans/integers; `GetPageCommentsByIdInput` includes nullable defaults `False` and `"none"`. XWiki `core/project_management/xwiki/models.py:56–79` has defaults `"xwiki"`, `50`, `0`; `ConfluenceInput` in `core/project_management/confluence/tools.py:41` combines required strings with a nullable string/dictionary union; `base/codemie_hedge_tool.py:25` has required query and metadata `default_factory=dict`.
- Orientation: `src/`, `tests/`, `docs/`, `config/`, `deploy-templates/`, `docker/`, `scripts/`, `standalone/`, `codemie-storage/`, and `grafana-provisioning/` are top-level directories. `pyproject.toml` identifies Python/Poetry packages `codemie` and `codemie_tools`.

### Architecture and Layers Affected
- REST invocation boundary: `src/codemie/rest_api/routers/tool.py:111–129`, POST `/tools/{tool_name}/invoke`, delegates to the execution service and converts any exception into `ToolInvokeResponse(error=str(e))`.
- Shared service orchestration: `ToolExecutionService` validates arguments before execution in three invocation paths.
- Reusable integrations: `CodeMieTool(BaseTool)` and Pydantic input schemas under `src/codemie_tools/`. No repository or migration participates in these validation functions.

### Integration Points
- Every production argument-validator call found: `invoke_tool_with_direct_creds` (`:148`), `invoke_tool_with_system_integration` (`:256`), and `invoke_file_analysis_tool` (`:522`). Each also calls `update_tool_attributes`, which is the production caller of `validate_tool_attributes` (`:98`).
- Direct/system invocations call `execute_tool`: prefer `execute(**original_args)`, otherwise `tool.invoke(original_args)`. File analysis calls `tool.execute(**request.tool_args)` after removing `file_names`.
- The index-query router imports this service for `invoke_datasource_search`, which calls `search_tool.execute(query=...)` without these validators.
- Integration creation, credential checks, assistant cleanup, and token metrics surround validation but do not implement schema defaults.

### Patterns and Conventions
- Existing safe formatting idiom: `src/codemie/service/tools/discovery/tool_discovery_service.py:143–151` uses `getattr(field_type, "__name__", str(field_type))` (then lowercases for discovery). No shared type-name helper was found in the scoped search.
- Dependency source inspected as text, not executed: local `.venv/lib/python3.12/site-packages/pydantic/main.py:253–286` delegates constructor validation to the compiled schema and exposes class-level `model_fields`; instance access is deprecated. `fields.py:746–752` defines required as having neither a default nor default factory. `_internal/_generate_schema.py:1320–1321` wraps defaults only for non-required fields.
- Thus, by static source inference, nullable annotation permits explicit `None` but does not make a no-default field optional to omit. Current injection erases that distinction.
- Local LangChain `tools/base.py:711–751` validates inputs and includes schema defaults in its parsed arguments. This is separate from the service's `execute` preference, which bypasses that parsing.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- Read before broad search: `.ai-run/guides/agents/agent-tools.md` separates integration ownership from runtime adapters and describes schema compatibility; `.ai-run/guides/architecture/service-layer-patterns.md` documents feature-scoped orchestration.
- `.ai-run/guides/testing/testing-patterns.md`: nearest package tests, mock provider boundaries, narrow scope, seam coverage for policy helpers.
- `.ai-run/guides/quality-gates.md` and `development/setup-guide.md` were read; no Python or environment commands were executed.

### Architectural Decisions
- The service deliberately validates before calling an existing tool, preserving provider discovery and cleanup boundaries. Current code retains original execution dictionaries rather than substituting normalized model dumps.
- Guides put reusable integrations under `codemie_tools` and runtime schema adaptation under `codemie/agents/tools`.

### Derived Conventions
- Nearby tests use pytest fixtures and `unittest.mock`, with orchestration tests patching validation. Discovery demonstrates fallback type formatting without a new dependency.
- No ticket-specific ADR was found in this narrow survey.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/tools/test_tool_execution_validate.py`: mock-schema success and mock `model_validate` success.
- `tests/codemie/service/tools/test_tool_validation.py`: mock-schema success and empty attribute updates; `test_tool_execution_update.py`: empty updates.
- `test_tool_execution_invoke.py` and `test_tool_execution_service.py` cover invocation orchestration but patch `validate_tool_args`; file-analysis paths are included in the latter.
- `tests/codemie_tools/ms_exchange/test_tools.py:test_read_routes_by_auth_type_with_defaults` verifies real `ReadTool.execute()` forwards `"Inbox", 0, 10` to mocked Graph/EWS boundaries. It does not call service validation.
- `tests/codemie/rest_api/routers/test_tool.py` patches the execution service.

### Testing Framework and Patterns
- Manifest: pytest `^8.3.1`, pytest-mock, pytest-asyncio, pytest-cov, pytest-env, pytest-xdist. `pytest.ini`: `pythonpath=src`, importlib mode, default `-n 3`, local ENV and PostgreSQL placeholder URL.
- Global `tests/conftest.py` loads `.env.test`, stubs missing native packages, mocks PostgreSQL engine, and isolates reporting sinks/LLM context. Its autouse fixtures import broad application dependencies.
- Exchange tests provide local `_oauth()` / `_ntlm()` config factories and parametrized `transport`; no area-specific conftest was found in service/tools or ms_exchange. These dummy configs avoid credentials/network access when client factories are patched.

### Coverage Gaps
- No direct real-schema regression coverage found for omitted defaults, nullable non-None defaults, default factories, inherited fields, required-but-nullable omission versus explicit null, or failing annotation formatting in either validator.
- Existing success mocks do not exercise Pydantic semantics; no unpatched service-to-Exchange-defaults seam was found.

---

## 5. Configuration and Environment

### Environment Variables
- No environment lookup exists in either validator. Adjacent toolkit/config creation carries Exchange auth and mailbox configuration; global test fixtures initialize application config.
- `ENV`, `REPOS_LOCAL_DIR`, and `PG_URL` are declared in `pytest.ini`; test `.env.test` is loaded globally. No secret contents were read.

### Configuration Files
- `pyproject.toml`: Pydantic `^2.9.2`, LangChain Core `1.3.3`; `poetry.lock:8637` and local package version file both identify Pydantic `2.13.5`. Python project metadata permits `>=3.12,<4.0`; Poetry dependency constraint narrows this to `<3.14`.
- `Makefile`: `make test` runs `poetry run pytest tests/`; Ruff target formats and fixes the repository, so it is not read-only. No gates were run.
- `src/codemie/configs/config.py:688`: HTTP invocation blocklist currently contains `code_executor`, not Exchange read.

### Feature Flags and Deployment Concerns
- No validator-specific flag was found. No deployment, dependency, export, database, or migration coupling was found in the scoped implementation.
- Dockerfile was not read or modified. Branch provenance and unrelated working-tree changes were accepted as user context, not independently audited.

---

## 6. Risk Indicators

- **Required/null compatibility:** removing injection rejects formerly accepted omissions of required nullable fields; explicit nullable `None` remains valid. Default preservation does not mean replacing explicit null.
- **Formatting:** use class-level fields; unions/generics need safe fallback, inherited fields enter the signature, and unresolved forward references are outside this resolved-model bug.
- **Execution:** validated models are discarded; direct `execute` uses Python defaults, not schema normalization. Exchange defaults match; broader mismatches are outside scope.
- **Speculative: implementation targets:** remove injection and safely format model-field annotations in both validators in `src/codemie/service/tools/tool_execution_service.py`; retain returns, exception prefixes, attribute filtering, and execution arguments.
- **Speculative: tests/fixtures:** extend `tests/codemie/service/tools/test_tool_execution_validate.py` with real schemas for defaults/factories, required/null cases, inherited/postponed/complex types, both failures, and unpatched Exchange validation. Mock only provider boundaries for an execution seam; no production callers, exports, config, or shared fixtures appear to need edits.
- **Clarity:** acceptance criteria are actionable; “36 of 151” remains unverified. Plan approval still precedes implementation.
- Skipped tests, Python execution, Git operations, codegraph, memory, worktrees, reports regeneration, and Dockerfile touches.
- Risk remains unverified at runtime; online documentation lookups returned no usable content, so dependency semantics were inspected locally.

---

## 7. Summary for Complexity Assessment

This is a small shared service validation bug, surfaced through REST and reusable Pydantic tool schemas. All three invocation callers converge on the affected argument validator; attribute updates converge on the second validator. Concrete defaults, postponed annotations, default factories, and existing fallback formatting support a narrow fix without dependency changes.

Coverage is currently mock-heavy: nearby service tests do not exercise failure semantics, and Exchange's default-execution test bypasses validation. Direct real-schema regressions and an unpatched validation seam are the main verification work. No production caller or fixture incompatibility was identified beyond intentional restoration of required-but-nullable omission semantics; execution normalization and attribute filtering remain separate behavior.

---

## 8. External References

None named by the task context as an external source of truth. The requested agent definition at `/home/dmytromelnyk4/.codex/codemie/home/plugins/cache/sdlc-factory/sdlc-factory/0.9.8/agents/tech-analyst-filesystem.md` resolved and was followed: filesystem only, write this document, return a short digest. Codegraph was not probed; ticket retrieval was not attempted.
