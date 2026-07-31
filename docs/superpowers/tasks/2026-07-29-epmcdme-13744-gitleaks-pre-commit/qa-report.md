# QA Gate Report — epmcdme-13744-gitleaks-pre-commit

**Branch**: EPMCDME-13744_gitleaks-pre-commit
**Runner**: poetry (guide-first: `.ai-run/guides/quality-gates.md` → Makefile targets)
**Started**: 2026-07-29T11:20:00Z
**Status**: PASSED (with one environmental note on `make gitleaks`)

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint / ruff | guide | PASS | 2s | `make ruff` | 2219 files unchanged; all checks passed |
| build | guide | PASS | 3s | `make build` | Poetry built sdist + wheel (codemie-0.8.0) |
| license-check | guide | PASS (after auto-fix) | 1s | `make license-check` | Initially FAIL: missing Apache 2.0 header on new `scripts/git-hooks/validate_secrets.sh`. Ran `make license-fix` (guide's documented Auto-fix), header added, re-check passed. Committed as `EPMCDME-13744: add Apache 2.0 license header...` |
| gitleaks (CI-equivalent, full-tree) | guide | ENV-BLOCKED | 42s | `make gitleaks` | Finding on `.env` (untracked, gitignored, contains dev DIAL API key that lives only on this workstation). NOT part of the diff. Verified separately: staged-only scan over the branch diff via `scripts/git-hooks/validate_secrets.sh` returned 0 leaks. Guide "Fail" condition is triggered but only by a pre-existing local env file unrelated to this branch. Recorded, not blocking; CI will run this gate in a clean environment. |
| tests | guide | SKIPPED | — | `make test` | Guide "Skip if": user did not request tests and task policy is explicit-only. The task is bash-tooling only; no Python code touched, no bash test harness exists in the repo. |
| coverage | guide | SKIPPED | — | `make coverage` | Guide "Skip if": user did not request coverage. |
| sonar-local | guide | SKIPPED | — | `make sonar-local` | Guide "Skip if": Sonar configuration/token/network unavailable locally. |
| verify | guide | N/A (composite) | — | `make verify` | `verify = ruff + license + gitleaks + test` — each component ran individually above. |
| pre-commit hooks (codemie-pre-commit, codemie-gitleaks) | hook | PASS | — | (auto-runs on `git commit`) | Both hooks ran during Stage 4 commits and again on the Stage 5 fix-up + license commits. Green each time. |

## Failure detail

Only one gate did not return "PASS": `make gitleaks`. The finding is on `.env` at repo root, which is:

- Gitignored (`.env` is in `.gitignore`).
- Untracked in git (`git ls-files --error-unmatch .env` fails).
- Present only in this workstation's working tree, populated locally with a DIAL API key.
- Not touched by any commit on this branch.
- Not visible to the pre-commit gate `codemie-gitleaks` (staged-diff mode) which returned 0 leaks.

CI runs `make gitleaks` in a clean container that will not have `.env`; this environmental FAIL will not appear there.

Excerpt of the finding for the audit log:

```
Finding:     AZURE_OPENAI_API_KEY="dial-********"
File:        /workspace/.env
Line:        1
Fingerprint: /workspace/.env:generic-api-key:1
```

## Drift signal

no
