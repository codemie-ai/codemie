# Enterprise Feature Gating by Package Presence — Story

**Date**: 2026-08-29
**Status**: Approved
**Ticket**: [EPMCDME-14536](https://jiraeu.epam.com/browse/EPMCDME-14536)

---

## Context

- Cost Centers, Budget Management, and Chargeback are controlled solely by boolean flags in `customer-config.yaml` (`costCenters`, `budgetManagement`, `projectChargeback`), with no check against whether the `codemie-enterprise` package is actually installed on the deployment.
- One flag, `enterpriseEdition`, already implements the target pattern: it is computed at runtime from package presence (`importlib.metadata.version("codemie-enterprise")`) and YAML cannot override it — this is the precedent to mirror, not a new mechanism to invent.
- Server-side enforcement is inconsistent across the three features today: Cost Centers and Chargeback each have a service-level flag check, but no backend code reference to `budgetManagement` was found anywhere outside the YAML file — Budget Management may not be enforced server-side at all.
- UI enforcement is also inconsistent: the Budget Management nav tab already combines its flag with the enterprise-edition check; the Cost Centers nav tab does not.
- A same-named but functionally unrelated `codemie-chargeback` service (a standalone BigQuery cost aggregator) exists in the workspace and is out of scope — this story concerns only the `features:projectChargeback` flag inside `codemie`/`codemie-ui`.

---

## Complexity

**Size**: M (20/36) · **Recommended flow**: sdlc-standard

Touches a shared config mechanism (`CustomerConfig`) plus service, router, and UI layers across two repos, closes a real licensing/access-control gap for Budget Management (no confirmed server-side gate exists today), and leaves open design decisions two prior stories deliberately deferred — enough uncertainty and cross-cutting risk to brainstorm the approach before planning.

---

## Story

**As a** platform engineer setting up a non-enterprise CodeMie deployment, **I want** Cost Centers, Budget Management, and Chargeback to stay unavailable regardless of how their customer-config flags are set, unless the `codemie-enterprise` package is actually installed, **so that** a customer config can't accidentally expose enterprise-only functionality on a deployment that doesn't have the enterprise package.

---

## Background

Today these three features are gated purely by boolean flags in `customer-config.yaml`. Nothing ties them to whether the `codemie-enterprise` package is present, unlike `enterpriseEdition`, which already computes its value from package presence and cannot be re-enabled by YAML. This means a misconfigured or copy-pasted customer config could turn on budget management, cost centers, or chargeback on a deployment that never installed the enterprise package — and enforcement itself is inconsistent today: Budget Management appears to have no server-side guard at all, and the Cost Centers nav tab isn't combined with the enterprise check the Budget Management tab already uses. Aligning all three flags with the existing `enterpriseEdition` pattern closes this class of misconfiguration and reduces the number of flags a customer setup needs to reason about correctly.

---

## Acceptance Criteria

- [ ] Given a deployment without the `codemie-enterprise` package installed, when `customer-config.yaml` sets `features:costCenters` to `true`, then Cost Centers remains unavailable in both the API and the UI, as if the flag were disabled.
- [ ] Given a deployment without the `codemie-enterprise` package installed, when `customer-config.yaml` sets `features:budgetManagement` to `true`, then Budget Management remains unavailable in both the API and the UI — including on deployments where this flag is not currently enforced server-side at all.
- [ ] Given a deployment without the `codemie-enterprise` package installed, when `customer-config.yaml` sets `features:projectChargeback` to `true`, then Chargeback remains unavailable in both the API and the UI, as if the flag were disabled.
- [ ] Given a deployment with the `codemie-enterprise` package installed, when all three flags are set to `true`, then Cost Centers, Budget Management, and Chargeback behave exactly as they do today — no regression for enterprise deployments.
- [ ] Given a deployment without the `codemie-enterprise` package installed, when a client calls a Cost Centers, Budget Management, or Chargeback API endpoint directly (bypassing the UI), then the request is rejected the same way it would be if the corresponding flag were explicitly disabled.
- [ ] Given `customer-config.yaml` omits one of the three flags entirely, when the `codemie-enterprise` package is not installed, then the feature defaults to unavailable rather than the current default-enabled-when-absent behavior.
- [ ] Given the `codemie-enterprise` package IS installed but a flag is explicitly set to `false`, when a client accesses that feature, then it remains disabled — package presence raises the ceiling, it does not override an explicit `false`.

---

## Out of Scope

- The standalone `codemie-chargeback` BigQuery aggregator service — unrelated to this story's `projectChargeback` flag; no change to that service.
- Changing whether these features are enabled by default when the `codemie-enterprise` package IS installed — two prior stories already deliberately left broader/default enablement out of scope, and this story does not revisit that.
- Any new UI/UX for explaining *why* a feature is hidden (e.g., an "enterprise only" tooltip or upsell) beyond simply not showing or allowing it.
- The pre-existing gap where Chargeback's `cost_center` attribution depends on Cost Centers functionally but not at the flag level — tracked as a separate, already-known issue, not part of this story.

---

## Open Questions

- Should the UI indicate that a feature is hidden specifically because the enterprise package is missing, or should it look identical to "disabled by flag" with no distinction?
- Is Budget Management truly unenforced server-side today, or is it reachable through a guard this research didn't surface? This determines whether the backend change is "add a missing check" or "fix an existing one."
