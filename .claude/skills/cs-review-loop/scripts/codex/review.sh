#!/usr/bin/env bash
# cs-review-loop / codex / review: fresh session, init $bmad-agent-sm, send review prompt.
# Used for both initial review and re-review (fresh session every round).
set -euo pipefail

PROMPT_FILE="${1:?prompt file required}"
[[ -r "$PROMPT_FILE" ]] || { echo "prompt file not readable: $PROMPT_FILE" >&2; exit 1; }

INIT_LOG="/tmp/codex_rinit_$$.txt"
codex exec --dangerously-bypass-approvals-and-sandbox '$bmad-agent-sm' >"$INIT_LOG" 2>&1
SESSION_ID=$(grep '^session id:' "$INIT_LOG" | awk '{print $NF}')
[[ -n "$SESSION_ID" ]] || { echo "codex session id capture failed; see $INIT_LOG" >&2; exit 1; }
mv "$INIT_LOG" "/tmp/codex_rinit_$SESSION_ID.txt"

codex exec resume "$SESSION_ID" --dangerously-bypass-approvals-and-sandbox \
  -o "/tmp/codex_review_$SESSION_ID.txt" "$(cat "$PROMPT_FILE")" >/dev/null 2>&1
