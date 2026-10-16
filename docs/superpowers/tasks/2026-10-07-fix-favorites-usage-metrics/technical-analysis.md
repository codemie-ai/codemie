# Technical Research

**Task**: favorites preferences assistants
**Generated**: 2026-10-07T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Expose unique_users_count and clone_count on GET /v1/preferences/{user_id}/favorites/assistants so the UI (codemie-ui) doesn't need per-item backfill calls to fetch usage metrics for favorited assistants. FavoriteItem (src/codemie/rest_api/models/user_preferences.py) doesn't declare these two fields, and get_favorite_assistants (src/codemie/service/user_preferences_service.py, around lines 124-145) never copies a.unique_users_count / a.clone_count onto the item, even though the Assistant row already has both columns loaded (select(Assistant)...). Fix: add both fields to the FavoriteItem model, and set them from the Assistant row in the service method. No new query or migration needed. Reference ticket: EPMCDME-15284.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/user_preferences.py:54-76` — `FavoriteItem(BaseModel)`. Current field list (verbatim):
  `id, icon_url, name, description, slug, project, type, is_global, shared, visibility, created_by, is_favorited, is_pinned, is_liked, is_disliked, unique_likes_count: int = 0, unique_dislikes_count: int = 0, assistants_count: int = 0, user_abilities, categories`.
  Neither `unique_users_count` nor `clone_count` is present. This is a shared DTO used by all three favorites list endpoints (assistants, skills, workflows), not an assistant-only model.
- `src/codemie/service/user_preferences_service.py:78-148` — `UserPreferencesService.get_favorite_assistants`. Confirmed current body:
  - `query = select(Assistant).where(Assistant.id.in_(favorite_ids))` (line 98) — selects full `Assistant` rows (no `.options(load_only(...))` or column-level projection), so every column on the table, including `unique_users_count` and `clone_count`, is already loaded on each `a` in `rows`.
  - The `FavoriteItem(...)` construction (lines 125-144) copies `unique_likes_count=a.unique_likes_count or 0` and `unique_dislikes_count=a.unique_dislikes_count or 0` but has no line for `unique_users_count` or `clone_count`. This confirms the bug exactly as described — the data is loaded but dropped on the floor when building the response DTO.
- `src/codemie/rest_api/models/assistant.py:703-706` — on `AssistantBase` (which `Assistant` extends, see below), the four counter columns are declared together:
  ```
  unique_users_count: Optional[int] = SQLField(default=0, index=False)
  unique_likes_count: Optional[int] = SQLField(default=0, index=False)
  unique_dislikes_count: Optional[int] = SQLField(default=0, index=False)
  clone_count: Optional[int] = SQLField(default=0, index=False)
  ```
  `class Assistant(BaseModelWithSQLSupport, AssistantBase, table=True)` at line 1076 is the SQLModel table class imported into the service (`from codemie.rest_api.models.assistant import Assistant`). Both target columns exist on the table and are `Optional[int]` with DB default `0` — confirming the task's claim that no migration is needed.
  Separately, `AssistantListResponse` (lines 583-601, a different, unrelated response DTO used elsewhere) already exposes all four fields (`unique_users_count`, `unique_likes_count`, `unique_dislikes_count`, `clone_count`) as `Optional[int] = None`, establishing a naming precedent for how these fields are typically surfaced on API DTOs.
- `clone_count` is written by `AssistantRepository.update_clone_count` (`src/codemie/service/assistant/assistant_repository.py`), invoked from `_update_source_clone_count` in `src/codemie/rest_api/routers/assistant.py:707-721` when an assistant is cloned.
- `unique_users_count` is written via the assistant interaction tracking path (`src/codemie/service/assistant/assistant_user_interaction_service.py`, `src/codemie/repository/assistants/assistant_user_interaction_repository.py`).
- `src/codemie/service/user_preferences_service.py:150-273` — the other two sibling methods, `get_favorite_skills` and `get_favorite_workflows`, build `FavoriteItem` instances for `Skill` and `WorkflowConfig` rows respectively. Neither `Skill` nor `WorkflowConfig` has `unique_users_count`/`clone_count` columns (not checked exhaustively, but `unique_likes_count`/`unique_dislikes_count` are the only counters they currently populate), so adding the two new fields to the shared `FavoriteItem` model only affects the assistants branch; the other two branches would simply leave the new fields at their default (`0`) unless also updated.

### Architecture and Layers Affected

- **API/router layer**: `src/codemie/rest_api/routers/user_preferences_router.py` — `get_favorite_assistants` (lines 73-108) calls the service and wraps the result in `FavoritesListResponse`. No change needed here; it passes `result.data` (list of `FavoriteItem`) straight through.
- **Models/DTO layer**: `src/codemie/rest_api/models/user_preferences.py` — `FavoriteItem` is the single response-shaping Pydantic model shared across assistants/skills/workflows favorites endpoints.
- **Service layer**: `src/codemie/service/user_preferences_service.py` — `UserPreferencesService.get_favorite_assistants` is the only place that needs new field-mapping lines.
- **No repository or migration layer changes**: `user_preferences_repository` (`src/codemie/repository/user_preferences_repository.py`) only manages the `UserPreferences` profile row (favorites id lists, pinned assistants) — it is not involved in building `FavoriteItem` and does not need changes.

### Integration Points

- Internal: `Assistant` SQLModel table (`src/codemie/rest_api/models/assistant.py`) → `UserPreferencesService.get_favorite_assistants` → `FavoriteItem` → `FavoritesListResponse` → FastAPI router → UI (`codemie-ui`).
- No external service/SDK calls are part of this fix; `unique_users_count`/`clone_count` are plain PostgreSQL integer columns already populated by existing write paths (clone tracking in `assistant.py` router, interaction tracking in `assistant_user_interaction_service.py`).
- The task_context states the UI currently does "per-item backfill calls" to fetch these metrics; no such backfill call site was located inside this backend repo (it would live in `codemie-ui`, a separate codebase not inspected in this filesystem-only pass).

### Patterns and Conventions

- All three `get_favorite_*` methods follow an identical structure: look up favorite IDs from the profile → build a `select()` query with optional filters → count + paginate → build a list of `FavoriteItem(...)` via list comprehension, defaulting nullable counters with `... or 0` (e.g. `unique_likes_count=a.unique_likes_count or 0`). The fix should follow this exact `or 0` defaulting idiom for the two new fields, consistent with `unique_likes_count`/`unique_dislikes_count` on the same line block (lines 140-141).
- `FavoriteItem` fields are plain `int = 0` (not `Optional[int]`), unlike the `Assistant.unique_users_count`/`clone_count` columns which are `Optional[int]` — the service already bridges this with `or 0` for the existing counters, and the same bridging will be needed for the two new fields since the underlying columns are nullable `Optional[int]`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` is present with categories: `agents`, `api`, `architecture`, `data`, `development`, `integration`, `security`, `standards`, `testing`, `workflows`, plus `project.md` and `quality-gates.md`. None of these were read in depth for this narrowly-scoped filesystem pass (task was to verify current code shape, not derive new conventions); `.ai-run/guides/api/rest-api-patterns.md` and `.ai-run/guides/architecture/service-layer-patterns.md` are the P0/P1 guides AGENTS.md routes "API" and "Workflows"-adjacent service-layer tasks to, and would be the next guides to consult before implementing.
- `docs/superpowers/tasks/2026-07-27-marketplace-clone-counter-backend/` contains a prior task's `technical-analysis.md`, `spec.md`, `plan.md`, and `actual-complexity.json` for the original introduction of `clone_count` tracking (event-table architecture: `assistant_clone_event` log table + cached `clone_count` column, mirroring the likes/dislikes pattern). That prior task's `actual-complexity.json` notes risk factors found only during QA ("index-name collision discovered and fixed post-implementation", "source_assistant_id leak into update mapping fixed post-implementation") — relevant history for the `clone_count` column's reliability, though not directly applicable to this read-only exposure fix.

### Architectural Decisions

- No inline `NOTE:`/`HACK:`/`ADR:`/`DECISION:` markers were found near `FavoriteItem` or `get_favorite_assistants`. The only inline comment of note is on `FavoriteItem.slug`/`project` (lines 59-60): "Let the UI build human-readable /assistants/{project}/{slug} links (assistant favorites only; stays None for workflows/skills)" — establishing that some `FavoriteItem` fields are intentionally assistant-only and left `None`/default for the other two favorite kinds, which is the same shape the fix would take for `unique_users_count`/`clone_count`.

### Derived Conventions

- New counter fields on `FavoriteItem` should follow the existing `unique_likes_count`/`unique_dislikes_count` precedent: declare as `int = 0` on the model, and populate via `a.<field> or 0` in the service method, directly adjacent to the existing two counter-mapping lines.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_user_preferences_service.py` (607 lines) — `TestGetFavoriteAssistants` class (lines 216-374) covers: empty-favorites short-circuit, profile-not-found short-circuit, `test_missing_ids_silently_skipped` (lines 251-305), `test_categories_populated_in_favorite_item` (lines 307-346), `test_pagination_math_correct` (lines 348-374). None of these assert on `unique_users_count` or `clone_count` today (they didn't exist on the DTO).
- `tests/codemie/rest_api/routers/test_user_preferences_router.py` (552 lines) — router-level tests mock `user_preferences_service` entirely and construct `FavoriteItem(...)` directly with only `id, icon_url, name, description` (e.g. line 104, 342, 376, 455); they do not exercise the service's field-mapping logic at all.

### Testing Framework and Patterns

- `pytest` with `unittest.mock.patch`/`MagicMock`. Service tests patch `get_session`, `user_preferences_repository`, `Ability`, and the reaction-lookup functions, then feed hand-built `MagicMock()` objects (`mock_a1`, `mock_a2`) as fake `Assistant` rows into `session.exec(...).all()`.
- **Important detail for the fix**: in `test_missing_ids_silently_skipped` (lines 263-291) and `test_categories_populated_in_favorite_item` (lines 323-336), `mock_a1`/`mock_a2` are generic `MagicMock()` instances with an explicit allow-list of attributes set (`id, name, description, slug, project, icon_url, type, is_global, shared, created_by, unique_likes_count, unique_dislikes_count, categories`). Neither test sets `unique_users_count` or `clone_count` on these mocks. If the service is changed to read `a.unique_users_count` / `a.clone_count` from these mock objects, unset attributes on a `MagicMock` auto-vivify as child `MagicMock` instances rather than raising `AttributeError` — which the current `FavoriteItem(int = 0)` field type would then try to coerce, risking a Pydantic validation error on these two existing tests unless they are updated to set the two new attributes explicitly.

### Coverage Gaps

- No existing test on either side (service or router) asserts the actual numeric value of `unique_likes_count`/`unique_dislikes_count` propagation from the `Assistant` row into the response `FavoriteItem`, so there is no existing regression test pattern to extend-by-example for the new fields beyond the attribute-setting convention noted above.
- No test exists for `unique_users_count`/`clone_count` on the `get_favorite_skills` or `get_favorite_workflows` paths (expected, since those branches don't set these fields and are out of scope per the task).

---

## 5. Configuration and Environment

### Environment Variables

- None found specific to `favorites`/`user_preferences`/`unique_users_count`/`clone_count`. These are plain DB-backed counters with no feature-flag or env-var gating identified.

### Configuration Files

- No config files govern this domain beyond the standard PostgreSQL connection setup (`codemie.clients.postgres.get_session`, used uniformly across the service layer).

### Feature Flags and Deployment Concerns

- None identified. No Dockerfile/CI reference to `favorites`, `unique_users_count`, or `clone_count` was found. No migration is implicated since both columns already exist on the `assistants` table (per `AssistantBase` field declarations and the historical migration that introduced `clone_count`, `src/external/alembic/versions/k6l7m8n9o0p1_add_workflow_marketplace_columns.py` and related files reference these columns elsewhere in the codebase, confirming they are already live schema).

---

## 6. Risk Indicators

- Two existing unit tests (`test_missing_ids_silently_skipped` and `test_categories_populated_in_favorite_item` in `tests/codemie/service/test_user_preferences_service.py`) build `Assistant`-row stand-ins as bare `MagicMock()` with an explicit attribute allow-list that does not include `unique_users_count` or `clone_count`. Speculative: once the service reads these two attributes, these two tests will need their mocks updated (set `mock_a1.unique_users_count = 0`, `mock_a1.clone_count = 0`, etc.) or they risk failing on `FavoriteItem` field-type validation / asserting unexpected `MagicMock` values.
- `FavoriteItem` fields are declared as plain `int = 0` while the source `Assistant` columns are `Optional[int]` (nullable) — the existing `unique_likes_count`/`unique_dislikes_count` mappings already bridge this with `a.<field> or 0`; the same idiom must be applied to the two new fields to avoid passing `None` into a non-optional `int` field.
- `FavoriteItem` is shared across three endpoints (assistants, skills, workflows). Speculative: adding `unique_users_count`/`clone_count` as model-level fields with default `0` is low-risk for the other two branches (they will just report `0`), but a reviewer should confirm the UI's consumption of this endpoint doesn't require these fields to be `null`/absent for skills and workflows rather than `0`.
- No test currently asserts correct numeric propagation of any counter field from DB row to API response for `get_favorite_assistants` — this is a pre-existing coverage gap, not introduced by this task, but it means the fix has no existing regression test to extend without writing a new assertion.
- The "per-item backfill calls" the UI currently makes to fetch usage metrics live in `codemie-ui`, a separate repository not inspected in this pass — confirming/removing that call site is outside this backend repo's scope and was not verified here.

---

## 7. Summary for Complexity Assessment

This is a narrowly-scoped, two-file fix confined to the API DTO layer and the service layer: `FavoriteItem` (`src/codemie/rest_api/models/user_preferences.py`) needs two new `int = 0` fields, and `UserPreferencesService.get_favorite_assistants` (`src/codemie/service/user_preferences_service.py`, lines 125-144) needs two new field-mapping lines following the exact `a.<field> or 0` idiom already used for the adjacent `unique_likes_count`/`unique_dislikes_count` fields. Both source columns (`unique_users_count`, `clone_count`) already exist on the `Assistant` SQLModel table (`AssistantBase`, lines 703-706) with DB defaults of `0`, and are already loaded by the existing `select(Assistant)...` query with no `load_only` projection — confirming the task's claim that no new query, join, or migration is required.

The main technical risk is test fragility rather than implementation complexity: two existing unit tests construct fake `Assistant` rows as `MagicMock()` objects with an explicit, incomplete attribute list that omits the two new fields, so touching the service's field-mapping block risks silently feeding unset `MagicMock` children into a non-optional `int` Pydantic field unless those two tests are updated alongside the fix. No architectural, integration, or configuration changes are implicated; the change is a same-pattern extension of a mapping that already exists for two sibling fields, with no new tests mandated by existing repo coverage norms but with the fragile-mock risk being the one thing a reviewer should check.

---

## 8. External References

None named by the task. The paths named in `task_context` (`src/codemie/rest_api/models/user_preferences.py` and `src/codemie/service/user_preferences_service.py`) are internal source files within this repository, not external sources of truth outside it — they are addressed directly in Section 2 above, not here.
