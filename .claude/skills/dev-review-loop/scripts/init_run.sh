#!/usr/bin/env bash
# dev-review-loop / init_run: allocate a run id, create the report directory.
# Prints the report directory path on stdout. Derive $ORCH_RUN_ID via `basename`.
set -euo pipefail

ORCH_RUN_ID=$(uuidgen)
REPORT_DIR="_bmad-output/dev-review-reports/$ORCH_RUN_ID"
mkdir -p "$REPORT_DIR"
echo "$REPORT_DIR"
