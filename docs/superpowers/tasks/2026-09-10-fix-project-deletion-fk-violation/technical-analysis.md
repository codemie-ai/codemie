# Technical Research

**Task**: project budget deletion soft-delete
**Generated**: 2026-09-10
**Research path**: filesystem

---

## 1. Original Context

Let's fix https://jiraeu.epam.com/browse/EPMCDME-13156. The bug's root cause is that postgres's budgets table project_name is a reference to existing project, budget is soft deleted, and deletion of the project produces FK violation error. Proposed solution: 1. when deleting project, set the project_name col of the budget table to null 2. Budget's are soft deleted on purpose, so we need to make sure, that we track the deletion even via activity events tracking. Also need to decide: should we set project_name to null for all existing soft deleted budgets that have projects.

---

## 2. Codebase Findings

### Existing Implementations

**Budget Models**:
- `src/codemie/service/budget/budget_models.py` — Budget table with soft delete support
  - `Budget.project_name` (line 101): Optional foreign key to `applications.id`
  - `Budget.deleted_at` (line 124): Soft delete timestamp
  - FK constraint: `fk_budgets_project_name` in migration `a9c8d7e6f5b4`

**Project Service**:
- `src/codemie/service/project/project_service.py` — ProjectService.delete_project (line 450)
  - Validates project has no assigned users or resources before deletion
  - Performs hard delete via `application_repository.delete_by_name`
  - Emits `ProjectManagementEvent.PROJECT_DELETED` activity event

**Budget Repository**:
- `src/codemie/repository/budget_repository.py` — BudgetRepository
  - `update()` method for partial updates (line 145)
  - Soft delete pattern used throughout queries with `Budget.deleted_at.is_(None)` filters

**Application Repository**:
- `src/codemie/repository/application_repository.py` — ApplicationRepository
  - `delete_by_name()` (line 276): Hard deletes Application row via session.delete()

### Architecture and Layers Affected

**Service Layer**:
- `ProjectService` — project deletion orchestration
- `ProjectBudgetService` — budget lifecycle management

**Repository Layer**:
- `BudgetRepository` — async budget CRUD operations
- `ApplicationRepository` — sync project CRUD operations

**Database Layer**:
- `budgets` table — stores budget records with soft delete
- `applications` table — stores project records
- Foreign key constraint from `budgets.project_name` to `applications.id`

### Integration Points

**Database Constraints**:
- Migration `a9c8d7e6f5b4_project_shared_subbudgets.py` (line 66):
  - FK `fk_budgets_project_name` from `budgets.project_name` to `applications.id` with no CASCADE action
  - Current behavior: constraint blocks project deletion when referenced by any Budget row

**Related Tables with FK to applications**:
- `project_budget_assignments.project_name` → `applications.id` with `ondelete="CASCADE"` (migration `a1b2c3d4e5f7`, line 86)
- `project_member_budget_assignments.project_name` → `applications.id` with `ondelete="CASCADE"` (migration `a1b2c3d4e5f7`, line 131)
- `project_budget_groups.project_name` → `applications.id` (migration `fd6943f33934`, line 46)

**Activity Events**:
- Activity tracking used throughout for audit trail
- Budget deletion events: `BudgetManagementEvent.BUDGET_DELETED`, `PROJECT_BUDGET_DELETED`
- Project deletion events: `ProjectManagementEvent.PROJECT_DELETED`

### Patterns and Conventions

**Soft Delete Pattern**:
- Budget models use `deleted_at` timestamp column for soft deletes
- All queries filter `deleted_at.is_(None)` to retrieve only active records
- Partial unique index on `budgets.name` enforces uniqueness only among active rows (migration `d4e5f6a7b8ca`)

**Activity Event Pattern**:
- All lifecycle operations emit activity events via `activity_event_repository.insert()`
- Event structure: domain, event_type, entity_type, entity_id, actor_id, attributes
- Used in `ProjectService.delete_project()` (line 495) and throughout budget services

**Repository Update Pattern**:
- `BudgetRepository.update()` accepts dict of fields to update
- Partial updates preserve unmodified fields
- Pattern at `src/codemie/repository/budget_repository.py:145`

---

## 3. Documentation Findings

### Guides and Architecture Docs

**Database patterns**: `.ai-run/guides/data/database-patterns.md`
- Migrations managed via Alembic under `src/external/alembic/versions/`
- SQLModel and session patterns documented

No specific guides found covering soft delete + FK nullification patterns or project-budget lifecycle coupling.

### Architectural Decisions

**Budget soft delete decision**: Migration `d4e5f6a7b8ca_add_deleted_at_to_budgets.py`
- Context: "Allow re-creation of deleted project budgets by soft-deleting Budget rows"
- Introduced partial unique index on `budgets.name WHERE deleted_at IS NULL`
- Soft delete enables name reuse and audit trail preservation

**FK constraint design**: Migration `a9c8d7e6f5b4_project_shared_subbudgets.py`
- Added `budgets.project_name` FK to `applications.id` without CASCADE action
- Lacks SET NULL or CASCADE, so default RESTRICT behavior applies

### Derived Conventions

- Hard deletes (via `session.delete()`) trigger FK violations when referenced
- Soft deletes (setting `deleted_at`) do not cascade or nullify FK references
- Activity events expected for all state transitions affecting auditable entities

---

## 4. Testing Landscape

### Existing Coverage

**Project deletion tests**: `tests/codemie/service/project/test_project_service_delete_update.py`
- Validates personal project deletion blocked (line 76)
- Validates assigned users block deletion (line 96)
- Validates resources block deletion (lines 141, 163, 183, 203, 223)
- No test coverage for budget FK violation scenario

**Budget lifecycle tests**:
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` — covers budget soft delete operations
- `tests/codemie/service/budget/test_budget_service_activity.py` — validates activity event emission for budget operations
- `tests/codemie/repository/test_project_budget_repository.py` — includes `test_soft_delete_all_by_budget_id_marks_all_rows_deleted` (line 474)

### Testing Framework and Patterns

- Framework: pytest with async support (`pytest-asyncio`)
- Patterns: Mock-based unit tests with `unittest.mock.patch`
- Test helpers for creating test fixtures (`_make_app`, `_zero_counts`)
- Activity event assertions validate event emission and structure

### Coverage Gaps

- Project deletion with soft-deleted budgets referencing the project
- Budget `project_name` nullification during project deletion
- Activity event emission for budget project_name nullification
- Edge case: multiple soft-deleted budgets with same project_name

---

## 5. Configuration and Environment

### Environment Variables

No environment variables directly govern budget or project deletion behavior. Relevant config:
- Database connection: PostgreSQL via SQLModel/SQLAlchemy
- Migration tool: Alembic

### Configuration Files

**Migration files**:
- `src/external/alembic/versions/d4e5f6a7b8ca_add_deleted_at_to_budgets.py` — soft delete column
- `src/external/alembic/versions/a9c8d7e6f5b4_project_shared_subbudgets.py` — FK constraint

**Database patterns guide**: `.ai-run/guides/data/database-patterns.md`
- Documents migration workflow and Alembic conventions

### Feature Flags and Deployment Concerns

No feature flags identified. Deployment considerations:
- Migration to alter FK constraint or nullify existing references must handle existing data
- Production databases may contain soft-deleted budgets with dangling project references

---

## 6. Risk Indicators

- **FK constraint blocks project deletion**: Migration `a9c8d7e6f5b4` creates FK without SET NULL or CASCADE; project deletion fails when any Budget (active or soft-deleted) references `project_name`.

- **Existing soft-deleted budgets**: Production may contain soft-deleted Budget rows with `project_name` set. Decision required: backfill nullification or handle at deletion time only.

- **Activity event gap**: No existing event for budget `project_name` nullification. New event type or attribute change needed to audit this state transition.

- **Async/sync repository mismatch**: `BudgetRepository` is async, `ApplicationRepository` is sync. `ProjectService.delete_project()` uses sync session; nullifying budgets requires sync operation or session type conversion.

- **Migration backward compatibility**: Altering FK constraint requires migration; downgrade path must be specified.

- **Multiple budget records per project**: A project may be referenced by multiple Budget rows (different categories, soft-deleted historical records). Bulk update logic required.

- **Related tables**: `project_budget_assignments` and `project_member_budget_assignments` have `ondelete="CASCADE"` FK to `applications.id`; these cascade correctly. `budgets` table FK does not cascade, causing the violation.

---

## 7. Summary for Complexity Assessment

**Layers touched**: Service (ProjectService), Repository (BudgetRepository, ApplicationRepository), Database (migration to alter FK or add nullification hook).

**File change surface**: Modifies project deletion flow to nullify `budgets.project_name` before hard-deleting project. Requires sync budget query and update within `ProjectService.delete_project`, or migration-based FK constraint change.

**Technical novelty**: Introduces cross-repository operation (sync ProjectService calling budget update) or migration-based FK action change. Activity event emission for nullification extends existing audit pattern but requires new event type or attribute structure.

**Test coverage posture**: Project deletion tests exist but lack budget FK violation coverage. Budget soft delete tests exist but do not cover project-driven nullification. New tests needed for nullification logic, activity event, and edge cases (multiple budgets, no budgets).

**Key risk factors**: Async/sync session mismatch between repositories, existing production data requiring backfill decision, FK constraint alteration requiring careful migration and rollback plan. Activity event extension must align with existing audit conventions.

---

## 8. External References

None named by the task.
