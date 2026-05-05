---
name: cs-review-loop
description: >
  Orchestrates the CodeMie BMAD context-story (SM) → review loop by running CLI agents (Claude,
  Codex, or Gemini) as autonomous subagents, routing messages between them via structured
  Markdown report files, and autonomously deciding when the review is approved.

  Use when the user says any of: "run the cs loop", "start the cs review cycle",
  "orchestrate context story creation", "run bmad sm review", "start the cs orchestration",
  "automate the sm and review", "run the cs loop with gemini as reviewer",
  "use claude as sm and gemini as reviewer", "use gemini for sm",
  "use codex, gemini, claude as reviewers", or similar requests to kick off the BMAD
  context-story workflow.

  The user can specify which agent plays each role via phrases like "gemini as reviewer",
  "claude as sm", "codex as reviewer", "use gemini for both", or
  "codex, gemini, claude as reviewers" for multiple reviewers.

  All agents spawned by this skill must be token-efficient: maximum information density,
  minimum verbosity. Reports must use bullet points over prose, omit filler sentences,
  and contain only actionable content.
---

# Context-Story + Review Loop Orchestrator

You are the orchestrator. Run SM and reviewer agents as subprocesses using Bash tool calls.
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
| SM                  | claude  | claude, codex, gemini, opencode                  |
| Reviewers           | codex   | one or more of: claude, codex, gemini, opencode  |
| Clarification Board | claude  | claude, codex, gemini, opencode                  |

If the user specified roles (e.g. "gemini as reviewer", "claude as sm", "codex as board"),
use that. Multiple reviewers are supported. Each reviewer runs independently in its own
session. When the same agent type plays multiple roles (SM, reviewer, board), use separate
session IDs/UUIDs for each.

**Clarification Board agent:** A separate agent role used by the Clarification Gate to
resolve ambiguous review findings autonomously without human intervention. Invoked with
`/bmad-agent-pm` (or `$bmad-agent-pm` for codex). User trigger phrases include "claude as
board", "use codex for clarifications", "gemini as clarification board", etc. Defaults to
`claude` if unspecified.

**Opencode model selection:** When opencode is used, the user may specify `-m provider/model`.
Capture in `OPENCODE_MODEL` at the start and pass to every `opencode run` call.

Confirm roles to the user in one line before starting: `SM: X / Reviewers: A, B, C / Board: Y`

After identifying roles, **Read the agent reference file(s)** for the selected agents before
making any agent calls.

---

## Report Protocol

### Per-run setup

```bash
REPORT_DIR=$(.claude/skills/cs-review-loop/scripts/init_run.sh)
ORCH_RUN_ID=$(basename "$REPORT_DIR")
```

The script allocates a UUID, creates `_bmad-output/cs-review-reports/<uuid>/`, and prints the
directory path. Report `$REPORT_DIR` to the user so they can inspect reports.

### Report file naming

| Phase / call                   | File name                   |
|--------------------------------|-----------------------------|
| Phase 1 — SM creates the story | `sm-create.md`              |
| Phase 2 — initial review       | `review-<agent>-r1.md`      |
| Loop — clarification board N   | `clarification-r<N>.md`     |
| Loop — SM fix iteration N      | `sm-fix-r<N>.md`            |
| Loop — re-review round N+1     | `review-<agent>-r<N+1>.md`  |

`<agent>` = reviewer's agent name. If the same agent type plays multiple reviewer slots,
suffix the role index: `review-opencode-1-r1.md`. Iteration numbers start at 1.
Clarification rounds are numbered to match the fix iteration they precede (e.g.
`clarification-r1.md` precedes `sm-fix-r1.md`); skip the file entirely when the gate
finds zero ambiguous findings.

### Report directives — embed the appropriate one in every prompt

Two variants exist — use the one matching the agent's role. Substitute `$REPORT_FILE` to the
concrete path for that call.

**AUTHOR directive** (SM create, SM fix):

> **REPORT INSTRUCTIONS — MANDATORY.** You MUST save your complete final report to the file
> `<REPORT_FILE>` in Markdown format. The report must be self-contained: a reader who opens
> only this file, without seeing your stdout, must fully understand what you created and why.
> Include: (1) a one-paragraph summary of what was created or changed; (2) files created or
> modified and key decisions made; (3) any open questions or assumptions. Create parent
> directories if they do not exist. This file is the ONLY channel the orchestrator uses to
> read your output — if you do not save it, your work is lost.
>
> **TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet
> points over prose. Omit filler sentences, preamble, and narrative. Every line must carry
> information not inferrable from context.

**REVIEWER directive** (review, re-review):

> **REPORT INSTRUCTIONS — MANDATORY.** You MUST save your complete final report to the file
> `<REPORT_FILE>` in Markdown format. The report must be self-contained: a reader who opens
> only this file, without seeing your stdout, must fully understand what you reviewed and your
> conclusion. Include: (1) a one-paragraph summary of the review scope and outcome; (2) findings
> grouped by severity — High, Medium, Low, Patch/Nit, Deferred — or an explicit APPROVED if
> none; (3) files reviewed; (4) a final status line on its own line: `STATUS: APPROVED` or
> `STATUS: CHANGES-REQUESTED`.
>
> **STATUS rules (severity-tiered):**
> - Set `STATUS: CHANGES-REQUESTED` only when you find **High** or **Medium** findings.
> - **Low** findings: set `STATUS: CHANGES-REQUESTED` in rounds 1–3 only; in rounds 4+
    >   they are advisory — report them but set `STATUS: APPROVED` unless High/Medium also exist.
> - **Patch, Nit, Deferred**: never change the STATUS — report for awareness only.
> - If the only findings are Low (round 4+), Nit, Patch, or Deferred: set `STATUS: APPROVED`
    >   and label those findings clearly as "non-blocking / advisory."
>
> This file is the ONLY channel the orchestrator uses to read your output — if you do not save it, your work is lost.
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
> **TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet
> points over prose. Omit filler sentences, preamble, and narrative. Every line must carry
> information not inferrable from context.

### Reading reports

After every agent call, **Read `$REPORT_FILE`** with the Read tool. Raw stdout captures
(`/tmp/codex_*`, `/tmp/opencode_*`) exist only for session-ID setup — never for findings.

---

## Workflow

```
Phase 0 — Story Resolution:            Read sprint-status.yaml → set $STORY_FILE (next backlog story)
Phase 1 — Context Story (SM agent):    /bmad-agent-sm  →  CS
                                        → writes $REPORT_DIR/sm-create.md
Phase 2 — Review (each reviewer):      /bmad-agent-sm  →  review @$STORY_FILE
                                        → writes $REPORT_DIR/review-<agent>-r1.md
Loop until ALL reviewers approve (max 5, with convergence auto-approve):
  Clarification Gate (orchestrator): scan rejecting reports for ambiguity / contradiction
    Clarification Board (PM agent): if any ambiguous finding → board discusses & answers
                                     → writes $REPORT_DIR/clarification-rN.md
  SM agent:       fix story based on combined review findings + board answers (if any)
                   → writes $REPORT_DIR/sm-fix-rN.md
  Each reviewer:  re-review (fresh session, exact same prompt as initial review)
                   → writes $REPORT_DIR/review-<agent>-r(N+1).md
Phase 3 — Report (orchestrator):    confirm story remains ready-for-dev, report files
```

Default sprint status path: `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Phase 0: Story Resolution

**Before any agent call**, Read `_bmad-output/implementation-artifacts/sprint-status.yaml` and
identify the next story in `backlog` status (first backlog story in numeric order). Set:

```bash
STORY_FILE="_bmad-output/implementation-artifacts/<story-id>.story.md"
```

Pass `$STORY_FILE` to every reviewer call. The SM agent in Phase 1 handles its own discovery
via `[CS]` — no need to pass the story file to it.

### Phase 1: Context Story Creation

Set `REPORT_FILE="$REPORT_DIR/sm-create.md"` before calling. Use the SM agent's Turn 1 (init
with `/bmad-agent-sm`) then Turn 2 (send `[CS]` prompt + report directive). See the selected
agent's reference file for exact commands.

Always append this standing instruction to the Turn 2 prompt:
> **Repository labeling (mandatory):** Every task and every subtask in the story must carry
> an explicit `[repo-name]` label (e.g., `[codemie]`, `[codemie-enterprise]`,
> `[codemie-mcp-connect-service]`) identifying which repository owns that work. A developer
> picking up the story must never have to infer ownership from context.

### Phase 2: Review

Run every configured reviewer in sequence. Each gets a **fresh session** (init with
`/bmad-agent-sm`, then review prompt) and its own report file (`review-<agent>-r1.md`).

Review prompt core (adapt per agent reference file):
> `[CS] There is a new story file created. Please review it. DO NOT CREATE A NEW ONE!!!`
> Thoroughly review `@<$STORY_FILE>`. Work autonomously.
> Return ALL findings in the report — compile every finding yourself, even from subagents.

**Repository labeling check (mandatory — always apply):** Every task and every subtask
must carry an explicit `[repo-name]` label (e.g., `[codemie]`, `[codemie-enterprise]`,
`[codemie-mcp-connect-service]`) identifying which repository owns that work. A task or
subtask without a label is a **Medium** finding regardless of how many repos the story
touches — even single-repo stories must label every task so a developer picking up the
story never has to infer ownership.

**Round-aware scoping (apply in fix+re-review loop):** When dispatching a re-review in
round 4 or later, prepend this context to the review prompt:
> "This is re-review round N (of a max-5 loop). The story has already gone through multiple
> fix cycles. **Focus only on High and Medium issues.** Low/Nit/Patch findings may be noted
> briefly but must not change STATUS — set `STATUS: APPROVED` unless you find a genuine
> High or Medium issue."

### Clarification Gate (mandatory before every fix round)

After reading all reviewer reports that contain `STATUS: CHANGES-REQUESTED`, and before
dispatching any fix prompt to the SM agent, you MUST run this gate:

1. **Analyze every finding** across all rejecting reports. Flag any finding that has:
   - **Ambiguity** — unclear what concrete story/spec change is expected.
   - **Uncertainty** — reviewer hedges ("might", "consider", "possibly") without a firm ask.
   - **Contradiction** — two reviewers (or two findings in the same report) request
     incompatible changes.
   - **Missing rationale** — reviewer says something is wrong but not why or what the fix is.
   - **Spec conflict** — finding appears to contradict an existing spec, requirement, or
     architectural invariant.
   - **Scope doubt** — unclear whether the finding belongs to this story or is out of scope.

2. **If zero issues found:** proceed to the SM fix step — no board call needed; do not
   create a `clarification-rN.md` file.

3. **If any issues found:** convene the **Clarification Board** — do NOT pause for human
   input, do NOT ask Taras. The board mechanics mirror SM/reviewer mechanics:
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
   > - `<latest sm-create.md or sm-fix-r<N-1>.md if relevant>`
   >
   > Let the team discuss and clarify the following:
   > 1. `<question 1, citing the exact finding from <reviewer> report>`
   > 2. `<question 2, ...>`
   > N. `<question N, ...>`

4. **After board answers received:** incorporate the board's answers into the fix prompt so
   the SM agent gets unambiguous, complete instructions for every finding. Pass the
   `clarification-r${ITERATION}.md` file path explicitly so the SM can read the full
   discussion if needed.

### Fix+Re-review Loop

Maintain `ITERATION` counter starting at 1. Each round:

1. **Collect the file paths** of every rejecting reviewer's latest report.
2. **Run the Clarification Gate** (see above). If the gate triggered the Clarification Board,
   you now also have `$REPORT_DIR/clarification-r${ITERATION}.md`.
3. **SM fix**: resume SM session, list the reviewer report file paths in the prompt and
   instruct the SM to read them directly. Do NOT copy review content into the prompt.
   If the Clarification Board produced answers, list its report path too and include the
   board's resolved decisions as explicit instructions in the fix prompt so the SM agent
   knows exactly how to address ambiguous findings.
   Always include this standing instruction in every SM fix prompt:
   > **Repository labeling (mandatory):** Every task and subtask must carry an explicit
   > `[repo-name]` label (e.g., `[codemie]`, `[codemie-enterprise]`,
   > `[codemie-mcp-connect-service]`). If any task or subtask is missing a label, add it.
   `REPORT_FILE="$REPORT_DIR/sm-fix-r${ITERATION}.md"`
4. **Each reviewer re-reviews**: **fresh session every round** (never resume previous reviewer
   session). Same review prompt as Phase 2.
   `REPORT_FILE="$REPORT_DIR/review-<agent>-r$((ITERATION + 1)).md"`

---

## Approval Decision

After all reviewers write their reports, Read each and inspect `STATUS:` + findings.

### Severity tiers

| Severity | Rounds 1–3 | Rounds 4–5 |
|----------|-----------|-----------|
| High     | blocker   | blocker   |
| Medium   | blocker   | blocker   |
| Low      | blocker   | advisory only |
| Patch / Nit / Deferred | advisory only | advisory only |

- **Approves this round**: `STATUS: APPROVED` (reviewer applied the rules above correctly).
- **Rejects this round**: `STATUS: CHANGES-REQUESTED` — only High/Medium remain as
  mandatory fixes; in rounds 4+, Low findings are advisory (pass them to the SM as
  "recommended but non-blocking").
- **Missing STATUS line**: treat as `CHANGES-REQUESTED`.

### Convergence: auto-approve after round 5

If the loop reaches round 5 and the only remaining findings are Low/Nit/Patch/Deferred,
**auto-approve** — do not run another round. Report the advisory findings to the user
so they can decide whether to manually address them later.

**Loop** if ANY reviewer rejects on a blocker finding. **Stop** when ALL approve (or
convergence kicks in). Report decision to the user before proceeding to Phase 3.

---

## Phase 3: Post-Approval Report

Once all reviewers approve, **do not update story status.** The story remains `ready-for-dev`
until a dev picks it up. Only update the `last_updated` timestamp:

```bash
STORY_ID=$(basename "$STORY_FILE" .story.md)
SPRINT_STATUS="_bmad-output/implementation-artifacts/sprint-status.yaml"
TODAY=$(date +%Y-%m-%d)
sed -i "s/^last_updated: .*/last_updated: $TODAY  # $STORY_ID cs-review approved/" "$SPRINT_STATUS"
grep "$STORY_ID" "$SPRINT_STATUS"
```

Report `$REPORT_DIR` and all report files to the user. Confirm the story status is still
`ready-for-dev`.

---

## Notes

- **Report files are authoritative** — never use stdout captures for findings.
- **One run ID, one report directory** — reuse `$REPORT_DIR` for the entire run.
- **Agent scripts MUST run in the background.** `author_init.sh`, `author_fix.sh`, `review.sh`, and `board.sh` invoke CLI agents that commonly run 5–30 minutes or longer — well past the Bash tool's 10-minute hard cap (`timeout` max is 600000 ms). Call them with `run_in_background: true`. If you call one synchronously, the Bash tool kills the agent mid-response, leaving an orphaned session and a half-written report. `init_run.sh` and plain bookkeeping bash (sed, date, basename, grep) complete in milliseconds — use default synchronous mode for those.
- **Wait patiently; never kill.** After launching a background agent script, poll with TaskOutput using `block: true, timeout: 600000`. If the task is still running when TaskOutput returns (10 min elapsed), call TaskOutput again. Repeat until it reports completion, no matter how many iterations that takes. **Never call TaskStop** on an agent script — terminating mid-run corrupts session state and loses report content. Never append `&` to any command either; always use the Bash tool's `run_in_background: true` parameter.
- **Capture `author_init.sh`'s session id from the output file.** Background mode means you cannot use `SESSION_ID=$(./author_init.sh …)`. Instead, after the task completes: Read the Bash output file path returned in the tool result, and take the last non-empty line — that is the session id. Then Read `$REPORT_FILE` for the agent's report.
- Run from **project root** so `@` file references resolve.
- When the same agent type plays both SM and reviewer, use **separate session IDs/UUIDs**.
- When multiple reviewers are configured, each gets its own session and report file. Use
  indexed names if the same agent type appears more than once.
- Report all session IDs/UUIDs and `$REPORT_DIR` to the user at the end.
