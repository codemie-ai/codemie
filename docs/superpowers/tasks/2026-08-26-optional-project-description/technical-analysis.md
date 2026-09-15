# Technical Research

**Task**: project description optional create edit
**Generated**: 2026-08-26T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14336 — Make the project description field optional in Create Project and Edit Project flows.

Ticket summary:
- `ProjectCreateRequest.description` is currently required (typed as `str`); backend validation in `ProjectService.create_shared_project` rejects blank / whitespace-only values.
- The `applications.description` DB column is already nullable — no migration needed.
- `ProjectListItem` and `ProjectDetailResponse` already return description as Optional.
- The web UI `ProjectModal` marks Description required in both create and edit.
- There is no way to clear a description once set — sending an empty value hits the blank check.
- Whitespace-only descriptions must be treated as "no description", not rejected.
- The 500-character upper limit stays.

Required behavior (backend scope only — FE is a separate repo):
1. Create project without description → succeeds.
2. Edit project → clearing description (explicit empty/null) removes it.
3. Edit project → adding a description to a project that had none works.
4. Edit project → changing another field leaves description untouched.
5. Search still returns projects without descriptions when name/display name match.
6. Whitespace-only saved as None; >500 chars still rejected; missing name still the rejection reason when both are absent.
7. Existing clients that always send a description continue to work.

IMPORTANT: The user also wants a FE-facing API contract deliverable, so the analysis must clearly enumerate the request/response schemas (before/after), the update-clear semantics (how does FE signal "clear description" — explicit `null`, empty string, both?), and any OpenAPI/schema files consumers rely on.

---

## 2. Codebase Findings

### Existing Implementations

All relevant code lives in two source files and two test files:

- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/rest_api/routers/projects.py` — Pydantic request/response models and FastAPI route handlers.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/service/project/project_service.py` — `ProjectService` with all validation logic.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/repository/application_repository.py` — `ApplicationRepository.update_project` that writes to the DB.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/core/models.py` — `Application` SQLModel (DB table `applications`).
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/project/test_project_service.py` — Unit tests for create.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/service/project/test_project_service_delete_update.py` — Unit tests for update.
- `/Users/yevhen_slyva/codemie-dev/codemie/tests/codemie/rest_api/routers/test_projects_router.py` — Router-level integration tests.

### Architecture and Layers Affected

| Layer | Component | File |
|---|---|---|
| API / Router | `ProjectCreateRequest`, `ProjectUpdateRequest`, route handlers `create_project`, `update_project` | `routers/projects.py` |
| Service | `ProjectService.create_shared_project`, `ProjectService.update_project`, `ProjectService._validate_project_description` | `service/project/project_service.py` |
| Repository | `ApplicationRepository.update_project` | `repository/application_repository.py` |
| DB / Persistence | `Application.description` (already `Optional[str]`, nullable) | `core/models.py` line 388 |

### Integration Points

- **Search**: `application_repository` search already queries `Application.description.ilike(...)` — projects without descriptions will simply not match on the description clause, which is correct.
- **`ProjectCreateResponse`**: `description: str` (non-optional) on line 244 of `routers/projects.py` — will need to change to `Optional[str]`.
- **`ProjectUpdateResponse`**: `description: Optional[str]` (already optional) — no change needed.
- **`ProjectListItem.description`**: already `Optional[str]` — no change needed.
- **`ProjectDetailResponse.description`**: already `Optional[str]` — no change needed.

### Patterns and Conventions

**Clear-field pattern (existing):** `display_name` and `cost_center` already implement explicit-clear via companion boolean flags:
- `ProjectUpdateRequest.clear_display_name: bool = False` — when `True`, sets `display_name` to `None` regardless of `display_name` value.
- `ProjectUpdateRequest.clear_cost_center: bool = False` — same pattern.
- The `model_validator(mode="after")` on `ProjectUpdateRequest` (lines 264–281) enforces that a caller cannot pass both a value and the clear flag simultaneously.

The description clear-field must follow this **same pattern**: add `clear_description: bool = False` to `ProjectUpdateRequest`, or alternatively accept explicit `null` JSON value. See Section 3 for the design trade-off.

**Partial-update semantics (update_project):** The service passes `description` as `str | None`, and the repository only writes it when it is not `None` (line 708: `if description is not None: application.description = description`). This means `None` currently means "leave unchanged". To support clearing, the caller must distinguish between "not provided" and "explicitly null/cleared".

---

## 3. Documentation Findings

### Guides and Architecture Docs

Relevant guides found in `.ai-run/guides/`:
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router conventions (models, validators).
- `.ai-run/guides/api/endpoint-conventions.md` — Route and response shape conventions.
- `.ai-run/guides/architecture/service-layer-patterns.md` — Service validation patterns.
- `.ai-run/guides/data/repository-patterns.md` — Repository update patterns.

### Architectural Decisions

- The codebase consistently uses **companion boolean clear-flags** (`clear_display_name`, `clear_cost_center`) rather than relying on `null` JSON semantics for clearing optional string fields on PATCH. This is the established pattern.
- The `model_validator(mode="after")` on `ProjectUpdateRequest` acts as a structural guard preventing mutually exclusive inputs.
- The `validate_non_empty` validator also guards against completely empty PATCH bodies — `clear_description` must be counted as a provided field in that guard.

### Derived Conventions

- Pydantic models in this file do not use `model_config = ConfigDict(...)` with `exclude_unset`. They use explicit `Optional` + boolean flags instead of `exclude_unset` partial-update semantics.
- All validation that would return HTTP 4xx lives in `ProjectService`, not in the Pydantic model itself (no `field_validator` on the model).
- String trimming for optional fields: `_validate_display_name` strips and returns `None` when the result is empty (`return stripped` where stripped may be `""`). For description, the equivalent would be strip → return `None` when empty.

---

## 4. Testing Landscape

### Existing Coverage

**`test_project_service.py`** (create path):
- Line 384: `@pytest.mark.parametrize("description", ["", "   "])` → `test_empty_description_returns_400` — currently asserts 400 with "Project description is required". After the change this test must be updated or replaced.
- Line 396: `test_description_too_long_returns_400` — still valid after the change.
- All other `create_shared_project` tests pass `description="desc"` or `description="Analytics pipeline"`.

**`test_project_service_delete_update.py`** (update path):
- Line 508: `test_update_description_calls_update_project` — passes `description="new desc"`, asserts the repository is called correctly.
- Many update tests pass `description=None` to verify other fields update independently (description left unchanged).

**`test_projects_router.py`** (router/integration):
- Multiple tests construct `ProjectCreateRequest(name="data-pipeline", description="Analytics pipeline")` — these remain valid.
- Line 479: passes `"description": None` in JSON — currently this would fail Pydantic validation because `description: str` is not optional. This represents an existing gap (or the test is currently failing/skipped).
- Line 1433–1465: `test_update_description_returns_updated_response` — valid, tests PATCH with description.
- Lines 1484, 1524: `updated_app.description = ""` — tests that map empty string from DB, currently router masks it as `project.description or ""`.

### Testing Framework and Patterns

- **Framework**: `pytest` with `unittest.mock.patch` for dependency injection.
- **Pattern**: All service tests patch repository, session, and infrastructure at the call site with `@patch(...)` decorators. No fixtures share state between test classes.
- **No integration DB tests** observed in scope — all service tests use mocks.

### Coverage Gaps

After the change, the following new test cases are needed:

1. **`test_project_service.py`**: `create_shared_project` with `description=None` → succeeds, `Application.description` is `None`.
2. **`test_project_service.py`**: `create_shared_project` with `description=""` → description saved as `None` (not rejected).
3. **`test_project_service.py`**: `create_shared_project` with `description="   "` → description saved as `None`.
4. **`test_project_service_delete_update.py`**: `update_project` with `clear_description=True` → `ApplicationRepository.update_project` called with `description=None`; repository must persist `None`.
5. **`test_project_service_delete_update.py`**: `update_project` with `description=None, clear_description=False` → description field unchanged (current behavior preserved).
6. **`test_projects_router.py`**: `POST /v1/projects` without `description` field → 201, `description` is `None` or absent.
7. **`test_projects_router.py`**: `PATCH /projects/{name}` with `clear_description=True` → 200, response `description` is `None`.
8. **`model_validator`**: PATCH with both `description="x"` and `clear_description=True` → 422.

---

## 5. Configuration and Environment

### Environment Variables

No environment variables gate the description field. `ENABLE_USER_MANAGEMENT` gates the endpoints but is unrelated to field optionality.

### Configuration Files

No config files involved. No feature flags.

### Feature Flags and Deployment Concerns

No deployment concerns — the DB column `applications.description` is already `nullable` (`Optional[str] = SQLField(default=None, max_length=500)`). No migration is required.

---

## 6. Risk Indicators

- **Breaking change — `ProjectCreateRequest.description`**: Changing `description: str` to `Optional[str] = None` is backward-compatible for existing clients that always send a value. It is not backward-compatible for generated clients that rely on the OpenAPI schema marking `description` as required (they may emit warnings but will continue to function).

- **`ProjectCreateResponse.description: str` (non-optional)** at `routers/projects.py` line 244: this must change to `Optional[str]` simultaneously. The router currently uses `project.description or payload.description` (line 675) — after the change both can be `None`, and the fallback logic must be updated.

- **`update_project` router response masking** at line 1016: `description=project.description or ""` — this coerces `None` to `""`, which is inconsistent with the ticket requirement (cleared description should return `null`/`None` in the response, not `""`). This must be removed.

- **Repository `update_project` cannot clear description currently**: The guard `if description is not None: application.description = description` (line 708 of `application_repository.py`) means passing `description=None` to the repository leaves the column unchanged. To support clearing, either (a) add `clear_description: bool` parameter to the repository method, or (b) use a sentinel value. The clean option is to follow the `display_name` pattern: always write `application.description = description` when the caller signals an explicit clear, and keep the `if description is not None` guard for the "no-change" case.

- **`ERRORS.DESC_REQUIRED` string** in `ProjectService` at line 82: will become dead code for the create path. If kept for future use (e.g., if a business rule enforces description on some project type), it should be documented; otherwise it should be removed to avoid confusion.

- **Existing test `test_empty_description_returns_400`** (parametrized on `""` and `"   "`) will fail after the change and must be replaced with tests asserting `None` is stored.

- **No codegraph indexing**: research performed via filesystem only — repo may not be indexed in codegraph.

- **`ProjectUpdateRequest.validate_non_empty`** (lines 264–281): currently does not include `clear_description` in its "at least one field provided" check. If `clear_description=True` is the only field sent, the validator would incorrectly reject the request. Must add `not self.clear_description` to the existing conjunction.

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers: API/Router (Pydantic model changes and response masking fix), Service (validation logic rewrite and new clear-description signal), and Repository (conditional write guard change). The DB layer requires no migration. The file change surface is small: two source files (`routers/projects.py` and `service/project/project_service.py`) plus the repository (`application_repository.py`) for the clear-description write path, and two test files. Total estimated changed files: 5.

The task follows a fully established pattern — the `clear_display_name` / `clear_cost_center` flag pair already exists on `ProjectUpdateRequest` and the service already implements the corresponding resolver methods. Description clearing is a direct application of that same pattern. No new architectural concept is introduced. The only slight novelty is that description on create becomes optional (currently required), which requires updating `_validate_project_description` to accept `None` or empty and normalize to `None` rather than rejecting.

Test coverage for the affected area is moderate. The create-with-empty-description tests exist and are currently asserting the wrong outcome (400), so they must be converted rather than supplemented. The update path has light description coverage. The router layer has a response-masking bug (`or ""`) that must be fixed as part of this ticket. Key risk factors are: (1) the `ProjectCreateResponse.description: str` non-optional type that must be relaxed in the same commit, (2) the `validate_non_empty` guard in `ProjectUpdateRequest` that must explicitly account for `clear_description=True`, and (3) the repository guard that prevents `None` from being written — clearing description requires a deliberate bypass of that guard.

---

## Appendix: API Contract Before/After (FE-facing deliverable)

### POST /v1/projects — Request Schema

**Before:**
```json
{
  "name": "my-project",          // required
  "display_name": "My Project",  // optional
  "description": "Some text",    // required (str, non-null, non-empty)
  "cost_center_id": null         // optional
}
```

**After:**
```json
{
  "name": "my-project",          // required
  "display_name": "My Project",  // optional
  "description": "Some text",    // optional (str | null | absent — all accepted)
  "cost_center_id": null         // optional
}
```

Whitespace-only strings (e.g. `"   "`) are normalized to `null` server-side.

### POST /v1/projects — Response Schema

**Before:** `description: string` (required, always present)
**After:** `description: string | null` (optional)

### PATCH /v1/projects/{projectName} — Request Schema

**Before:**
```json
{
  "description": "New text"  // sets description; null means "leave unchanged"
}
```

**After:**
```json
{
  "description": "New text",     // sets or updates description
  "clear_description": true      // clears description (sets to null); mutually exclusive with description
}
```

Semantics:
- `description` omitted, `clear_description` omitted → description unchanged.
- `description: "text"`, `clear_description` omitted/false → description set to `"text"`.
- `description` omitted, `clear_description: true` → description set to `null`.
- `description: "text"`, `clear_description: true` → **422 Unprocessable Entity** (mutually exclusive).
- `description: ""` or `description: "   "` → normalized to `null` (same as clear).
- `description: null` in JSON → treated as omitted (Pydantic `Optional[str] = None`); to clear explicitly, use `clear_description: true`.

### PATCH /v1/projects/{projectName} — Response Schema

Currently uses `ProjectCreateResponse`. After the fix, `description` will be `string | null` instead of `string`.

The `description or ""` coercion at line 1016 of `routers/projects.py` must be removed.

### OpenAPI schema generation

FastAPI generates the OpenAPI spec dynamically from the Pydantic models at startup. There is no separate schema file committed to the repository. Consumers (FE, generated SDK clients) obtain the spec from `/openapi.json` at runtime. After the Pydantic model changes, the generated spec will automatically reflect `description` as optional with `anyOf: [{type: string}, {type: null}]` in both the create request and both response models.
