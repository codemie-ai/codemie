# EPMCDME-15146 — Prune Disposable SDLC Run Artifacts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Untrack the disposable SDLC machine state under `docs/superpowers/tasks/` and `docs/superpowers/runs/`, and reshape the `.gitignore` SDLC block so future disposable artifacts need no per-filename edit.

**Architecture:** Repository hygiene only — no application code. Two surfaces: the root `.gitignore` (SDLC block, lines 69–79) and the git index (`git rm --cached`, forward-only; files stay on disk). Verification is git-command evidence: no pytest suite can assert a tracked-file set, and every gate in this repo (`make ruff`, license-check, gitleaks, pytest, Sonar) scopes to `src/codemie` and `tests`, so a green gate run carries no signal here.

**Tech Stack:** git, `.gitignore` pattern semantics, POSIX shell.

**Spec:** none — ticket EPMCDME-15146. Research: `docs/superpowers/tasks/2026-09-19-epmcdme-15146/technical-analysis.md`.

## Acceptance criteria

- [ ] `git ls-files` reports zero tracked files under `docs/superpowers/tasks/` and `docs/superpowers/runs/` whose filename is outside the durable keep-list.
- [ ] Every currently-tracked durable file (ticket says 872) is still tracked and unmodified.
- [ ] `.gitignore` carries `docs/superpowers/tasks/*/**/*.local.*`, `docs/superpowers/qa-tasks/*/**/*.local.*` and `.claude/settings.local.json`, and the per-filename allowlist rows they supersede are gone.
- [ ] Every legacy document under `docs/superpowers/plans/ specs/ reviews/ handoffs/ work-items/` (ticket says 135) is still tracked.
- [ ] A fresh run writing `state.local.json`, `gate-plan.local.json`, `code-review.local.diff` leaves `git status` clean with no new ignore rule.
- [ ] No commit rewritten, no force-push; untracked content remains retrievable from history.

## Global Constraints

- **Durable keep-list (exactly ten names):** `actual-complexity.json`, `code-review-final.json`, `complexity-assessment.json`, `decisions.jsonl`, `events.jsonl`, `gate-run.json`, `plan.md`, `qa-report.md`, `spec.md`, `technical-analysis.md`.
- `code-review-check.json` is **disposable** — decision taken; `code-review-final.json` supersedes it. Its allowlist row goes.
- **Never `git rm` without `--cached`.** Working-copy files stay on disk; an in-flight run depends on them.
- **Never rewrite history:** no `rebase`, `filter-branch`, `filter-repo`, `push --force`.
- **Never touch** `docs/superpowers/plans/ specs/ reviews/ handoffs/ work-items/`, or anything outside `docs/superpowers/tasks/`, `docs/superpowers/runs/` and `.claude/settings.local.json`. Anchor every rule and every sweep at a named root — never at `docs/superpowers/**`, which would catch legacy strays like `work-items/work-item-events.jsonl`.
- **Do not change what the Factory writes** — only what the repo tracks. No skill, agent or script edits.
- This run is **live** in `docs/superpowers/tasks/2026-09-19-epmcdme-15146/`, writing `state.local.json`, `events.jsonl`, `decisions.jsonl`, `code-review.local.diff` while the sweep executes. Untracking its disposables is correct; its durable artifacts must stay committable afterwards.
- Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`).
- Shell setup, run once per session:

```bash
SCRATCH="${TMPDIR:-/tmp}/15146"; mkdir -p "$SCRATCH"
KEEP='/(actual-complexity\.json|code-review-final\.json|complexity-assessment\.json|decisions\.jsonl|events\.jsonl|gate-run\.json|plan\.md|qa-report\.md|spec\.md|technical-analysis\.md)$'
```

---

### Task 1: Reshape the `.gitignore` SDLC block

**Files:** Modify `.gitignore:69-79` (replace the whole block). `.gitignore:45` stays as it is.

**Interfaces:** Produces the ignore rules Task 2 depends on — every disposable path under both roots must be ignored before it is untracked, or it resurfaces in `git status` as untracked.

**Test-first: yes** — evidence command, run before editing:

```bash
git check-ignore -v docs/superpowers/tasks/2026-08-27-skip-private-project-creation/enterprise-gates/gate-run.json \
  docs/superpowers/tasks/2026-09-19-epmcdme-15146/decisions.jsonl docs/superpowers/qa-tasks/x/y/a.local.json
```

RED (now): the first two print `.gitignore:70:docs/superpowers/tasks/**` — keep-list files are ignored, one of them nested a level deeper than the current `!…/tasks/*/` row can reach; the third prints nothing, because no `qa-tasks` rule exists. GREEN (after): the first two print nothing, the third prints the new `qa-tasks` row. `check-ignore` evaluates patterns, not the disk, so it works even though `qa-tasks/` does not exist here.

- [ ] **Step 1: Capture the baseline** — Task 4 diffs against these.

```bash
git ls-files docs/superpowers/tasks docs/superpowers/runs | grep -E "$KEEP" | sort > "$SCRATCH/before-durable.txt"
git ls-files docs/superpowers/{plans,specs,reviews,handoffs,work-items} | sort > "$SCRATCH/before-legacy.txt"
wc -l "$SCRATCH"/before-*.txt
```

- [ ] **Step 2: Run the evidence command above; record the RED output.**

- [ ] **Step 3: Replace `.gitignore` lines 69–79 with the block below.**

Two root-anchored blocks — git has no brace expansion, so `runs/` is spelled out. `!<root>/*/**/` re-includes nested directories (`enterprise-gates/`, `evidence/`), which the one-level `!<root>/*/` row cannot; the `*/**/` keep-list rows then match at any depth, including directly in the run dir, since `**/` also matches zero directories. The `*.local.*` rows come **last** so they outrank every `!` row above them.

```gitignore
# SDLC Factory: keep only the durable planning artifacts, ignore working files
docs/superpowers/tasks/**
!docs/superpowers/tasks/*/
!docs/superpowers/tasks/*/**/
!docs/superpowers/tasks/*/**/actual-complexity.json
!docs/superpowers/tasks/*/**/code-review-final.json
!docs/superpowers/tasks/*/**/complexity-assessment.json
!docs/superpowers/tasks/*/**/decisions.jsonl
!docs/superpowers/tasks/*/**/events.jsonl
!docs/superpowers/tasks/*/**/gate-run.json
!docs/superpowers/tasks/*/**/plan.md
!docs/superpowers/tasks/*/**/qa-report.md
!docs/superpowers/tasks/*/**/spec.md
!docs/superpowers/tasks/*/**/technical-analysis.md
docs/superpowers/runs/**
!docs/superpowers/runs/*/
!docs/superpowers/runs/*/**/
!docs/superpowers/runs/*/**/actual-complexity.json
!docs/superpowers/runs/*/**/code-review-final.json
!docs/superpowers/runs/*/**/complexity-assessment.json
!docs/superpowers/runs/*/**/decisions.jsonl
!docs/superpowers/runs/*/**/events.jsonl
!docs/superpowers/runs/*/**/gate-run.json
!docs/superpowers/runs/*/**/plan.md
!docs/superpowers/runs/*/**/qa-report.md
!docs/superpowers/runs/*/**/spec.md
!docs/superpowers/runs/*/**/technical-analysis.md
# Shape rules: disposable machine state, whatever the Factory names it next
docs/superpowers/tasks/*/**/*.local.*
docs/superpowers/runs/*/**/*.local.*
docs/superpowers/qa-tasks/*/**/*.local.*
```

Three points for the commit body: the `code-review-check.json` allowlist row is deliberately dropped; rows for `decisions.jsonl`, `qa-report.md` and `gate-run.json` are new, because the old block never had them and those files were silently ignored; and AC3's third rule, `.claude/settings.local.json`, already exists at `.gitignore:45` and is not duplicated here.

- [ ] **Step 4: Re-run the evidence command — expect GREEN.** Then confirm the rules reach nothing they must not: `git check-ignore -v docs/superpowers/work-items/work-item-events.jsonl docs/superpowers/plans/2026-07-01-google-docs-oauth.md.old` must print nothing (exit 1), while `git check-ignore -v docs/superpowers/tasks/2026-09-19-epmcdme-15146/state.local.json` must still match.

- [ ] **Step 5: Commit `.gitignore` alone.** Stage nothing under `docs/superpowers/` yet.

---

### Task 2: Untrack the disposable files under `tasks/` and `runs/`

**Files:** the git index only, under `docs/superpowers/tasks/` and `docs/superpowers/runs/`. No file contents change; nothing leaves the disk.

**Interfaces:** Consumes Task 1's committed rules.

**Test-first: yes** — evidence command:

```bash
git ls-files docs/superpowers/tasks docs/superpowers/runs | sed 's#.*/##' | sort -u
```

RED (now): prints disposable names alongside the durable ones — `.state.json`, `state.local.json`, `gate-plan.json`, `code-review.diff`, `code-review-check.json`, `complexity.json`, `requirements.md`, `meta.json`, `reviewed_head.txt`, `verify_fix.py`, among others. GREEN (after): prints exactly the ten keep-list names and nothing else.

- [ ] **Step 1: Run the evidence command; save the RED output to `$SCRATCH/basenames-before.txt`.**

- [ ] **Step 2: Build the removal list and check it before using it.**

```bash
git ls-files docs/superpowers/tasks docs/superpowers/runs | grep -vE "$KEEP" | sort > "$SCRATCH/disposable.txt"
wc -l < "$SCRATCH/disposable.txt"                                       # ticket says 542
grep -cvE '^docs/superpowers/(tasks|runs)/' "$SCRATCH/disposable.txt"   # MUST be 0
```

If the second count is anything but `0`, stop and fix the filter — the sweep is reaching outside scope. A mismatch against 542 is different: the ticket's counts were never verified (research had no shell), so record the observed number as a finding in the commit body and continue.

- [ ] **Step 3: Untrack — index only.**

```bash
tr '\n' '\0' < "$SCRATCH/disposable.txt" | xargs -0 git rm --cached --quiet --
```

- [ ] **Step 4: Verify GREEN.** Re-run Step 1's command: only the ten keep-list names. Then confirm nothing vanished from disk and no stray appeared — `while read -r f; do [ -f "$f" ] || echo "MISSING: $f"; done < "$SCRATCH/disposable.txt"` prints nothing, and `git status --porcelain docs/superpowers/ | grep -v '^D '` prints nothing. Two `runs/` directories (`20260803-EPMCDME-10045/`, `20260918-EPMCDME-15137/`) end up with no tracked files at all — expected and accepted.

- [ ] **Step 5: Commit the staged deletions.**

---

### Task 3: Untrack `.claude/settings.local.json` if tracked

**Files:** the git index entry for `.claude/settings.local.json`, if one exists. `.gitignore:45` already ignores the path; ignore rules never untrack, so tracking status has to be checked directly.

**Test-first: yes** — evidence command: `git ls-files --error-unmatch .claude/settings.local.json`. RED (if tracked): prints the path, exit 0 — a file the repo claims to ignore is tracked anyway. GREEN: `error: pathspec … did not match any file(s) known to git`, exit 1. If it already exits 1 on the first run, this task is a verified no-op — record that output and skip the remaining steps.

- [ ] **Step 1: Run the evidence command and record the result.**

- [ ] **Step 2: If tracked, run `git rm --cached .claude/settings.local.json`.** The file stays on disk; local settings are unaffected.

- [ ] **Step 3: Re-run the evidence command (expect exit 1), confirm `git status --porcelain` shows only the staged `D`, and commit.**

---

### Task 4: Verification guard — counts, legacy trees, fresh-artifact clean tree

**Files:** none. This task changes nothing; it produces the evidence a reviewer reads, and it is the only verification this change can have.

**Test-first: no** — the task *is* the evidence pass. Run it after Tasks 1–3 are committed.

- [ ] **Step 1: Run all five checks and keep the output.**

```bash
git ls-files docs/superpowers/tasks docs/superpowers/runs | sed 's#.*/##' | sort -u   # AC1: ten names only
git ls-files docs/superpowers/tasks docs/superpowers/runs | grep -E "$KEEP" | sort \
  | diff - "$SCRATCH/before-durable.txt"                                              # AC2: empty
git diff --numstat "$(git merge-base HEAD origin/main)" -- docs/superpowers/tasks docs/superpowers/runs \
  | grep -vE '^0\s' | grep -v '^-' || echo "AC2: no durable file modified"
git ls-files docs/superpowers/{plans,specs,reviews,handoffs,work-items} | sort \
  | diff - "$SCRATCH/before-legacy.txt"                                               # AC4: empty
git show "$(git merge-base HEAD origin/main)":docs/superpowers/runs/20260803-EPMCDME-10045/code-review-check.json | head -3
```

The third command lists only paths with non-zero additions that are not pure deletions: durable files must show up nowhere in it. The last proves AC6 — untracked content is still retrievable from history, so no rewrite happened.

- [ ] **Step 2: Prove AC5 with a fresh Factory-shaped artifact.** In `docs/superpowers/tasks/2026-09-19-epmcdme-15146/`, `touch gate-plan.local.json code-review.local.diff`, confirm `git status --porcelain` reports nothing for that directory, then delete both. No ignore-rule edit is permitted to make this pass — if it fails, the fix belongs in Task 1's shape rules.

- [ ] **Step 3: Reconcile the counts.** Compare the observed totals against the ticket's 542 disposable / 872 durable / 1245 tasks / 169 runs / 135 legacy. Every one of those figures is unverified. Report each observed number next to the ticket's, and flag any divergence as a finding — it does not block the change. Note alongside it that no repo gate covers `docs/`, so this evidence, not a gate run, is what verifies the work.
