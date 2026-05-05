#!/usr/bin/env bash
# cs-review-loop / claude / review: fresh session, init /bmad-agent-sm, send review prompt.
# Used for both initial review and re-review (fresh session every round).
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

SESSION_ID=$(uuidgen)
./run_claude.sh --session-id "$SESSION_ID" -p "/bmad-agent-sm" --dangerously-skip-permissions >"/tmp/claude_rinit_$SESSION_ID.txt" 2>&1
./run_claude.sh --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions >"/tmp/claude_review_$SESSION_ID.txt" 2>&1
