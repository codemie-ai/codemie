# Spec — EPMCDME-14336: Optional Project Description

**Scope:** backend (this repo). FE will consume the API contract described in the last section.

## Problem

`ProjectCreateRequest.description` is required. `ProjectService` rejects blank / whitespace input. Once set, a description cannot be cleared: `application_repository.update_project` only writes description when it is not `None`, and the router masks a null description as `""` in the response. The DB column is already nullable, and list/detail response models already return description as optional — only validation, the update-clear path, and one response masking bug need to change.

## Goals

1. `POST /v1/projects` accepts a request without `description` (omitted, `null`, empty string, or whitespace-only). Server stores `null`.
2. `PATCH /v1/projects/{name}` supports clearing an existing description.
3. Existing clients that always send a non-empty description continue to work.
4. Response models return `description` as `string | null`, never coerced to `""`.
5. 500-character upper bound is unchanged.
6. Missing `name` remains the rejection reason when both name and description are absent.

## Non-goals

Per ticket "Out of Scope": no bulk edit, no auto-generated descriptions, no changes to name/display_name/cost_center validation, no markdown, no search-ranking changes, no new columns in list responses.

## Design

Mirror the existing `clear_display_name` / `clear_cost_center` pattern already present on `ProjectUpdateRequest`. This is the established convention in this codebase — the analysis found the pattern at `routers/projects.py:264–281`, and the service uses corresponding `_resolve_updated_display_name` resolvers.

### Backend changes (5 files)

| File | Change |
|---|---|
| `src/codemie/rest_api/routers/projects.py` | `ProjectCreateRequest.description: Optional[str] = None`; `ProjectCreateResponse.description: Optional[str] = None`; `ProjectUpdateRequest.clear_description: bool = False`; extend `validate_non_empty` to count `clear_description`; add model_validator guard so `description` and `clear_description=True` cannot both be set; remove the `project.description or ""` masking at ~line 1016; simplify the `project.description or payload.description` fallback at ~line 675 (both may legitimately be `None`). |
| `src/codemie/service/project/project_service.py` | `create_shared_project` accepts `description: str \| None = None`; rewrite `_validate_project_description` to strip, return `None` when empty (not raise), keep 500-char check; add `update_project(clear_description: bool = False, ...)` parameter and resolver mirroring `_resolve_updated_display_name`; remove `ERRORS.DESC_REQUIRED` usage in the create path (keep the constant only if referenced elsewhere). |
| `src/codemie/repository/application_repository.py` | `update_project` gains a way to write `None` on explicit clear. Simplest: accept a `clear_description: bool = False` parameter; when true, write `application.description = None` unconditionally. Preserves the "None means leave unchanged" contract for legacy callers. |
| `tests/codemie/service/project/test_project_service.py` | Replace `test_empty_description_returns_400` with tests asserting `None`/`""`/`"   "` all normalize to stored `None`; keep `test_description_too_long_returns_400`. |
| `tests/codemie/service/project/test_project_service_delete_update.py` and `tests/codemie/rest_api/routers/test_projects_router.py` | Add coverage for the update-clear path, the mutual-exclusion 422, and the POST-without-description case. Adjust the existing `updated_app.description = ""` router tests that assert `""` in the response — they should now assert `None`. |

### Clear-description semantics — decision

Two paths are workable; we pick the one that matches the existing codebase pattern **and** is friendly to FE:

- **Chosen:** `clear_description: bool = False` companion flag on `ProjectUpdateRequest`. Consistent with `clear_display_name` / `clear_cost_center`.
- **Additionally:** treat `description: ""` and `description: "   "` on PATCH as an implicit clear (normalized to `None` server-side), so FE can also just send an empty string without a companion flag. This mirrors what the create path already does after this change and is what the ticket AC says ("whitespace-only saved as no description").
- **Explicit JSON `null` for `description`** on PATCH remains Pydantic's "field omitted" semantic — no field change, description untouched. To clear explicitly and unambiguously, FE sends `clear_description: true`.

### Rejected input

- `description` string longer than 500 chars → 422 (Pydantic max_length) or 400 (service check) — whichever currently fires; keep current behavior.
- PATCH with both `description="text"` and `clear_description=true` → 422 via model_validator.
- POST/PATCH with no `name` and no `description` → still 400 with the missing-name reason (unchanged).

## Acceptance Criteria (test-mapped)

| AC | Test |
|---|---|
| Create without description succeeds | `test_create_project_without_description_succeeds` |
| Whitespace-only description on create stored as null | `test_create_project_whitespace_description_stored_as_null` |
| Empty string on create stored as null | `test_create_project_empty_description_stored_as_null` |
| >500 chars still rejected | `test_description_too_long_returns_400` (existing) |
| Missing name still rejects with name reason | `test_missing_name_rejected_when_description_absent` |
| PATCH with `clear_description=true` clears the field | `test_update_project_clear_description_removes_value` |
| PATCH with only `clear_description=true` passes `validate_non_empty` | `test_update_project_clear_description_alone_valid` |
| PATCH with `description` and `clear_description=true` → 422 | `test_update_project_description_and_clear_mutually_exclusive` |
| PATCH omitting description leaves it unchanged | `test_update_project_omitted_description_unchanged` (add/verify) |
| Response returns `null`, never `""`, when description is unset | `test_project_response_null_description_not_coerced_to_empty` |
| Existing clients sending a description still work | Existing create/update tests unchanged |

---

## API Contract for Frontend

This is the deliverable for the FE team. Once merged, it reflects the live API.

### POST `/v1/projects`

Request (all fields shown; only `name` is required):

```json
{
  "name": "my-project",
  "display_name": "My Project",
  "description": "Optional text",
  "cost_center_id": null
}
```

Acceptable variants for `description` on create — all produce `null` server-side:
- omit the field
- `"description": null`
- `"description": ""`
- `"description": "   "` (whitespace-only)

Rejected:
- `description` longer than 500 chars → 400/422.

Response schema change: `description` is now `string | null` (was `string`).

### PATCH `/v1/projects/{projectName}`

Update behavior for `description`:

| FE sends | Effect |
|---|---|
| `description` omitted, `clear_description` omitted/false | Description unchanged |
| `"description": "New text"` | Description set to `"New text"` |
| `"description": ""` or `"description": "   "` | Description cleared (stored as `null`) |
| `"clear_description": true` (no `description`) | Description cleared (stored as `null`) |
| `"description": "text"` + `"clear_description": true` | **422** (mutually exclusive) |
| `"description": null` (explicit JSON null, no clear flag) | Treated as omitted → description unchanged. Use `clear_description: true` (or empty string) to clear explicitly. |

**Recommendation for FE:** use `clear_description: true` when the user explicitly wipes the description field, since it makes intent unambiguous in logs and requests. Empty string also works and is simpler if the form already binds `""` to an empty input.

Other update fields are unchanged.

Response: `description` returned as `string | null`. The previous `""` coercion is removed.

### OpenAPI

FastAPI regenerates `/openapi.json` from the Pydantic models at startup; there is no committed schema file. After merge, `description` on the create request and both response models will show `anyOf: [{type: string}, {type: "null"}]`, and `clear_description: boolean` will appear on the update request.

### Migration notes for FE

- No breaking change for FE code that always sent a non-empty description on create.
- FE must add null handling wherever it reads `project.description` (previously guaranteed non-null on create response).
- FE must add UI + wire-up for the "clear description" action on Edit — either bind the empty input to `""` in the request, or send `clear_description: true`.
- Details page must render a "no value" placeholder when `description` is `null` (as ticket AC requires).
