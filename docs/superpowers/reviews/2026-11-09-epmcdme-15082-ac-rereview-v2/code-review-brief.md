# Code review (revision) — EPMCDME-15082, against the revised Jira ACs (2026-11-09)

Revises `../2026-11-06-epmcdme-15082-ac-rereview/`. Reviewed head: `b7da5aca6b` (the 2026-11-06 review ran at `5a35573fdb`).
Basis: the six current ACs (spec section 2.1, J1-J6). Re-assessment by reading the code at HEAD; the review lenses were not re-run.

**Update 2026-11-09:** CR-007 and CR-003 are fixed. CR-004 is not applicable (see table). Open: CR-008.

**Update 2026-11-10:** CR-003 is out of scope of this ticket. Choosing a first live model, and skipping models removed from the platform catalog, is a platform-level list concern. The logic was removed again: the override fallback is now the override's `default_model`, else the project default, else the platform default.

Original verdict: request-changes (1 AC-blocking finding, CR-007). Now: CR-001, CR-005, CR-007 fixed · CR-002, CR-006 withdrawn · CR-003, CR-004 out of scope / skipped · CR-008 open.

## What changed in the ACs

- Old AC6 ("fallback uses the asset-scope default, regardless of any user-level override") became J6: the fallback uses the default of the user's scope — the override's default if it exists, else the project default (project-scoped) or global default (global).
- Old AC7 (platform-disabled model stays blocked) was removed.

## AC status

| AC | Status | Note |
|---|---|---|
| J1 single-user set | pass | unchanged from 2026-11-06 |
| J2 bulk set | pass | unchanged |
| J3 override grants model outside project set | pass | unchanged |
| J4 override takes precedence over project set | pass | unchanged |
| J5 override excludes a project model | pass | unchanged |
| J6 fallback = user-scope default | **partial** | override-first fallback is now the required behaviour and is implemented in `_pick_from_override`, `ResultFinalizerNode`, `SummarizeConversationNode`, and the `default` flag in `get_llm_models`. Gap: `SupervisorWorkflow` (CR-007) |

## Findings

| ID | Was | Now | Why |
|---|---|---|---|
| CR-002 | critical, AC7 no platform-disabled gate | **withdrawn** | AC7 removed from the ticket |
| CR-006 | critical, AC6 fallback prefers override default | **withdrawn** | this is exactly what J6 requires; the 2026-11-06 recommendation (swap the precedence) must NOT be applied |
| CR-007 | major, `SupervisorWorkflow` omits `user=` | **fixed** (was: blocks J6 and J4) | `supervisor_workflow.py` built `ResultFinalizerNode` and `SummarizeConversationNode` without `user=`. `BaseNode.resolve_user_default_model` then returned `None`, so for supervisor workflows the fallback skipped the user's override default and used the project/global default. Fixed by passing `user=self.user`, as `workflow.py` does |
| CR-003 | major | **out of scope (2026-11-10)** | `_pick_from_override` returned the override default / first allowed model without checking it was still in the catalog. Now fallback candidates that are neither catalog models nor enabled routers are skipped; none live → `None` and project logic applies; same check in `get_user_override_default_model` |. Removed from this ticket: first-live-model selection is a platform-level list concern; the fallback is the override default, else the project default, else the platform default
| CR-004 | major | **skipped, not applicable** | `validate_model_for_asset_creation` has one caller, `create_assistant`, whose request cannot be global, so the missing `is_global` guard is unreachable. The project-flag-off early return matches pre-override behaviour; J5 concerns invocation, which is already enforced. Decision 2026-11-09: skip |
| CR-008 | major | open | `test_openapi_snapshot.py:33` still skipped pending baseline regeneration |
| CR-001 | major | **fixed** | `6030acd9bf`: access checked first, 403 before the existence check (`llm_models.py:35-52`) |
| CR-005 | major | **fixed** | `e4336125bf`: empty override list falls back to project filtering (`llm_service.py:958-960`) |

## Remaining

1. CR-008 — regenerate `OPENAPI_BASELINE` (`regenerate_baselines`, enterprise package needed) and remove the skip in `test_openapi_snapshot.py`.
2. Do not act on the CR-006 entry in the old fix pack.
