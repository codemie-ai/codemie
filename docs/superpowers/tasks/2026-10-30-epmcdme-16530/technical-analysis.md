# EPMCDME-16530 — Technical analysis (adopted from the existing plan)

> Not a fresh research run. Adopted from `plan.md` (its "Background (verified 2026-10-30)" and
> "File Structure" sections), because the investigation was done before this run.

## Codebase Findings

- Defect site: `CLIHandler._parse_cli_repositories_result` in
  `src/codemie/service/analytics/handlers/cli/handler.py`. A `(repository, branch, user)` bucket with
  no `cli_tool_usage_total` document is skipped, so proxy tokens stored with branch `""`
  (`proxy_router.py:472`, no `X-CodeMie-Branch`) vanish while the real-branch session row shows 0 tokens.
- Fix is read-time only: fold `""`-branch token-only buckets into the same repository/user session row.
  No ES query, mapping or ingest change; historical data is repaired too.
- Tests live in `tests/codemie/service/analytics/handlers/test_cli_handler.py` (`TestCLIRepositories`).
- Delivered in commit `6f8b552c6` (2 files: `handler.py`, `test_cli_handler.py`).

## Risk Indicators

- Noise filter from EPMCDME-12687 must stay: token-only buckets with no session row for the repo/user stay dropped.
- Token-only bucket on a real (non-empty) branch stays dropped, unchanged.
- Ambiguous case (2+ session rows for the same repo/user): kept as its own `""` row.
- Row shape and `_get_cli_repositories_columns()` unchanged; pagination slices flattened rows per repository bucket, unaffected.
- Out of scope, unverified: `CLIClassificationEngine._build_cli_repository_classifications` may lose cost the same way.
