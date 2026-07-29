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
# 2) If no changes applied -> run ruff check + license check + pytest
# 3) If tests pass -> run the shared local SonarQube check

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

# --- 1. Ruff formatting and fixes (fast pass, staged Python content only) ---
# The staged-only detection lives in _ruff_staged.sh so it is unit-testable
# (see tests/scripts/test_ruff_staged_hook.py). It exits 1 if ruff would
# change any staged .py, 0 otherwise. Working tree is not mutated - the user
# runs `ruff format` explicitly and re-stages when the helper flags files.
bash "$(dirname "$0")/_ruff_staged.sh"

# --- 2. Full verification (Ruff + license headers + Pytest) ---
echo "[pre-commit] No formatting changes detected. Running ruff checks, license checks, and tests..."

# 2.a Ruff check (non-mutating)
if ! poetry run ruff check; then
  echo "[pre-commit] Ruff check failed. Please fix linting issues above."
  exit 1
fi

# 2.b Apache 2.0 license header check
echo "[pre-commit] Checking Apache 2.0 license headers..."
if ! poetry run python scripts/license_headers/check_license_headers.py --check --quiet; then
  echo "[pre-commit] License header check failed."
  echo "[pre-commit] Tip: run 'make verify' to fix or validate license headers locally."
  exit 1
fi

# 2.c Pytest with compact summary, print it clearly
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

# 2.c Shared local SonarQube check
echo "[pre-commit] Tests passed. Running shared local SonarQube check..."
if ! make sonar-local; then
  echo "[pre-commit] SonarQube check failed."
  echo "[pre-commit] Tip: run 'make sonar-local' to reproduce locally."
  exit 1
fi

# Success path: always print concise summary before committing
# Note: pre-commit may hide stdout on success; print to stderr to ensure visibility
if [[ -n "$summary_line" ]]; then
  >&2 echo "[pre-commit] Tests passed. Summary: $summary_line"
  >&2 echo "[pre-commit] SonarQube check passed."
else
  >&2 echo "[pre-commit] Tests completed. See pytest output above."
  >&2 echo "[pre-commit] SonarQube check passed."
fi
# Proceed with commit
