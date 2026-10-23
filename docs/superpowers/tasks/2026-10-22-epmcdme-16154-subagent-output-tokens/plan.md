# EPMCDME-16154 Plan: partial subagent output_tokens in CLI Analytics rollups

Commit per task using the repository's existing convention. Backend only; CLI PR #674 already emits `output_tokens_partial`.

## Acceptance criteria

- Session `output_tokens` (session_usage, cost_daily, session_usage_hourly via `_cli_requests`) equals the OTel api_request total for plain `claude` and Claude Desktop with subagents.
- Subagent output <= session output (the reader's subagent row is already OTel-only; no change).
- `claude -p` and `codemie-claude` (stop_reason set, so not partial) are unchanged.

## Assumptions (owner has not contradicted)

- Partial = the merged transcript request has no stop_reason (`usage_requests.stop_reason` NULL or empty). `output_tokens_partial` is NOT ingested today (`otlp.py:610-616` stores only `stop_reason`), so no schema migration and no `otlp.py` change.
- Matched pair, partial: `greatest(transcript, OTel)` output; not partial: unchanged. Unpaired partial: keep transcript value (lower bound).
- Tier-2 fingerprint: drop only the output-equality predicate, and only for partial transcript requests; timestamp, model, input, cache match stay.
- Negative-constraints pass: "claude -p / codemie-claude unchanged" is honored by gating every change on `partial`; "no schema migration" honored (no alembic task).

## Task 1 - Carry a `partial` flag on transcript requests and use it in matching and matched tokens

Files: `src/codemie/repository/cli_analytics/postgres/rollups.py`; `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`

Test-first: yes - SQL-text tests fail before the change: (a) the tier-2 candidate block (`c`) contains the output-equality predicate only inside an `a.partial OR ...` guard; (b) the 'matched' SELECT (first `UNION ALL` part of `_BUILD_REQUESTS`) takes output via `CASE WHEN a.partial THEN greatest(...)` while the 'transcript' part still uses plain `a.output_tokens`.

- [ ] In the `tr` CTE (`rollups.py:318-338`) add `(max(nullif(u.stop_reason, '')) IS NULL) AS partial` (merged record has no stop_reason; `t` inherits it via `tr.*`).
- [ ] Split `_TRANSCRIPT_COLUMNS` (`rollups.py:264-269`) so the output expression is a parameter: keep the 'transcript' branch (L434-439) on `a.output_tokens`; the 'matched' branch (L426-431) uses `CASE WHEN a.partial THEN greatest(coalesce(a.output_tokens, 0), coalesce(b.output_tokens, 0)) ELSE a.output_tokens END`.
- [ ] In tier 2 (`rollups.py:388`) change the output predicate to `(a.partial OR coalesce(b.output_tokens, 0) = coalesce(a.output_tokens, 0))`; update the comment at L377-378 to say output is compared only for final transcript requests.
- [ ] Update `test_the_fingerprint_compares_the_raw_model_and_the_token_counts` (~L1028-1037) for the guarded predicate and add the two tests above; run `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`.

## Task 2 - Update guides

Files: `.ai-run/guides/integration/cli-analytics-storage.md` (Request matching ~L126; matched-row token statements ~L141 and ~L263); `.ai-run/guides/integration/cli-analytics-events.md` (`stop_reason` / `output_tokens_partial` of `agent.usage.request` and `agent.subagent.usage.usage[]`)

Test-first: no - documentation only.

- [ ] State: a matched row takes tokens from the transcript, except output of a partial request (no stop_reason) which is `greatest(transcript, OTel)`; tier-2 pairing ignores output equality for partial requests; an unpaired partial request keeps its transcript output as a lower bound; document `output_tokens_partial` as a CLI-sent flag the backend does not store (partial is derived from `stop_reason`).

## Post-deploy (operational, not code)

Stored rollups keep old values until recomputed: run `python -m codemie.repository.cli_analytics.postgres.backfill --from <date> --to <date>` for affected days. Raw retention is 90 days; older days cannot be recomputed. After deploy, check session 42b807fc-style data: session output equals OTel api_request total and subagent <= session.
