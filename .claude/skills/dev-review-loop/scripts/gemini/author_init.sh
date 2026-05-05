#!/usr/bin/env bash
# dev-review-loop / gemini / author_init: init /bmad-agent-dev session, run first prompt.
# Gemini assigns its own UUID; capture it via --list-sessions (newest is last).
# Prints session id on the last line of stdout.
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

INIT_LOG="/tmp/gemini_init_$$.txt"
gemini --yolo -p "/bmad-agent-dev" >"$INIT_LOG" 2>&1
SESSION_ID=$(gemini --list-sessions 2>&1 | grep -oP '(?<=\[)[0-9a-f-]{36}(?=\])' | tail -1)
[[ -n "$SESSION_ID" ]] || { echo "gemini session id capture failed" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/gemini_init_$SESSION_ID.txt"

gemini --yolo --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" >"/tmp/gemini_author_$SESSION_ID.txt" 2>&1
echo "$SESSION_ID"
