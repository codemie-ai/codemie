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

# Pre-commit hook for Codemie (fast path — commit stays snappy):
# 1) Ruff fast formatting/fixes on staged Python; if changes needed -> exit 1
# 2) If no changes -> ruff check (full) + Apache 2.0 license headers
# Heavy steps (full pytest + sonar-local) run on 'git push' via pre-push hook.

# Friendly failure message for any unexpected error
trap 'echo "[pre-commit] Error: hook failed. See output above for details."; echo "[pre-commit] Tip: you can run '\''make ruff'\'' locally to reproduce."' ERR

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

# --- 2. Fast checks only (ruff lint + license headers) ---
# Heavy steps (full pytest + sonar-local) are in the pre-push hook (EPMCDME-13747).
echo "[pre-commit] No formatting changes detected. Running ruff checks and license checks..."

RUFF_CMD="${RUFF_CMD:-poetry run ruff}"
LICENSE_CMD="${LICENSE_CMD:-poetry run python scripts/license_headers/check_license_headers.py}"

# 2.a Ruff check (non-mutating)
if ! $RUFF_CMD check; then
  echo "[pre-commit] Ruff check failed. Please fix linting issues above."
  exit 1
fi

# 2.b Apache 2.0 license header check
echo "[pre-commit] Checking Apache 2.0 license headers..."
if ! $LICENSE_CMD --check --quiet; then
  echo "[pre-commit] License header check failed."
  echo "[pre-commit] Tip: run 'make license-check' to fix or validate license headers locally."
  exit 1
fi

# Success: heavy checks (full pytest + sonar-local) run on 'git push' via pre-push hook.
>&2 echo "[pre-commit] Fast checks passed. Full tests + SonarQube run on push (pre-push hook)."
