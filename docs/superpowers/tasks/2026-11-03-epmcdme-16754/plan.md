# Untrack and gitignore doctor.json Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove `.ai-run/sdlc-factory/doctor.json` from Git tracking (keeping the working copy) and ignore it via one targeted root `.gitignore` rule.

**Architecture:** Configuration only. One new line in the root `.gitignore` plus an index-only removal (`git rm --cached`) of a single path. No application code.

**Tech Stack:** git.

Commit per task using the repository's existing convention (`EPMCDME-16754: <Description>`); stage by explicit path, never `git add -A`.

## Global Constraints

- Exactly one new ignore rule, for the single path `.ai-run/sdlc-factory/doctor.json`, in the root `.gitignore`.
- Do NOT ignore the whole `.ai-run/sdlc-factory/` directory (`workspace-image-inspector/code-review-final.json` stays tracked).
- Untrack with `git rm --cached` only; never delete the working copy.
- The ignore rule and the untracking land in the same commit.

## Acceptance criteria

- `git ls-files -- .ai-run/sdlc-factory/doctor.json` prints nothing.
- `git check-ignore .ai-run/sdlc-factory/doctor.json` prints the path (ignored).
- Modifying or recreating the local file does not appear in `git status`.
- The working copy of `.ai-run/sdlc-factory/doctor.json` still exists after the change.
- `git ls-files -- .ai-run/sdlc-factory/workspace-image-inspector/code-review-final.json` still lists the file.

---

### Task 1: Untrack doctor.json and add targeted ignore rule

**Files:**
- Modify: `.gitignore:72-74` (add rule with a one-line comment directly after `/.codegraph/` at line 73, before the "SDLC Factory: keep the durable record" comment block)
- Untrack: `.ai-run/sdlc-factory/doctor.json` (index only)

Test-first: no — config-only change, verified by git commands (the acceptance criteria above); no application behaviour to test.

- [ ] **Step 1: Add the rule, untrack, verify, commit**

  1. In `.gitignore`, after line 73 (`/.codegraph/`), insert a comment line (`# SDLC Factory pre-flight cache: machine-local, not shared`) followed by the anchored rule `/.ai-run/sdlc-factory/doctor.json`. Change nothing else in the file.
  2. Run `git rm --cached -- .ai-run/sdlc-factory/doctor.json`. This stages a deletion and leaves the file on disk. The file currently shows as modified in the working tree; that is expected and the local content is kept as is.
  3. Verify each criterion:
     - `git ls-files -- .ai-run/sdlc-factory/doctor.json` prints nothing.
     - `git check-ignore -v .ai-run/sdlc-factory/doctor.json` reports the root `.gitignore` rule. The untracked inner `.ai-run/sdlc-factory/.gitignore` (`*`) also matches locally, so confirm `-v` names the root `.gitignore` line, not just the inner file.
     - `test -f .ai-run/sdlc-factory/doctor.json && echo present` prints `present`.
     - `git status --short` shows only `M .gitignore` and `D  .ai-run/sdlc-factory/doctor.json` (staged), with no untracked `doctor.json` entry. The `docs/superpowers/tasks/` run folder is outside this change and must not be staged.
     - `git ls-files -- .ai-run/sdlc-factory/workspace-image-inspector/code-review-final.json` still lists the file.
  4. Stage `.gitignore` explicitly (`git add -- .gitignore`) and commit both changes together as `EPMCDME-16754: Untrack and gitignore SDLC Factory doctor.json`.

---

negative-constraints: whole-directory ignore forbidden and working-copy deletion forbidden — both honored by Task 1 (single-path anchored rule; `git rm --cached`). "Without unrelated repo changes" — honored by staging explicit paths only.
