# EPMCDME-15111 — Budget Attribution: Spec

## Story

As a platform administrator in an organization where every project is a department with its own budget, I want the user's default project used as the billing project wherever the charged project is today arbitrary or personal, so that spend by people who work across several projects is charged to a predictable project instead of a personal budget or an arbitrary one, and analytics show the same project that paid.

Depends on EPMCDME-15110 (Default Project Foundation — done, same branch): `UserProjectRepository.get_default_for_user/set_default/clear_default`, `is_default` boolean column on `UserProject` with a DB-level partial unique index enforcing at most one default per user.

## Acceptance Criteria

1. Member of marketplace assistant's project -> charge assistant's project, current behavior preserved.
2. Not a member, has default project -> charge default project's budget.
3. Not a member, no default project -> charge personal budget, current behavior preserved.
4. User in several projects with default assigned, runs a skill/workflow/any flow not bound to a specific project asset -> charged to default project not an arbitrary one.
5. User in several projects with no default assigned, unbound flow -> charged project is the same on every request (deterministic), not dependent on data ordering.
6. Default project has no budget allocation for user -> falls back to personal/default budget, fallback visible in logs/analytics not silently looking like personal usage.
7. Member of assistant's project AND has different default project -> assistant's project wins (membership beats default).
8. CLI/IDE request without explicit project, user has default -> charged to default project, same fallback rules as web.
9. CLI/IDE request with explicit project -> explicit wins regardless of default, current behavior preserved.
10. Request charged to a project -> analytics show same project whose budget was charged.
11. Admin changes default project -> new spend goes to new default within ~1min normal budget cache delay, previously recorded spend not moved.
12. User removed from project set as default -> that project no longer charged, non-default rules apply.

## Out of Scope

- Storage/API/role-gating for default project (EPMCDME-15110, already done).
- User Management UI screens (EPMCDME-15112, separate ticket).
- Per-chat/per-conversation project picker.
- Showing users their billing project.
- Searching other projects for a budget when default has none (fallback is always personal).
- Blocking requests when default has no budget.
- Changing how project-owned assets (non-marketplace assistants, workflows, datasources) bill — they keep billing their own project.
- Changing CLI explicit-project selection behavior.
- Re-attributing historical spend.
- Multiple default projects per user (e.g. per budget category) — see "Extensibility note" below; not requested, not built.

## Root Causes (confirmed via codebase research)

- **AC4/AC5 bug**: `User.current_project` property (`src/codemie/rest_api/security/user.py:112-115`) returns `project_names[0]` where `project_names` is populated from an **unordered** DB query (`UserProjectRepository.aget_by_user_id`, no `ORDER BY`). Zero `is_default` awareness. Fed as `fallback_project_name` into `set_llm_context(None, user.current_project, user)` at 9 REST call sites (all AI-generation endpoints: `assistant.py` generate/refine/marketplace-index/marketplace-remove, `workflow.py` generate/refine, `workflow_executions.py` output-change-request, `skill.py` generate/refine/generate-instructions).
- **AC1/2/3/7 surface**: `_resolve_effective_project`'s `is_global` branch (`src/codemie/service/llm_service/utils.py`) — checks membership (`user.project_names`/`admin_project_names`), else falls to `user.email` (personal). No default-project tier exists between them.
- **AC8/9 root**: `_extract_request_info()` (`src/codemie/enterprise/litellm/proxy_router.py:412-427`) — explicit `HEADER_CODEMIE_CLI_PROJECT` header already wins (AC9 already correct, no change needed there). When absent, falls straight to `user.username` — never consults a default.
- **AC6**: likely already satisfied. `BudgetResolutionService.resolve()`/`.resolve_sync()` (`src/codemie/service/budget/budget_resolution_service.py`) already logs INFO-level `budget_event=budget_resolution_global_fallback ... reason=project_budget_not_found` with `project_name` present, when a project has no active budget/member-allocation for the user, and falls back to the global/personal budget path. This needs verification once default projects flow through as `project_name`, not new logging.
- **AC10**: likely already satisfied — `get_current_project()`/`get_project_for_metric()` read the same LiteLLM context that `set_llm_context` sets; monitoring services already tag spend metrics with this value. Verification only.
- **AC11**: likely already satisfied — `_resolution_cache` is keyed by `(project_name, budget_category, user_id)` with a 60s TTL; changing a user's default to a different project name is naturally a new cache key, no explicit invalidation needed. Verification only.
- **AC12**: likely already satisfied at the data layer — all project-membership-removal paths hard-delete the `UserProject` row, which removes `is_default=True` along with it. `get_default_for_user()` returns `None` immediately after. Verification only, provided the new `default_project` field on `User` is re-resolved per session/login rather than cached indefinitely (same staleness profile `project_names` itself already has today).

## Design

### Approach: fix at the choke point

Add a `default_project: str | None` field to the `User` security model, populated once at user-construction time (same two sites that already populate `project_names`). Rewrite `User.current_project` to consult it first, with a deterministic fallback. Because all 9 "unbound flow" call sites already read `user.current_project`, this fixes AC4 and AC5 in one function — zero changes needed to the 9 router call sites themselves. Separately thread a default-project tier into the two other existing resolution surfaces (`_resolve_effective_project`'s `is_global` branch, and `_extract_request_info`).

**Alternatives considered**: an on-demand repository lookup at each call site (more files touched, always DB-fresh) and a hybrid service-function approach (keeps the `User` model lean, still call-site-based). Rejected for this ticket: DB layer (from EPMCDME-15110) already hard-constrains to a single default per user regardless of which approach is chosen, so the on-demand approaches' only real advantage — cheaper extension if a future story adds multiple defaults (e.g. per budget category) — is speculative. YAGNI: no such story exists yet, and today's schema has no category dimension on `is_default` at all. Chosen approach optimizes for this ticket's actual scope (single default, minimal footprint, lowest risk on an already L-tier/XXL-component-scope task).

**Extensibility note**: if a future story requires multiple defaults (e.g. per `BudgetCategory`), `User.current_project`'s parameterless-property shape will need to be partially unwound and the 9 call sites revisited to pass a category — a known, bounded cost deferred by this choice, not a hidden one. That future story would also require a new DB migration (the partial unique index is currently scoped per-user, not per-user-per-category) regardless of which approach this ticket takes.

### Components

1. **`User` model** (`src/codemie/rest_api/security/user.py`):
   - Add field: `default_project: str | None = None`.
   - Rewrite `current_project` property:
     ```python
     @property
     def current_project(self) -> str:
         if self.default_project:
             return self.default_project
         apps = sorted(self.project_names) if self.project_names else [DEMO_PROJECT]
         return apps[0]
     ```
     (Deterministic sort replaces the previous unordered `apps[0]`.)

2. **New repository method** — `UserProjectRepository.get_default_for_user` (added by EPMCDME-15110, `user_project_repository.py:128`) is **sync-only**: `def get_default_for_user(self, session: Session, user_id: str) -> Optional[UserProject]`. Both population sites below use `AsyncSession` (they call `aget_by_user_id`, not the sync `get_by_user_id`), so a new async twin is needed:
   ```python
   async def aget_default_for_user(self, session: AsyncSession, user_id: str) -> Optional[UserProject]:
       """Get the user's current default-project membership row, if any (async)."""
       statement = select(UserProject).where(UserProject.user_id == user_id, UserProject.is_default == True)  # noqa: E712
       result = await session.execute(statement)
       return result.scalar_one_or_none()
   ```
   Mirrors `aget_by_user_id`'s existing async pattern (`user_project_repository.py:595-599`). Returns the `UserProject` row (not a bare string) — callers read `.project_name` off it, consistent with `get_default_for_user`'s existing sync contract.

3. **Population sites** (both async, both already populate `project_names` via `aget_by_user_id`):
   - `AuthenticationService.load_user_for_auth()` (`src/codemie/service/user/authentication_service.py:140-174`) — after building `projects`/`project_names`, call `default_row = await user_project_repository.aget_default_for_user(session, db_user.id)` and pass `default_project=default_row.project_name if default_row else None` into the constructed `security_user.User(...)`.
   - `LocalIdp` (`src/codemie/rest_api/security/idp/local.py:82-117`) — identical pattern.

4. **`_resolve_effective_project`** (`src/codemie/service/llm_service/utils.py`), inside the existing `is_global` branch: after the membership check fails (`project not in user_projects`), before falling to `user.email`, insert:
   ```python
   if user.default_project:
       return user.default_project
   ```
   No additional budget check here — `BudgetResolutionService.resolve()` already handles "project has no budget for this user" via its existing global-fallback path (AC6), so this tier does not duplicate that logic.

5. **`_extract_request_info`** (`src/codemie/enterprise/litellm/proxy_router.py:412-427`):
   ```python
   project = (
       headers.get(HEADER_CODEMIE_CLI_PROJECT)
       or (user.default_project if user else None)
       or (user.username if user else "")
   )
   ```

6. **`_probe_project_budget_scopes`** (`proxy_router.py:539`) / **`_probe_direct_project_budget_scopes`** (`llm_factory.py:631`): **no internal change**. They receive whatever `project_name` their caller resolves; the change is entirely upstream at (4)/(5). Implementation must re-verify both call sites still receive the corrected value consistently (the two functions must stay in lockstep per standing project convention — never unify/refactor them).

### Data Flow

Request (web AI-generation endpoint, chat/skill/workflow execution, or CLI/IDE proxy call) → the applicable resolution surface picks the effective project in priority order: **explicit param/header > asset ownership/membership > user's default project > personal/username fallback** → `BudgetResolutionService.resolve()`/`.resolve_sync()` takes that project name, checks for an active project budget + per-user member allocation, falls back to the global/personal budget path with its existing INFO-level log if none exists (unchanged) → LiteLLM/analytics tag the charged project via the existing `get_current_project()`/`request_info` mechanisms (unchanged).

### Error Handling

- `get_default_for_user` returning `None` is the normal "no default set" case — falls through to the next tier, not an error.
- DB-call failure handling at the two population sites follows whatever convention `project_names`' own population already uses at that call site (no new error-handling policy introduced).
- No new exception types.

### Testing

- Extend `tests/codemie/service/llm_service/test_utils_litellm_context.py`: `is_global` asset + not-a-member + has `default_project` → charged default (AC2); not-a-member + no default → charged `user.email`, confirm unchanged (AC3); member of asset's project AND has a different default → charged asset's project, membership wins (AC7); member case with no default set, confirm unchanged (AC1).
- New/extended tests for `User.current_project`: `default_project` set → returned regardless of `project_names` order (AC4); unset, multiple `project_names` → deterministic (sorted) pick, same result across repeated calls with input reordered (AC5); unset, empty `project_names` → `DEMO_PROJECT`, confirm unchanged.
- Extend the CLI proxy's `_extract_request_info` test coverage: header present → header wins regardless of default, confirm unchanged (AC9); header absent + default set → default (AC8); header absent + no default → `user.username`, confirm unchanged (AC3-CLI-equivalent).
- Extend population-site tests (`AuthenticationService`, `LocalIdp` — exact existing test file names to be confirmed at plan time) to assert `default_project` is populated correctly from the repository.
- No new tests for `BudgetResolutionService`, monitoring services, or the resolution caches — these are verify-only per the root-cause analysis above; confirmed via QA/manual trace rather than new speculative test coverage (YAGNI).

## Acceptance Criteria → Design Mapping

| AC | Covered by |
|---|---|
| 1 | `is_global` branch, membership check (unchanged) |
| 2 | `is_global` branch, new default-project tier |
| 3 | `is_global` branch, personal fallback (unchanged) |
| 4 | `User.current_project` rewrite |
| 5 | `User.current_project` rewrite (deterministic sort) |
| 6 | `BudgetResolutionService` existing fallback + log (verify only) |
| 7 | `is_global` branch, membership check ordered before default tier |
| 8 | `_extract_request_info` new default-project tier |
| 9 | `_extract_request_info`, header check (unchanged, already correct) |
| 10 | Existing `get_current_project`/monitoring tagging (verify only) |
| 11 | Existing `_resolution_cache` keying (verify only) |
| 12 | Existing hard-delete of `UserProject` row (verify only) |

## Post-analysis corrections (2026-09-25)

Superseding decisions — details in `post-analysis-fixes.md`:
- `current_project` without a default resolves to the personal project when it is a membership, then sorted first (was: sorted first).
- A named project without budget for the user is re-attributed to the personal identity in analytics, with `budget_fallback_from=<project>` on every metric (AC6/AC10 were not "verify only").
- `default_project` is populated in `_finalize_authentication()` — the login path actually used — not only `load_user_for_auth()`.
