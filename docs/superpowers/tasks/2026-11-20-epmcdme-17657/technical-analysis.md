# Technical Research

**Task**: ai-attribution cli-analytics rbac auditor
**Generated**: 2026-11-20
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-17657 (Bug): [AI Attribution][RBAC] Auditor role cannot access AI Attribution analytics - Backend returns 403.

Environment: CodeMie Preview; Analytics Dashboard -> AI Attribution; affected role: Auditor (is_auditor=true, without Admin or Project Admin privileges).

Description: AI Attribution restricts access to Super Admin or Project Admin. Auditors get HTTP 403 ("requires administrator or project administrator privileges"). The current implementation does not grant Auditor users read-only access to AI Attribution.

Steps: log in as Auditor (no Admin/Project Admin); open Analytics Dashboard -> AI Attribution; try the tabs and their API endpoints; check HTTP responses.

Actual: 403 on AI Attribution endpoints; Auditor role not in the backend authorization gate; UI access restricted or redirected by role.

Expected: Auditor has read-only access to AI Attribution data within authorized scope, subject to confirmation of RBAC requirements; Auditor must not get unauthorized access to private user sessions or projects; UI and backend enforce consistent rules.

Technical Findings (ticket): `cli_analytics.py` line 151 `_admin_gate` allows only is_admin or is_applications_admin; line 141 `viewer_access` grants Auditor nothing; `/sessions` is an exception (regular users see only own sessions). UI `AnalyticsDashboard.tsx` hides/redirects the tab (out of this repo).

Acceptance Criteria:
1. Confirm and document expected AI Attribution access for Super Admin, Project Admin, Auditor, Regular User.
2. If Auditor access is required, provide read-only access to authorized analytics data.
3. Strict project/user-level data isolation; prevent unauthorized session or prompt exposure.
4. Align frontend visibility and backend authorization.
5. Add automated RBAC regression tests covering all roles and AI Attribution endpoints.

Linked: EPMCDME-17650 (clones) - percentages rounding, unrelated.

---

## 2. Codebase Findings

### Existing Implementations

Purpose: "an Auditor can read AI Attribution analytics". The name "AI Attribution" appears nowhere in `src`, `tests` or `.ai-run/guides` (grep, case-insensitive, several spellings). The backend surface that matches is the OTel CLI analytics router `src/codemie/rest_api/routers/cli_analytics.py`, prefix `/v1/analytics/cli-analytics`. The mapping from UI name to this router is an inference.

- `_admin_gate` `cli_analytics.py:155-167`: allows `user.is_admin or user.is_applications_admin`, otherwise 403 "This action requires administrator or project administrator privileges." (matches the ticket's error text). Ticket line numbers are stale (the ticket says 151 and 141; the code has the gate at 155 and `viewer_access` at 145).
  - `User.is_applications_admin` (`rest_api/security/user.py:199`) = `admin_project_names` non-empty OR `is_admin_or_maintainer`. Auditor is not part of it.
  - Applied via `dependencies=[Depends(_admin_gate)]` to: `/overview`, `/cost`, `/users`, `/user-charts`, `/repositories`, `/branches`, `/projects`, `/tools`, `/activity`, `/efficiency`, `/frameworks` (routes at ~633-1030).
  - Not gated: `/classification-policy` (:187), `/sessions` (:1033, comment at :1054), `/sessions/{trace_id}` (:1074, comment at :1084), and ingest `/logs /metrics /traces /event-hooks` (:544-559).
- `viewer_access(user)` `cli_analytics.py:145-152`: returns `(bool(user.is_admin), frozenset(admin_project_names))`. Only a super admin is unrestricted; the docstring says this is deliberate because the shared `AccessFilter` would treat an auditor as unrestricted and "let an auditor read other users' shared sessions here although they cannot list them".
- `FilterParams._resolve` `:453-504`: for a non-unrestricted viewer it intersects requested projects with the viewer's admin projects, and sets `deny_all` when none are left. So an auditor with no admin projects would get an empty scope, not all data, if the gate alone were opened. `project_unattributed` is super-admin only (:490-493).
- `FilterParams.resolve_sessions` `:442-451`: `privileged = is_admin or is_applications_admin`; non-privileged users are forced `own_only`.
- Privacy rule (guide `.ai-run/guides/integration/cli-analytics-events.md:514`): a private Personal session is visible only to its owner "strictly, even for a super-admin or auditor". Non-admins get only own Personal on `/sessions`; "other routes stay 403". "An auditor has no extra right (own private Personal only, unless also a project admin)."
- Other auditor-aware analytics (the same purpose in sibling dashboards):
  - `service/analytics/access_filter.py:95-125`: `AccessFilter` gives super admin AND auditor unrestricted access.
  - `rest_api/routers/analytics/_spending_support.py` `_authorize_admin_budget_view`, with a test in `tests/codemie/rest_api/routers/test_analytics_auditor.py` (EPMCDME-10930).
  - `service/project/project_spending_service.py:258,482`: auditor allowed.
  - `service/user/user_management_service.py:704`: auditor allowed.

### Decisions Already in the Code

- CLI analytics uses its own privilege policy, not the shared `AccessFilter`. Auditor is deliberately excluded (`cli_analytics.py:145-152`; guide line 514). Alternative: the shared policy (auditor unrestricted), which the other analytics routers use. Reason is recorded in the docstring: avoid exposing others' shared sessions.
- Private Personal sessions are owner-only for every role including super admin and auditor (guide :514). Detail of another user's Personal session returns 404.
- Tests lock the auditor decision: `test_cli_analytics_owner_sessions.py:224-250` (role matrix; auditor -> `own_only`, `unrestricted=False`; auditor+project-admin keeps only its own projects).

### Task Statements Checked Against the Code

- "_admin_gate allows only is_admin or is_applications_admin": holds (`:158`).
- "viewer_access does not grant Auditor additional permissions": holds (`:152`), and it is documented as intended, so it keeps an existing mechanism in place (above; consumers `_resolve` :481 and `/sessions/{trace_id}` :1090).
- "/sessions allows regular users to retrieve only their own sessions": holds (`resolve_sessions`, :1054, :1084).
- "Auditor ... UI redirects": unverified (UI not in this repo; `AnalyticsDashboard.tsx` would settle it).
- "Line 151 / line 141": contradicted for line numbers only (155 / 145).
- "AI Attribution" = this router: unverified (the name is absent from the repo).
- "Auditor ... should be subject to confirmation of RBAC requirements": the ticket records no confirmed requirement; the guide records the opposite ("auditor has no extra right").

### Consumers of Changed Contracts

- `_admin_gate`: 11 route dependencies in `cli_analytics.py` (listed above). Tests that exercise it: `test_cli_analytics_pricing_visibility.py`, `test_cli_analytics_policy_contracts.py`, `test_cli_analytics_statement_timeout.py`, `test_cli_analytics_storage_wiring.py`, `test_analytics_no_core.py`.
- `viewer_access`: `FilterParams._resolve` (:481), `/sessions/{trace_id}` (:1090), and the role-matrix test above.
- `User.is_applications_admin`: used in 7 places outside `user.py`; not enumerated here.
- OpenAPI snapshot `tests/codemie/rest_api/router_guards/baselines/openapi.json` includes these routes (a description or response change may touch it).
- Reader-side scope (`scope.mode`, `unrestricted`, `admin_projects`) lives under `repository/cli_analytics` (not read in depth).

### Architecture and Layers Affected

Router (`cli_analytics.py`: gate, `viewer_access`, `FilterParams`) -> `LocalAnalyticsHandler` -> storage reader (`get_cli_analytics_storage().reader`, Postgres) with a scope object. Auth model `rest_api/security/user.py`.

### Integration Points

`authenticate` dependency, `customer_config.is_feature_enabled("cliAnalytics")` (`_ensure_enabled`, 404 when off), `UserIdentityResolver`, storage reader.

### Patterns and Conventions

- Gating is by route-level `dependencies=[Depends(_admin_gate)]`.
- Errors use `ExtendedHTTPException` (code/message/details/help).
- Scope is built in `FilterParams`, not in the handlers.
- Routes carry `@handle_errors`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-events.md:514` (visibility and role policy, quoted above). Also `cli-analytics-storage.md`, `development/security-patterns.md` (no auditor/_admin_gate mention found by grep).
- No guide documents a role matrix for Super Admin / Project Admin / Auditor / Regular User (AC 1 is a gap).

### Architectural Decisions
Auditor exclusion recorded in the `viewer_access` docstring and in the guide, as above.

### Derived Conventions
Read-only is inherent (all gated routes are GET).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_cli_analytics_owner_sessions.py`: `/sessions` role matrix incl. auditor (list and detail); aggregate path for auditor+project-admin (:277).
- `test_analytics_auditor.py`: auditor on the other analytics guards.
- `test_cli_analytics_project_gate.py`: ingest project gate.

### Testing Framework and Patterns
pytest, FastAPI TestClient via a `make(user)` fixture with a real `User` and a mocked handler (`env.handler`); `_role(**kw)` helper.

### Coverage Gaps
- No found test that asserts an auditor gets 403 (or 200) on each `_admin_gate` route.
- No per-endpoint role matrix across all four roles for the aggregate routes (`/overview`, `/cost`, ...).
- No test for `/classification-policy` and `/frameworks` by role.

---

## 5. Configuration and Environment

### Environment Variables
None specific found for this gate.

### Configuration Files
Feature flag `cliAnalytics` via `customer_config` (`cli_analytics.py:178`); the ingest policy and retention come from storage settings (`raw_retention_days`).

### Feature Flags and Deployment Concerns
The flag off -> 404 for the routes calling `_ensure_enabled`. No deployment manifest references the gate.

---

## 6. Risk Indicators

- The ticket's premise conflicts with a recorded decision: auditor deliberately gets no shared CLI-analytics rights (`cli_analytics.py:145-152`, guide :514, test `test_cli_analytics_owner_sessions.py:224`). It keeps `viewer_access` and the role-matrix tests in place (see Task Statements).
- The ticket says RBAC is "subject to confirmation"; no confirmed matrix exists in the repo, so AC 1 precedes AC 2.
- Speculative: opening `_admin_gate` alone leaves an auditor without admin projects at `deny_all` (empty data) through `_resolve`, because `viewer_access` marks them restricted. Visible data depends on how scope is defined for the auditor.
- Speculative: the two privilege policies (`AccessFilter`: auditor unrestricted; CLI policy: auditor restricted) coexist; a change to one touches the consumers of the other (`/sessions`, `/sessions/{trace_id}`, aggregates).
- Private Personal sessions must stay owner-only for auditors (guide :514); the reader-side predicate was not inspected.
- "AI Attribution" is not named in this repo; the UI part (AC 4) is outside this repository.
- OpenAPI baseline snapshot and the existing role-matrix tests may need updates.

---

## 7. Summary for Complexity Assessment

The surface is one router file, `cli_analytics.py`: the `_admin_gate` dependency (11 routes), `viewer_access`, and `FilterParams.resolve` / `resolve_sessions`. Behind it are the handler and the storage reader scope, which this research did not read in depth. The gate itself is a few lines. The scope logic is where an auditor's visible data is decided, and the reader-side privacy predicates sit below it.

The product has an explicit, documented and tested decision that an auditor has no extra CLI-analytics right, so the ticket asks to reverse a recorded policy while its own text says the requirement is unconfirmed. There is no role matrix document. Other analytics routers do give auditors unrestricted read access, via a different shared policy.

Test posture is good for `/sessions` roles and weak for per-endpoint role coverage of the gated aggregate routes. No UI code exists in this repo.

---

## 8. External References

None named by the task. (The UI file `AnalyticsDashboard.tsx` and the Jira ticket are outside this repository and were not read.)
