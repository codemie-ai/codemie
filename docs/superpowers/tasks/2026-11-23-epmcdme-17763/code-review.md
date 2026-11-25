---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 2179a38f36779c47e7d134a2228116d3ebef1fc2
---

# Code review — 2026-11-23-epmcdme-17763

**Decision:** approve (confidence high).
**Base:** `f97c9a6dc50c6b86f73b7b63b0d857dffe5db947` (origin/main) · **Reviewed head:** `2179a38f36779c47e7d134a2228116d3ebef1fc2` · **Ticket:** EPMCDME-17763 · **MR:** !4932

The commit that adds this directory comes after the reviewed head and changes no code.

## Scope

Branch commits:

- `2179a38f3` EPMCDME-17763: Pass provider argument description and enum to tool args schema

Diff against the base: 2 files, +108/-6.

- `src/codemie/service/provider/provider_tool_factory.py`: `_generate_args_schema()` builds each field with `pydantic.Field(default=..., description=...)`; `String` / `List` arguments with `enum` get `Annotated[str, WithJsonSchema({"type": "string", "enum": [...]})]` (as the item type for `List`).
- `tests/codemie/service/provider/test_provider_tool_factory.py`: 7 tests for description, enum and the unchanged default schema.

## Acceptance

| Item | Status | Evidence |
|---|---|---|
| Every argument with a non-empty `description` carries it into the JSON schema | pass | `Field(description=param_config.description or None)`; `test_args_schema_carries_description`, `test_args_schema_omits_empty_description` |
| A `String` argument with `enum` is declared as `{"type": "string", "enum": [...]}` | pass | `test_args_schema_declares_enum_for_string` (required and optional) |
| A `List` argument with `enum` is declared as an array whose items have that enum | pass | `test_args_schema_declares_enum_for_list_items` |
| Required / optional handling unchanged; arguments without description / enum produce the same schema as before | pass | `test_args_schema_without_description_and_enum_is_unchanged`; the existing `test_build_tool_class_args_schema` (annotations `Optional[str]` / `int`) still passes |

## Behaviour check

Checked on the reviewed head with real `ProviderToolkit.Tool` / `ProviderToolArgument` models:

| Case | Result |
|---|---|
| Tool declaration after `langchain_core.utils.function_calling.convert_to_openai_tool` | `description` and `enum` present (before the change: type only) |
| `enum` value outside the list | accepted by `ArgsSchema` and passed to the provider: `WithJsonSchema` changes only the JSON schema, the provider stays the validating side (`test_args_schema_enum_is_not_enforced_on_validation`) |
| Empty `enum: []` | ignored, plain string schema |
| `Text` argument with `enum` | gets the enum: `Text` maps to `str` in `ARG_SCHEMA_TYPE_MAPPING` |
| `Number` argument with `enum` | enum ignored, as before (`test_args_schema_ignores_enum_for_non_string_types`) |
| Field `title` | kept for enum fields |
| Optional argument not supplied | still `None`, as before |

## Standards

- Commit format: pass, `EPMCDME-####: Description`.
- Code quality: pass. One method, no new module; the type branch is limited to `str` / `list[str]`.
- Security: pass. No logging, no change to the invocation request; only the schema exposed to the model changes.

## Findings

None blocking. Note, no change requested: `ARG_SCHEMA_TYPE_MAPPING` still has no entry for `Boolean` and `Integer` (both fall back to `str`); out of scope, recorded in the ticket.

## Verification

- `ruff check`, `ruff format --check` on the changed files: exit 0.
- `pytest tests/codemie/service/provider/`: 96 passed; the 3 description / enum tests fail on the base.
- `poetry run pytest tests/ --ignore=tests/enterprise/` (standard install, no extras): 36800 passed, 796 skipped, 5 xfailed, 0 failed.
- `codemie-test-harness --sanity-api` against this branch's server: 187 passed, 3 skipped, 4 rerun (LLM-dependent workflow tests, passed on retry).
