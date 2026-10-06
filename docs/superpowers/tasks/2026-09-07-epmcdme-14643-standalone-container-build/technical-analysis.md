# Technical Research

**Task**: standalone container build podman docker packaging
**Generated**: 2026-09-07T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14643: Prepare reusable local standalone container build tooling. Implement in codemie/standalone-build/: A PowerShell 7 script for Windows/macOS/Linux. Parameterized BackendRoot, FrontendRoot, PackagingRoot and ImageTag with defaults derived from script location. Support Podman and Docker via explicit engine parameter. Reuse proven packaging flow from codemie-standalone repository (Dockerfile, docker/entrypoint.sh, docker/nginx.conf). Do not fetch/switch/modify source repos. Print source branches/SHAs and dirty status. Validate required inputs before assembly. Keep generated context/logs out of git. Preserve real build exit code; cache by default with NoCache option. Handle paths with spaces and shell-script line endings portably. Add README with prerequisites, examples, packaging-repo dependency, and known ES-free startup failure.

Spike artifacts already reviewed:
- C:/codemie-dev/codemie-standalone/local/build-upstream.ps1 (prototype PowerShell build script)
- C:/codemie-dev/codemie-standalone/local/spike-report.md (full spike results: upstream build PASS, ES-free startup FAIL confirmed)

Key spike findings:
1. Build-time compatibility confirmed: standalone Dockerfile/entrypoint.sh/nginx.conf build upstream sources without modification
2. CRLF bug: entrypoint.sh needs LF line endings on Linux (fix: .gitattributes *.sh eol=lf)
3. Podman HEALTHCHECK dropped in OCI format; add --format docker if needed
4. Three frontend directories needed: src, public, scripts, .keycloakify + several root config files
5. Backend needs: pyproject.toml, poetry.lock, src/, config/
6. ES-free runtime fails at manage_preconfigured_assistants() - known, not to be fixed
7. Packaging assets live in codemie-standalone repo (sibling directory to codemie)

Research needed:
1. What is in codemie/standalone-build/ currently (may be empty/non-existent)
2. What .gitignore patterns exist in this repo for generated artifacts
3. What sibling repos are present (codemie-ui, codemie-standalone layout)
4. Whether there are existing PowerShell scripts in the repo to understand conventions
5. The packaging repo structure (Dockerfile location, docker/ directory)

---

## 2. Codebase Findings

### Existing Implementations

- `codemie/standalone-build/` — does not exist; directory is fully absent from the repo (greenfield)
- No project-authored `.ps1` scripts exist in codemie; only `.venv/Scripts/activate.ps1` (third-party venv activation) is present
- `C:\codemie-dev\codemie-standalone\local\build-upstream.ps1` — prototype spike script; hardcoded absolute paths (`$STANDALONE`, `$CODEMIE`, `$CODEMIE_UI`, `$CTX`, `$LOG`, `$TAG`), no parameter block beyond `param()`, no engine abstraction (calls `podman` directly), no dirty-status or branch reporting, no input validation, `--no-cache` always set
- `C:\codemie-dev\codemie-standalone\Dockerfile` — three-stage: `node:20-alpine` (frontend build via `npm ci` + `vite build`), `python:3.12-slim` (backend via `poetry install --without dev --no-root` with `.venv` in-project), `python:3.12-slim` final (nginx + curl + git; COPY frontend dist + venv + backend src/config)
- `C:\codemie-dev\codemie-standalone\docker\entrypoint.sh` — generates `/usr/share/nginx/html/config.js` from env vars using embedded Python, starts uvicorn on port 8000, polls `/v1/healthcheck` for up to 60 s, then execs `nginx -g "daemon off;"` as PID 1
- `C:\codemie-dev\codemie-standalone\docker\nginx.conf` — nginx on 8080; SPA at `/`; proxies `/api/`, `/v1/`, `/code-assistant-api/` to uvicorn 8000; `/healthcheck` returns 200 inline

### Architecture and Layers Affected

This task creates a new **build tooling layer** only. No Python application code, no database code, and no FastAPI layer is touched.

- **New layer**: `codemie/standalone-build/` — PowerShell build script and README
- **Touched configuration**: `codemie/.gitignore` — no existing pattern covers `standalone-build/ctx/` or `standalone-build/*.log`; patterns must be added
- **Read-only dependency**: `codemie-standalone/` repo (sibling) — Dockerfile, `docker/entrypoint.sh`, `docker/nginx.conf` are consumed unchanged
- **Read-only dependency**: `codemie-ui/` repo (sibling) — frontend sources consumed read-only

### Integration Points

Sibling repo layout discovered at `C:\codemie-dev\`:

| Repo | Role |
|---|---|
| `codemie` (this repo) | Backend source (`src/`, `config/`, `pyproject.toml`, `poetry.lock`) |
| `codemie-standalone` | Packaging assets: `Dockerfile`, `docker/entrypoint.sh`, `docker/nginx.conf` |
| `codemie-ui` | Frontend source: `src/`, `public/`, `scripts/`, `.keycloakify/`, plus 13 root config files |
| `codemie-onboarding` | Separate repo; not involved |

The script must locate sibling repos by deriving paths from its own location (`$PSScriptRoot/../..`) and accept overrides via parameters.

Dockerfile COPY layout expected:
```
<CTX>/
  Dockerfile
  docker/nginx.conf
  docker/entrypoint.sh
  backend/pyproject.toml
  backend/poetry.lock
  backend/src/
  backend/config/
  frontend/package.json, package-lock.json, index.html, ...  (13 root files)
  frontend/src/
  frontend/public/
  frontend/scripts/
  frontend/.keycloakify/
```

### Patterns and Conventions

- `codemie/.gitattributes` line 1: `* text=auto eol=lf` — project-wide LF enforcement already in place for all files checked into this repo; this does NOT cover files from `codemie-standalone` when checked out on Windows
- `codemie/.gitignore` patterns for generated/local artifacts: `local/*`, `codemie-storage/`, `dist/`, `.ai-run/runs`, `docs/superpowers/tasks/**/*.diff` — pattern of excluding generated and runtime artifacts is established; `standalone-build/ctx/` and `standalone-build/*.log` follow the same convention but are not yet listed
- No PowerShell coding conventions exist in this repo to follow; the prototype in `codemie-standalone/local/build-upstream.ps1` is the only reference style

---

## 3. Documentation Findings

### Guides and Architecture Docs

No `.ai-run/guides/` guide covers build tooling, PowerShell scripting, or container packaging. The codegraph index covers the Python application; the build tooling domain is entirely outside it.

### Architectural Decisions

- Spike report (`C:\codemie-dev\codemie-standalone\local\spike-report.md`) records all key decisions: packaging assets are reusable without modification; upstream sources build successfully; ES-free runtime is a confirmed non-addressable failure; CRLF is a confirmed packaging bug requiring workaround
- `codemie-standalone` is explicitly the packaging repo; codemie contains only application code

### Derived Conventions

- From `.gitignore`: generated build context directories and log files belong in a subdirectory that is fully gitignored
- From `.gitattributes`: `* text=auto eol=lf` covers files checked into codemie; `entrypoint.sh` is in a different repo so the CRLF conversion must happen at copy time in the script (e.g., `[IO.File]::WriteAllText` with LF or a `sed`-equivalent in PowerShell)
- From spike prototype: build context is assembled under `local/upstream-ctx/`; the production script should use an analogous path such as `standalone-build/ctx/`

---

## 4. Testing Landscape

### Existing Coverage

None. Build tooling scripts have no test coverage in this repo. The codegraph index returned no test files for any build or packaging domain.

### Testing Framework and Patterns

Not applicable for this task. The validation of the build script is operational (run it, observe exit code).

### Coverage Gaps

The entire `standalone-build/` layer has no tests. This is expected and acceptable for build tooling; the spike itself served as the integration test.

---

## 5. Configuration and Environment

### Environment Variables

No application env vars are involved in building the image. The Dockerfile sets these runtime env vars with defaults:

- `FRONTEND_URL=http://localhost:8080`
- `ENV=development`
- `IDP_PROVIDER=local`
- `RATE_LIMIT_LOGIN=100/15minutes`
- `PYTHONPATH=/app/backend/src`

These are container runtime concerns, not build-script concerns.

### Configuration Files

- `C:\codemie-dev\codemie\.gitattributes` — `* text=auto eol=lf`; already enforces LF for all files in codemie
- `C:\codemie-dev\codemie\.gitignore` — does not yet include `standalone-build/ctx/` or `standalone-build/*.log`
- `C:\codemie-dev\codemie-standalone\Dockerfile` — three-stage build; defines expected context directory layout (`frontend/`, `backend/`, `docker/`)
- `C:\codemie-dev\codemie-standalone\docker\entrypoint.sh` — CRLF-sensitive; checked into a repo that may lack `*.sh eol=lf` in `.gitattributes`

### Feature Flags and Deployment Concerns

- `VITE_IS_ENTERPRISE_EDITION=false` is baked into the JS bundle at frontend build time (`npm run build` in Stage 1 of Dockerfile); the build script does not need to set this
- `--format docker` flag on `podman build` is needed to preserve `HEALTHCHECK` in the final image; the build script should surface this as a documented or optional flag
- GCP Poetry registry (R1) was not reproduced as a blocker in spike but depends on network conditions; no mitigation is needed in the script beyond documentation

---

## 6. Risk Indicators

- **CRLF in entrypoint.sh** (`C:\codemie-dev\codemie-standalone\docker\entrypoint.sh`): when codemie-standalone is cloned on Windows without `*.sh eol=lf` in its `.gitattributes`, entrypoint.sh will have CRLF, causing `#!/bin/sh\r` shebang failure on Linux. The build script must convert CRLF to LF when copying this file into the build context (spike confirmed workaround: `sed -i 's/\r//g'`; PowerShell equivalent: `[IO.File]::WriteAllText` with explicit `\n` replacement).
- **No `.gitignore` coverage for generated artifacts**: `standalone-build/ctx/` (assembled build context, can be hundreds of MB) and `standalone-build/*.log` are not yet ignored. Without this, a developer running the script will stage gigabytes of copied source files.
- **Podman OCI format drops HEALTHCHECK silently**: confirmed in spike. If `HEALTHCHECK` must be preserved in the built image, `--format docker` is required. The script should either always add it or document the flag.
- **Frontend selective copy complexity**: 4 directories (`src/`, `public/`, `scripts/`, `.keycloakify/`) plus 13 root config files (some of which may not exist in all checkouts — spike script already handles missing files with `WARN` log). Path-with-spaces handling needed throughout.
- **No PowerShell conventions in codemie**: the script is the first project-authored PowerShell file; no linting, no CI validation for it exists.
- **PackagingRoot default derivation**: the script must derive the default for `PackagingRoot` (codemie-standalone) from `$PSScriptRoot`. The expected relative path is `$PSScriptRoot/../../codemie-standalone` — this is fragile if the repo is cloned elsewhere; must be validated before use.
- **ES-free startup failure is a known non-issue** (`manage_preconfigured_assistants()` → `es.indices.create()` → `ConnectionRefusedError`): confirmed in spike, classified as runtime prerequisite not a packaging failure. Must be documented in README; no code fix expected.
- Speculative: if codemie-standalone gets a `.gitattributes` fix for `*.sh eol=lf`, the CRLF workaround in the build script may become redundant but is still safe to keep.

---

## 7. Summary for Complexity Assessment

This task creates a new build tooling directory `codemie/standalone-build/` that does not currently exist. The file change surface is narrow: one new PowerShell script, one new README, and a `.gitignore` extension. No Python application code, no database schema, no API routes, and no service layer are touched. The codegraph index has no relevant coverage because the domain is entirely outside the indexed Python application. All required knowledge comes from the two spike artifacts (prototype script and spike report), both of which were fully read.

The technical novelty is moderate. The prototype spike script (`codemie-standalone/local/build-upstream.ps1`) provides the complete assembly logic in 124 lines but is a throwaway with hardcoded paths. The production script must add parameter blocks with defaults derived from `$PSScriptRoot`, engine abstraction (`podman`/`docker` via `-Engine` parameter), `git` branch/SHA/dirty-status reporting for all three repos, input validation with clear error messages before assembly begins, CRLF-to-LF conversion when copying `entrypoint.sh`, path-quoting throughout, caching control (`-NoCache` switch), and exit-code propagation from the build command. The frontend selective-copy logic (4 dirs + 13 root files with per-file existence checks) is the most mechanically involved part.

Test coverage is zero and appropriately so: build scripts are validated operationally. The main risk factors are the CRLF bug (confirmed, must be handled at copy time), missing `.gitignore` patterns for generated artifacts (straightforward addition), and the Podman OCI HEALTHCHECK issue (documented, optional flag). The ES-free runtime failure is fully characterised in the spike and belongs only in the README. Overall complexity is low-to-medium: the logic is well-specified, the prototype exists, no application code is touched, and no unknowns remain.

---

## 8. External References

**`C:\codemie-dev\codemie-standalone\local\build-upstream.ps1`** — RESOLVED. Prototype 124-line PowerShell script. Hardcoded: `$STANDALONE=C:\codemie-dev\codemie-standalone`, `$CODEMIE`, `$CODEMIE_UI`, `$CTX=...local\upstream-ctx`, `$LOG`, `$TAG`. Calls `git -C <repo> rev-parse --short HEAD` for SHAs but no branch or dirty checks. Copies packaging assets, backend (`pyproject.toml`, `poetry.lock`, `src/`, `config/`), frontend (4 dirs + 13 root files with existence checks and WARN logs). Calls `podman build --no-cache` directly. Preserves exit code in `$buildExit`. No parameter block, no engine abstraction, no CRLF handling.

**`C:\codemie-dev\codemie-standalone\local\spike-report.md`** — RESOLVED. Key findings: (1) Build PASS confirmed for upstream sources (`codemie@e953905` + `codemie-ui@1bf0010`) using existing packaging assets — 478 s with Podman, exit 0. (2) CRLF bug: `entrypoint.sh` checked out on Windows has CRLF; Linux kernel cannot resolve `#!/bin/sh\r` shebang. (3) Podman OCI format silently drops `HEALTHCHECK`; add `--format docker` to preserve it. (4) Frontend: `src/`, `public/`, `scripts/`, `.keycloakify/` + 13 root config files. (5) Backend: `pyproject.toml`, `poetry.lock`, `src/`, `config/`. (6) Runtime FAIL: `manage_preconfigured_assistants()` → `es.indices.create()` → `ConnectionRefusedError`; not a packaging failure, confirmed known prerequisite. (7) GCP Poetry registry (R1) and `poetry.lock` compatibility (R2) were not reproduced as blockers. (8) `codemie.enterprise.*` OSS shim handles absent `codemie_enterprise` package gracefully.
