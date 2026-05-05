#!/usr/bin/env bash
# dev-review-loop / claude / board: fresh session, init /bmad-agent-pm, send board prompt.
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

SESSION_ID=$(uuidgen)
./run_claude.sh --session-id "$SESSION_ID" -p "/bmad-agent-pm" --dangerously-skip-permissions >"/tmp/claude_binit_$SESSION_ID.txt" 2>&1
./run_claude.sh --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions >"/tmp/claude_board_$SESSION_ID.txt" 2>&1
