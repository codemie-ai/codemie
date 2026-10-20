# Endpoint Conventions

## Request And Response Models

Keep API schemas near the REST API layer and use Pydantic/SQLModel models consistently.

| Avoid | Prefer |
|---|---|
| Unstructured request dictionaries | Typed request models |
| Returning persistence objects without considering API shape | Use explicit response models when behavior is user-facing |
| Defining a schema in a router module that a service or another module imports | Put it in `src/codemie/rest_api/models/<feature>.py`; router and service both import it from there |

Evidence: REST API models live under `src/codemie/rest_api/models/`; budget schemas are imported from `codemie.rest_api.models.budget` by both `src/codemie/rest_api/routers/budget_router.py:25` and `src/codemie/service/budget/budget_service.py:51`.

## Router Size

Use handlers or services when endpoint logic grows beyond request validation and delegation.

| Avoid | Prefer |
|---|---|
| Large endpoint functions with orchestration and persistence | Router validates, service orchestrates |
| Copying helper logic across routers, or importing another router module's private `_helper` | Router helpers shared across modules in `src/codemie/rest_api/routers/utils.py`; request-context utilities in `src/codemie/rest_api/utils/`; business logic in the service layer |
| Putting a helper used by one router package into a route submodule | A package-private underscore module with no `router`: `_common.py`, `_shared.py`, `_<concern>_support.py` |

Evidence: assistant request handling uses `src/codemie/rest_api/handlers/assistant_handlers.py` for non-trivial endpoint behavior; `get_assistant_by_id_or_raise` at `src/codemie/rest_api/routers/utils.py:103` serves `src/codemie/rest_api/routers/ide.py:33` and the assistant package; `_authorize_project_access` at `src/codemie/rest_api/routers/projects/_common.py:47`.

## Update Semantics (PUT)

PUT routes are full-replace, not patch. Re-sending the same complete body leaves the stored entity unchanged, apart from `update_date`, `updated_date` and `last_reindex_date`. Pinned by `tests/codemie/rest_api/routers/test_idempotent_write.py` and `test_index.py`; the status codes and response schemas are pinned by `test_contract_baseline.py`.

| Behaviour | Where | Notes |
|---|---|---|
| Knowledge-base and code-repo datasource PUTs are keyed by `(project_name, name)`; the provider PUT is keyed by id (`PUT /index/provider/{id}`) | `routers/index/` (`kb_*.py`, `code_repositories.py`, `providers.py`) | Name is unique per project. |
| Datasource POST create routes that call `_index_unique_check` reject an existing name with 409 | `routers/index/_common.py` (`_index_unique_check`) and the `kb_*.py` create routes | They never update. |
| Omitted optional fields on a partial body are overwritten by request defaults | svn PUT | `prompt`, `files_filter`, `project_space_visible`, `docs_generation`. |
| Omitted `files_filter` sets the top-level `IndexInfo.files_filter` to None | sharepoint PUT | The `sharepoint.files_filter` copy the processor reads is kept. |
| Omitted `description` or `project_space_visible` returns 500 | provider PUT | Mapped fields are set unconditionally. |
| `error` is reset to False on a metadata edit | google, git_faq, file PUT | Other routes that call `update_index` pass `reset_error=False`. |
| Assistant context entries whose datasource is missing or has a different type are dropped silently | assistant PUT (`drop_missing_context`) | No error is returned. |
| A masked sensitive prompt variable is restored from the stored value | assistant PUT | `_restore_existing_encrypted_values`. |
| Workflow `yaml_config` is stored exactly as sent (comments, key order and defaults preserved); only `model` values are rewritten to deployment ids, and only when a model id changed | workflow PUT (`_rewrite_yaml_config_from_objects` in `routers/workflow.py`) | Re-sending the same body is a fixed point. |
| `sanitize_for_save` only validates MCP servers (returns them unmutated, raises on violation) | assistant save | |
| Ciphertext may change on resubmission of a plaintext secret under non-deterministic encryption (KMS, Vault, Azure) | settings, provider PUT | Masked values are not re-encrypted. Plain and Base64 are deterministic. |

## Create Semantics (POST)

Assistants and workflows create a new entity on every POST with no id, even when an identical one exists. Skills and categories reject a duplicate name with 409; project and user settings reject a duplicate alias with 422. Pinned by `tests/codemie/rest_api/routers/test_create_always_new.py`.
