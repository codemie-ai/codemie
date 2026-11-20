# Gate bulk budget tests without codemie-enterprise — Story

**Date**: 2026-11-18
**Status**: Approved
**Architecture review**: intent, 2026-11-18, 9312305286e2
**Ticket**: EPMCDME-17403

---

## Context

- The test suite has two files involved: the project-budget router tests and the spend-attribution tests. Together they hold 157 tests; with the enterprise package installed all pass.
- Without the enterprise package, 6 of them fail (151 pass). Five are the bulk project-budget router tests; one is the spend-attribution drift test.
- The budget-management guard rejects requests when the feature is off. The member-request test helper turns the feature on (EPMCDME-14536); the bulk-request helper added in EPMCDME-16071 does not, so even the validation cases return 403 instead of 200/422.
- The drift test imports the enterprise litellm service in its body with no skip when the package is missing.
- The existing `--skip-enterprise` option skips only tests marked enterprise or under the enterprise folder, so it does not cover these tests (EPMCDME-17212 does not fix them).
- Test-only change; no product runtime or UI behaviour changes.

---

## Complexity

**Size**: XS (7/36) · **Recommended flow**: sdlc-light

Two test files, one fixture and one test method; both fixes have exact precedents in the same codebase.

---

## Story

**As a** backend developer running the test suite without the enterprise package, **I want** the bulk budget router tests and the attribution drift test to pass or skip cleanly **so that** a non-enterprise run reports no false failures.

---

## Background

Contributors and CI runs without codemie-enterprise get 6 failures in two budget test files, even when enterprise tests are skipped. The failures are not product defects: five come from a test helper that does not enable budget management, one from an unguarded enterprise import. The noise hides real regressions. It was reproduced on macOS and Windows/WSL.

---

## Acceptance Criteria

- [ ] AC1: Given no codemie-enterprise package, when the bulk router tests run, then all five pass (200 for the valid request, 422 for zero items, over 1000 items and negative amounts).
- [ ] AC2: Given no codemie-enterprise package, when the attribution drift test runs, then it is reported as skipped, not failed.
- [ ] AC3: Given no codemie-enterprise package, when the full suite runs with enterprise tests skipped, then neither file contributes a failure.
- [ ] AC4: Given codemie-enterprise is installed, when the two files run, then all 157 tests pass.
- [ ] AC5: Given budget management is disabled, when the project-budget admin endpoint is called, then the existing disabled-guard test still verifies the rejection.

---

## Out of Scope

- NG1: Any product runtime, API or UI change.
- NG2: Changes to the `--skip-enterprise` option or other test gating (EPMCDME-17212).
- NG3: Gating other tests not named in the ticket.

---

## Decisions

- D1: Scope and fix approach → as specified in EPMCDME-17403 — user (ticket text)

---

## Open Questions

- None
