# Technical Research

**Task**: gitleaks pre-commit secrets-scanning make-gitleaks
**Generated**: 2026-07-29T00:00:00Z
**Research path**: codegraph + filesystem

---

## 1. Original Context

EPMCDME-13744 — [PoC] Add gitleaks to pre-commit (port UI's validate-secrets.mjs)

## What
Secrets scan lives only in `make verify` — nothing runs at commit time. `.gitleaks.toml` exists.

## How
Port `codemie-ui/scripts/validate-secrets.mjs`:
- Cross-platform gitleaks runner via Docker / Podman / Colima / OrbStack / Apple Containers
- Picks up `.gitleaks.toml`
- Friendly "colima start" hints
- Backend's current `make gitleaks` assumes a running docker daemon on PATH — no fallback

For speed: prefer `gitleaks protect --staged` (staged diff only) over full-dir scan.

## Priority
HIGH — secrets should never reach even a local commit.

Target repo: /Users/oleg_sotnichenko/codemie-dev/codemie (backend, Python). Reference source: /Users/oleg_sotnichenko/codemie-dev/codemie-ui/scripts/validate-secrets.mjs

---

## 2. Codebase Findings

### Existing Implementations

**Backend — current gitleaks surface:**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/Makefile` lines 51–54 — `gitleaks` target:
  ```makefile
  gitleaks:
      docker run --rm -v $$(pwd):/path zricethezav/gitleaks:v8.30.0 dir --no-banner --verbose /path

  verify: ruff license gitleaks test
  ```
  Three problems: Docker-only (no fallback), old registry path (`zricethezav/` not `ghcr.io/gitleaks/`), and `--config` not passed so `.gitleaks.toml` is silently ignored.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/.pre-commit-config.yaml` — single hook entry:
  ```yaml
  repos:
    - repo: local
      hooks:
        - id: codemie-pre-commit
          name: Codemie pre-commit (ruff fast fix + tests + sonar)
          entry: bash -lc 'bash scripts/git-hooks/pre_commit.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'
          language: system
          pass_filenames: false
          stages: [commit]
          always_run: true
  ```
  Note: `stages: [commit]` is deprecated in pre-commit ≥3.0; modern spelling is `stages: [pre-commit]`.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/scripts/git-hooks/pre_commit.sh` — the actual hook body. Steps in order: ruff format+fix on entire working tree → detect ruff-modified files and exit 1 → ruff check → license headers → `pytest -q -r a tests/` → `make sonar-local`. **Gitleaks is absent.** The hook is guarded by `CODEMIE_PRECOMMIT_ENABLED` env var (set to `false`/`0`/`off` to skip).

- `/Users/oleg_sotnichenko/codemie-dev/codemie/.gitleaks.toml` — config file:
  - `[extend] useDefault = true` (layers on top of official default ruleset)
  - Allowed paths: `config/index-dumps/.*\.json$`, `.*/__pycache__/.*`, `.pytest_cache/.*`, `.idea/.*`, `.keys/.*`, `.env_local`, `.env\.local`
  - Stopwords: `"limit=self.MAX_RESULTS"` (suppresses one false positive in query-limit code)

**Reference implementation (UI):**

- `/Users/oleg_sotnichenko/codemie-dev/codemie-ui/scripts/validate-secrets.mjs` — Node.js/ESM. Key behaviors:
  - **PATH augmentation** (macOS): prepends `/opt/podman/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `/Applications/Docker.app/Contents/Resources/bin`
  - **Engine detection order**: `docker` → `podman` → Apple Containers (`container system status`, macOS only). Each engine is tested for binary existence AND daemon liveness (`<engine> info`). First live engine wins.
  - **Fallback tiers**: (1) running engine, (2) installed-but-stopped engine (to print a targeted hint), (3) no engine (skip with exit 1).
  - **Daemon-stopped hints**: colima detected → `"Run 'colima start'"`, OrbStack detected → `"Start OrbStack"`, podman → `"Run 'podman machine start'"`, generic → `"Start your container engine"`.
  - **Image used**: `ghcr.io/gitleaks/gitleaks:v8.30.1`
  - **Run command**: `<engine> run --rm -v <projectPath>:/workspace <IMAGE> dir --no-banner --verbose [--config=/workspace/.gitleaks.toml] /workspace`
  - **Config auto-detection**: checks `process.cwd()/.gitleaks.toml`; passes `--config` only if found.
  - **Scan mode**: `gitleaks dir` (full working-tree scan). The task spec says "prefer `protect --staged`" — this is a **deviation from the source** that must be explicitly decided.
  - **Exit policy**: exits 1 both when no engine is found and when secrets are detected.
  - **Windows support**: uses `where` instead of `which`, quotes paths with spaces, sets `shell: true` for spawn.

**UI hook wiring (for reference):**

- `/Users/oleg_sotnichenko/codemie-dev/codemie-ui/.husky/pre-commit`: `npx lint-staged && npm run license-headers:check && npm run secrets:check && npm run sonar-local`
  The UI uses Husky + `package.json` scripts; `secrets:check` calls `validate-secrets.mjs`. The backend uses the `pre-commit` Python framework, not Husky.

**Pending/draft work (not yet applied to working tree):**

- `docs/superpowers/tasks/2026-07-28-epmcdme-13740-ruff-hook-staged-detection/` contains draft diffs proposing a `scripts/git-hooks/_ruff_staged.sh` helper to fix staged-only ruff detection. That work has not been committed. Any changes to `pre_commit.sh` or `.pre-commit-config.yaml` for this task should be designed to compose cleanly with that pending change.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| Developer workflow / CI gate | `Makefile` — `gitleaks` and `verify` targets |
| Git hook layer | `.pre-commit-config.yaml` + `scripts/git-hooks/pre_commit.sh` |
| Script/tooling layer | New script to be added (the ported runner) |
| Container runtime layer | Docker / Podman / Apple Containers (external, developer-installed) |
| Config layer | `.gitleaks.toml` (rules and allowlist) |

### Integration Point

The backend pre-commit hook is wired via the `pre-commit` Python framework:

```
.git/hooks/pre-commit  (generated by `pre-commit install`)
  → python -mpre_commit hook-impl
    → bash scripts/git-hooks/pre_commit.sh
```

To add gitleaks scanning at commit time, one of two integration points can be used:

**Option A — Append to `pre_commit.sh`**: call the new runner script at the end of the existing bash script. Keeps everything in one hook entry; simpler. Risk: the hook already runs ruff, license checks, full pytest, and sonar — adding gitleaks here extends an already-slow hook.

**Option B — Add a second `local` hook entry in `.pre-commit-config.yaml`**: gives a separate hook entry with its own `id` and `name`, making it independently disable-able and clearly labeled. Slightly more visible to contributors.

Either option requires the new runner to be a **shell script or Python script** — the backend has no Node.js runtime, so the ESM source cannot run directly. Bash is the natural choice: it keeps the same shell conventions as `pre_commit.sh` and requires no additional toolchain.

### Integration Points (dependencies)

- `docker` or `podman` or `container` (Apple) — must be present and daemon must be running; no pip/poetry dep.
- `ghcr.io/gitleaks/gitleaks` container image — pulled at first run; pin a specific tag.
- `.gitleaks.toml` at repo root — must be passed via `--config`; currently missing from `make gitleaks`.
- `CODEMIE_PRECOMMIT_ENABLED` — existing bypass mechanism; the new gitleaks step should respect this guard if integrated via `pre_commit.sh`.

### Patterns and Conventions

- Pre-commit hooks in this repo are bash scripts; tooling is invoked via `poetry run <tool>` for Python tools and `make <target>` for containerised tooling.
- The hook uses `CODEMIE_PRECOMMIT_ENABLED` for opt-out; any new step should honour it.
- The `pre-commit` framework is already installed (`.git/hooks/pre-commit` is the generated entry); no new framework needed.
- `make verify` is the all-in-one quality gate: ruff → license → gitleaks → test. The Makefile `gitleaks` target should also be updated (fix registry, fix `--config` flag) even though it is not the primary deliverable.
- The UI script pattern (augment PATH → detect engine → check daemon → print hint → run container) is the exact pattern to replicate in bash.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/quality-gates.md` — authoritative gate definition. Documents "Secret Scan" gate: `make gitleaks` — pass if Docker runs gitleaks with no secrets; **skip if Docker is unavailable**. This guide must be updated when the pre-commit hook is added.
- `/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/development/security-patterns.md` — minimal; mentions secrets handling conceptually but has no hook-level guidance.
- `/Users/oleg_sotnichenko/codemie-dev/codemie/.ai-run/guides/development/setup-guide.md` — references `pre-commit install` and the ruff PATH note for the Stop hook; should gain a note about the gitleaks hook and container engine requirement.

### Architectural Decisions

- `quality-gates.md` records the policy decision "skip if Docker is unavailable" — this now conflicts with the task requirement to support Podman/Colima/OrbStack. The guide policy needs updating to reflect multi-engine support.
- The `.gitleaks.toml` `extend.useDefault = true` pattern is a deliberate decision to use the upstream ruleset as a base and layer project-specific allow rules on top, rather than maintaining a full custom ruleset.
- The pre-commit framework (`pre-commit` package) was chosen over raw git hooks to give hook version management; the backend does not use Husky (UI's choice).

### Derived Conventions

- New hook scripts live in `scripts/git-hooks/` (following `pre_commit.sh`).
- Scripts are bash (not Python or Node) for portability within the developer toolchain.
- Gitleaks image should be pinned to a specific tag (currently `v8.30.0` in Makefile); new script should use `ghcr.io/gitleaks/gitleaks:v8.30.1` to match UI and use current registry.
- Colima/OrbStack hints are a project convention established by the UI side and expected here per the ticket AC.

### External Documentation Findings

No third-party library integration requiring external doc consultation — gitleaks is invoked as a container, not imported as a library. The container image documentation is embedded in the reference script itself.

---

## 4. Testing Landscape

### Existing Coverage

- No tests exist for any gitleaks or secrets-scanning code path in the backend.
- No tests exist for `validate-secrets.mjs` in the UI either — the reference implementation is itself untested.
- `scripts/git-hooks/pre_commit.sh` is untested (no test file exercises hook logic).

### Testing Framework and Patterns

- Backend uses `pytest` (`poetry run pytest -q -r a tests/`).
- Hook scripts are bash — no existing pattern for testing bash scripts (no bats or shellspec configuration found).
- The new script, if it is a bash wrapper, will likely remain untested by default (consistent with `pre_commit.sh`); if the policy requires test coverage, a bash testing framework would need to be introduced.

### Coverage Gaps

- The new gitleaks runner script will have zero test coverage unless explicitly planned.
- The engine-detection logic (the most complex part) has no existing test harness to build on.
- The "staged vs. full scan" branching (if implemented) will be untested.

---

## 5. Configuration and Environment

### Environment Variables

- `CODEMIE_PRECOMMIT_ENABLED` — existing guard in `pre_commit.sh`; set to `false`/`0`/`off` to skip the entire hook. The gitleaks step should honour this if integrated via `pre_commit.sh`.
- No dedicated gitleaks env vars exist in the backend yet (the UI script also has none).

### Configuration Files

- `/Users/oleg_sotnichenko/codemie-dev/codemie/.gitleaks.toml` — rules and allowlist; must be passed to gitleaks via `--config` flag (currently not passed in `make gitleaks` — active bug).
- `/Users/oleg_sotnichenko/codemie-dev/codemie/.pre-commit-config.yaml` — hook wiring; will require editing if the gitleaks step is added as a second hook entry.
- `/Users/oleg_sotnichenko/codemie-dev/codemie/Makefile` — `gitleaks` target; should be updated to fix registry, version, and `--config` flag as part of this task.

### Feature Flags and Deployment Concerns

- No feature flags relevant to hook tooling.
- The new script must handle the case where no container engine is available (CI environments running gitleaks natively, or developer machines with no Docker/Podman). Policy decision: hard-block (exit 1, like the UI) or warn-and-continue. The existing `quality-gates.md` says "skip if Docker is unavailable" — this conflicts with the ticket's HIGH priority. Resolution must be explicit.
- The `stages: [commit]` deprecation warning in `.pre-commit-config.yaml` will produce noisy output in pre-commit ≥3.0; fixing it to `stages: [pre-commit]` is a low-risk housekeeping change that belongs in this same PR.

---

## 6. Risk Indicators

- **`.gitleaks.toml` is currently ignored by `make gitleaks`** — `--config` is not passed, so the allowlist and stopwords in `.gitleaks.toml` are never applied when running `make verify`. The `config/index-dumps/*.json` paths and the `limit=self.MAX_RESULTS` stopword are silently not suppressed. This is an active bug that the new hook must not replicate.

- **No Node.js runtime in the backend** — the reference `validate-secrets.mjs` is ESM Node.js and cannot be run directly. A port to bash (or Python) is required. This is the primary implementation decision to make before writing code.

- **Scan mode mismatch between task spec and reference source** — the task says "prefer `protect --staged`" but `validate-secrets.mjs` uses `gitleaks dir` (full scan). `protect --staged` only scans the git staged diff and is faster but misses unstaged secrets. `dir` scans the full working tree. For a pre-commit hook, `protect --staged` is semantically correct; `dir` is what the UI does. The port must explicitly choose and document this.

- **Image registry and version divergence** — `make gitleaks` uses `zricethezav/gitleaks:v8.30.0`; UI uses `ghcr.io/gitleaks/gitleaks:v8.30.1`. The `zricethezav/` registry is the legacy path; `ghcr.io/gitleaks/gitleaks` is the current official registry. The new script should use `ghcr.io/gitleaks/gitleaks:v8.30.1` and the Makefile should be updated to match.

- **EPMCDME-13740 draft diffs not yet committed** — `docs/superpowers/tasks/2026-07-28-epmcdme-13740-ruff-hook-staged-detection/` contains a proposed `scripts/git-hooks/_ruff_staged.sh` that modifies `pre_commit.sh` and `.pre-commit-config.yaml`. If that work lands before or concurrently with this task, there may be merge conflicts in `pre_commit.sh`. Coordinate or rebase on that branch before opening the MR.

- **`stages: [commit]` deprecation** — `.pre-commit-config.yaml` uses the deprecated stage name. Pre-commit ≥3.0 will warn on every hook run. Should be fixed to `stages: [pre-commit]` in this PR.

- **Hard-block vs. warn-and-continue policy unresolved** — `quality-gates.md` says "skip if Docker is unavailable," which implies warn-and-continue. The ticket's HIGH priority implies hard-block. This policy decision affects developer onboarding experience (new devs without Docker/Colima installed will fail commits) and must be explicit in the implementation.

- **No tests for hook scripts** — neither the existing `pre_commit.sh` nor the proposed gitleaks script have test coverage. If the complexity assessor scores this as requiring test coverage, a bash testing framework (bats-core) would need to be introduced — that is a non-trivial addition.

- **Colima/OrbStack detection is macOS-specific** — the PATH augmentation and `container system status` check in the UI script are macOS-only. Linux CI runners (GitLab CI) should use native `docker` or `podman` from system PATH without the macOS PATH augmentation. The bash port must handle this gracefully.

---

## 7. Summary for Complexity Assessment

The task is a contained tooling addition: write a bash script (approximately 60–100 lines following the `validate-secrets.mjs` pattern) and wire it into the existing pre-commit framework. Files expected to change: `scripts/git-hooks/pre_commit.sh` or `.pre-commit-config.yaml` (integration point), one new file `scripts/git-hooks/validate_secrets.sh` (the runner), `Makefile` (fix registry, add `--config` flag), and optionally `.ai-run/guides/quality-gates.md` and `.ai-run/guides/development/setup-guide.md` (doc updates). Total file change surface is 3–5 files, all in the tooling/scripts layer with no impact on Python application code.

The implementation follows an established pattern (the UI's `validate-secrets.mjs`) and the target infrastructure (pre-commit framework, bash hook scripts) is already in place. The primary technical novelty is writing bash equivalents of the Node.js runtime-detection logic: PATH augmentation, `docker info` / `podman info` daemon checks, `colima`/`orbstack` binary detection for hints. This is straightforward bash but has several decision branches (engine detection order, scan mode, block vs. warn) each of which should be explicitly documented in the code. No new Python dependencies, no schema changes, no API surface changes.

Test coverage posture is weak across the board: neither the existing `pre_commit.sh` nor the reference `validate-secrets.mjs` has tests, and no bash testing framework is configured. The most pragmatic approach is to ship the script with manual verification steps in the PR description rather than introducing bats-core for this task. Key risk factors for scoring: (1) the hard-block vs. warn-and-continue policy decision must be made before implementation, (2) the scan mode choice (`protect --staged` vs. `dir`) differs between the spec and the reference and must be explicit, and (3) EPMCDME-13740 draft changes to the same files create a potential conflict that should be checked before branching.
