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

# pre-push hook: run the heavy quality gates before code reaches the remote.
#   1) full pytest (excluding tests/enterprise, which need codemie_enterprise)
#      with coverage -> coverage.xml
#   2) make sonar-local, reusing that coverage.xml (SONAR_SKIP_TESTS=1)
#
# Opt-in: set CODEMIE_PREPUSH_ENABLED=true to enable. Disabled by default so
# the hook never blocks pushes unless the developer explicitly opts in.
# PYTEST_CMD / SONAR_CMD override the tool invocations for unit testing.

set -euo pipefail

# Backward compatibility: CODEMIE_PRECOMMIT_ENABLED=true used to enable the
# heavy suite when it lived in the pre-commit hook. Treat it as opt-in for
# pre-push too, but warn the developer to rename the variable.
_legacy="${CODEMIE_PRECOMMIT_ENABLED:-}"
shopt -s nocasematch
if [[ "$_legacy" == "true" || "$_legacy" == "1" || "$_legacy" == "on" ]]; then
  echo "╔══════════════════════════════════════════════════════════════╗" >&2
  echo "║  [pre-push] DEPRECATION WARNING                              ║" >&2
  echo "║                                                              ║" >&2
  echo "║  CODEMIE_PRECOMMIT_ENABLED is no longer used.                ║" >&2
  echo "║  The heavy pytest + sonar suite now runs at git push, not    ║" >&2
  echo "║  git commit. Rename your variable:                           ║" >&2
  echo "║                                                              ║" >&2
  echo "║    Old: export CODEMIE_PRECOMMIT_ENABLED=true                ║" >&2
  echo "║    New: export CODEMIE_PREPUSH_ENABLED=true                  ║" >&2
  echo "║                                                              ║" >&2
  echo "║  Proceeding as if CODEMIE_PREPUSH_ENABLED=true for now.      ║" >&2
  echo "╚══════════════════════════════════════════════════════════════╝" >&2
  CODEMIE_PREPUSH_ENABLED="true"
fi
shopt -u nocasematch

enabled="${CODEMIE_PREPUSH_ENABLED:-false}"
shopt -s nocasematch
if [[ "$enabled" != "true" && "$enabled" != "1" && "$enabled" != "on" ]]; then
  echo "[pre-push] CODEMIE_PREPUSH_ENABLED=$enabled -> skipping heavy checks."
  echo "[pre-push] Tip: set CODEMIE_PREPUSH_ENABLED=true to run pytest + sonar before push."
  exit 0
fi
shopt -u nocasematch

PYTEST_CMD="${PYTEST_CMD:-poetry run pytest}"
SONAR_CMD="${SONAR_CMD:-make sonar-local}"

echo "[pre-push] Running full test suite (excluding tests/enterprise)..."
set +e
$PYTEST_CMD tests/ --ignore=tests/enterprise/ -W ignore::DeprecationWarning \
  --cov --cov-report=xml:coverage.xml
pytest_rc=$?
set -e
if [[ $pytest_rc -ne 0 ]]; then
  echo "[pre-push] Tests failed (exit $pytest_rc). Tip: run 'make test' to reproduce."
  exit $pytest_rc
fi

echo "[pre-push] Tests passed. Running shared local SonarQube check..."
if ! SONAR_SKIP_TESTS=1 $SONAR_CMD; then
  echo "[pre-push] SonarQube check failed. Tip: run 'make sonar-local' to reproduce."
  exit 1
fi

echo "[pre-push] All heavy checks passed. Push proceeding."
