# EPMCDME-13744 — Gitleaks pre-commit hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fast, cross-platform gitleaks scan to the backend pre-commit hook by porting the pattern from `codemie-ui/scripts/validate-secrets.mjs` into a bash runner, wire it as a second `local` pre-commit entry, and fix the three existing bugs in `make gitleaks` so the same allowlist is honoured by both paths.

**Architecture:** New standalone bash script `scripts/git-hooks/validate_secrets.sh` handles container-engine detection (Docker → Podman → Apple Containers), daemon-liveness check with actionable hints, and runs `gitleaks protect --staged` in a container using the pinned image `ghcr.io/gitleaks/gitleaks:v8.30.1`. The script is wired via a second `local` entry in `.pre-commit-config.yaml` so it is independently disable-able and does not lengthen the monolithic `pre_commit.sh`. `make gitleaks` (CI gate) stays docker-only but is fixed to pass `--config` and use the current registry.

**Tech Stack:** bash (POSIX-ish with common non-POSIX bits already used in the repo — `set -euo pipefail`, `[[ ]]`, `$OSTYPE`), pre-commit (Python framework, already installed), gitleaks v8.30.1 (containerised), Docker/Podman/`container` (developer-installed).

## Global Constraints

- Target repo: `/Users/oleg_sotnichenko/codemie-dev/codemie` (backend). No changes outside this repo.
- Gitleaks image pinned to `ghcr.io/gitleaks/gitleaks:v8.30.1` everywhere (both new script and Makefile).
- No Node.js dependency in backend — port is bash.
- Honour existing `CODEMIE_PRECOMMIT_ENABLED=false/0/off` bypass.
- Scan mode in the pre-commit path: `gitleaks protect --staged` (staged diff only). `make gitleaks` keeps `gitleaks dir` (full working-tree scan for CI).
- No-engine / dead-daemon policy: hard-block (exit 1) with a friendly install/start hint.
- `.gitleaks.toml` at repo root is passed via `--config=/workspace/.gitleaks.toml` whenever it exists.
- Fix `.pre-commit-config.yaml` `stages: [commit]` deprecation to `stages: [pre-commit]` on the existing entry AND use `stages: [pre-commit]` for the new entry.
- No bats/shellspec harness. Testing is manual verification per the existing `pre_commit.sh` convention.
- Coordinate with EPMCDME-13740 draft in `docs/superpowers/tasks/2026-07-28-epmcdme-13740-ruff-hook-staged-detection/` — that touches `pre_commit.sh`; this task does not touch `pre_commit.sh`. Only `.pre-commit-config.yaml` is a possible merge point; both edits are additive/format-only there.

---

## File Structure

**Create:**
- `scripts/git-hooks/validate_secrets.sh` — the runner. Engine detection, daemon check, hint printing, gitleaks container invocation, exit-code passthrough. ≤120 lines.

**Modify:**
- `.pre-commit-config.yaml` — add a second `local` hook entry `codemie-gitleaks`; fix `stages: [commit]` deprecation on the existing entry.
- `Makefile` — fix `gitleaks` target: switch image to `ghcr.io/gitleaks/gitleaks:v8.30.1`, add `--config=/path/.gitleaks.toml`. Keep it docker-only (see rationale below).
- `.ai-run/guides/quality-gates.md` — document the new pre-commit secret gate (multi-engine, hard-block) and that `make gitleaks` is the CI equivalent (docker-only, full-scan).

**No test files.** No bash test harness exists in the repo; introducing one is out of scope for this PoC.

---

## Task 1: Runner script `validate_secrets.sh`

**Files:**
- Create: `scripts/git-hooks/validate_secrets.sh`

**Interfaces:**
- Consumes: `CODEMIE_PRECOMMIT_ENABLED` env var (opt-out; same semantics as `pre_commit.sh`), `.gitleaks.toml` at `$(git rev-parse --show-toplevel)` (optional).
- Produces: exit 0 on success (no engine bypass NOT allowed — this exits non-zero); exit 1 on missing engine, dead daemon, or gitleaks finding a secret. Passes through the gitleaks exit code otherwise.

Test-first: no — bash runner, no bash test harness in the repo; verified manually per Task 6.

- [ ] **Step 1: Create the script skeleton**

Create `scripts/git-hooks/validate_secrets.sh` with the following exact content:

```bash
#!/usr/bin/env bash
# Cross-platform secrets scan for the pre-commit hook.
# Ports codemie-ui/scripts/validate-secrets.mjs to bash so the backend
# (which has no Node runtime) can run gitleaks at commit time.
#
# Behaviour:
#   * Detects Docker -> Podman -> Apple Containers (macOS) in that order.
#   * Uses the first engine whose daemon is live.
#   * Scans staged changes only (`gitleaks protect --staged`).
#   * Honours .gitleaks.toml if present.
#   * Exit 1 (hard block) when no engine is available or its daemon is not
#     running, printing an actionable hint.
#   * Honours CODEMIE_PRECOMMIT_ENABLED=false/0/off to skip.

set -euo pipefail

GITLEAKS_IMAGE="ghcr.io/gitleaks/gitleaks:v8.30.1"

# Opt-out: consistent with scripts/git-hooks/pre_commit.sh.
case "${CODEMIE_PRECOMMIT_ENABLED:-true}" in
  false|0|off|OFF|False|FALSE)
    echo "codemie-gitleaks: skipped (CODEMIE_PRECOMMIT_ENABLED=$CODEMIE_PRECOMMIT_ENABLED)"
    exit 0
    ;;
esac

repo_root="$(git rev-parse --show-toplevel)"
config_path="$repo_root/.gitleaks.toml"

# --- PATH augmentation (macOS only) ---------------------------------------
# Mirrors the UI script so engines installed by Homebrew / Colima / OrbStack /
# Docker Desktop / Apple Containers are visible even when the shell PATH is
# minimal (e.g. IDE-invoked pre-commit).
if [[ "${OSTYPE:-}" == darwin* ]]; then
  for extra in /opt/podman/bin /opt/homebrew/bin /usr/local/bin \
               /Applications/Docker.app/Contents/Resources/bin; do
    if [[ -d "$extra" && ":$PATH:" != *":$extra:"* ]]; then
      PATH="$PATH:$extra"
    fi
  done
  export PATH
fi

# --- Engine detection -----------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

daemon_running() {
  local engine="$1"
  have "$engine" && "$engine" info >/dev/null 2>&1
}

apple_containers_running() {
  [[ "${OSTYPE:-}" == darwin* ]] && have container && container system status >/dev/null 2>&1
}

detect_engine() {
  for e in docker podman; do
    if daemon_running "$e"; then echo "$e"; return 0; fi
  done
  if apple_containers_running; then echo container; return 0; fi
  # Second pass: installed but not running -> report so we can hint.
  for e in docker podman; do
    if have "$e"; then echo "$e"; return 0; fi
  done
  echo ""
}

hint_for_stopped_daemon() {
  local engine="$1"
  case "$engine" in
    docker)
      if have colima;   then echo "Run 'colima start' to enable secrets detection locally"; return; fi
      if have orbstack; then echo "Start OrbStack to enable secrets detection locally";     return; fi
      echo "Start Docker Desktop to enable secrets detection locally"
      ;;
    podman)
      echo "Run 'podman machine start' to enable secrets detection locally"
      ;;
    container)
      echo "Start Apple Containers to enable secrets detection locally"
      ;;
    *)
      echo "Start your container engine to enable secrets detection locally"
      ;;
  esac
}

engine="$(detect_engine)"

if [[ -z "$engine" ]]; then
  echo "codemie-gitleaks: no container engine found" >&2
  echo "Install Docker, Podman, or Apple Containers to enable local secrets scanning." >&2
  echo "(Set CODEMIE_PRECOMMIT_ENABLED=false to skip the whole pre-commit hook if you must commit now.)" >&2
  exit 1
fi

if ! ( [[ "$engine" == "container" ]] && apple_containers_running ) \
     && ! ( [[ "$engine" != "container" ]] && daemon_running "$engine" ); then
  case "$engine" in
    container) label="Apple Containers" ;;
    *)         label="$(tr '[:lower:]' '[:upper:]' <<<"${engine:0:1}")${engine:1}" ;;
  esac
  echo "codemie-gitleaks: $label daemon is not running" >&2
  echo "$(hint_for_stopped_daemon "$engine")" >&2
  echo "(Set CODEMIE_PRECOMMIT_ENABLED=false to skip the whole pre-commit hook.)" >&2
  exit 1
fi

# --- Run gitleaks in staged-diff mode -------------------------------------
args=(run --rm -v "$repo_root:/workspace" -w /workspace "$GITLEAKS_IMAGE"
      protect --staged --no-banner --verbose --source=/workspace)

if [[ -f "$config_path" ]]; then
  args+=(--config=/workspace/.gitleaks.toml)
fi

echo "codemie-gitleaks: scanning staged changes with $engine ($GITLEAKS_IMAGE)"
if ! "$engine" "${args[@]}"; then
  echo "" >&2
  echo "codemie-gitleaks: secrets detected in staged changes. Remove them before committing." >&2
  echo "If this is a false positive, extend the allowlist/stopwords in .gitleaks.toml." >&2
  exit 1
fi
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/git-hooks/validate_secrets.sh
```

- [ ] **Step 3: Manual smoke — dry syntax check**

Run: `bash -n scripts/git-hooks/validate_secrets.sh`
Expected: no output, exit 0 (syntax OK).

Run: `shellcheck scripts/git-hooks/validate_secrets.sh || true` (shellcheck may not be installed; this is best-effort).
Expected: no critical errors; warnings acceptable.

- [ ] **Step 4: Manual smoke — engine detection (dev machine)**

Run: `bash scripts/git-hooks/validate_secrets.sh`
Expected (on a machine with Docker/Colima running): prints `codemie-gitleaks: scanning staged changes with docker (ghcr.io/gitleaks/gitleaks:v8.30.1)` and exits 0 (no staged files → nothing to scan → success).

Run: `CODEMIE_PRECOMMIT_ENABLED=false bash scripts/git-hooks/validate_secrets.sh`
Expected: prints skip message, exits 0.

Run (temporarily stop Colima or Docker to simulate dead daemon): `colima stop && bash scripts/git-hooks/validate_secrets.sh; echo "exit=$?"`
Expected: prints `Docker daemon is not running` + `Run 'colima start' to enable secrets detection locally`, exits 1.

- [ ] **Step 5: Commit**

```bash
git add scripts/git-hooks/validate_secrets.sh
git commit -m "EPMCDME-13744: add cross-platform gitleaks pre-commit runner (bash port of validate-secrets.mjs)"
```

---

## Task 2: Wire the runner into `.pre-commit-config.yaml`

**Files:**
- Modify: `.pre-commit-config.yaml`

**Interfaces:**
- Consumes: `scripts/git-hooks/validate_secrets.sh` from Task 1.
- Produces: a new hook id `codemie-gitleaks` invoked by `pre-commit run` and `.git/hooks/pre-commit`.

Test-first: no — pre-commit config is validated by running the hook framework itself in Step 3.

- [ ] **Step 1: Replace `.pre-commit-config.yaml` with**

```yaml
repos:
  - repo: local
    hooks:
      - id: codemie-pre-commit
        name: Codemie pre-commit (ruff fast fix + tests + sonar)
        entry: bash -lc 'bash scripts/git-hooks/pre_commit.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'
        language: system
        pass_filenames: false
        stages: [pre-commit]
        always_run: true
      - id: codemie-gitleaks
        name: Codemie gitleaks (staged secrets scan, multi-engine)
        entry: bash -lc 'bash scripts/git-hooks/validate_secrets.sh 2>&1 | tee /dev/tty 2>/dev/null; exit ${PIPESTATUS[0]}'
        language: system
        pass_filenames: false
        stages: [pre-commit]
        always_run: true
```

Notes:
- `stages: [pre-commit]` on both entries fixes the pre-commit ≥3.0 deprecation warning.
- `always_run: true` matches the sibling hook — the staged-diff filter is done by gitleaks itself, not by pre-commit's changed-files filter.
- Separate `id` (`codemie-gitleaks`) so users can `SKIP=codemie-gitleaks git commit ...` independently of the main hook.

- [ ] **Step 2: Reinstall pre-commit hooks so the config is picked up**

Run: `poetry run pre-commit install --overwrite`
Expected: `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 3: Manual smoke — run just the new hook**

Run: `poetry run pre-commit run codemie-gitleaks --all-files`
Expected (engine running): passes with `codemie-gitleaks: scanning staged changes with <engine> ...`. `--all-files` triggers the hook once; since gitleaks reads the staged diff (empty when nothing is staged), it exits 0. To exercise staged-diff behavior positively, do a clean throwaway test in Step 4.

- [ ] **Step 4: Manual smoke — staged secret detection (positive case)**

Run (in a scratch worktree or throwaway branch):
```bash
echo 'AWS_SECRET_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"' > /tmp/fake_secret.txt
cp /tmp/fake_secret.txt ./_gitleaks_probe.txt
git add ./_gitleaks_probe.txt
SKIP=codemie-pre-commit poetry run pre-commit run codemie-gitleaks
git restore --staged ./_gitleaks_probe.txt && rm ./_gitleaks_probe.txt
```
Expected: gitleaks reports a finding and the hook exits 1. `SKIP=codemie-pre-commit` bypasses the (slow) main hook so we only exercise gitleaks.

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "EPMCDME-13744: wire codemie-gitleaks pre-commit entry; fix stages: [commit] deprecation"
```

---

## Task 3: Fix `make gitleaks`

**Files:**
- Modify: `Makefile` (lines 51-54, `gitleaks` target)

**Interfaces:**
- Produces: `make gitleaks` now honours `.gitleaks.toml` and uses the current official image, so CI and the pre-commit path apply the same allowlist and same rules.

Test-first: no — Makefile target verified by running it.

- [ ] **Step 1: Replace the `gitleaks` target**

Change:

```makefile
gitleaks:
	docker run --rm -v $$(pwd):/path zricethezav/gitleaks:v8.30.0 dir --no-banner --verbose /path
```

To:

```makefile
gitleaks:
	docker run --rm -v $$(pwd):/workspace ghcr.io/gitleaks/gitleaks:v8.30.1 \
	    dir --no-banner --verbose --config=/workspace/.gitleaks.toml /workspace
```

Rationale (kept out of the Makefile itself but noted here for the reviewer): `make gitleaks` is the CI gate. CI has Docker on the PATH; multi-engine fallback is a developer-workstation ergonomics feature and lives in the pre-commit runner. Keeping the Makefile target as a plain `docker run` avoids drift between CI's expectation ("run gitleaks") and CI's actual invocation.

- [ ] **Step 2: Verify locally**

Run: `make gitleaks`
Expected: gitleaks scans the working tree using `.gitleaks.toml`; existing false positives (e.g. `limit=self.MAX_RESULTS`) are no longer reported thanks to `--config`. Exit 0 (no leaks).

Compare with previous behavior: `docker run --rm -v $(pwd):/path zricethezav/gitleaks:v8.30.0 dir --no-banner --verbose /path` — this may report stopword hits that `.gitleaks.toml` allows.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "EPMCDME-13744: fix make gitleaks - pass --config and use ghcr.io/gitleaks/gitleaks:v8.30.1"
```

---

## Task 4: Update `.ai-run/guides/quality-gates.md`

**Files:**
- Modify: `.ai-run/guides/quality-gates.md` (Secret Scan section)

**Interfaces:**
- Produces: authoritative doc that agents will follow.

Test-first: no — documentation change.

- [ ] **Step 1: Read the current Secret Scan section**

Run: `grep -n -i -A 10 "gitleaks\|secret" .ai-run/guides/quality-gates.md`
Locate the block that says "skip if Docker is unavailable" and the `make gitleaks` reference.

- [ ] **Step 2: Rewrite the Secret Scan section**

Replace the existing block with:

```markdown
### Secret Scan

- **CI gate**: `make gitleaks` — runs `gitleaks dir` in `ghcr.io/gitleaks/gitleaks:v8.30.1` against the full working tree, honouring `.gitleaks.toml`. Docker-only; CI images always have Docker. Skip **only** in environments without Docker.
- **Local pre-commit gate**: `scripts/git-hooks/validate_secrets.sh` (wired as pre-commit hook `codemie-gitleaks`) — runs `gitleaks protect --staged` in the same image. Detects Docker, Podman, and Apple Containers; picks the first live engine; hard-blocks the commit (exit 1) with an actionable hint if no engine is running.
- **Bypass**: set `CODEMIE_PRECOMMIT_ENABLED=false` to skip the entire pre-commit hook (both `codemie-pre-commit` and `codemie-gitleaks`). Do NOT push secret-containing commits. If you must commit a false positive, extend `.gitleaks.toml` allowlist/stopwords in the same PR.
- **Policy**: HIGH priority. Secrets must never enter a local commit. The `codemie-gitleaks` hook hard-blocks; do not weaken it to warn-and-continue.
```

- [ ] **Step 3: Commit**

```bash
git add .ai-run/guides/quality-gates.md
git commit -m "EPMCDME-13744: document new pre-commit secret scan gate (multi-engine, hard-block)"
```

---

## Task 5: Final integration check

**Files:** none.

Test-first: no — end-to-end smoke.

- [ ] **Step 1: Fresh pre-commit run on staged files**

Stage a benign change and run the full hook:

```bash
touch /tmp/gitleaks_e2e_note.txt
cp /tmp/gitleaks_e2e_note.txt ./
git add ./gitleaks_e2e_note.txt
poetry run pre-commit run --hook-stage pre-commit --all-files || true
git restore --staged ./gitleaks_e2e_note.txt && rm ./gitleaks_e2e_note.txt
```

Expected: `codemie-gitleaks` appears in the hook list, runs, and passes. The main `codemie-pre-commit` hook still runs (or the outcome is the current pre-existing state — this task does not change it).

- [ ] **Step 2: Verify `make verify` still works**

Run: `make verify` (only if a working container engine is available). If slow, run the isolated bits: `make gitleaks && make ruff && make license`.
Expected: green.

- [ ] **Step 3: Check for conflict with EPMCDME-13740 draft**

Run: `git log --oneline main..HEAD -- .pre-commit-config.yaml scripts/git-hooks/pre_commit.sh`
Expected: only this task's commits. Note that EPMCDME-13740 also plans to touch `.pre-commit-config.yaml`; rebase/coordinate before merging both branches. Add a note in the MR description.

- [ ] **Step 4: No commit** — this task is a verification pass.

---

## Task 6: PR description — manual verification checklist

**Files:** none in-repo. This is the checklist to paste into the MR description.

Test-first: no — descriptive.

- [ ] **Step 1: Draft the MR verification section**

Copy this block into the MR description:

```markdown
### Manual verification

- [ ] `bash -n scripts/git-hooks/validate_secrets.sh` passes.
- [ ] `poetry run pre-commit run codemie-gitleaks --all-files` passes with a live engine.
- [ ] Staging a fake secret (`AKIAIOSFODNN7EXAMPLE`) makes `codemie-gitleaks` exit 1.
- [ ] Stopping the engine (`colima stop` / `podman machine stop`) reproduces the hard-block with the correct hint.
- [ ] `CODEMIE_PRECOMMIT_ENABLED=false git commit` bypasses the hook (both entries).
- [ ] `make gitleaks` passes locally, uses `.gitleaks.toml` (verify by checking the container command output includes `--config`).
- [ ] No regression in the existing `codemie-pre-commit` hook.

### Coordination note

- EPMCDME-13740 (ruff staged detection) also modifies `.pre-commit-config.yaml`. Whichever branch lands second should rebase and re-add the second hook entry / re-apply the ruff staged-detection changes cleanly.
```

- [ ] **Step 2: No commit** — the checklist lives in the MR description, not in the repo.

---

## Self-review checklist (author-only, not a task)

Verified against the requirements block:

- **Runner script**: Task 1 covers PATH augmentation (macOS), engine detection order (docker → podman → container), daemon liveness (`info`, `system status`), hints (colima/orbstack/podman/generic), pinned image `ghcr.io/gitleaks/gitleaks:v8.30.1`, `gitleaks protect --staged` mode, conditional `--config`, `CODEMIE_PRECOMMIT_ENABLED` opt-out, hard-block exit codes. ✓
- **Integration**: Task 2 adds `codemie-gitleaks` local hook, fixes `stages: [commit]` on the existing entry. ✓
- **Makefile fixes**: Task 3 passes `--config`, uses `ghcr.io/gitleaks/gitleaks:v8.30.1`, keeps `make gitleaks` docker-only with rationale. ✓
- **Docs**: Task 4 rewrites the Secret Scan section of `quality-gates.md`. ✓
- **Testing**: no bats/shellspec; manual verification steps in Tasks 1, 2, 3, 5, and the MR checklist in Task 6. ✓
- **EPMCDME-13740 coordination**: called out in Task 5 Step 3 and Task 6. ✓
- **No placeholders / TBDs / "add appropriate error handling"** — every step contains the actual content. ✓
