# Claude Commands — Dev Review Loop

Claude is stateful via session IDs. **Never use `-c`** — it resumes the orchestrator's session.

All CLI mechanics live in `scripts/claude/*.sh`. Write each prompt to a temp file, then call
the appropriate script. Read `$REPORT_FILE` after every call — never parse script stdout for
findings.

## Phase → Script map

| Phase                                | Script                           | Args                         | Captures                       |
|--------------------------------------|----------------------------------|------------------------------|--------------------------------|
| Phase 1 — Dev implements story   | `scripts/claude/author_init.sh`  | `<prompt-file>`              | `DEV_SESSION_ID` (stdout last line) |
| Phase 2 — initial review             | `scripts/claude/review.sh`       | `<prompt-file>`              | —                              |
| Loop — Dev fix iteration N            | `scripts/claude/author_fix.sh`   | `<DEV_SESSION_ID> <prompt-file>` | —                           |
| Loop — re-review round N+1           | `scripts/claude/review.sh`       | `<prompt-file>`              | —                              |
| Clarification board (if triggered)   | `scripts/claude/board.sh`        | `<prompt-file>`              | —                              |

Each script runs Turn 1 (agent init) and Turn 2 (prompt) in one call. `author_fix.sh` is
Turn 2 only (resumes the existing Dev session). `review.sh` and `board.sh` always use fresh
sessions.

## Orchestrator usage

Every agent-script call follows this sequence:

1. **Write prompt to a temp file** (Bash, synchronous — fast).
2. **Launch the script with `run_in_background: true`**. The Bash tool returns a `task_id` and an output-file path. Do NOT run these scripts synchronously — agents routinely take 15–60 minutes, well past the Bash tool's 10-minute hard cap, and a synchronous call will kill the agent mid-response.
3. **Wait** with `TaskOutput(task_id, block=true, timeout=600000)`. If it returns while the task is still running, call `TaskOutput` again. Repeat until done. **Never `TaskStop`.**
4. **For `author_init.sh` only:** after completion, Read the task's output file and take the last non-empty line — that is the session id. Save it for later `author_fix.sh` calls.
5. **Read `$REPORT_FILE`** to get the agent's actual report.

Sketch:

```text
# Phase 1 — SM/Dev author_init (captures DEV_SESSION_ID)
write /tmp/p_dev_impl.txt                                           (Bash, sync)
Bash { command: ".claude/skills/dev-review-loop/scripts/claude/author_init.sh /tmp/p_dev_impl.txt",
        run_in_background: true } → task_id, output_file
loop: TaskOutput(task_id, block=true, timeout=600000) until done
Read output_file → last non-empty line is DEV_SESSION_ID
Read $REPORT_FILE

# Fix iteration
write /tmp/p_fix.txt
Bash { command: ".claude/skills/dev-review-loop/scripts/claude/author_fix.sh \"$DEV_SESSION_ID\" /tmp/p_fix.txt",
        run_in_background: true } → task_id
loop: TaskOutput until done
Read $REPORT_FILE

# Review / re-review / board — same pattern, no session id to capture
Bash { command: ".claude/skills/dev-review-loop/scripts/claude/review.sh /tmp/p_review.txt", run_in_background: true }
loop: TaskOutput until done
Read $REPORT_FILE
```

`init_run.sh` is the one exception — it completes in milliseconds and runs synchronously.

## Prompt templates

Write the final prompt (with `$REPORT_FILE`, `$STORY_FILE`, `$REPORT_DIR`, and the
`{{AUTHOR_DIRECTIVE}}` / `{{REVIEWER_DIRECTIVE}}` / `{{BOARD_DIRECTIVE}}` placeholders from
SKILL.md substituted) to a temp file before calling the script.

### Phase 1 — Dev implements story (`author_init.sh`)

```
[DS] @$STORY_FILE

**TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet points over prose. Omit filler sentences, preamble, and narrative. Every line must carry information not inferrable from context.

{{AUTHOR_DIRECTIVE substituted with $REPORT_FILE}}
```

### Phase 2 & Re-review (`review.sh`)

```
[CR] @$STORY_FILE
Work autonomously without any requests for additional confirmation. Read and analyze all the files related to the story being reviewed.
Return ALL your findings in your report file — even if you use subagents for analysis, compile and include every finding yourself. Do not omit findings because a subagent reported them. All findings must be reported without exceptions, even minor, non-blocking, tech debts etc. Only if you find no issues, explicitly state APPROVED.

**TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet points over prose. Omit filler sentences, preamble, and narrative. Every line must carry information not inferrable from context.

{{REVIEWER_DIRECTIVE substituted with $REPORT_FILE}}
```

For rounds 4+, prepend the round-aware scoping paragraph from SKILL.md § Phase 2.

### Fix iteration (`author_fix.sh`)

```
Code review feedback from the following reviewer report files. Read each file to understand the findings:
- $REPORT_DIR/review-<agent>-r<N>.md
(list all rejecting reviewer report paths here)

Fix ALL findings — every single one, regardless of how they are labelled (patch, bad-spec, deferred, nit, or any other category). Nothing can be skipped or deferred. The story cannot be marked done until zero findings remain.

**TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet points over prose. Omit filler sentences, preamble, and narrative. Every line must carry information not inferrable from context.

{{AUTHOR_DIRECTIVE substituted with $REPORT_FILE}}
```

If the Clarification Board produced answers, also list `$REPORT_DIR/clarification-r${ITERATION}.md`
in the report paths list and embed the board's resolved decisions as explicit fix instructions.

### Clarification board (`board.sh`)

```
First, Run the Party Mode, no exceptions.
Then let the team read the following files:
- @$STORY_FILE
- @$REPORT_DIR/review-<agent>-r<N>.md
  (list every rejecting reviewer report path here, one per line)
- @$REPORT_DIR/dev-impl.md (or dev-fix-r<N-1>.md if a prior fix exists)

Let the team discuss and clarify the following:
1. (question 1, citing the exact finding from <reviewer> report)
2. (question 2, ...)
N. (question N, ...)

**TOKEN-EFFICIENT — MANDATORY.** Maximum information density, minimum verbosity. Use bullet points over prose. Omit filler sentences, preamble, and narrative. Every line must carry information not inferrable from context.

{{BOARD_DIRECTIVE substituted with $REPORT_FILE}}
```
