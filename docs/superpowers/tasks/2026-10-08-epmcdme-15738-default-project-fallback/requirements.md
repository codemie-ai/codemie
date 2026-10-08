# EPMCDME-15738: personal project is not selected as the default project

## Ticket

In the user details Projects table (Settings → Administration → Users), all Default options appear
unselected. The user's personal project should be the default.

Acceptance criteria (ticket):

1. For the reported scenario, the user's personal project is selected and displayed as the sole default.
2. Other assigned projects remain unselected.
3. The default shown in the table agrees with the stored default-project assignment.

## Agreed scope (with the product owner, 2026-10-08)

- Exactly one project is always shown as default for a user who has projects:
  stored default → personal project (named after the user's email) → first project by name.
- The fallback is derived, not stored: no migration, no DB writes. AC3 is relaxed accordingly
  (the shown default may be derived while the DB has no `is_default` row).
- The derived default is used everywhere the stored default was: every API response that reports
  `is_default`, and the authenticated user's `default_project`, so CLI/proxy calls without the
  `X-CodeMie-Project` header use the shown project.
- Approved behaviour change: for users without a personal project and without a stored default,
  global-assistant spend and header-less CLI/proxy calls go to the first project by name.
- An admin selecting a default stores it and it wins over the derived fallback.
- Legacy mode (`ENABLE_USER_MANAGEMENT=false`) is unchanged.

## Decision recorded during code review (product owner, 2026-10-08)

Admin views apply visibility rules (personal projects are visible only to their owner and super
admins; project admins see only shared projects they belong to). The default is decided from **all**
memberships before that filtering. When the effective default is a project hidden from the viewer,
the viewer sees no radio checked: the hidden project's name is not revealed, and no visible project
is falsely marked as the default runtime uses. "Exactly one default shown" therefore holds whenever
the default is visible to the viewer.
