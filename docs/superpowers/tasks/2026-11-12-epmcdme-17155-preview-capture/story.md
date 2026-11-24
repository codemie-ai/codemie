# Capture preview in the validation pass — Story

**Date**: 2026-11-12
**Status**: Approved
**Ticket**: EPMCDME-17155 (https://jiraeu.epam.com/browse/EPMCDME-17155)
**Parent**: ../../story.md
**Feature**: automatic-preview-cover

---

## Context

- Every version passes through the validator pod before it can run. That pod has no credentials, no service-account token and a network policy that allows only DNS and the scanner.
- The validator process parses files and executes nothing. The validation policy records this (`execution.server: False`), and the architecture document records "no server-side rendering" as decision D1.
- The worker already pulls the validator's output back file by file and writes it under the validation attempt prefix. The attempt ledger, retention, cleanup, the storage sweep and quota already cover everything under that prefix.
- No headless browser exists anywhere in the repository. The validator image installs only seven hash-pinned packages.
- This child produces the stored picture. The sibling child "Serve preview from /thumbnail" shows it on the card.

---

## Complexity

**Size**: XL (28/36) · **Recommended flow**: sdlc-standard (kept whole by user decision D7; the score recommends a split)

The renderer runs untrusted uploaded code in the validator pod, and no sandbox in the repository covers that; the work also spans the validator, the worker, storage, the image and deployment.

---

## Story

**As an** owner of a hosted application, **I want** CodeMie to take one picture of my version's start page while it checks the version **so that** the catalog can show what my application looks like, without anyone launching it and without new infrastructure.

---

## Background

Most cards show initials because owners rarely upload a cover and bundles rarely contain one. The validation pass already holds every version's files in an isolated pod. A capture there, in a separate sandboxed process, gives each accepted version a picture once. It adds no pod, no job kind and no storage kind.

---

## Acceptance Criteria

- [ ] AC1: Given a version passes validation and its start page renders, when validation finishes, then one picture of the start page is stored with that version's validation output, within the existing cover limits (1 MiB, 2048 px per side). (Amended by the MR !4837 review (AR-2): a version that is accepted on a retry gets no picture. A retried job never captures, so a capture that crashed the validator cannot repeat. See D7 in `docs/hosted-applications/architecture.md`.)
- [ ] AC2: Given the capture fails for a version (the page renders nothing, the renderer crashes or the capture times out), when validation finishes, then the verdict and the validation report are exactly the same as without previews, and no picture is stored.
- [ ] AC3: Given a slow or endless start page, when the capture runs, then it stops at its own time limit, and the validation pass still finishes inside its existing budget.
- [ ] AC4: Given a version that is rejected or fails validation, when validation finishes, then no picture is kept for it.
- [ ] AC5: Given the application's code tries to reach any network address, read any credential or read files outside the version's files during capture, when the picture is captured, then the attempt fails and nothing leaves the renderer except the picture. Accepted exception (decision CR-002, 2026-11-12): Chromium 156 needs reads of the browser install, `/usr/lib`, `/usr/share/fonts`, `/proc` and `/dev/null`, and execution of its crashpad handler and the musl loader. The validator hides its own `/proc` files (`PR_SET_DUMPABLE` 0), so the renderer cannot read the validator's credentials there.
- [ ] AC6: Given the validator process, when a version is validated, then it still executes no application code, and the validation policy and its version stay unchanged; only the separate renderer process runs application code.
- [ ] AC7: Given a version is retired or its application is purged, when cleanup runs, then the picture is deleted with the rest of the validation output, and its bytes count toward the creator's quota like the rest of that output.
- [ ] AC8: Given the feature is deployed, when an operator compares the deployment with the one before, then it has no new pod type, Deployment, Service or network policy; only the validator image and its resource limits change, and the chart, `kubernetes.yaml` and the runbook describe the same topology.
- [ ] AC9: Given the architecture document, when a reader looks up server-side rendering, then a new recorded decision states that the validator pod captures one picture per version in a sandboxed renderer, and why.

---

## Out of Scope

- Showing the picture on the card or changing the `/thumbnail` cover order (sibling child "Serve preview from /thumbnail").
- NG1: Interactive or live previews on cards.
- NG2: Previews for versions validated before this feature (no backfill).
- NG3: Changes to the manual cover upload, replace and remove in the form.
- New infrastructure components (pods, Deployments, Services, network policies, images other than the existing validator and API images).

---

## Decisions

- D1: Where does the renderer run? → In the existing validator pod, as a separate process next to the validator, reusing the pod's lack of credentials, its network policy and its sandbox — user (parent D5).
- D2: When is the picture captured? → In the validation pass, from the files the validator pod already holds; the worker keeps it only when the version is accepted; a capture failure never changes the verdict — user (parent D6).
- D3: May the feature add infrastructure? → No new pod, Deployment, Service or network policy — user (parent D7).
- D4: Where does the picture live? → Under the validation attempt prefix, with the rest of the validation output — proposed by PO (parent D8; technical analysis §9).
- D5: How does the version point to its picture (a manifest field or a version column)? → Left to the spec; the sibling child reads it through that pointer — proposed by PO: both fit D4, and the choice is technical.
- D6: Where is the new architecture decision recorded? → In this child, because this child introduces server-side capture; the sibling child only updates the "Card covers" order — proposed by PO: the decision must land with the change that reverses architecture D1.
- D7: Split this child further (renderer, then storage)? → No, keep one sub-task; its sdlc-standard run treats the split verdict as advisory — user (2026-11-12).

---

## Open Questions

- None

---

## Sources

- Parent story `../../story.md` (AC1, AC5, AC6, AC8, AC9; D2, D5–D8).
- `technical-analysis.md` in this folder (sliced from the parent).
