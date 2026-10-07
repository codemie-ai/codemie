# EPMCDME-15109: Acceptance criteria reconciliation

Date: 2026-09-26

Follow-up: R01, R03 and R05 are now addressed in the working trees. See
[fixes.md](fixes.md). The source assessments below preserve the original review
baseline; runtime verification remains outstanding.

## Review basis

The source of requirements is the full text of EPMCDME-15109, EPMCDME-15110,
EPMCDME-15111 and EPMCDME-15112 supplied by the user in this conversation. It
supersedes assumptions in the earlier checked-in implementation specifications.
Live Jira retrieval was unsuccessful; this review does not claim to have read
additional Jira comments or ticket revisions.

Reviewed implementation:

- Backend: `EPMCDME-15110_default-project-foundation`, commit
  `785cefa78c6a4526957b17dc2cb4991b7fb497dc`.
- UI: `EPMCDME-15112_user-management-default-project`, commit
  `dd6f9c394da37e4ed50bab3db353bfbe9a634416`, local repository
  `D:/Projects/codemie-ui`.
- Origins of findings were compared with each repository's local `main`.

This is source analysis. “Implemented by source” below means a relevant code
path exists and no additional defect was identified in this review; it does not
mean the AC was freshly verified with runtime tests. Prior live-verification
reports were read as historical evidence, not treated as new test results.

See [findings.md](findings.md) for scenarios, source references, provenance and
the disposition of every finding from the initial review.

## EPMCDME-15110: Foundation

| AC | Requirement | Source assessment |
|---|---|---|
| F1 | Replacing a default leaves the old membership ordinary and exactly one default | Ordinary swaps are implemented, including the old-row-first flush fix. Concurrent updates remain unsafe: R03. |
| F2 | Reject setting a non-membership as default, including direct API calls | Implemented by source: mutation requires an existing membership; missing membership returns 404. |
| F3 | Single or bulk removal leaves no default pointing to removed membership | Implemented by source: the flag lives on the deleted membership row. |
| F4 | Unauthorized users cannot set or clear another user's default | Implemented by source: both routes use platform admin/maintainer authorization. Project-admin status alone does not qualify. |

The story also asks for a deterministic resolved project list. Both synchronous
and asynchronous per-user membership queries now order by `project_name`.
`User.current_project` separately prefers the explicit default, then personal
membership, then a sorted fallback. Do not infer a requirement that the default
membership must be the first element of every project-list response: the text
requires deterministic resolution, not that serialization convention.

An initial suspected maintainer mismatch was withdrawn after reading the actual
dependency: `admin_access_only`, despite its name, accepts administrators AND
maintainers.

## EPMCDME-15111: Budget attribution

Numbering B1-B12 follows the order in the supplied ticket.

| AC | Requirement | Source assessment |
|---|---|---|
| B1 | Marketplace member bills assistant's project | Implemented by source: membership check precedes default selection. |
| B2 | Marketplace non-member with default bills default | Implemented by source for normal funded cases; fallback attribution edge case R01 remains. |
| B3 | Marketplace non-member without default bills personal | Implemented by source: existing personal fallback retained. |
| B4 | Unbound skill/workflow/other flow bills default | Implemented by source through `User.current_project` and context setup. Project-owned workflows are explicitly excluded from this rule. |
| B5 | Unbound flow without default is deterministic | Implemented by source: personal membership preference and sorted final fallback. The AC does not prescribe which non-default project must win. |
| B6 | Default without allocation falls back to personal/default, visibly flagged | Zero-scope case is implemented. Partially funded projects can fall back without corrected analytics/marker: R01. Category-specific policy is not explicitly defined by this AC. |
| B7 | Marketplace membership beats a different default | Implemented by source: same precedence as B1. |
| B8 | Headerless CLI/IDE uses default and same fallback rules as web | Implemented by source for normal cases; R01 affects category-gap attribution, and D02 is a pre-existing web/proxy eligibility inconsistency. |
| B9 | Explicit CLI project wins | Implemented by source: explicit header remains the first selection tier. This story does not authorize changing existing category-selection policy. |
| B10 | Analytics show the project whose budget paid | R01: project attribution can remain on an unfunded selected category after actual billing falls back to personal/global. Personal identifier spelling is not specified; see Q06. |
| B11 | Default changes affect new requests within about one minute; historical spend stays put | Implemented by source: default enters authentication snapshots, mutation invalidates local auth cache, auth TTL is 30 s, budget resolution TTL is 60 s. No historical re-attribution code was added. R03 can prevent a requested default change from producing the intended state. |
| B12 | Removed default project no longer bills; non-default rules apply | Implemented by source via membership deletion and refreshed authentication relationships, subject to existing cache delays. |

No requirement is inferred to redesign premium/CLI/platform category precedence.
R01 is about reporting the billing result that actually occurred, not prescribing
a different category or changing billing of project-owned assets.

## EPMCDME-15112: User Management UI

| AC | Requirement | Source assessment |
|---|---|---|
| U1 | Admin/maintainer sees each row's default and can select any membership | Implemented by source, including personal memberships. R05 covers stale displayed state after a successful mutation and failed refresh. |
| U2 | Add Project supports setting default as part of the same action | Implemented by source: one submission runs add then set-default; partial failure is explained and triggers resync. This AC does not require a single atomic backend transaction. |
| U3 | Projects cell marks default, clear without opening user | Marker exists. A default beyond three badges is available through the overflow tooltip without opening the user; Q04 is a UX concern, not a demonstrated literal AC violation. |
| U4 | No default means no marker | Implemented by source: marker depends on `is_default`. |
| U5 | Read-only viewers see markers but cannot change them | Implemented by source for auditors: markers remain visible, actions disabled, backend rejects mutation. Plain-user access needs the interpretation noted below. |

### Plain-user visibility interpretation

The supplied U5 says “for example an auditor or a plain user” and conditions the
behavior on opening User Management. Existing navigation permits admin,
maintainer and auditor; genuinely plain users do not have that tab. The saved
UI spec narrowed the reachable case to auditors. That saved interpretation is
not itself proof of an approved change to the supplied AC.

Do not report tab visibility as a confirmed bug or expand access automatically.
If the ticket intends to grant plain users read access to User Management, that
requires explicit clarification of the existing navigation and backend read
permissions. If it describes the behavior of an otherwise authorized read-only
viewer, the auditor path covers the reachable case.

## Parent EPMCDME-15109 coverage

The parent's assignment ACs map to:

| Parent assignment AC | Child coverage |
|---|---|
| 1: details rows and set any membership | U1 |
| 2: add dialog can set default | U2 |
| 3: replacing default preserves old membership | F1 |
| 4: users-list default marker | U3 |
| 5: no-default users have no marker | U4 |
| 6: reject non-membership | F2 |
| 7: removal clears default | F3 |
| 8: read-only visibility and API rejection | U5 and F4 |

The parent's twelve budget ACs map one-to-one to B1-B12 above.

## Scope constraints used to reassess the review

- Only platform administrators and maintainers assign defaults; project-admin
  status alone is insufficient. User self-service assignment is excluded.
- Defaults must be memberships; removal clears them. No bulk-default assignment
  or backfill is required.
- No billing-project picker, chat-header/profile visibility, or populated
  frontend `currentProject` is required. The context's “natural place” observation
  is not an AC, and user-facing billing visibility is explicitly excluded.
- No separate default-project users-list column is required.
- Do not search other projects for funding. Do not block merely because the
  default has no allocation.
- Project-owned non-marketplace assistants, workflows and datasources keep their
  existing asset-project billing behavior.
- Explicit CLI project selection and historical spend remain unchanged.
- The ACs do not require a UI control to clear a default without replacing it;
  the foundation nevertheless provides the requested clear API.

## Validation

No application code changed. No tests were written or run: the user requested AC
analysis and Markdown documentation. Evidence consists of source reads, diff
comparison and the existing review artifacts. Documentation verification is
recorded in the final response.
