---
name: dev-review-loop
description: >
  Orchestrates the CodeMie BMAD dev→review→fix loop by running CLI agents (Claude, Codex,
  or Gemini) as autonomous subagents, routing messages between them via structured Markdown
  report files, and autonomously deciding when the review is approved.

  Use when the user says any of: "run the dev loop", "start the dev+review cycle",
  "orchestrate story development", "run bmad dev review", "start the orchestration",
  "automate the dev and review", "run the dev loop with gemini as reviewer",
  "use claude as dev and gemini as reviewer", "use gemini for dev",
  "use codex, gemini, claude as reviewers", or similar requests to kick off the BMAD
  development workflow for the next sprint story.

  The user can specify which agent plays each role via phrases like "gemini as reviewer",
  "claude as dev", "codex as reviewer", "use gemini for both", or
  "codex, gemini, claude as reviewers" for multiple reviewers.

  All agents spawned by this skill must be token-efficient: maximum information density,
  minimum verbosity. Reports must use bullet points over prose, omit filler sentences,
  and contain only actionable content.
---

# Dev+Review Loop Orchestrator

You are the orchestrator. Run dev and reviewer agents as subprocesses using Bash tool calls.
**Every prompt you send must instruct the target agent to save its final report to a unique
Markdown file whose path you provide.** You then Read that report file directly — you never
parse the agent's raw stdout for findings or decisions. Decide autonomously when the review
is approved based on the report contents.

**Agent command references** (paths relative to project root) — read only the files for agents you need. CLI mechanics live in `scripts/<tool>/*.sh`; each reference file documents the phase → script map and the prompt templates to write to the script's prompt-file argument.
- `references/claude.md` — Claude phase→script map + prompts
- `references/codex.md` — Codex phase→script map + prompts
- `references/gemini.md` — Gemini phase→script map + prompts
- `references/opencode.md` — Opencode phase→script map + prompts

## Role Configuration

| Role                | Default | Options                                          |
|---------------------|---------|--------------------------------------------------|
| Dev                 | claude  | claude, codex, gemini, opencode                  |
| Reviewers           | codex   | one or more of: claude, codex, gemini, opencode  |
| Clarification Board | claude  | claude, codex, gemini, opencode                  |

If the user specified roles (e.g. "gemini as reviewer", "claude as dev", "codex as board"),
use that. Multiple reviewers are supported. Each reviewer runs independently in its own
session. When the same agent type plays multiple roles (dev, reviewer, board), use separate
session IDs/UUIDs for each.

**Clarification Board agent:** A separate agent role used by the Clarification Gate to
resolve ambiguous review findings autonomously without human intervention. Invoked with
`/bmad-agent-pm` (or `$bmad-agent-pm` for codex). User trigger phrases include "claude as
board", "use codex for clarifications", "gemini as clarification board", etc. Defaults to
`claude` if unspecified.

**Opencode model selection:** When opencode is used, the user may specify `-m provider/model`.
Capture in `OPENCODE_MODEL` at the start and pass to every `opencode run` call.

Confirm roles to the user in one line before starting: `Dev: X / Reviewers: A, B, C / Board: Y`

After identifying roles, **Read the agent reference file(s)** for the selected agents before
making any agent calls.

---

## Report Protocol

### Per-run setup

```bash
REPORT_DIR=$(.claude/skills/dev-review-loop/scripts/init_run.sh)
ORCH_RUN_ID=$(basename "$REPORT_DIR")
```

The script allocates a UUID, creates `_bmad-output/dev-review-reports/<uuid>/`, and prints the
directory path. Report `$REPORT_DIR` to the user so they can inspect reports.

### Report file naming

| Phase / call                   | File name                   |
|--------------------------------|-----------------------------|
| Phase 1 — dev implements story | `dev-impl.md`               |
| Phase 2 — initial review       | `review-<agent>-r1.md`      |
| Loop — clarification board N   | `clarification-r<N>.md`     |
| Loop — dev fix iteration N     | `dev-fix-r<N>.md`           |
| Loop — re-review round N+1     | `review-<agent>-r<N+1>.md`  |

`<agent>` = reviewer's agent name. If the same agent type plays multiple reviewer slots,
suffix the role index: `review-opencode-1-r1.md`. Iteration numbers start at 1.
Clarification rounds are numbered to match the fix iteration they precede (e.g.
`clarification-r1.md` precedes `dev-fix-r1.md`); skip the file entirely when the gate
finds zero ambiguous findings.

### Report directives — embed the appropriate one in every prompt

Two variants exist — use the one matching the agent's role. Substitute `$REPORT_FILE` to the
concrete path for that call.

**AUTHOR directive** (dev implement, dev fix, status update):

> **NO GIT COMMITS — MANDATORY.** Do NOT stage, commit, or push any changes. Leave all
> modifications in the working tree only. The developer will commit everything independently
> after an external review. Do not run `git add`, `git commit`, or `git push` under any
> circumstances.
>
> **TEST SUITE — MANDATORY.** Before saving your report, you MUST run the complete test suite:
> ```
> source .venv/bin/activate && poetry run pytest tests/ -v
> ```
> If any test fails, you MUST fix all failures before saving the report. Do NOT save the report
> with a failing test suite — keep fixing until all tests pass or you have exhausted all
> reasonable attempts (in which case include the full failure output and explain why you could
> not fix it). Include a `## Test Results` section in your report with: pass/fail counts,
> the full list of any failures, and the fix applied (or reason it could not be fixed).
>
> **REPORT INSTRUCTIONS — MANDATORY.** You MUST save your complete final report to the file
> `<REPORT_FILE>` in Markdown format. The report must be self-contained: a reader who opens
> only this file, without seeing your stdout, must fully understand what you created and why.
> Include: (1) a one-paragraph summary of what was implemented or changed; (2) files created
> or modified, tests added or changed, and key decisions made; (3) test results (pass/fail
> counts and any failures); (4) any open questions or assumptions. Create parent directories
> if they do not exist. This file is the ONLY channel the orchestrator uses to read your
> output — if you do not save it, your work is lost.
>
> **TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet
> points over prose. Omit filler sentences, preamble, and narrative. Every line must carry
> information not inferrable from context.

**REVIEWER directive** (review, re-review):

> **REPORT INSTRUCTIONS — MANDATORY.** You MUST save your complete final report to the file
> `<REPORT_FILE>` in Markdown format. The report must be self-contained: a reader who opens
> only this file, without seeing your stdout, must fully understand what you reviewed and your
> conclusion. Include: (1) a one-paragraph summary of the review scope and outcome; (2) ALL
> findings — every severity and every category (High, Medium, Low, Patch, Bad Spec, Deferred,
> Intent Gap, Nit, or any other label) — or an explicit APPROVED if zero findings; (3) files
> reviewed; (4) a final status line on its own line: `STATUS: APPROVED` or
> `STATUS: CHANGES-REQUESTED`. This file is the ONLY channel the orchestrator uses to read your output — if you do not save it, your
> work is lost.
>
> **REVIEW SCOPE — MANDATORY CONSTRAINT.** Your review scope is strictly bounded by the story
> file. You MUST validate the implementation only against what the story file explicitly
> requires. Do NOT raise findings based on your own judgment of what should be done, best
> practices, or requirements that are not stated in the story file. Do NOT flag anything that
> is outside the story's acceptance criteria, tasks, or explicit constraints — even if you
> personally believe it should be done differently. If something is not in the story file, it
> is out of scope for this review and must not appear in your report.
>
> **TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet
> points over prose. Omit filler sentences, preamble, and narrative. Every line must carry
> information not inferrable from context.

**BOARD directive** (clarification board):

> **REPORT INSTRUCTIONS — MANDATORY.** You MUST save the team's complete answers to the file
> `<REPORT_FILE>` in Markdown format. The report must be self-contained: a reader who opens
> only this file, without seeing your stdout, must fully understand the team's answer to every
> question. Include: (1) for each numbered question from the orchestrator, a clear, actionable
> answer that explicitly resolves the ambiguity, contradiction, or scope doubt — quote the
> question number and the exact finding it refers to; (2) any decisions the team made and the
> rationale behind them; (3) any new constraints, out-of-scope items, or follow-ups the team
> identified. This file is the ONLY channel the orchestrator uses to read the team's output — if you do not save it, the clarification
> is lost.
>
> **NO CODE CHANGES — MANDATORY.** The board MUST NOT modify, stage, commit, or push any code
> or spec files. Its only output is the clarification report file. All implementation changes
> happen later in the dev fix step.
>
> **TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet
> points over prose. Omit filler sentences, preamble, and narrative. Every line must carry
> information not inferrable from context.

### Reading reports

After every agent call, **Read `$REPORT_FILE`** with the Read tool. Raw stdout captures
(`/tmp/codex_*`, `/tmp/opencode_*`) exist only for session-ID setup — never for findings.

---

## Workflow

```
Phase 0 — Story Resolution:         Read sprint-status.yaml → set $STORY_FILE
Phase 1 — Dev    (dev agent):       /bmad-agent-dev  →  [DS] @$STORY_FILE
                                     → writes $REPORT_DIR/dev-impl.md
Phase 2 — Review (each reviewer):   /bmad-agent-dev  →  [CR] @$STORY_FILE
                                     → writes $REPORT_DIR/review-<agent>-r1.md
Loop until ALL reviewers approve (max 10):
  Clarification Gate (orchestrator): scan rejecting reports for ambiguity / contradiction
    Clarification Board (PM agent): if any ambiguous finding → board discusses & answers
                                     → writes $REPORT_DIR/clarification-rN.md
  Dev agent:      fix implementation based on combined findings + board answers (if any)
                   → writes $REPORT_DIR/dev-fix-rN.md
  Each reviewer:  re-review (fresh session, exact same prompt as initial review)
                   → writes $REPORT_DIR/review-<agent>-r(N+1).md
Phase 3 — Status (orchestrator):    sed update sprint-status.yaml directly
```

Default sprint status path: `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Phase 0: Story Resolution

**Before any agent call**, Read `_bmad-output/implementation-artifacts/sprint-status.yaml` and
identify the story that is `ready-for-dev` (or `in-progress` if resuming). Set:

```bash
STORY_FILE="_bmad-output/implementation-artifacts/<story-id>.story.md"
```

Pass `$STORY_FILE` to every subsequent agent call. No agent should need to read sprint-status.

### Phase 1: Development

Set `REPORT_FILE="$REPORT_DIR/dev-impl.md"` before calling. Use the dev agent's Turn 1 (init
with `/bmad-agent-dev`) then Turn 2 (send `[DS]` prompt + report directive). See the selected
agent's reference file for exact commands.

### Phase 2: Code Review

Run every configured reviewer in sequence. Each gets a **fresh session** (init with
`/bmad-agent-dev`, then review prompt) and its own report file (`review-<agent>-r1.md`).

Review prompt core (adapt per agent reference file):
> `[CR] @<$STORY_FILE>` Work autonomously.
> Return ALL findings in the report — compile every finding yourself, even from subagents.
> **CRITICAL: Review ONLY against the story file.** Do not raise findings based on your own
> understanding of the requirements or anything not explicitly stated in the story file.
> Do not flag anything outside the story's acceptance criteria, tasks, or explicit constraints,
> even if you personally think it should be done differently.

### Clarification Gate (mandatory before every fix round)

After reading all reviewer reports that contain `STATUS: CHANGES-REQUESTED`, and before
dispatching any fix prompt to the dev agent, you MUST run this gate:

1. **Analyze every finding** across all rejecting reports. Flag any finding that has:
   - **Ambiguity** — unclear what concrete code/spec change is expected.
   - **Uncertainty** — reviewer hedges ("might", "consider", "possibly") without a firm ask.
   - **Contradiction** — two reviewers (or two findings in the same report) request
     incompatible changes.
   - **Missing rationale** — reviewer says something is wrong but not why or what the fix is.
   - **Spec conflict** — finding appears to contradict an existing spec, requirement, or
     architectural invariant.
   - **Scope doubt** — unclear whether the finding belongs to this story or is out of scope.

2. **If zero issues found:** proceed to the dev fix step — no board call needed; do not
   create a `clarification-rN.md` file.

3. **If any issues found:** convene the **Clarification Board** — do NOT pause for human
   input, do NOT ask Taras. The board mechanics mirror dev/reviewer mechanics:
   - **Fresh session every round** — never resume a previous board session.
   - Initialize the configured Board agent with `/bmad-agent-pm` (or `$bmad-agent-pm` for
     codex) on Turn 1; see the agent reference file for the exact command.
   - On Turn 2, send the Party Mode prompt below + the BOARD directive substituted with
     `REPORT_FILE="$REPORT_DIR/clarification-r${ITERATION}.md"`.
   - Draft a numbered list of clarifying questions BEFORE calling the board. For each question:
     - Quote the exact finding (reviewer, report file, finding text).
     - State what is unclear or conflicting.
     - Propose options if you can, but do not assume an answer.
   - Wait for the board call to complete, then `Read $REPORT_FILE` for the answers.

   **Party Mode prompt template** (substitute the bracketed lists):
   > First, Run the Party Mode, no exceptions.
   > Then let the team read the following files:
   > - `@$STORY_FILE`
   > - `<every rejecting reviewer report path, one per line>`
   > - `<latest dev-impl.md or dev-fix-r<N-1>.md if relevant>`
   >
   > Let the team discuss and clarify the following:
   > 1. `<question 1, citing the exact finding from <reviewer> report>`
   > 2. `<question 2, ...>`
   > N. `<question N, ...>`

4. **After board answers received:** incorporate the board's answers into the fix prompt so
   the dev agent gets unambiguous, complete instructions for every finding. Pass the
   `clarification-r${ITERATION}.md` file path explicitly so the dev can read the full
   discussion if needed.

### Fix+Re-review Loop

Maintain `ITERATION` counter starting at 1. Each round:

1. **Collect the file paths** of every rejecting reviewer's latest report.
2. **Run the Clarification Gate** (see above). If the gate triggered the Clarification Board,
   you now also have `$REPORT_DIR/clarification-r${ITERATION}.md`.
3. **Dev fix**: resume dev session, list the reviewer report file paths in the prompt and
   instruct the dev to read them directly. Do NOT copy review content into the prompt.
   Fix prompt must include: `Fix ALL findings — every single one, regardless of how they are
   labelled. Nothing can be skipped or deferred.`
   If the Clarification Board produced answers, list its report path too and include the
   board's resolved decisions as explicit instructions in the fix prompt so the dev agent
   knows exactly how to address ambiguous findings.
   `REPORT_FILE="$REPORT_DIR/dev-fix-r${ITERATION}.md"`
4. **Each reviewer re-reviews**: **fresh session every round** (never resume previous reviewer
   session). Same review prompt as Phase 2.
   `REPORT_FILE="$REPORT_DIR/review-<agent>-r$((ITERATION + 1)).md"`

---

## Approval Decision

After all reviewers write their reports, Read each and inspect `STATUS:` + findings:

- **Approves**: `STATUS: APPROVED` and findings section is empty/APPROVED.
- **Rejects**: `STATUS: CHANGES-REQUESTED` or any finding of any severity/category. Every
  finding is a blocker; none can be skipped or deferred.
- **Missing STATUS line**: treat as `CHANGES-REQUESTED`.

**Loop** if ANY reviewer rejects. **Stop** only when ALL approve. Report decision to the user
before proceeding to Phase 3.

---

## Phase 3: Post-Approval Status Update

Once all reviewers approve, update **both** the story file and sprint status directly — no agent call needed:

```bash
STORY_ID=$(basename "$STORY_FILE" .story.md)
STORY_ID=$(basename "$STORY_ID" .md)   # handle both .story.md and .md extensions
SPRINT_STATUS="_bmad-output/implementation-artifacts/sprint-status.yaml"
TODAY=$(date +%Y-%m-%d)

# 1. Update story file Status line (matches any current status value)
sed -i "s/^Status: .*/Status: done/" "$STORY_FILE"

# 2. Update sprint-status.yaml (matches any current status value)
sed -i "s/^\(  ${STORY_ID}:\) .*$/\1 done/" "$SPRINT_STATUS"
sed -i "s/^last_updated: .*/last_updated: $TODAY  # $STORY_ID marked done/" "$SPRINT_STATUS"

# 3. Verify both updates
echo "Story file status:"
grep "^Status:" "$STORY_FILE"
echo "Sprint status:"
grep "$STORY_ID" "$SPRINT_STATUS"
```

Report the updated status lines, `$REPORT_DIR`, and all report files to the user.

---

## Notes

- **No git commits** — Neither the dev agent nor any reviewer agent should ever run
  `git add`, `git commit`, or `git push`. All implementation changes must remain in the
  working tree. The developer commits independently after an external review.
- **Report files are authoritative** — never use stdout captures for findings.
- **One run ID, one report directory** — reuse `$REPORT_DIR` for the entire run.
- **Agent scripts MUST run in the background.** `author_init.sh`, `author_fix.sh`, `review.sh`, and `board.sh` invoke CLI agents that commonly run 5–30 minutes or longer — well past the Bash tool's 10-minute hard cap (`timeout` max is 600000 ms). Call them with `run_in_background: true`. If you call one synchronously, the Bash tool kills the agent mid-response, leaving an orphaned session and a half-written report. `init_run.sh` and plain bookkeeping bash (sed, date, basename, grep) complete in milliseconds — use default synchronous mode for those.
- **Wait patiently; never kill.** After launching a background agent script, poll with TaskOutput using `block: true, timeout: 600000`. If the task is still running when TaskOutput returns (10 min elapsed), call TaskOutput again. Repeat until it reports completion, no matter how many iterations that takes. **Never call TaskStop** on an agent script — terminating mid-run corrupts session state and loses report content. Never append `&` to any command either; always use the Bash tool's `run_in_background: true` parameter.
- **Capture `author_init.sh`'s session id from the output file.** Background mode means you cannot use `SESSION_ID=$(./author_init.sh …)`. Instead, after the task completes: Read the Bash output file path returned in the tool result, and take the last non-empty line — that is the session id. Then Read `$REPORT_FILE` for the agent's report.
- Run from **project root** so `@` file references resolve.
- When the same agent type plays both dev and reviewer, use **separate session IDs/UUIDs**.
- When multiple reviewers are configured, each gets its own session and report file. Use
  indexed names if the same agent type appears more than once.
- Report all session IDs/UUIDs and `$REPORT_DIR` to the user at the end.
