# Test Harness Results — EPMCDME-13887

## Main Run

**Command:** `uvx codemie-test-harness run sanity-api -n 1 --reruns 3`
**Date:** 2026-09-21
**Stack:** Live local Docker stack. Backend running this branch's code via the `src` volume mount.
QEMU-emulated environment (Apple Silicon host). `-n 1` (single worker) to eliminate the
cross-worker load that caused indexing timeouts in earlier runs.

| Result   | Count |
|----------|-------|
| Passed   | 195   |
| Skipped  | 4     |
| Failed   | 1     |
| Rerun    | 7     |
| Duration | 1810s |

**Single failing test:** `test_workflow_store_in_context_false`

This test is in the workflow/context-management area. It is not in `file_analysis`, the DOCX/PPTX
converter, or any code touched by this MR.

## Isolated Re-run — test_workflow_store_in_context_false

**Command:** `uvx codemie-test-harness run sanity-api -- -k test_workflow_store_in_context_false`
**Date:** 2026-09-22
**Stack:** Same local Docker stack (colima + existing containers, no rebuild).

| Session | Result | Duration |
|---------|--------|----------|
| 1 | `1 failed, 2 rerun` — failed 3/3 | 76.54s |
| 2 | `1 passed` | 39.94s |
| 3 | `1 passed` | 40.43s |

The test is non-deterministic: failed on every attempt in session 1, passed on every attempt in
sessions 2 and 3. The full assertion error from session 1 was not captured;
from the test docstring visible in the log, the test verifies that `{{secret_code}}` is NOT
resolved in the state-2 prompt when `store_in_context=False` — the second state's LLM is expected
to output "Variable not in context" when it sees the literal template text in its input.

This test is not in `file_analysis`, the DOCX/PPTX converter, or the routing code this MR
introduces.

## Run History

| Date | Workers | Passed | Failed | Skipped | Rerun | Duration | Failures |
|------|---------|--------|--------|---------|-------|----------|----------|
| 2026-09-18 | default | 194 | 2 | 4 | 12 | 802s | `test_create_file_datasource_from_zip_with_mixed_file_types`, `test_assistant_has_an_access_to_the_history` — indexing `TimeoutError` on QEMU stack under load |
| 2026-09-21 | `-n 4` | — | 3 | — | — | — | Same two indexing `TimeoutError`s + `test_context_merging_after_iteration` |
| 2026-09-21 | `-n 1` | 195 | 1 | 4 | 7 | 1810s | `test_workflow_store_in_context_false` — timeouts gone with single worker; passes in isolated re-runs on 22.09 (see above); not in code touched by this MR |

## Conclusion

None of the failures across all three runs are in `file_analysis`, the DOCX/PPTX converter, or
the routing code this MR introduces:

- The indexing `TimeoutError`s (18.09 and 21.09 `-n 4`) are QEMU-stack load artifacts that
  disappear under `-n 1`.
- `test_context_merging_after_iteration` (21.09 `-n 4`) is a workflow context test — no
  connection to file conversion.
- `test_workflow_store_in_context_false` (21.09 `-n 1`) is a workflow context test — no
  connection to file conversion.

The MR code changes are in the converter routing layer and do not affect any of the failing tests.
