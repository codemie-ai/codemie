#!/usr/bin/env bash
# cs-review-loop / codex / author_fix: resume author session, send fix prompt.
set -euo pipefail

SESSION_ID="${1:?session id required}"
PROMPT_FILE="${2:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

codex exec resume "$SESSION_ID" --dangerously-bypass-approvals-and-sandbox \
  -o "/tmp/codex_fix_$SESSION_ID.txt" "$(cat "$PROMPT_FILE")" >/dev/null 2>&1
