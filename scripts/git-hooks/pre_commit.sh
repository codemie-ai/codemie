#!/usr/bin/env bash
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

set -euo pipefail

# Pre-commit hook for Codemie:
# 1) Ruff fast formatting/fixes; if any changes applied -> show files and exit 1
# 2) If no changes applied -> run ruff check + pytest and print a concise summary

# Friendly failure message for any unexpected error
trap 'echo "[pre-commit] Error: hook failed. See output above for details."; echo "[pre-commit] Tip: you can run '\''make verify'\'' locally to reproduce."' ERR

# --- Hook toggle via env var ---
# Set CODEMIE_PRECOMMIT_ENABLED=false (or 0/off) to skip this hook
enabled="${CODEMIE_PRECOMMIT_ENABLED:-true}"
shopt -s nocasematch
if [[ "$enabled" == "false" || "$enabled" == "0" || "$enabled" == "off" ]]; then
  echo "[pre-commit] CODEMIE_PRECOMMIT_ENABLED=$enabled -> skipping hook."
  exit 0
fi
shopt -u nocasematch

# Ensure Poetry is available (clear hint if missing)
if ! command -v poetry >/dev/null 2>&1; then
  echo "[pre-commit] Poetry is not installed or not on PATH."
  echo "[pre-commit] Please install Poetry (https://python-poetry.org/) and run: poetry install && poetry run pre-commit install"
  exit 1
fi

# --- 1. Ruff formatting and fixes (fast pass) ---
echo "[pre-commit] Running Ruff format..."
poetry run ruff format

echo "[pre-commit] Running Ruff lint with --fix..."
poetry run ruff check --fix || true

# Detect modified tracked files caused by formatting/fixes
changed_files=$(git ls-files -m)
if [[ -n "$changed_files" ]]; then
  echo "[pre-commit] Ruff applied changes to the following files:"
  echo "$changed_files" | tr ' ' '\n'
  echo "[pre-commit] Please stage the changes (git add ...) and commit again."
  echo "[pre-commit] Skipping tests now to avoid running them twice."
  exit 1
fi

# --- 2. Full verification (Ruff + Pytest, print concise summary) ---
echo "[pre-commit] No formatting changes detected. Running ruff checks and tests..."

# 2.a Ruff check (non-mutating)
if ! poetry run ruff check; then
  echo "[pre-commit] Ruff check failed. Please fix linting issues above."
  exit 1
fi

# 2.b Pytest with compact summary, print it clearly
pytest_log=$(mktemp)
# Ensure temporary pytest log is cleaned up on script exit
trap 'rm -f "$pytest_log"' EXIT
set +e
poetry run pytest -q -r a tests/ 2>&1 | tee "$pytest_log"
pytest_rc=${PIPESTATUS[0]}
set -e

# Extract concise summary like: "123 passed, 2 skipped in 45.67s"
summary_line=$(grep -E "(^[0-9]+ (passed|failed|skipped|xfailed|xpassed|error|warnings)|^no tests ran)" "$pytest_log" | tail -n 1 || true)
if [[ -z "$summary_line" ]]; then
  summary_line=$(grep -E "=+ .* in .*s =+" "$pytest_log" | tail -n 1 | sed 's/==* \(.*\) ==*/\1/' || true)
fi

if [[ $pytest_rc -ne 0 ]]; then
  echo "[pre-commit] Tests failed. $( [[ -n "$summary_line" ]] && echo "Summary: $summary_line" )"
  echo "[pre-commit] Tip: run 'make test' to reproduce locally."
  exit $pytest_rc
fi

# Success path: always print concise summary before committing
# Note: pre-commit may hide stdout on success; print to stderr to ensure visibility
if [[ -n "$summary_line" ]]; then
  >&2 echo "[pre-commit] Tests passed. Summary: $summary_line"
else
  >&2 echo "[pre-commit] Tests completed. See pytest output above."
fi
# Proceed with commit
