#!/usr/bin/env bash
# cs-review-loop / gemini / author_fix: resume author session, send fix prompt.
set -euo pipefail

SESSION_ID="${1:?session id required}"
PROMPT_FILE="${2:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

gemini --yolo --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" >"/tmp/gemini_fix_$SESSION_ID.txt" 2>&1
