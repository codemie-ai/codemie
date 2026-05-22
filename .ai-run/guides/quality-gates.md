# Quality Gates

## Gate Order

Use Makefile targets when available; they are the command source of truth for this repo. The Makefile defines install, build, test, ruff, license, gitleaks, verify, coverage, sonar-local, and run targets at `Makefile:15`.

### Lint And Format

**Run**: `make ruff`

**Pass**: Ruff format completes, `ruff check --fix` applies safe fixes, and final `ruff check` exits successfully. See `Makefile:30`.

**Fail**: Ruff reports remaining violations after auto-fix; fix the reported files before delivery.

**Auto-fix**: `make ruff` already runs format and fix steps.

### Build

**Run**: `make build`

**Pass**: Poetry builds the package successfully. See `Makefile:24`.

**Fail**: Packaging metadata, dependencies, or build configuration are invalid.

### License Headers

**Run**: `make license-check`

**Pass**: The Apache 2.0 header checker exits successfully. See `Makefile:45`.

**Fail**: One or more Python or shell files are missing required headers.

**Auto-fix**: `make license-fix`

### Secret Scan

**Run**: `make gitleaks`

**Pass**: Docker runs gitleaks and no hardcoded secrets are found. See `Makefile:51`.

**Fail**: A secret-like value is detected or Docker is unavailable.

**Skip if**: Docker is unavailable; report the environment block explicitly.

### Tests

**Run**: `make test`

**Pass**: Pytest exits successfully over `tests/`. See `Makefile:27`.

**Fail**: A test failure, import error, fixture error, or environment prerequisite is missing.

**Skip if**: The user did not request tests and the active task policy says tests are explicit-only.

### Coverage

**Run**: `make coverage`

**Pass**: Coverage runs pytest and writes HTML coverage output. See `Makefile:56`.

**Fail**: Test or coverage command fails.

**Skip if**: The user did not request coverage.

### Static Analysis

**Run**: `make sonar-local`

**Pass**: The Node-based Sonar runner completes successfully. See `Makefile:63`.

**Fail**: Sonar prerequisites, token/config, Node runtime, coverage generation, or server-side quality gate fails.

**Skip if**: Sonar configuration, network access, or required credentials are unavailable.

### Full Verification

**Run**: `make verify`

**Pass**: Ruff, license, gitleaks, and tests complete successfully. See `Makefile:54`.

**Fail**: The first failing prerequisite determines the next debugging target.

**Skip if**: The task scope does not call for full verification or environment prerequisites are missing.
