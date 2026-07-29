#!/usr/bin/env bash
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Ruff format/fix check against staged Python content only.
#
# Reads the staged blob of each .py file into a temp file, pipes it through
# ruff format then ruff check --fix on stdin, and compares the original staged
# blob to the post-ruff output BYTE-FOR-BYTE with `cmp`. Temp files (not
# command substitution) are used deliberately so trailing newlines are
# preserved — command substitution strips them and would silently miss files
# whose only defect is a missing trailing newline. The working tree is never
# touched, so unstaged edits (including the git add -p scenario) cannot cause
# false positives.
#
# Exit 0 = no staged .py OR every staged .py is already formatted/clean.
# Exit 1 = ruff would change at least one staged .py, OR ruff itself failed
#          (syntax error in staged content, ruff binary missing, ruff crash).
#          User-facing message distinguishes the two cases.
#
# Invoked as a subprocess from scripts/git-hooks/pre_commit.sh. RUFF_CMD env
# overrides the ruff invocation prefix (default "poetry run ruff") for testing.

set -euo pipefail

RUFF_CMD="${RUFF_CMD:-poetry run ruff}"

staged_py_files=$(git diff --cached --name-only --diff-filter=ACMR -- '*.py')

if [[ -z "$staged_py_files" ]]; then
  echo "[pre-commit] No staged Python files - skipping Ruff format/fix check."
  exit 0
fi

echo "[pre-commit] Checking staged Python content against Ruff..."

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

changed_files=""
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  # Extract the staged blob to a temp file (preserves trailing newline).
  git show ":$f" > "$tmpdir/orig"

  # ruff format on stdin. Explicit exit-code check: any non-zero here means
  # a real ruff failure (syntax error in staged content, bad config, missing
  # binary) — surface it clearly instead of dying with the parent's generic
  # ERR trap.
  if ! $RUFF_CMD format --force-exclude --stdin-filename "$f" - \
       < "$tmpdir/orig" > "$tmpdir/formatted" 2> "$tmpdir/format.err"; then
    echo "[pre-commit] Ruff format failed on staged content of '$f' (syntax error?):"
    cat "$tmpdir/format.err" >&2
    exit 1
  fi

  # ruff check --fix on stdin. Ruff exit codes:
  #   0 = clean
  #   1 = fixes applied and/or unfixed lint remains (both acceptable here;
  #        the read-only `ruff check` step in pre_commit.sh surfaces
  #        genuinely unfixed lint violations)
  #   2+ = real error (bad flag, config error, internal crash, binary missing)
  # Only 2+ is fatal to this helper.
  rc=0
  $RUFF_CMD check --fix --force-exclude --stdin-filename "$f" - \
    < "$tmpdir/formatted" > "$tmpdir/fixed" 2> "$tmpdir/check.err" || rc=$?
  if [[ $rc -gt 1 ]]; then
    echo "[pre-commit] Ruff check failed on staged content of '$f' (exit $rc):"
    cat "$tmpdir/check.err" >&2
    exit 1
  fi

  # Byte-for-byte comparison — cmp respects trailing newlines that command
  # substitution would strip.
  if ! cmp -s "$tmpdir/orig" "$tmpdir/fixed"; then
    changed_files+="$f"$'\n'
  fi
done <<< "$staged_py_files"

if [[ -n "$changed_files" ]]; then
  echo "[pre-commit] Ruff would change the following staged files:"
  printf '%s' "$changed_files"
  echo "[pre-commit] Please run 'poetry run ruff format <files>' (or 'make ruff'), git add the fixes, and commit again."
  echo "[pre-commit] Skipping tests now to avoid running them twice."
  exit 1
fi

exit 0
