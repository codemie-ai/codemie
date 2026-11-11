# EPMCDME-15577: Remove leftover project memberships of deleted projects

## The problem in plain words

**What's wrong.** Some users still see projects that were deleted long ago. These projects show up in the project picker on the assistant, workflow and skill settings pages. If they pick one, they get a project that no longer exists.

**What data causes it.** A user's projects are listed in the `user_projects` table, one row per user and project. Before release 2.51 (EPMCDME-14501, 2026-09-18), deleting a project removed the project itself but left the creator's row behind. Nothing links that table to the projects table, so the database never noticed. About 657 such leftover rows exist on Production. The project list a user sees (`GET /v1/user`) is built straight from these rows, so the deleted projects still appear. Since 2.51, deleting a project removes these rows, so no new leftovers appear.

**What we do.** We add a one-time database cleanup. It runs automatically when the app starts and deletes membership rows whose project no longer exists. It keeps anything that might still be valid, and it logs what it did. We change no application code. Once the data is clean, the existing code shows the right list.

## Which rows are removed

A row is an **orphan** when its `project_name` matches no project at all, under any letter casing. Projects live in the `applications` table, and the name is compared case-insensitively against both `applications.id` and `applications.name`. Orphans are deleted.

These rows are kept:

- **Exact match.** A project with exactly that name exists. This is a normal, valid membership.
- **Case-only match.** A project exists, but only with different letter casing. For example, the row says `MyTeam` and the project is `myteam`. Rows like this come from IDP/Keycloak sign-up, which creates projects without regard to case. They may be real access, so we keep them and only log how many there are.
- **Soft-deleted project.** The project still exists but is marked deleted (`deleted_at` set). This is not an orphan for this cleanup. Only the email-change flow soft-deletes a project, and that flow already removes the membership.

Personal projects (named after the user's email) get no special treatment. A personal-project orphan is deleted like any other. That is harmless: the API cannot delete personal projects (403 guard, `project_service.py:596`), and `ensure_personal_project_async` recreates the project and membership on the user's next login (`personal_project_service.py:152-240`).

## How it runs

- A new Alembic data migration placed directly after the current head `e5f9a1c3b7d2`. It runs on every pod start (`rest_api/main.py:448-452`), like all migrations.
- It follows the pattern of `e5f9a1c3b7d2_repair_ms_teams_migrated_settings.py`: SQL via `op.get_bind()` / `sa.text`, a module logger, and a summary log line.
- It logs counts for scanned rows, exact matches kept, case-only matches kept and orphans deleted. It logs each deleted row as `user_id`, `project_name`, `is_default`. No backup table is created.
- It is safe to run more than once: a second run finds no orphans and changes nothing.
- The downgrade only logs that the cleanup is intentionally not reverted, the same as `e5f9a1c3b7d2`.
- The process-wide login cache is cleared by the restart that runs the migration, so users see the cleaned list right away.

## Acceptance criteria

1. **AC1.** After the migration, every `user_projects` row whose `project_name` matches no `applications` row (id or name, case-insensitive) is gone.
2. **AC2.** Rows with an exact match, a case-only match or a soft-deleted project are unchanged.
3. **AC3.** The migration logs per-class counts and one line per deleted row (`user_id`, `project_name`, `is_default`).
4. **AC4.** A second run deletes nothing and leaves the data the same.
5. **AC5.** The downgrade changes no data and logs that the cleanup is not reverted.
6. **AC6.** Regression test: `ProjectService.delete_project` leaves no `user_projects` row for the deleted project.
7. **AC7.** Unit tests for the migration in the style of `tests/codemie/migrations/test_e5f9a1c3b7d2_*`. They cover: deleting orphans, keeping live and case-only rows, a second run doing nothing, and the revision chaining to `e5f9a1c3b7d2`.
8. **AC8.** Manual check: locally, add an orphan row and run the migration. `GET /v1/user` no longer lists the project, and it no longer appears in the assistant, workflow or skill project pickers.

## Non-goals

- Changing any code that reads `user_projects` (`/v1/user`, profile, login, security context, admin and analytics lists). There are no new existence filters.
- A foreign key from `user_projects.project_name` to `applications`.
- The UI repo.
- Admins and auditors seeing soft-deleted personal projects through `/v1/admin/applications` search. This is expected behavior.
- Fixing the case-insensitive project creation during IDP/Keycloak sign-up (core and enterprise coordinator).
- Detecting leftover rows that were re-attached to a new project created later under the same name.
- A backup table, an ops script, or a reversible downgrade.
