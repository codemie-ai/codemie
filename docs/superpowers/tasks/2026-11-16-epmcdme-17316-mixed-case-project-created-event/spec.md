# EPMCDME-17316: a mixed-case-email user gets a personal project and its `project.created` event

## Problem

On the preview stand a Keycloak user was created with email `AutoTest.Mixed.<hex>@Email.com`. Administration > Projects lists a project named with the mixed-case value, but its type is regular (code: `shared`), not personal. No `project.created` event exists for it, and the user has no personal project or membership. The lowercase-email control user is fine.

## Cause, as far as code proves it

Verified in code:
- `ensure_personal_project_async` runs on every uncached authenticated request (`authentication_service.py:396`, from `_finalize_authentication`) and on local login (`:788`). It passes the **stored** email (`_build_security_user`, `:363`).
- If the application found case-insensitively (`_find_application`, `personal_project_service.py:195`) has a name different from that email, is not personal, and is not exactly named like the email, `_is_reusable` is False (`:197-205`). `_create_personal_project` then raises `ValueError` (`:232-237`). `ensure_personal_project_async` catches it and returns False (`:103-111`).
- The emitter is reached only after `_create_personal_project` succeeds (`:81-95`). So in this state no conversion, no membership and no event happen, and the project stays regular. This matches all three symptoms.
- This is the EPMCDME-16566 rule "a case variant is reused only if already personal" (16566 spec, line 37; test `test_create_personal_project_rejects_foreign_case_variant`, `not-personal` case).
- It can only happen when the stored email and the pre-existing application name differ in case. If they are the same string, the application is converted normally (exact name is reusable). That includes the application that first login pre-creates from `db_user.email` (`authentication_service.py:242`, `aget_or_create`, case-insensitive at `application_repository.py:971-996`). So first login alone cannot cause it.

Not verified (not readable here): what Keycloak delivers as email (it may lowercase it), which flow created the mixed-case regular application, and the exact string the harness queries.

Whatever created that application, the stand state is "case-variant, non-personal application, no other members", and this change handles that state.

## Approach (primary, and the whole hotfix)

In `PersonalProjectService` (`personal_project_service.py:197-237`), make a case-variant, non-personal application convertible exactly like an exact-name one. Keep two guards:
- a case-variant that is **another user's personal project** (personal and `created_by != user_id`) is still refused;
- the existing "other members" check stays (`:242-257`): a project that has members other than this user is refused.

Then `_create_personal_project` converts the application and keeps its real name. It adds one membership under that real name, and `ensure_personal_project_async` emits one `project.created` event with `entity_id` = that real name (unchanged, `:90`).

Existing broken users heal on their next uncached request or login, with no migration or script. This follows the 16566 stance (no cleanup). Why it holds:
- For them `_has_personal_project_complete` is False, because the application is not personal (`:181`), so the flow reaches `_create_personal_project`.
- After conversion the early return (`:76`) stops any second event.
- Users whose regular project has other members still log a warning and are not converted. That is today's behaviour.

## Acceptance criteria

1. A non-personal application whose name differs from the stored email only by case, with no members other than the user, becomes personal with `created_by` = the user.
2. The user gets exactly one membership, named exactly like the application (not the email).
3. Exactly one `project.created` event is written, with `entity_id` = the application's real stored name. A later call emits nothing.
4. A case-variant that is another user's personal project is still refused: no change, no membership, no event.
5. A project with other members is still refused (case-variant or exact).
6. Lowercase-email users, and exact-name conversions, behave as before.
7. Harness test `test_mixed_case_email_user_gets_one_membership_and_one_default_project` (the ticket's AC4) passes without `xfail(strict=True)`. This is verified on the stand, not here.

## Decisions

- Relax the 16566 case-variant rule for non-personal applications over keeping it: it leaves the user without a personal project and without an event, and no later login can fix that. This reverses one line of the 16566 spec (line 37) and needs your confirmation at the gate. My call.
- Still refuse a case-variant personal project of another user over converting it regardless: the owner may have no membership row, so the members check would not protect it. My call.
- `_ensure_projects_exist` (`authentication_service.py:242-265`) is left unchanged over skipping the user's own email: it already reuses a case-variant application, and the personal path handles what it leaves behind. My call.
- When both an exact-case and a case-variant application exist, the existing pick (exact-case first, `application_repository.py:866`) is unchanged over a new rule. My call.
- The read-path `entity_id` case-insensitive filter is **not built** over building it: the harness queries the project's real name, which this fix makes exist. The filter would widen an audit API for all entity types, unmeasured on the index, and needs its own fix. Add it only if the stand still returns 0 after deploy and the stored `entity_id` differs from the queried string by case alone. My call.
- If that filter is ever added, it applies to all entity types, since the endpoint does not require `entity_type`. — user, answers AR-2
- No data migration, no cleanup script, no re-emission for already-broken users over a one-off repair (16566 stance). My call; it holds as explained above.

## Non-goals

- Normalising emails (`EmailStr`, IdP claims) or renaming applications.
- Changing the activity-events read API, `find_all`, or any index.
- Changing the emitter code or `project_service.py:271-280`.
- Strict event writes. A swallowed insert still leaves no event, and no repair job exists for it.
- Membership and default-project resolution logic (`resolve_default_project`); one membership under the real name is a result of this fix, not a change to it.
- Fixing the description text (it uses the email, `:263`).

## Tests

- Extend `tests/codemie/service/project/test_personal_project_service.py` (mocked-repository pattern). The existing `not-personal` case of `test_create_personal_project_rejects_foreign_case_variant` (shared, `created_by` = this user) now expects conversion. Keep the `owned-by-another-user` case.
- New or changed cases:
  - a non-personal case-variant with no other members is converted, with membership under the real name and one event whose `entity_id` is the real name, driven through `ensure_personal_project_async`;
  - a case-variant with other members is refused and no event is written;
  - the second call after conversion emits no event;
  - a lowercase email with a lowercase application is unchanged.
- Not testable here: Keycloak's behaviour, and the harness repo test.

## Open risks

- The Keycloak delivery and the origin of the mixed-case application are unproven. If the stand state differs (for example the application has other members), the fix does not apply.
- A shared project with no members named as a case-variant of someone's email is converted at their login. The exact-name rule already allows the same.
- A lost event (`async_insert` swallowed) is not repaired. No owner or follow-up exists.
- Stand build and `ACTIVITY_EVENTS_ENABLED` are unverified. If the harness still returns 0 after deploy, check these and the log line `[activity_events] async_insert failed`, and compare the stored `entity_id` with the harness string.
