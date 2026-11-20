# QA unit gate: failures are pre-existing

`make test` (gate-run.json, status BLOCKED) reported 78 failed + 1 collection error. The same full suite
(`pytest tests/ --continue-on-collection-errors`) was run on a clean `main` worktree (fbfeb23c9) and on this
branch (cf9bd22a8) with the same venv and flags:

| Run | Failed | Errors | Passed |
|---|---|---|---|
| main (fbfeb23c9) | 77 | 1 | 35760 |
| branch (cf9bd22a8) | 77 | 1 | 35870 |

The two sets of failing test ids are identical (79 entries each; `comm` shows none only-on-branch and none
only-on-main). 53 of them are in `tests/enterprise/mcp_auth` (`codemie_enterprise` is not installed); the full
list is in `qa-unit-failures-preexisting.txt` (local only, gitignored). The +110 passed on the branch are this change's new tests.

gate-run.json is left as the runner wrote it (BLOCKED); `feature.verification` was recorded `approve` by the user.
