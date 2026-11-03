# Technical Research

**Task**: gitignore sdlc-factory doctor.json
**Generated**: 2026-11-03
**Research path**: codegraph

---

## 1. Original Context

Ticket EPMCDME-16754, Task, from Jira:

Summary: Untrack and gitignore .ai-run/sdlc-factory/doctor.json in codemie

Description: In the `codemie` repository, `.ai-run/sdlc-factory/doctor.json` is tracked and not gitignored, unlike in `codemie-ui`. This causes friction when working with SDLC Factory. Remove the file from Git tracking and add a targeted `.gitignore` rule so it remains usable locally without introducing unrelated repository changes.

Preconditions: SDLC Factory is used in a local checkout of codemie; `.ai-run/sdlc-factory/doctor.json` is currently tracked.

Scenarios of Use:
1. Remove the file from the Git index without deleting the working copy.
2. Add an ignore rule for `.ai-run/sdlc-factory/doctor.json`.
3. Use SDLC Factory and verify that local changes to this file no longer appear in Git status.

Affected Areas: codemie repository: `.gitignore` and `.ai-run/sdlc-factory/doctor.json`; local SDLC Factory development workflow.

Acceptance Criteria:
1. `git ls-files -- .ai-run/sdlc-factory/doctor.json` returns no tracked file after the change.
2. `git check-ignore .ai-run/sdlc-factory/doctor.json` confirms the file is ignored.
3. Creating or modifying the local file does not appear in normal `git status` output.
4. The untracking operation preserves the working copy, and SDLC Factory can continue using it locally.
5. The ignore rule does not exclude other SDLC Factory files that should remain tracked.

Notes: the working tree currently shows .ai-run/sdlc-factory/doctor.json as modified (refreshed by pre-flight); the file is tracked on main. Compare how codemie-ui handles this if a sibling checkout is available (e.g. ../codemie-ui). Also check whether anything in the repo (scripts, Makefile, docs, other .ai-run files) depends on doctor.json being tracked.

(feature_area: gitignore sdlc-factory doctor.json; run_dir: /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-11-03-epmcdme-16754)

---

## 2. Codebase Findings

Method note: git itself could not be run in this session (read/search tools only). Tracked-file facts come from the names stored in `.git/index` of each checkout; nothing was executed against git.

### Existing Implementations

Purpose A: keep SDLC Factory's machine-local pre-flight cache out of `git status` in codemie.
- `.ai-run/sdlc-factory/doctor.json` (1 line, JSON: `schema`, `checked_at`, `passed`, `checks{superpowers,node,git}`, `fingerprint`). Written by the SDLC Factory pre-flight ("doctor"), not by any repo code. Currently tracked (name is in `.git/index`) and modified in the working tree (`checked_at` 2026-11-03T11:10:11Z, node v24.19.0, git 2.54.0).
- `.ai-run/sdlc-factory/.gitignore` (content: `*`). Present on disk, NOT in `.git/index`, so untracked; it ignores itself and every sibling. This is why `reply-*.txt`, `mr-comment*.txt`, `mr-description.md`, `commit-msg.txt` in that directory do not show in status. Tracked files are unaffected by it, which is why `doctor.json` still shows as modified. Who creates it: unknown (not authored in repo; likely the Factory tooling). `codemie-ui` has an identical `*` file.
- Root `/.gitignore` has no `doctor` or `sdlc-factory` entry. Nearest related rules: `.ai-run/runs` (line 72, unanchored, no slash prefix), `/.codegraph/` (73), and the "SDLC Factory: keep the durable record, ignore the machine state" block for `docs/superpowers/tasks/**` (74-94, uses `**` plus `!` re-includes).
- Purpose A in `codemie-ui` (sibling at `/Users/Oleksandr_Cherevach/work/codemie-dev/codemie-ui`): root `.gitignore` lines 41-43 ignore the whole directory `.ai-run/sdlc-factory/`, plus `.ai-run/runs/` and `.agents/memory/sdlc/daily/`. No path under `.ai-run/sdlc-factory/` is in its `.git/index`. `doctor.json`, `commit-msg.txt`, `mr-comment.txt` and the inner `.gitignore` exist on disk only.

Other touched code: none. No application code, tests, Makefile, scripts or CI are involved.

### Decisions Already in the Code
- Sibling repo chose a directory-wide ignore (`.ai-run/sdlc-factory/`), not a file rule. Alternative: file-specific rule (the ticket's choice). Reason: none found.
- codemie has a tracked file besides `doctor.json` in the same directory: `.ai-run/sdlc-factory/workspace-image-inspector/code-review-final.json` (in `.git/index`). A directory-wide ignore would not untrack it but would hide any new files under it. Reason it is tracked: none found.
- Convention for ignore rules in this repo's root `.gitignore`: leading-slash anchored paths for fixed locations (e.g. `/.codegraph/`, `/.superpowers/`), but `.ai-run/runs` is unanchored. Both styles coexist.
- A rule matching a tracked file has no effect on it until untracked (git semantics); `git check-ignore` on a still-tracked path prints nothing unless `--no-index` is given.

### Task Statements Checked Against the Code
- "`doctor.json` is tracked and not gitignored in codemie": holds. In `.git/index`; no root rule. Nuance: the untracked inner `.gitignore` (`*`) already matches it locally, but it does not apply to a tracked file and is not shared via the repo.
- "unlike in codemie-ui": holds. codemie-ui `.gitignore:41` ignores the directory; nothing from it is tracked.
- "file is modified in the working tree": holds (content above; status snapshot lists only this file).
- "ignore rule must not exclude other SDLC Factory files that should remain tracked" (AC5): the only other tracked path under `.ai-run/sdlc-factory/` is `workspace-image-inspector/code-review-final.json`. Whether it "should" remain tracked is unverified (no recorded reason found).
- AC2/AC3 after the change: rest on git semantics, not verified here by execution.
- "Nothing depends on it being tracked": see next subsection.

### Consumers of Changed Contracts
Searched `doctor.json` and `sdlc-factory` across the repo (Makefile, `*.mk`, `pyproject`/toml, yaml, sh, `.pre-commit-config.yaml`, `.gitleaks.toml`, `.dockerignore`, `.gitattributes`, AGENTS.md, README, `.ai-run/**`, `src/`, `tests/`):
- No script, Makefile, CI/pipeline config, pre-commit hook, Dockerfile or `.ai-run` guide reads `doctor.json` or requires it tracked. Only hit in config: `config/customer/customer-config.yaml:127` (`regex: "^sdlc-factory:"`, a skill-name pattern, unrelated to the file).
- `.ai-run/guides/*` mention `sdlc-factory` only as skill/plugin names (`project.md:53,70`, `integration/cli-analytics-events.md`), not the file.
- References in `docs/superpowers/**` are historical records only: review diffs (`reviews/2026-08-25-EPMCDME-13903/code-review.diff`, `reviews/2026-09-01-EPMCDME-13903-mr3941/code-review.diff`, `reviews/20260720-epmcdme-13259-codemie/code-review.diff`), a run event listing it as an artifact (`runs/20260723-1200-EPMCDME-13541/events.jsonl:2`), and many task notes saying "do not stage `doctor.json`" (e.g. tasks 15327, 14763, 16435, 15350). `tasks/2026-07-17-epmcdme-10928-.../actual-complexity.json:28` and `tasks/2026-07-23-epmcdme-13666-.../code-review-final.json:83` list it among changed files. None need it tracked.
- Runtime consumer is the SDLC Factory pre-flight (outside this repo); it caches check results (TTL 7d per `tasks/2026-08-29-epmcdme-11696-faq-git-datasource/technical-analysis.md:75`) and rewrites the file. It stays working so long as the working copy is preserved; behaviour when the file is absent is not verifiable here.
- Workspace-image-inspector file: no references outside `docs/superpowers/reviews|runs` (searched whole repo).

### Architecture and Layers Affected
Repository configuration only (git ignore / index). No application layers.

### Integration Points
SDLC Factory plugin pre-flight (external to repo) writes the file. Git index/`.gitignore` is the only repo mechanism.

### Patterns and Conventions
- Repo already groups SDLC Factory ignore rules under a comment block in root `.gitignore` (lines 74-94).
- Commit subject convention `EPMCDME-<n>: <summary>`; stage by explicit path (task notes forbid `git add -A`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` has no guide about `.gitignore`, `doctor.json` or SDLC Factory local state. `standards/git-workflow.md` exists (branch/commit conventions); not read in detail, no mention of `doctor.json` found by search.

### Architectural Decisions
None found for tracking `doctor.json`; no ADR or comment explains why it is tracked.

### Derived Conventions
codemie-ui treats `.ai-run/sdlc-factory/` as machine state. Many past codemie task notes treat `doctor.json` as unrelated dirty noise that must not be committed.

---

## 4. Testing Landscape

### Existing Coverage
None; no tests reference `doctor.json` or `.gitignore`.

### Testing Framework and Patterns
pytest (project-wide); not relevant to this change. Verification is by git commands (the ticket's acceptance criteria).

### Coverage Gaps
Not applicable (no code).

---

## 5. Configuration and Environment

### Environment Variables
None found.

### Configuration Files
- `/.gitignore` (root) is the only file that carries shared ignore rules; `.git/info/exclude` has no `ai-run` entry.
- `.ai-run/sdlc-factory/.gitignore` (`*`) is untracked, local.

### Feature Flags and Deployment Concerns
None. `.dockerignore`/Dockerfile have no reference to the file.

---

## 6. Risk Indicators

- Low-complexity, no code: two repo-state changes (`.gitignore` rule; index removal of one path).
- Index removal must keep the working copy (scenario 1/AC4); mechanism is a git-level operation, not run or verified here. Speculative: a plain file deletion would break AC4.
- Past task folders record `doctor.json` as modified-and-unstaged on many branches; after untracking, the commit that removes it from the index shows as a deletion for other open branches/MRs that modify it (merge friction on branches touching the file; dozens of historical notes suggest several exist). Speculative.
- Second tracked path in the same directory, `workspace-image-inspector/code-review-final.json`, interacts with how broad the rule is (AC5). Pointer: Decisions Already in the Code.
- The untracked self-ignoring `.ai-run/sdlc-factory/.gitignore` (`*`) already hides all untracked siblings locally but is not a shared repo rule; its origin is unknown. For `git check-ignore` (AC2), a still-tracked path reports nothing; whether the existing inner file or a new root rule is what matches after untracking is not verified. Speculative.
- Existing mechanism for the same purpose kept in place by the ticket: none; the ticket adds a rule beside the inner `*` file (both would match).
- Historical diff files under `docs/superpowers/reviews/` that contain `doctor.json` hunks are static records; no impact found.
- Local `main` is stale per memory notes; branch diffs may include unrelated changes. Compare against `origin/main`.

---

## 7. Summary for Complexity Assessment

The change touches configuration only: the root `/.gitignore` and the git index entry for one 1-line JSON cache file written by the external SDLC Factory pre-flight. No application layer, test or dependency is involved. The file change surface is one ignore rule and one path removed from tracking.

No technical novelty. Search of Makefile, scripts, pre-commit, CI-adjacent config, `.ai-run` guides and docs found no consumer that needs `doctor.json` tracked; references are historical records and "do not stage" notes. The sibling `codemie-ui` already ignores the whole `.ai-run/sdlc-factory/` directory and tracks nothing under it; codemie additionally tracks `workspace-image-inspector/code-review-final.json` there, which bears on how broad the rule can be under AC5.

There are no tests to adjust; verification is by git commands. Risk factors are limited to preserving the working copy, the already-present untracked inner `.gitignore` obscuring what `git check-ignore` reports, and merge friction on open branches that modify the file. The complexity signal is trivial.

---

## 8. External References

None named by the task (the `../codemie-ui` sibling was read as suggested: `/Users/Oleksandr_Cherevach/work/codemie-dev/codemie-ui/.gitignore:41-43` ignores `.ai-run/sdlc-factory/`, `.ai-run/runs/`, `.agents/memory/sdlc/daily/`; `.ai-run/sdlc-factory/.gitignore` there contains `*`).
