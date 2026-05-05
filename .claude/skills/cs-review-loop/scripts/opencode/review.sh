#!/usr/bin/env bash
# cs-review-loop / opencode / review: fresh session, init /bmad-agent-sm, send review prompt.
# Used for both initial review and re-review (fresh session every round).
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

MODEL_ARGS=()
[[ -n "${OPENCODE_MODEL:-}" ]] && MODEL_ARGS=(-m "$OPENCODE_MODEL")

INIT_LOG="/tmp/opencode_rinit_$$.txt"
opencode run "${MODEL_ARGS[@]}" "/bmad-agent-sm" </dev/null >"$INIT_LOG" 2>&1
SESSION_ID=$(opencode session list --format json 2>/dev/null | jq -r '.[0].id')
[[ -n "$SESSION_ID" && "$SESSION_ID" != "null" ]] || { echo "opencode session id capture failed" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/opencode_rinit_$SESSION_ID.txt"

opencode run "${MODEL_ARGS[@]}" -s "$SESSION_ID" "$(cat "$PROMPT_FILE")" </dev/null >"/tmp/opencode_review_$SESSION_ID.txt" 2>&1
