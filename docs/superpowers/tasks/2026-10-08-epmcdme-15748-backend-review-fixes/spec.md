# Spec: Backend review fixes for per-user assistant sharing (EPMCDME-15748)

Source: `docs/stories/2026-10-08-assistant-sharing-review-fixes/children/backend-review-fixes/story.md` (owner decisions are binding). Research: `technical-analysis.md`. Size L (22/36). Branch `EPMCDME-14786_sharing-assistants`.

## Goal
Make named assistant sharing safe to deploy and roll back, confined to the user-management feature, respectful of personal integrations, and auditable. Fix the correctness gaps found in MR review (a1, a3, a5, a7, a8, a9, a13-a18).

## Design

**Migration and enum mapping (a1).** Rewrite `a1b2c3d4e5f8` in place (never applied to a shared env). Upgrade only adds the uppercase `ASSISTANT` label to `resourcetype`; no relabel of any label in resourcetype, principaltype or action. Remove the three `values_callable` in `models/permission.py:74-94` so SQLAlchemy persists enum names; existing labels are already uppercase, so old pods and datasource checks read rows unchanged. Downgrade deletes ASSISTANT grant rows, then trims the enum. The uncommitted edit already in that file is the starting point; keep it.

**Gating and search (a3).** The four sharing routes (`routers/assistant.py` ~L3318-3342) reject with `ExtendedHTTPException(400)` when `ENABLE_USER_MANAGEMENT` is off, same pattern and message as `user_management_router.py:144`. `sharable-users`: `q` has `min_length=2` (validation error, not empty result); service caps results at 20.

**Single authorized entry points (a15, a16).** All four routes resolve the assistant with `_get_assistant_by_id_or_raise` (404). The share service exposes public methods that include the owner check; the router stops calling `_require_owner`. No other refactor.

**Personal-integration guard (a5).** One service-level check: an assistant "uses a personal integration" if any toolkit/MCP-server setting it references has `setting_type` USER. Grant rejects (400, message naming the cause) when so. Assistant create/update rejects (400, descriptive) when the resulting assistant would use a personal integration and has named grants. Project-share behaviour is untouched.

**Audit (a7).** Add assistant-domain event constants to `activity_models.py` and a new strict insert on `ActivityRepository` that runs in the caller's session/transaction and raises on failure. When `ACTIVITY_EVENTS_ENABLED` is off it is a no-op. Existing fail-open `insert` and its ~14 callers stay unchanged. One event per changed principal, written only when a row actually changes. Payload: actor (durable username/id in `attributes`, since `actor_id` FK is SET NULL), principal type and id, assistant id, project, before/after, UTC time. Grant, revoke, generic permission delete of ASSISTANT rows (a17) and assistant-delete grant removal (a18) all go through the share service so they share the same code path. Retention: no change; existing purge applies.

**Generic delete (a17).** `DELETE /v1/permissions/{id}` for ASSISTANT rows keeps grantor-or-admin authorization but delegates to the share service revoke. Other resource types untouched.

**Access correctness.**
- a8: `is_shared_with` and both repository SQL predicates ignore named grants when `user.is_external_user`.
- a14: one helper builds the named-grant predicate, used by `models/assistant.py:~874` and `assistant_repository.py:~598,~692`. Not `Permission.get_for`.
- a9: list path loads the page's granted assistant IDs in one query; per-row `Ability.list` grant lookup removed.
- a13: grant dedupes `user_ids`, validates all-or-none first, then inserts conflict-safe (on `unique_resource_principal`) in one transaction; repeats and races succeed idempotently and write no duplicate audit event.
- a18: `delete_assistant` deletes live ASSISTANT grant rows (with audit events); event history is retained.

## Acceptance criteria
Those in the story, unchanged (Migration x3, Gate/search x2, Personal integrations x2, Audit x6, Access correctness x4, Robustness x3). Tests extend the existing share-service, router, security, ability and activity-repository tests, plus new coverage for the revoke route, generic permission delete, assistant delete and the migration.

## Non-goals
- a2 (system prompt/history access narrowing), a4 (project-member restriction on targets), a6 (runtime credential resolution).
- Any change to activity-event retention period or purge mechanism.
- Changing the existing fail-open `ActivityRepository.insert` or its callers.
- Auto-unsetting personal integrations (reject only), and changes to project-share logic.
- Frontend changes; planning-artifact work; unrelated refactors.

## Risks
Strict audit alongside a fail-open repository; enum name/value consistency for datasource app-permission checks after dropping `values_callable`; all three predicate sites must get the external-user skip.
