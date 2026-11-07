---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 0d743105a452bb98f026c07a4a3e119309507519
---

# Code review — EPMCDME-16849 backend capability

**Decision:** approve (completed implementation review: final round `request-changes`, one fix round, check round `approve`, confidence medium). Gate record: `decisions.jsonl` (`code-review.final` request-changes, `code-review.check` approve, both human-decided); machine-readable record: `code-review-final.json` (`passed: true`, `open: 0`). This is not a new review run, and it is not the responsible security approval, which stays a separate reviewer step before merge.

## Revision binding

| | SHA |
|---|---|
| Diff base (`origin/main` at review time) | `25a3458506ab12b3c93ce2540d096480cd2fac94` |
| Original reviewed code head (check round approved, pinned) | `bb83b827cc08a2a036144b7df8acd4d598a9ac21` |
| Same commit after rebase onto `origin/main` `4d5635444` (patch-identical per `git range-diff`) | `dc83dfcc1c4038307dcde87fd33add4647775701` |
| Same commit after the second rebase onto `origin/main` `e0ce2543cc84804c952054c50f7860acc6482802` (all 13 earlier entries patch-identical per `git range-diff`) | `1e70b0408873f7c5abd47e6f869ebe4f1e1fa23e` |
| Review-fix code head for autoreview finding `f-aa09e547e185`, independently reviewed (the original `approve` plus this delta review) | `5996b17a541de4c3389767b3d8eb1da76e10a986` |
| Same fix head after the third rebase onto `origin/main` `cdfa7083ef` (two unrelated EPMCDME-13429 commits, no file overlap; all 15 earlier entries patch-identical per `git range-diff`) | `3a759943c82f57d46cd6f649cd7ae55bfb93967b` |
| Same fix head after the fourth rebase onto `origin/main` `b870d1f18f03f0de1ad5238cbfc303ba178fa107`; this is `reviewed_head` above | `0d743105a452bb98f026c07a4a3e119309507519` |
| Earlier published heads (MR !4734, before the force-with-lease update) | `2c6d29d62e4f4ec434a88d9883357a03e2376650`, `1f49ba9fc112875dfbe29fc0deb85ffb794cf941`, `121393c4c0e5ad14eb02d75873413c1f26b49fec` |

- **Autoreview finding `f-aa09e547e185` (legacy append to a contract-bound session): confirmed and fixed** in `5996b17a54`. The shared locked guard (`_resolve_session`, after `check_owner`) now discards a legacy request on a session whose stored (contract, consent) is not (None, None) with `session_contract_conflict`, for the whole batch; funding is not compared. Evidence, all with real exit 0: ingestor-level tests (3 OTLP routes x N0/N1, mixed batch, hook events) and one persisted PG17 test; after rebase 778 unit tests and 71 PG17 tests passed (`evidence/backend-aa09-independent-review.md`). No red-before-fix log was persisted, so none is cited. PG14, the full harness and the 47-minute sanity run were not rerun for this fix.

- Fourth rebase verification: no conflicts or manual source resolutions. New target changes EPMCDME-16713 and EPMCDME-16833 preserve nullable line counts and whole-session summary fields alongside this MR's funding buckets. Range-diff maps all 16 branch commits; two entries differ only in inherited hunk context. At rebased head `94ee0c0872`, 532 scoped handler/model/reader/privacy-guard tests passed (exit 0), and 39 funding/summary/nullable public properties and their requiredness match the OpenAPI snapshot using FastAPI's `exclude_none=True` serialization. Prior PG and manual harness evidence remains bound to its recorded revisions; neither was rerun for this context-only rebase.

- Reviewed scope: 35 files, +2225/-74 at `1b7916e51` (final round, full profile: blind, edge-case, verification-gap, acceptance, standards), plus the 4-file, +39/-1 review-fix diff at `bb83b827c` (check round, targeted verifier). Final diff against base: 35 files, +2263/-74, plus the task artifacts.
- After the review, two commits were added: the one-line OpenAPI snapshot `$ref` correction (`45b2eaf2b`, rebased `a7ce27fb2`) and the task-artifact commit (docs only). The rebase onto `4d5635444` brought two unrelated target commits (EPMCDME-13944, EPMCDME-16609) that touch five of the same files; all 11 branch commits are patch-identical after it. The overlap was rechecked with 539 targeted tests, `make ruff`, a live-vs-snapshot OpenAPI `components` comparison and a gitleaks scan of the pushed patch; it was not re-reviewed by a review round.

## Findings

| ID | Final round | Check round |
|---|---|---|
| CR-001 | Blocking: contract/consent headers without evidence headers fell to the legacy gate and were stored with a 200 | **Resolved**: 422 `publication_contract_requires_session` (final) before the body on all four ingest routes; tests added |
| CR-002 | Blocking (reachability unconfirmed): echo of the bound project name might fail latin-1 header encoding after the write | **Conditional / unconfirmed, not a confirmed fixed defect**: project names are ASCII-only (`ProjectService.PROJECT_NAME_PATTERN`); `Bound-Project` is always sent; hardening noted in the contract handoff |
| CR-003 | Not in the verdict JSON (controller-added acceptance gap) | **Resolved**: `cost_by_model` now lists personal-only models with `cost_usd` 0 and their buckets; recorded in the check JSON `finding_status`; test added |

Checked clean in the final round: commit format, code quality, security. 15 findings filtered as noise.

## Accepted QA limits (not a pass)

- QA gates ran at `bb83b827c` (`dc83dfcc1` after rebase); the snapshot and docs commits were not rerun through the gates. `gate-run.json` is `BLOCKED` with accepted exceptions recorded; it is not a full PASS.
- Passed: `make ruff`, `make build`, `make license-check`, 230 targeted tests, gitleaks via Podman, live consent/funding/binding tests and migration `a1c1a0000010` up/down/re-up on Podman PG14 and PG17 (affected files only, 0 skipped). Post-rebase: 539 targeted tests, `make ruff`, OpenAPI components comparison and gitleaks over the pushed patch.
- Full OpenAPI guard NOT passed: 24 enterprise LLM-proxy path entries missing live (same on baseline 25a3458); live `components` equal the snapshot. `test_baseline.py::test_matching_live_object_passes` fails here (Windows newline translation, source-identified, not baseline-executed).
- 7 live `test_live_dispatch_gate.py` failures on PG14 and PG17: 2 proven on baseline 25a3458, 5 same-cause by source-backed inference only.
- **Sanity-API test harness passed (manual run, 2026-11-05):** `codemie-test-harness==0.1.770 run sanity-api -n 2 --reruns 2`, offline, against a backend built from this branch's source before the `f-aa09e547e185` fix and the second rebase (tested at `1f49ba9fc1`, code head `dc83dfcc1`; it is **revision-bound to that code and is not a result for `5996b17a54`**, and it was not rerun) in the supported no-retrieval profile: `190 passed, 10 skipped, 2 rerun in 2829.20s (0:47:09)`, exit 0 (`results.json`: 200 collected, 0 failed, 0 error). The 10 skips are not passes: 7 are retrieval-capability skips (datasource/file indexing not verified) and 3 are excluded from the sanity suite; the `/v1/index` cleanup `404` is expected in that profile. This is sanity-regression evidence only; it does not change the accepted limits above, make `gate-run.json` a PASS, or replace the focused analytics evidence and the separate security approval.
- Documentation cleanliness: the local research file `arch-review/facts-topup.local/technical-analysis.md` remains untracked (archived outside the repo in the task's evidence folder) and is not part of the MR.

## Cross-repository evidence (CLI child, not part of this repository's diff)

The earlier blanket "no real CLI or E2E run" is superseded by `evidence/live/manual-result.md` of the same task: a live `codemie-claude` manual run (Bash `cd` to another folder, then `/clear`) through the task launcher against a real local daemon, backend router, ingestor and PostgreSQL 17 passed, after a CLI + daemon + backend + PG17 harness run of 45/45 (CLI reviewed at `60252f55`, later docs-only head `3d735270`) and focused tests for the final CLI fixes. Limits that stand: the backend auth/application directory was synthetic and production authentication was not verified (the interactive re-auth prompt hit a stub 404), and no complete outbound model request was inspected, so model-context separation is supporting evidence, not wire-level proof. Mocks and synthetic echo tests remain not CLI acceptance.

## Separate approvals still required before merge

Responsible security reviewer approval and a reviewer `/sanity` run (security-relevant code, no pipeline config in this repository). Neither is given or implied by this digest.
