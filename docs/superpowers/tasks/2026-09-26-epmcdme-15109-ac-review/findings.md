# EPMCDME-15109: Findings after acceptance criteria review

Date: 2026-09-26

The three retained issues R01, R03 and R05 have since been addressed in the
working trees. See [fixes.md](fixes.md) for the changes and validation limits.
The evidence below describes the implementation before these fixes.

Requirements: the four complete ticket texts supplied by the user. See
[acceptance-criteria-review.md](acceptance-criteria-review.md) for scope and AC
mapping. Reviewed backend `785cefa78` and UI `dd6f9c394` against local `main`.

All scenarios below are source-traced, not newly executed reproductions. The
initial review's six issues are preserved here with corrected classification;
they must not all be treated as regressions or confirmed AC failures.

## Disposition of the initial findings

| Original finding | ID | Decision after reading supplied ACs |
|---|---|---|
| 1: partially funded default misattribution | R01 | Retain: substantive story-attribution gap. Do not infer new category-selection rules. |
| 2: inactive budgets accepted by web probe | D02 | Separate: pre-existing dependency problem, not introduced by this story. |
| 3: concurrent updates leave no default | R03 | Retain: new foundation correctness defect. |
| 4: marker hidden beyond three badges | Q04 | Downgrade: UX concern; tooltip still works without opening the user. Not a proven literal AC violation. |
| 5: successful mutation + failed refresh shows old default | R05 | Retain as a reliability defect in the new UI flow, inherited from existing state-management behavior. |
| 6: personal default changes email/username attribution | Q06 | Withdraw as a confirmed AC bug: requirements do not define the canonical personal-project analytics identifier. |

## R01: Selected category falls back, but analytics retain the default project

Priority: P1. Owner: EPMCDME-15111. Status: retained, source-traced.

**Origin:** The underlying project/personal attribution mismatch already exists
on `main`. This branch introduces default-project routing and new fallback
correction, but the correction handles only zero available project categories.
This is incomplete story coverage, not a claim that all billing behavior is new.

**Requirement:** B6 requires observable personal/default fallback when the
default cannot fund the user. The story says analytics show the same project
that paid; B10 requires analytics to agree with project billing. B8 applies the
same fallback rules to headerless proxy traffic. The ACs do not explicitly
define category precedence or require personal-premium instead of
project-platform selection.

**Evidence:**

- [utils.py](../../../../src/codemie/service/llm_service/utils.py),
  `_unfunded_project`, line 88: any nonempty project-scope set suppresses fallback.
- [proxy_router.py](../../../../src/codemie/enterprise/litellm/proxy_router.py),
  `_flag_personal_budget_fallback`, line 612: the same all-categories check.
- [llm_factory.py](../../../../src/codemie/enterprise/litellm/llm_factory.py),
  `_resolve_direct_budget_category`, line 714, and
  `_resolve_direct_project_budget_runtime`, line 815: category selection can
  choose a category absent from the project's scopes; resolution returns global.
- [llm_proxy_monitoring_service.py](../../../../src/codemie/service/monitoring/llm_proxy_monitoring_service.py),
  `track_usage`, line 509: personal attribution depends on the fallback marker.

**Scenario:** Default P has a CLI allocation for the user but no platform
allocation. Send a standard non-premium web or non-CLI proxy request. The
nonempty `{CLI}` scope set suppresses the fallback marker. Category selection
chooses PLATFORM, whose project resolution is global. Actual billing uses
personal/global handling, while usage analytics retain P.

**Correction direction:** Propagate actual resolved scope/charged attribution
for the selected category to monitoring. Preserve existing category precedence
and asset ownership rules; do not search another project for a budget. The two
existing budget probes can remain separate while their eligibility rules stay
consistent.

**Verification needed:** Direct and proxy calls with CLI-only and premium-only
allocations, selecting an absent platform category. Compare provider spend,
analytics project and fallback marker, including cache hits. No new test was
run during this review.

## R03: Concurrent default requests can commit zero defaults and return success

Priority: P2. Owner: EPMCDME-15110. Status: retained, source-traced.

**Origin:** New code. `set_default()` does not exist on `main`.

**Requirement:** F1 says replacing a default produces the new default while the
old membership remains ordinary and exactly one default remains. Intentional
clear/removal is allowed elsewhere; this is a set request accidentally clearing
the default, not a suggestion that zero defaults are always forbidden.

**Evidence:**
[user_project_repository.py](../../../../src/codemie/repository/user_project_repository.py),
`set_default`, lines 155-175, reads the target and current default separately,
without locking, and uses the originally loaded target's flag to decide whether
to update it. The partial unique index enforces at most one; it cannot prevent
this zero-default outcome.

**Scenario under ordinary PostgreSQL READ COMMITTED isolation:**

1. A is initially default. Request X to set A reads its row with `is_default=True`.
2. Request Y switches the default to B and commits.
3. X's next query sees B as current default and resets B to false.
4. X's identity-mapped A object still says true, so X skips assigning A.
5. X refreshes A, now false, and commits. The service returns 200 with no default.

The supplied AC is written around replacement; the concurrent sequence includes
a replacement to B followed by an overlapping set request that unexpectedly
removes it. No integrity violation or stale-row deletion is necessary, so
existing 409/404 exception mappings do not handle it.

**Correction direction:** Serialize set/clear operations per user before reading
state, using a consistent lock target, or use a mutation design that cannot skip
the desired assignment based on stale ORM state. Merely adding another flush
does not solve this race.

**Verification needed:** Controlled two-session interleaving of set-current and
replace-default; both successful requests must leave a valid selected default,
or one request must explicitly fail with a retryable conflict.

## R05: A confirmed default change is lost from the UI after refresh failure

Priority: P2. Owner: EPMCDME-15112. Status: retained as reliability issue.

**Origin:** The optimistic hook and refresh pattern already exist on `main`.
The branch introduces a default-change handler that inherits this behavior.

**Requirement connection:** U1 requires rows to show whether a project is the
user's default. The AC does not explicitly prescribe optimistic updates,
zero-flicker behavior or handling every network failure. The issue is a
successful assignment remaining displayed as its old state, not an argument
that a particular optimistic implementation is mandatory.

**Evidence in the UI checkout:**

- `src/pages/settings/administration/usersManagement/components/UserProjectsTable.tsx:144`:
  `handleSetDefault` finishes the optimistic operation, then requests refresh.
- `src/hooks/useOptimistic.ts:48`: success removes the pending updater without
  committing its result to source state.
- `src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx:75`:
  refresh only sets the new user after both details and budgets fetch successfully;
  its catch logs the error and retains the old user.

**Scenario:** Set B as default successfully, then fail the following details
fetch or budget-details fetch. The pending update is removed, the stored user
object still marks A, and the refresh failure never replaces it. The dialog
returns to displaying A despite a success notification and B in the database.

**Correction direction:** Preserve confirmed membership/default state until
refresh succeeds, or make authoritative refresh and its visible failure handling
part of the mutation lifecycle. A budgets-fetch failure should not discard an
otherwise successful project-details refresh.

**Verification needed:** Successful set-default followed by a failed details
fetch and, separately, a failed budget fetch. Do not count ordinary loading
flicker alone as an AC failure.

## D02: Inactive budget query inconsistency (pre-existing dependency)

Priority: P1 for separate triage; not a new-branch regression.

[llm_factory.py](../../../../src/codemie/enterprise/litellm/llm_factory.py),
`_probe_direct_project_budget_scopes`, line 667, omits `b.is_active = TRUE`.
The same omission exists on `main`. The async batch probe in
[project_budget_repository.py](../../../../src/codemie/repository/project_budget_repository.py),
line 415, includes it. Both paths use the shared resolution cache.

The branch's new `_unfunded_project()` now depends on the synchronous probe.
An inactive assignment can therefore suppress fallback and populate a project
context in the shared cache. This is relevant to B6/B8 and should be linked to
the story, but it should not inflate the count of defects introduced by it.
Verify disabled-budget direct/proxy behavior separately before claiming a
specific provider-side outcome. Align eligibility checks without redesigning
category selection.

## Q04: Overflow marker is a UX concern, not a confirmed AC failure

`UsersManagementPage.tsx:288` adds a marker to the default badge.
`MAX_DISPLAYED_PROJECTS` is 3, and `DetailsBadges` renders later badges in an
overflow tooltip. The marker can be hidden at rest, but it is still accessible
without opening the user's details.

U3's literal wording is “clear without opening the user”; it does not require
that every default be in the first three permanently visible badges. The
story's “at a glance” intent supports prioritizing the default badge, but this
is weaker evidence than the initial review claimed. Record as a UX improvement
or clarify whether hover counts as sufficiently visible; do not treat it as a
confirmed blocker solely from the supplied ACs.

## Q06: Personal identity spelling is not specified by the ACs

With different username/email values, choosing the email-named personal
membership as default changes the headerless proxy project's reporting value
from username to email. Billing still follows personal handling.

The requirements identify the personal project but do not prescribe a canonical
analytics spelling for it. An email-valued project may correctly identify that
membership even though the provider customer is keyed by username. Equality
between a provider customer ID and a project attribute is not required.

Consequently, withdraw the initial classification as a confirmed B10 bug.
Any normalization change needs an agreed mapping between personal project,
analytics grouping and provider customer identity; do not re-key customers or
historical spend based only on this observation.

## Other scope and design decisions

- Add-with-default is one UI action implemented with two API calls. U2 does not
  require transactional atomicity. The existing partial-success message and
  resync are relevant; do not demand a new combined endpoint as an AC fix.
- A populated frontend `currentProject`, billing information in chat/profile,
  or a per-request billing selector is not required. Billing-project visibility
  outside User Management is explicitly excluded.
- No UI clear-default action or new users-list column is mandated.
- The old dependency name `admin_access_only` is misleading but its behavior
  admits both admin and maintainer. The earlier suspected role mismatch was
  withdrawn after source inspection.
- New set-default success toasts are duplicated between store and component.
  This is a minor code smell, not an AC failure.
- `_unfunded_project()` imports a private factory database probe and predicts
  attribution before model/category selection. This design concern explains
  R01; it is not an additional independent bug.

## Delivery observation (separate from AC correctness)

At the preceding review's GitLab lookup,
[MR !4315](https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4315) had head
`edca7d818bb60c12958b1c9ce1bedd89d34bec13`, behind reviewed local backend
`785cefa78`. Its description still excluded billing work and mentioned the
removed personal-project guard. This is a timestamped publication/documentation
observation, not an application bug or a claim that the MR has not changed since.

## Original review validation limits

No runtime reproductions, tests, fixes, commits, pushes, Jira mutations or MR
comments were performed for this AC reconciliation. Only Markdown review files
were added in the backend workspace. Existing unrelated working-tree changes
were preserved.
