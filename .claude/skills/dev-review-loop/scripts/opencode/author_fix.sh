#!/usr/bin/env bash
# dev-review-loop / opencode / author_fix: resume author session, send fix prompt.
set -euo pipefail

SESSION_ID="${1:?session id required}"
PROMPT_FILE="${2:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

MODEL_ARGS=()
[[ -n "${OPENCODE_MODEL:-}" ]] && MODEL_ARGS=(-m "$OPENCODE_MODEL")

opencode run "${MODEL_ARGS[@]}" -s "$SESSION_ID" "$(cat "$PROMPT_FILE")" </dev/null >"/tmp/opencode_fix_$SESSION_ID.txt" 2>&1
