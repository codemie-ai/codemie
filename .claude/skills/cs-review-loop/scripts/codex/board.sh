#!/usr/bin/env bash
# cs-review-loop / codex / board: fresh session, init $bmad-agent-pm, send board prompt.
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

INIT_LOG="/tmp/codex_binit_$$.txt"
codex exec --dangerously-bypass-approvals-and-sandbox '$bmad-agent-pm' >"$INIT_LOG" 2>&1
SESSION_ID=$(grep '^session id:' "$INIT_LOG" | awk '{print $NF}')
[[ -n "$SESSION_ID" ]] || { echo "codex session id capture failed; see $INIT_LOG" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/codex_binit_$SESSION_ID.txt"

codex exec resume "$SESSION_ID" --dangerously-bypass-approvals-and-sandbox \
  -o "/tmp/codex_board_$SESSION_ID.txt" "$(cat "$PROMPT_FILE")" >/dev/null 2>&1
