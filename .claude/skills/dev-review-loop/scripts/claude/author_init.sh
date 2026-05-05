#!/usr/bin/env bash
# dev-review-loop / claude / author_init: init /bmad-agent-dev session, run first prompt.
# Prints session id on the last line of stdout.
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

SESSION_ID=$(uuidgen)
./run_claude.sh --session-id "$SESSION_ID" -p "/bmad-agent-dev" --dangerously-skip-permissions >"/tmp/claude_init_$SESSION_ID.txt" 2>&1
./run_claude.sh --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions >"/tmp/claude_author_$SESSION_ID.txt" 2>&1
echo "$SESSION_ID"
