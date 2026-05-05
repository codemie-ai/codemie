#!/usr/bin/env bash
# dev-review-loop / opencode / author_init: init /bmad-agent-dev session, run first prompt.
# OPENCODE_MODEL env var selects provider/model; unset to use opencode's configured default.
# Always pass `< /dev/null` — opencode hangs on stdin without a TTY.
# Prints session id on the last line of stdout.
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

MODEL_ARGS=()
[[ -n "${OPENCODE_MODEL:-}" ]] && MODEL_ARGS=(-m "$OPENCODE_MODEL")

INIT_LOG="/tmp/opencode_init_$$.txt"
opencode run "${MODEL_ARGS[@]}" "/bmad-agent-dev" </dev/null >"$INIT_LOG" 2>&1
SESSION_ID=$(opencode session list --format json 2>/dev/null | jq -r '.[0].id')
[[ -n "$SESSION_ID" && "$SESSION_ID" != "null" ]] || { echo "opencode session id capture failed" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/opencode_init_$SESSION_ID.txt"

opencode run "${MODEL_ARGS[@]}" -s "$SESSION_ID" "$(cat "$PROMPT_FILE")" </dev/null >"/tmp/opencode_author_$SESSION_ID.txt" 2>&1
echo "$SESSION_ID"
