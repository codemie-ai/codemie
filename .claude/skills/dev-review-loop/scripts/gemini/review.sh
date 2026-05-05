#!/usr/bin/env bash
# dev-review-loop / gemini / review: fresh session, init /bmad-agent-dev, send review prompt.
# Used for both initial review and re-review (fresh session every round).
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

INIT_LOG="/tmp/gemini_rinit_$$.txt"
gemini --yolo -p "/bmad-agent-dev" >"$INIT_LOG" 2>&1
SESSION_ID=$(gemini --list-sessions 2>&1 | grep -oP '(?<=\[)[0-9a-f-]{36}(?=\])' | tail -1)
[[ -n "$SESSION_ID" ]] || { echo "gemini session id capture failed" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/gemini_rinit_$SESSION_ID.txt"

gemini --yolo --resume "$SESSION_ID" -p "$(cat "$PROMPT_FILE")" >"/tmp/gemini_review_$SESSION_ID.txt" 2>&1
