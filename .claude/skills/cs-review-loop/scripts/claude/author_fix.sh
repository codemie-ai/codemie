#!/usr/bin/env bash
# cs-review-loop / claude / author_fix: resume author session, send fix prompt.
set -euo pipefail

SESSION_ID="${1:?session id required}"
PROMPT_FILE="${2:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

./run_claude.sh --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions >"/tmp/claude_fix_$SESSION_ID.txt" 2>&1
