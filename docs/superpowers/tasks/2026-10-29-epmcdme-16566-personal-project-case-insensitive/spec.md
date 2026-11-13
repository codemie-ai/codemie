# EPMCDME-16566: Case-insensitive personal-project matching, no orphan memberships

## Intent

A user whose IdP email has uppercase letters (stored email is lowercase) ends up with two `user_projects` rows: the real, mixed-case personal project and an orphan row named after the lowercase email. The orphan has no application. It wins the default-project rule (its name equals the email), then the admin view hides it, so User Details shows no default project, and budget code treats the user's own personal project as a foreign project. This design makes every place that decides "is this the user's personal project" or "which row is the default" match case-insensitively and stops creating the orphan. Existing orphan rows are not deleted or migrated: with case-insensitive matching they stop mattering.

Reproduced locally (DB `codemie_13933`): a user with application `Adam_Topolewicz@epam.com` and one membership row; calling `ensure_personal_project_async(user_id, "adam_topolewicz@epam.com")` adds a lowercase row, and the admin detail API then returns `is_default: false` for the only visible project.

## Goals

1. A user with a mixed-case personal project gets exactly one personal-project membership, named exactly like the application. No orphan row is created, on login, registration or email-change reconcile.
2. `resolve_default_project` matches the user's email against member project names case-insensitively and returns the member's real name.
3. Budget code recognises the user's personal project regardless of case, so a mixed-case personal project is not treated as a shared project that "fell back" to personal.
4. Users who already have an orphan row see the correct default in User Management, with no data migration or script.
5. Unit tests cover the mixed-case paths named in the ticket's acceptance criteria.

## Non-goals

- The budget cost-direction rework (default-project charging for all non-personal assistants). Parked; separate ticket.
- Changing the DB: no case-insensitive unique index, no foreign key from `user_projects.project_name` to `applications`.
- Normalising IdP emails or renaming existing applications.
- Any data migration, cleanup script or deletion of existing orphan rows (explicitly decided: not applicable; case-insensitive comparison is the fix).
- Changing how the admin user-detail view derives the default from all memberships; only the final name comparison becomes case-insensitive.
- Frontend changes. The Default radio is correct once the API returns `is_default: true`.

## Design

### 1. Canonical project name in personal-project setup
`src/codemie/service/project/personal_project_service.py`

- `_has_personal_project_complete`: find the application with the case-insensitive lookup (active, non-deleted, preferring an exact-case match; `aget_active_by_name_case_insensitive` already does this). Require `project_type == PERSONAL` and `created_by == user_id`. Look up the membership by the application's real `name`, not by the email.
- `_create_personal_project`: resolve the existing application case-insensitively first.
  - If one exists: run the existing "other users have access" safety check against its real `name`; convert it to personal as today; ensure the membership using its real `name`.
  - If none exists: create it with the given email as name (unchanged); ensure the membership with that name.
  - `aget_or_create` stays as the race-safe creator.
- `aadd_project` is only ever called with a name that equals an application's `name`. If only an orphan lowercase row exists, the real row is added; the orphan is ignored.
- A case-variant application (not an exact-case match) is reused only when it is already personal and `created_by == user_id`. If it belongs to someone else or is a non-personal project, log a warning (no email) and skip, leaving it untouched. Exact-case behaviour is unchanged.
- The activity event `entity_id` uses the canonical name.
- `reconcile_personal_project_on_email_change`: do nothing when `old_email.lower() == new_email.lower()` (a case-only change must not soft-delete the project or its memberships). Otherwise find the old application and membership case-insensitively and remove using the real names.
- Logging stays free of email addresses (user id and project type only).

### 2. Default resolution
`src/codemie/rest_api/security/user.py`

`resolve_default_project` compares case-insensitively in two places: the stored default and the email match.
- Collect member names equal to the candidate ignoring case. One match: return it.
- Several matches: prefer the name that is **not** exactly equal to the user's stored (lowercase) email. The exact-email name can only be an orphan created by this bug, because the real mixed-case project differs from the lowercase email. If all matches equal the email exactly, return it. Ties break by sorted name, so the result is deterministic.
- No match: unchanged (`min(names)`).
`resolve_membership_default` and `User.current_project` inherit this, so users with an existing orphan resolve to the real project at runtime too, not only in the UI.

### 3. Personal-project detection in budget attribution
- `src/codemie/service/llm_service/utils.py` `_unfunded_project`: replace `project == user.email` with a case-insensitive comparison.
- `src/codemie/enterprise/litellm/llm_factory.py`: `_record_direct_budget_attribution` (`project == personal_project`) and the `current_project != user_email` check in `_configure_direct_runtime_overrides` become case-insensitive.
- `src/codemie/enterprise/litellm/proxy_router.py` `_flag_personal_budget_fallback`: the `project in {user.username, user.email}` check becomes case-insensitive.
- Implementation starts with a grep sweep of `== user.email`, `== email`, `{user.username, user.email}` and `project_names` membership tests under `src/codemie`. Stop rule: change only checks that decide "is this the user's personal project" or "which membership is the default". Access and permission checks on project membership stay exact.
- A shared helper (for example `is_same_project_name(a, b)`) next to `resolve_default_project` avoids repeating the comparison; sync and async copies of the probe stay in lockstep.
- Spend for the personal project stays keyed by `user.email` (the LiteLLM customer id). My call: only the recognition of "this is the personal project" changes, so attribution no longer reports `budget_fallback_from = <mixed-case personal project>` for these users.

### 4. Existing orphan rows stay harmless
No cleanup. For a user who already has the mixed-case project plus the lowercase orphan:

- `resolve_default_project` returns the real mixed-case project (section 2), so `User.current_project`, the admin detail, list and profile views all name the real project. The orphan is not listed (no application), as today.
- The `ProjectInfo(... is_default=...)` sites need no change because the resolved default now equals a listed row's name exactly.
- Budget code treats the personal project case-insensitively (section 3), so the orphan never decides routing.
- New orphans are not created (section 1). Nothing deletes the existing ones.

## Constraints

- Follow repository patterns: sync and async twins, singleton repositories, Apache license header on new files, no email in logs.
- No new dependencies. Commit prefix `EPMCDME-16566:`.
- The MR description asks a reviewer to post `/sanity` (budget routing code is touched).

## Tests (acceptance criterion 3)

- `tests/codemie/service/project/test_personal_project_service.py`: mixed-case email with an existing mixed-case personal application and membership produces no second membership; with no existing membership, it adds one named like the application; reconcile is a no-op on a case-only change; existing exact-case behaviour unchanged.
- `tests/codemie/rest_api/security/test_user.py`: `resolve_default_project` returns the real member name for a mixed-case match; stored default still wins; no-match and no-email cases unchanged.
- `tests/codemie/service/llm_service/test_utils_litellm_context.py`: `_unfunded_project` returns None for a mixed-case personal project (no probe, no fallback log).
- `tests/enterprise/litellm/test_llm_factory.py` and the proxy router tests: personal-project recognition is case-insensitive.
- A user with both the mixed-case row and a lowercase orphan resolves to the real project; the admin detail/list/profile views mark exactly one `is_default: true`, on the real row.
- A case-variant application owned by another user or non-personal is skipped, not converted.
- Existing tests that assert exact `aget_by_name` call arguments are updated to the case-insensitive lookups.

## Decisions I made (please check)

1. Existing orphan rows are left in place (your decision): no migration, no script. Prevention plus case-insensitive comparison is the whole fix.
2. Personal spend stays keyed by the lowercase email (LiteLLM customer id); only the comparison becomes case-insensitive.
3. A case-only email change never triggers personal-project re-creation.
4. With several case-insensitive matches, the default prefers the name that differs from the lowercase email (the exact-email name can only be a bug-created orphan); ties break by sorted name.
5. A case-variant application is reused only if it is personal and created by the same user; otherwise it is skipped with a warning.
6. Analytics joins by project name are verified during implementation (spend stays keyed by customer id).
7. The admin detail view's derivation of the default from all memberships is left as is.

## Left to the builder

Names of the helpers; where the shared comparison helper lives; precise log wording.
