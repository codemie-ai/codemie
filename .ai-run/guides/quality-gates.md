# Quality Gates

## Gate Order

Use Makefile targets when available; they are the command source of truth for this repo. List the current targets rather than trusting a copy here: `grep -nE '^[a-zA-Z_-]+:' Makefile`.

### Lint And Format

**Policy**: Every language present in a change should be covered by a lint gate before delivery. Python is covered below; gaps for other languages (shell, container manifests, config formats) are tracked under EPMCDME-13739 and its sub-tasks.

**Run**: `make ruff`

**Pass**: Ruff format completes, `ruff check --fix` applies safe fixes, and final `ruff check` exits successfully. See the `ruff` target.

**Fail**: Ruff reports remaining violations after auto-fix; fix the reported files before delivery.

**Auto-fix**: `make ruff` already runs format and fix steps.

### Build

**Run**: `make build`

**Pass**: Poetry builds the package successfully. See the `build` target.

**Fail**: Packaging metadata, dependencies, or build configuration are invalid.

### License Headers

**Run**: `make license-check`

**Pass**: The Apache 2.0 header checker exits successfully. See the `license-check` target.

**Fail**: One or more Python or shell files are missing required headers.

**Auto-fix**: `make license-fix`

### Secret Scan

Two gates run gitleaks against the same image with the same `.gitleaks.toml` allowlist. The
image and tag are in the `Makefile`.

**CI / verify gate — `make gitleaks`**

Runs `gitleaks dir` against the full working tree. Docker-only; CI runners always have Docker. See the `gitleaks` target.

- **Pass**: Docker runs gitleaks with `--config=/workspace/.gitleaks.toml` and no hardcoded secrets are found.
- **Fail**: A secret-like value is detected or Docker is unavailable.
- **Skip if**: Docker is unavailable; report the environment block explicitly.

**Local pre-commit gate — `codemie-gitleaks`**

Runs `gitleaks protect --staged` via `scripts/git-hooks/validate_secrets.sh`, wired as the `codemie-gitleaks` local hook in `.pre-commit-config.yaml`. Detects Docker → Podman → Apple Containers and uses the first live engine. Hard-blocks the commit (exit 1) with an actionable hint if no engine is running.

- **Pass**: gitleaks reports no leaks in the staged diff.
- **Fail**: A secret is detected in staged changes, or no container engine is available.
- **Bypass**: `CODEMIE_PRECOMMIT_ENABLED=false` skips both `codemie-pre-commit` and `codemie-gitleaks`. Use only when Docker/Podman is unavailable and the change is verified to contain no secrets — do NOT weaken to warn-and-continue.
- **Policy**: HIGH priority. Secrets must not enter a local commit.

### Tests

**Run**: `make test`

**Pass**: Pytest exits successfully over `tests/`. See the `test` target.

**Fail**: A test failure, import error, fixture error, or environment prerequisite is missing.

**Skip if**: The user did not request tests and the active task policy says tests are explicit-only.

### Coverage

**Run**: `make coverage`

**Pass**: Coverage runs pytest and writes HTML coverage output. See the `coverage` target.

**Fail**: Test or coverage command fails.

**Skip if**: The user did not request coverage.

### Static Analysis

**Run**: `make sonar-local`

**Pass**: The Node-based Sonar runner completes successfully. See the `sonar-local` target.

**Fail**: Sonar prerequisites, token/config, Node runtime, coverage generation, or server-side quality gate fails.

**Skip if**: Sonar configuration, network access, or required credentials are unavailable.

### Full Verification

**Run**: `make verify`

**Pass**: Ruff, license, gitleaks, and tests complete successfully. See the `verify` target.

**Fail**: The first failing prerequisite determines the next debugging target.

**Skip if**: The task scope does not call for full verification or environment prerequisites are missing.

### Test Harness (MR compliance gate)

**Run**: `make test-harness`

**Pass**: The end-to-end test harness completes successfully. See the `test-harness` target.

**Fail**: One or more scenarios fail; paste the terminal summary into the MR anyway so reviewers see the failure.

**Skip if**: You are not opening a merge request (local iteration only).

To run live checks against the changed behavior, follow `.ai-run/guides/testing/local-verification.md`.

> **Required for the MR compliance bot**: paste the copy-pasted terminal summary of `make test-harness` into a `## Test harness` section of the MR description as a code block, following the [MR template](../../.gitlab/merge_request_templates/Default.md) (see [git-workflow.md](standards/git-workflow.md#mr-description)). Screenshots are not accepted. Without this section the `auto_epm-cdme_vcs` bot fails checks 3.1 and 3.2. Prereqs: docker stack up (`docker compose up -d`), superadmin fixtures, `~/.codemie/test-harness.json`; see the setup guide for the ENV=local Bearer-hijack patch.

## Exit codes that mislead

A gate here can fail for reasons unrelated to the change. The table is in
[`security/README.md`](security/README.md#exit-codes-that-mislead) — `make gitleaks` needing Docker,
the pre-commit hook running `make sonar-local`, and collection errors caused by a stale environment.

A gate that could not run is unverified, not passed.

## After merge

No pipeline in this repository runs these gates:

```bash
ls .gitlab-ci.yml .github/workflows
```

Two mechanisms partly cover that gap, and neither executes a test:

- The `auto_epm-cdme_vcs` bot reads the MR description for the sections it requires, including the
  `## Test harness` block above. It checks text only.
- The regression suite runs when a human posts `/sanity` on the MR.

Whatever was not run locally was not run. The `/sanity` request wording is in
[`security/README.md`](security/README.md) § Regression run.
