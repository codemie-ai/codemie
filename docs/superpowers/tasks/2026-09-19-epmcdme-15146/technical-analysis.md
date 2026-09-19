# Technical Research

**Task**: gitignore sdlc-run-artifacts git-tracking repo-hygiene
**Generated**: 2026-09-19
**Research path**: codegraph

---

## 1. Original Context

task_context (verbatim ticket EPMCDME-15146 "Prune disposable SDLC run artifacts - codemie", Sub-task of EPMCDME-15145):

h2. Context

* 1245 files are tracked under docs/superpowers/tasks/ across 168 run directories; *421 are disposable* machine state or regenerable intermediates.
* A second run root, docs/superpowers/runs/, tracks 169 files across 18 directories, *121 of them disposable*. Total clean-up scope: *542 files*.
* The .gitignore already uses the right idea - ignore docs/superpowers/tasks/**, then un-ignore each durable filename - but the 542 files predate it, and a gitignore never untracks what git is already tracking.
* The allowlist needs a per-filename edit for every new artifact the Factory invents. It has no .local.* shape rule, which is what makes the reference pattern maintenance-free.
* The allowlist currently un-ignores code-review-check.json (102 files). The reference pattern treats it as disposable because code-review-final.json supersedes it. *Decision taken: disposable.*
* docs/superpowers/plans/, specs/, reviews/, handoffs/, work-items/ hold 135 hand-authored legacy documents. They are not machine state and are out of scope.

Reference: codemie-code PR 568, commit 8fe683d. Repo: codemie-ai/codemie, branch main. Estimated size: S.

h2. Background

Every SDLC Factory run writes both a durable record and a pile of machine state - resume cursors, detected-runner caches, per-lens review inputs, diff snapshots. In codemie both kinds have been committed for months. The cost lands on reviewers: a tracked code-review.diff from a previous run gets counted inside the next review's scope, inflating it. The keep-list is already encoded in the repo's .gitignore, so the intent exists; what is missing is the one-time untrack and a rule shaped to survive the next artifact the Factory adds.

h2. Acceptance Criteria

* Given the 542 disposable files currently tracked under docs/superpowers/tasks/ and docs/superpowers/runs/, when the clean-up commit lands, then git ls-files reports zero tracked files in those roots whose filename is outside the durable keep-list.
* Given the durable keep-list (actual-complexity.json, code-review-final.json, complexity-assessment.json, decisions.jsonl, events.jsonl, gate-run.json, plan.md, qa-report.md, spec.md, technical-analysis.md), when the clean-up commit lands, then all 872 currently-tracked durable files are still tracked and unmodified.
* Given .gitignore, when the change lands, then it carries the shape-based rules docs/superpowers/tasks/*/**/*.local.*, docs/superpowers/qa-tasks/*/**/*.local.* and .claude/settings.local.json, and the per-filename allowlist rows they supersede are removed.
* Given the 135 legacy documents under docs/superpowers/plans/, specs/, reviews/, handoffs/ and work-items/, when the clean-up commit lands, then every one of them is still tracked - the clean-up must not reach outside tasks/ and runs/.
* Given a fresh SDLC Factory run after the change, when it writes state.local.json, gate-plan.local.json and code-review.local.diff, then git status reports a clean tree without any new ignore-rule edit.
* Given the repository history, when the change lands, then no commit has been rewritten - the files remain retrievable from history and no force-push was performed.

h2. Out of Scope

* Rewriting git history to purge the artifacts from past commits. Untracking is forward-only.
* The legacy plans/, specs/, reviews/, handoffs/, work-items/ document trees.
* Changing what the SDLC Factory itself writes - this sub-task only changes what the repo tracks.
* Deleting the local working-copy files; they stay on disk for in-flight runs.

h2. Open Questions

* The runs/ root is not covered by the reference PR's rule set (codemie-code ignores docs/superpowers/runs/ wholesale). Should codemie do the same, or apply the keep-list there too? 18 of its directories hold 48 durable files, which argues for the keep-list.

NOTE: this task is repository-hygiene work (.gitignore rules and `git rm --cached` untracking), not application-code work. Ground your findings in the actual repository state: the current .gitignore content and its SDLC-artifact section, the exact set of tracked files and distinct filenames under docs/superpowers/tasks/, docs/superpowers/runs/ and docs/superpowers/qa-tasks/, whether .claude/settings.local.json is tracked, and any CI/tooling that reads those paths. If the codegraph tool cannot serve this, say so.

---

## 2. Codebase Findings

**Research-tool note (recorded as a finding, not a risk):** codegraph is available and was queried, but it indexes Python symbols and their edges. Two explore calls (`gitignore`, and a direct query for `docs/superpowers` artifact paths referenced in code/tests/config) returned only unrelated application code — `src/codemie/core/utils.py::check_file_type`, `_create_pathspec_from_filter`, the Git/SVN/FAQ loaders, `checkpoint_saver.py`, tool tests. **No source file, test, or config in this repository references `docs/superpowers/`, `docs/superpowers/tasks/`, `docs/superpowers/runs/` or `.gitignore` as data.** Codegraph cannot enumerate git-tracked files; the filesystem facts below come from `Read` of the actual config files and `Glob` of the working tree.

### Existing Implementations

- `/Users/Nikita_Levyankov/repos/codemie-ai/codemie/.gitignore` — 82 lines. The SDLC-artifact block is lines 69–79:
  - L69 comment `# SDLC Factory: keep only the key planning artifacts, ignore working files`
  - L70 `docs/superpowers/tasks/**`
  - L71 `!docs/superpowers/tasks/*/`
  - L72–L79 per-filename un-ignore rows, exactly eight: `technical-analysis.md`, `complexity-assessment.json`, `actual-complexity.json`, `spec.md`, `plan.md`, `code-review-final.json`, `code-review-check.json`, `events.jsonl` — all under the `docs/superpowers/tasks/*/` prefix.
  - Relevant rows elsewhere in the same file: L3 `.DS_Store`, L45 `.claude/settings.local.json` (already present), L62 `/.superpowers/`, L67 `.ai-run/runs`, L68 `/.codegraph/`, L81 `.code-graph/`.
- The keep-list in the ticket has ten filenames. Three of them — `decisions.jsonl`, `qa-report.md`, `gate-run.json` — have **no un-ignore row** in the current block, while `code-review-check.json` (L78) has one and the ticket classifies it as disposable.
- No `.gitignore` exists anywhere under `docs/` (Glob `docs/**/.gitignore` → no matches); all rules live in the repo-root file.
- **No rule in the file mentions `docs/superpowers/runs/` or `docs/superpowers/qa-tasks/` at all.**

### Architecture and Layers Affected

This task has no application layer. The surfaces are:

- **VCS-configuration layer** — the single root `.gitignore`.
- **Git index** — tracked-file set under `docs/superpowers/tasks/` and `docs/superpowers/runs/` (changed by `git rm --cached`, which is an index operation, not a code change).
- **Working tree / documentation tree** — `docs/superpowers/` and its sub-roots; files stay on disk.
- Untouched: `src/codemie/` (~2,764 indexed files), `tests/`, Makefile targets, deployment manifests.

### Integration Points

Tooling that could read or be affected by these paths, checked directly:

- `sonar-project.properties` — `sonar.sources=src/codemie`, `sonar.tests=tests`, exclusions cover deploy-templates/charts/templates/provider. `docs/` is outside Sonar's scope entirely.
- `.gitlab-ci.yml` — 34 lines; only includes `epm-cdme/codemie-gitlab-templates` `pre-ci-guard.yml`, runs on merge-request events, has a `no-op` job and a `wait-ci-init` job expecting contexts `ci-pipeline` and `compliance-report`. No path rules, no job reads `docs/`.
- `.github/` — contains only `ISSUE_TEMPLATE/bug_report.md`, `ISSUE_TEMPLATE/feature_request.md`, `PULL_REQUEST_TEMPLATE.md`. No workflows.
- `.pre-commit-config.yaml` — four local hooks (`codemie-pre-commit` ruff+license via `scripts/git-hooks/pre_commit.sh`, `codemie-commit-msg`, `codemie-pre-push` pytest+sonar-local, `codemie-gitleaks` `gitleaks protect --staged`). All are `pass_filenames: false`/`always_run: true`; none is path-scoped to `docs/`.
- `.claude/settings.json` — tracked-looking sibling of the ignored local file; declares a `Stop` hook running `make ruff` and `enabledPlugins: sdlc-factory@sdlc-factory`.
- `.ai-run/guides/quality-gates.md` — `make gitleaks` runs `gitleaks dir` over the **full working tree**, i.e. over files regardless of git tracking status.

### Patterns and Conventions

- The repo's SDLC block uses the **ignore-all-then-allowlist** shape (`tasks/**` + `!` rows). The reference repo uses the opposite, **denylist** shape (see Section 8).
- Git semantics that govern this block: `!docs/superpowers/tasks/*/` re-includes only the **first level** of run directories. Deeper directories are not re-included, so files inside them cannot be un-ignored by a later `!` row. Such deeper directories exist today:
  - `docs/superpowers/tasks/2026-08-27-skip-private-project-creation/enterprise-gates/` (`gate-plan.json`, `gate-run.json`)
  - `docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/evidence/` (11 `*.json` files)
  - `docs/superpowers/runs/<dir>/evidence/` in at least four run directories, and `docs/superpowers/runs/20260701-1735-main/.claude/skills/.codemie-sync.json` (a hidden nested path).
- Distinct artifact filenames observed in the working tree under `docs/superpowers/tasks/` (sample across 2026-05 → 2026-09):
  - keep-list shapes: `spec.md`, `plan.md`, `technical-analysis.md`, `complexity-assessment.json`, `actual-complexity.json`, `qa-report.md`, `decisions.jsonl`, `events.jsonl`, `code-review-final.json`, `gate-run.json`
  - disposable shapes: `.state.json` (≈105 occurrences), `state.local.json`, `.stop-nudge`, `.test.log`, `.DS_Store`, `gate-plan.json`, `code-review.diff`, `code-review-check.diff`, `code-review-ui.diff`, `code-review-check-ui.diff`, `code-review-fixup.diff`, `code-review.head`, `code-review-brief.md`, `code-review-deferred.md`, `code-review-check.json`, `code-review-fix-evidence.json`, `cr-012-pipeline-evidence.md`, `lens-edge-case.json`, `lens-acceptance.md`, `standards-review.json`, `implementation.jsonl`, `handoff.md`, `complexity-assessment.md` (`.md` variant, not the `.json` keep-list entry), `evidence/*.json`, `enterprise-gates/*.json`.
- Distinct filenames under `docs/superpowers/runs/` diverge from the tasks/ vocabulary: `complexity.json` (not `complexity-assessment.json`), `requirements.md`, `work-item.md`, `work-item-events.jsonl`, `design.md`, `meta.json`, `review-bundle.json`, `review.diff`, `code-review-be.diff`, `code-review-fe.diff`, `code-review-fe.json`, `qa-gates.json`, `reviewed_head.txt`, `branch-guard.json`, `plan-corrections-needed.md`, `feature-verification-plan.json`, `mr-description.md`, research notes (`analysis.md`, `backend-map.md`, `frontend-map.md`, `tech-landscape.md`, `a2ui-deep-dive.md`), plus executable scripts `verify_fix.py` and `feature-verification.py`.
- `docs/superpowers/qa-tasks/` **does not exist** in the working tree (Glob `docs/superpowers/qa-tasks/**/*` → no files).
- `.claude/settings.local.json` **exists on disk** alongside `.claude/settings.json`, and `.gitignore:45` already ignores it.
- Loose files sit directly under `docs/superpowers/`: `agentcore-non-streaming.md`, `EPMCDME-14249-duplicate-litellm-keys-investigation.md`, `.DS_Store`.
- Legacy roots confirmed present and populated: `plans/` (~40 `.md`, plus `2026-07-01-google-docs-oauth.md.old`), `specs/` (~30 `-design.md`, plus a vim swap file `.2026-05-21-agentcore-list-prototype-design.md.swp`), `handoffs/` (~12), `work-items/` (~12, including `work-item-events.jsonl`). No `reviews/` directory matched the glob.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` holds 41 guides. Two are relevant to this task; none covers SDLC-artifact retention or `.gitignore` policy.

- `.ai-run/guides/quality-gates.md` — gate order and exact commands: `make ruff`, `make build`, `make license-check`, `make gitleaks` (`gitleaks dir` over the whole working tree, Docker-only), `make test`, `make coverage`, `make sonar-local`, `make verify`, `make test-harness`. Explicitly states "No pipeline in this repository runs these gates" and instructs verifying with `ls .gitlab-ci.yml .github/workflows`; it also documents that a human posting `/sanity` on the MR is what runs the regression suite, and that the `auto_epm-cdme_vcs` bot only reads MR description text.
- `.ai-run/guides/standards/git-workflow.md` — branch `EPMCDME-12345_short-description`, commit `EPMCDME-####: Description`, squash merge, git side effects only on explicit request, `glab mr create` invocation, inline-comment API recipe.
- `AGENTS.md` / `CLAUDE.md` — guide routing table plus the repository rule: an MR touching dependencies, the Dockerfile, or security-relevant code must ask a reviewer to post `/sanity`.

No guide describes the durable-vs-disposable artifact taxonomy; the keep-list exists only in the ticket and, partially, in `.gitignore` lines 72–79.

### Architectural Decisions

- Encoded in `.gitignore:69` as a comment: "SDLC Factory: keep only the key planning artifacts, ignore working files" — the retention intent is recorded in the file itself.
- Recorded in the reference repo's `.gitignore` as inline rationale comments (see Section 8): the detected-runner cache is machine-specific and regenerated; review-diff snapshots are regenerable from `git diff <merge-base>...HEAD` and otherwise inflate the next review's scope.
- The ticket records one decision already taken: `code-review-check.json` is disposable because `code-review-final.json` supersedes it.
- No ADR directory exists in this repository.

### Derived Conventions

- Run directories under `tasks/` are named `YYYY-MM-DD-<slug>`; under `runs/` they are `YYYYMMDD[-HHMM]-<TICKET|slug>`. One tasks/ directory breaks the pattern: `docs/superpowers/tasks/epmcdme-11252/`.
- Newer runs (2026-09 onward) emit `.local.`-infixed names — `state.local.json` appears in exactly one directory in the tree, `docs/superpowers/tasks/2026-09-19-epmcdme-15146/state.local.json` (this task's own run) — while older runs emit dot-prefixed `.state.json`. Both shapes coexist on disk.
- Quality gates in this repo are Makefile targets run by hand or by git hooks; nothing external validates a commit's file list.

---

## 4. Testing Landscape

### Existing Coverage

No automated test in this repository exercises `.gitignore` semantics or the contents of `docs/superpowers/`. Codegraph's targeted query for tests referencing those paths returned unrelated suites (`tests/codemie_tools/base/test_codemie_tool.py`, `tests/codemie_tools/git/devops/test_azure_tools.py`). Dimension result: **empty for the feature area**.

### Testing Framework and Patterns

pytest, run through `make test` over `tests/` (`.ai-run/guides/quality-gates.md`; `.ai-run/guides/testing/testing-patterns.md` is the policy guide). Sonar declares `sonar.python.version=3.12` and `sonar.tests=tests`. Pre-push hook `codemie-pre-push` runs pytest plus `make sonar-local`.

### Coverage Gaps

- Nothing asserts which files are tracked under the two run roots; verification is necessarily by git command (`git ls-files docs/superpowers/tasks docs/superpowers/runs`, `git status --porcelain`, `git check-ignore -v <path>`) rather than by a test.
- The gate suite that will actually execute over this change (`make ruff`, `make license-check`, `make gitleaks`, `make test`) touches `src/codemie` and `tests` and so exercises none of the changed surface — a passing gate run says nothing about correctness here.

---

## 5. Configuration and Environment

### Environment Variables

None in the feature area. The only env toggle adjacent to the commit path is `CODEMIE_PRECOMMIT_ENABLED=false`, documented in `.ai-run/guides/quality-gates.md` as the bypass for `codemie-pre-commit` and `codemie-gitleaks`.

### Configuration Files

- `.gitignore` (repo root) — the only file governing tracking for these paths; contents detailed in Section 2.
- `.pre-commit-config.yaml` — hooks that run on commit/push; none path-scoped to `docs/`.
- `.gitlab-ci.yml` — pre-CI guard only; no path rules.
- `sonar-project.properties` — scope excludes `docs/`.
- `.claude/settings.json` (present) and `.claude/settings.local.json` (present on disk, ignored by `.gitignore:45`).
- `Makefile` — gate command source of truth per the quality-gates guide.

### Feature Flags and Deployment Concerns

- No feature flags. No deployment manifest references these paths.
- `make gitleaks` runs `gitleaks dir` against the working tree; because the clean-up deliberately leaves files on disk, the secret-scan surface is unchanged by untracking.
- Merge-side mechanisms are text-only: the `auto_epm-cdme_vcs` bot reads the MR description; the regression suite runs only when a human posts `/sanity`.

---

## 6. Risk Indicators

- **Tracked-vs-untracked state could not be verified in this research pass.** This agent has no shell, so `git ls-files` was not run; every filesystem fact above comes from `Glob` of the working tree, which lists ignored files identically to tracked ones. The counts 1245 / 169 / 542 / 872 / 102 are the ticket's, unverified here.
- `.gitignore:72–79` has no un-ignore row for `decisions.jsonl`, `qa-report.md` or `gate-run.json`, yet files with those names exist in many `tasks/` run directories (e.g. `2026-09-03-epmcdme-13567-budget-reset-member-spend/gate-run.json`, `.../decisions.jsonl`, `2026-09-02-.../qa-report.md`). Any such file created after the ignore block landed is currently ignored, so the "872 durable files still tracked" figure and the working-tree contents cannot be assumed to agree. *Speculative: reconciling the block with the ten-name keep-list implies adding rows for these three and removing the `code-review-check.json` row.*
- Git cannot re-include a file inside a directory that is still excluded. `!docs/superpowers/tasks/*/` re-includes one level only, and a keep-listed `gate-run.json` lives at `docs/superpowers/tasks/2026-08-27-skip-private-project-creation/enterprise-gates/gate-run.json`. *Speculative: preserving that file under an allowlist shape would need a nested-directory re-include, or the shape switched to a denylist as in the reference repo.*
- `docs/superpowers/runs/` has zero ignore coverage today and its filename vocabulary does not match the tasks/ keep-list (`complexity.json`, `requirements.md`, `work-item.md`, `design.md`, `meta.json`, `review-bundle.json`, `reviewed_head.txt`). Applying the tasks/ keep-list verbatim there would empty whole directories — `docs/superpowers/runs/20260803-EPMCDME-10045/` holds only `code-review-check.json`, and `20260918-EPMCDME-15137/` only `code-review-check.json` + `code-review-final.json`. This is the ticket's open question and it is unresolved.
- `runs/` contains executables and odd shapes that no filename allowlist anticipates: `20260728-1024-EPMCDME-11241_update-dashboard-tool/verify_fix.py`, `20260806-1647-EPMCDME-13912/feature-verification.py`, `.../reviewed_head.txt`, and the hidden nested `20260701-1735-main/.claude/skills/.codemie-sync.json` — hidden segments are a classic miss for `*` patterns.
- The `code-review-check.json` flip is the largest single behavioural change (ticket: 102 files). It is currently an explicitly allowlisted row, so untracking it reverses a prior, deliberate decision; reviewers browsing `main` lose that artifact going forward.
- The clean-up executes inside a live run directory: `docs/superpowers/tasks/2026-09-19-epmcdme-15146/state.local.json` is being written by this very run. An untrack sweep that also stages deletions, or a rule that hides the in-flight run's own outputs, can disturb the run producing the change.
- `make gitleaks` scans the working tree (`gitleaks dir`), not the index. Untracking removes nothing from that scan, so leftover `code-review*.diff` snapshots on disk remain a secret-scan surface.
- Artifact-shaped files exist **inside** the out-of-scope legacy trees: `docs/superpowers/work-items/work-item-events.jsonl`, `docs/superpowers/specs/.2026-05-21-agentcore-list-prototype-design.md.swp`, `docs/superpowers/plans/2026-07-01-google-docs-oauth.md.old`. A shape-based rule written loosely (e.g. anchored at `docs/superpowers/**`) would catch them and breach the "must not reach outside tasks/ and runs/" criterion.
- `docs/superpowers/qa-tasks/` does not exist in the working tree, yet the acceptance criteria require a rule for it — the rule will be unverifiable against real files at commit time.
- `.claude/settings.local.json` is already ignored at `.gitignore:45` and exists on disk; whether it is nonetheless tracked (ignore rules do not untrack) could not be checked without git.
- No test, CI job, Sonar scope, or source file covers the changed surface, so nothing in the automated gate set can detect a mistake. Verification depends entirely on manually run git commands, and the guide is explicit that no pipeline runs the gates after merge.
- The local reference checkout (`/Users/Nikita_Levyankov/repos/codemie-ai/codemie-code/.gitignore`) does **not** contain the `*.local.*` shape rules the acceptance criteria name; it may predate commit `8fe683d`. The authoritative reference text for those rules has not been observed (see Section 8).
- Repo-level process risk: `.ai-run/guides/quality-gates.md` and `AGENTS.md` require the MR description to carry a `## Test harness` block for the `auto_epm-cdme_vcs` bot, and a `/sanity` request when relevant — a commit that changes no application code still has to satisfy those text checks.

---

## 7. Summary for Complexity Assessment

The change surface is narrow and entirely outside application code. One configuration file — `/Users/Nikita_Levyankov/repos/codemie-ai/codemie/.gitignore`, 82 lines, with an eleven-line SDLC block at lines 69–79 — plus a bulk git-index operation over two documentation roots. Codegraph confirms nothing in `src/codemie/` or `tests/` reads these paths; Sonar excludes `docs/` (`sonar.sources=src/codemie`); `.gitlab-ci.yml` is a pre-CI guard with no path rules; `.github/` has no workflows; the four pre-commit hooks are `always_run` and not path-scoped. So blast radius on running code is nil, and the file-count is large only in the index, where it is one mechanical sweep rather than many edited files.

Technical novelty is low but the semantics are sharp-edged, and that is where the real difficulty sits. Three facts observed in the tree complicate a naive edit: the current allowlist is missing rows for three of the ten keep-list names (`decisions.jsonl`, `qa-report.md`, `gate-run.json`) while carrying a row for one name the ticket declares disposable (`code-review-check.json`); nested directories genuinely exist under run dirs (`enterprise-gates/`, `evidence/`, and a hidden `.claude/skills/` path under `runs/`), and git's re-inclusion rule stops at the one level the current `!docs/superpowers/tasks/*/` row re-includes; and `docs/superpowers/runs/` has no ignore coverage at all plus a filename vocabulary (`complexity.json`, `requirements.md`, `work-item.md`, `meta.json`, `reviewed_head.txt`) that does not overlap the tasks/ keep-list — two of its directories would be emptied outright by applying that keep-list. The ticket leaves the runs/ treatment as an open question, and `docs/superpowers/qa-tasks/` does not exist on disk although a rule for it is required.

Test-coverage posture is empty by nature and cannot be improved: no pytest suite can assert a tracked-file set, and the gates that will run (`make ruff`, `make license-check`, `make gitleaks`, `make test`) all operate on `src/codemie` and `tests`, so a green gate run carries no signal about this change. Verification is git-command evidence only (`git ls-files`, `git status --porcelain`, `git check-ignore -v`), which was itself not executable during this research pass — all counts in the ticket remain unverified here. The main risk factors are therefore verification-by-inspection rather than implementation difficulty: an over-broad rule reaching the legacy `plans/ specs/ handoffs/ work-items/` trees (which contain artifact-shaped strays like `work-items/work-item-events.jsonl` and a `.md.swp` in `specs/`), an under-broad rule silently dropping keep-list files nested one level deeper, and the fact that the clean-up runs inside a live run directory writing `state.local.json` as it executes.

---

## 8. External References

The task names one source of truth: **codemie-code PR 568, commit `8fe683d`** — the "reference pattern". No local filesystem path was given, but the repository is checked out locally and its ignore file was read.

- **Resolved**: `/Users/Nikita_Levyankov/repos/codemie-ai/codemie-code/.gitignore` (92 lines). Caveat: this is whatever revision the local checkout currently holds; it was **not** confirmed to be commit `8fe683d`, and it does not contain the `*.local.*` rules the acceptance criteria attribute to the reference — so it is likely an earlier revision.
- Facts the task needs from it, close to verbatim:
  - It is a **denylist**, not an allowlist: there is no `docs/superpowers/tasks/**` blanket ignore and no `!` un-ignore rows at all. Disposable shapes are named individually.
  - `docs/superpowers/runs/` (L76) — the entire runs/ root is ignored wholesale. This is the basis of the ticket's open question.
  - `docs/superpowers/tasks/*/.state.json` (L77).
  - `docs/superpowers/tasks/*/gate-plan.json` (L79), preceded by the rationale comment (L78): "Detected-runner cache — machine/environment-specific, regenerated by qa-gates on every run."
  - `docs/superpowers/tasks/*/code-review*.diff` (L86), preceded by the rationale comment (L84–85): "Review-diff snapshots are regenerable from git (git diff <merge-base>...HEAD) and otherwise get counted inside the next diff, inflating review scope."
  - Adjacent AI-state rules in the same block: `.ai-run/sdlc-factory/` (L73), `.ai-run/runs/` (L74), `.agents/memory/sdlc/daily/` (L75), `.superpowers` (L80), `/docs/superpowers/resume/` (L89), `/docs/superpowers/review-prompts/` (L90).
  - `.claude/skills/.codemie-sync.json` (L39) — the same filename that appears nested under `docs/superpowers/runs/20260701-1735-main/.claude/skills/` in this repository.
  - `**/CLAUDE.local.md` (L45) under the comment "Claude Code local memory"; `*.swp`, `*.swo`, `*~` (L19–21); `*.log` (L28); `.DS_Store` (L24).
  - **Not present in this revision**: any `*.local.*` shape rule, any `qa-tasks` path, and `.claude/settings.local.json`. The codemie repo already carries `.claude/settings.local.json` independently at `.gitignore:45`.
