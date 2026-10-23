# EPMCDME-15506: Session start is never later than the session's first recorded activity

## Problem

CLI Analytics reports a session `start_time` later than events of the same session. Session
`b4685a4a-681c-409f-833e-d2255b4b142a` on Preview: an `api_request`/`tool_call` at 15:56:17 UTC,
`start_time` 15:56:29 UTC, `duration_ms` 628000.

Cause:

- `session_dims.started_at` / `last_event_at` are the min/max `ts` of the 13 dimension hook types only
  (`rollups.py` DIMENSIONS upsert). OTel ingest never marks DIMENSIONS (`dirty.py`).
- The client stamps a hook's time after its own setup work (codemie-code `OtlpAgentAdapter.ts:126`:
  allowlist read, proxy daemon start, SSO gate, git/identity lookups), so hook times lag real activity.
- Every reported start and duration is `started_at` and `last_event_at − started_at`
  (`get_session_durations`, `get_session_cost_facts` → Sessions list, `get_session_detail_meta`),
  while the Session Detail timeline comes from OTel `log_events` / `spans`, stamped by Claude Code at
  the event time.

## Goal

For every session the API reports, `start_time` ≤ the earliest event of the session, the session end ≥
the latest one, and `duration_ms` is the length of that range. Window membership does not change.

## Non-goals

- Changing `session_dims.started_at` / `last_event_at` or the rollup / ingest pipeline. They stay the
  hook times and keep deciding the dashboard windows (storage guide, Window Semantics).
- Fixing the client's late hook timestamps (codemie-code). Follow-up outside this repository.
- Response model changes. `start_time: str`, `duration_ms: int` keep their shape.
- OTel-only sessions (no dimension hook, `started_at` NULL) staying out of every window.
- The `coalesce(..., 0)` of a missing detail duration.

## Design

### The session's activity range (one definition)

A reader helper produces, per session, the reported range. `cand_start` / `cand_end` are the
column-only bounds:

```
cand_start  = least(d.started_at, d.activity_started_at)
cand_end    = greatest(d.last_event_at, d.ended_at)
start       = least(cand_start, earliest log_events.ts / spans.ts in [cand_start − 1 day, cand_start))
end         = greatest(cand_end, latest log_events.ts / spans.ts in (cand_end, cand_end + 1 day])
duration_ms = _ms(end) − _ms(start)
```

- `least` / `greatest` skip NULLs, so a missing column never hides another source.
- The four probes are lateral `min` / `max` lookups on the `(session_id, ts)` indexes. The ts bound gives
  run-time partition pruning (weekly partitions over 90 days of raw retention), so a probe touches one
  or two partitions and usually finds nothing. An event more than a day before the hook / transcript
  start, or more than a day after the end, is not looked for.
- `activity_started_at` / `ended_at` already cover `usage_requests`, `metric_points` and all hook types
  for sessions without a summary (the raw range), and the transcript for sessions with one.

The helper is a CTE `act(session_id, session_start, session_end, duration_ms)` over `sel`, used only by
the queries below; `sel` itself is unchanged, so the window and every other windowed query keep their
cost and meaning.

### Consumers

| Path | Change |
|---|---|
| `get_session_durations` → Users Avg Session Duration, Overview `duration_ms` | `duration_ms` from `act`. The priced-sessions (D1) aggregation rule is unchanged |
| `get_session_cost_facts` → Sessions list | Returns `started_at` = `act.session_start`, `last_event_at` = `act.session_end`, `duration_ms` = `act.duration_ms`. The handler reports `duration_ms` from the row instead of its own timedelta, so list, detail and averages share one expression. Sorting by start / duration and `data_as_of` follow |
| `get_session_start_times` → activity heatmap | Buckets by `act.session_start` |
| `get_session_detail_meta` → Session Detail | `started_at` and `duration_ms` from the same range, computed for the one session (not windowed) with the same one-day bound, so it always agrees with the list; without a `session_dims` row the bound is open (`coalesce` to ±infinity). Dispatch bars are offset from the corrected start; an agent launched before the old late start is now placed. A session with OTel rows and no `session_dims` row gets its earliest event as start instead of 1970-01-01 |

### Error handling

No new failure modes: the probes are read-only lookups inside existing queries. A session with no
source at all keeps today's result (detail: epoch start, `duration_ms` 0).

## Testing

- **Live PostgreSQL** (same harness as `test_reader_live_dispatches.py`, skipped when the local analytics
  Postgres is unreachable): one session with dimension hooks at T and T+10 min, an `api_request` log
  event at T−12 s and a tool span at T+11 min. Asserts, through the real reader and handler:
  - Sessions list and Session Detail `start_time` = T−12 s (≤ the earliest event), `duration_ms` = 11 min 12 s;
  - `get_session_durations` returns the same duration;
  - a window starting at T−6 s still contains the session and a window ending at T−1 s does not
    (membership by `started_at`).
- **SQL shape** (`test_reader.py`): the four queries read the activity range; the window predicate is
  still only in `sel`; the probes are ts-bounded.
- **Handler** (`test_cli_analytics_handler.py`): the Sessions list reports the row's `duration_ms`; Users
  Avg averages corrected durations of priced sessions only; detail dispatch offsets are measured from the
  corrected start.

## Documentation

`.ai-run/guides/integration/cli-analytics-storage.md`: Window Semantics and Notes for a reader state the
two kinds of times: the stored hook times decide the window, the reported start / end / duration are the
session's activity range defined above, with the one-day probe bound.

## Acceptance criteria mapping

1. start ≤ earliest event: `start` includes the earliest `log_events` / `spans` row and the raw range.
2. One identity: every source is keyed by the same `session_id`; one helper feeds all paths.
3. Duration recalculated: `end − start` everywhere, documented in the storage guide.
4. Automated test with an event earlier than the derived start: the live PG test.
5. Users Avg uses corrected durations under the unchanged priced-sessions rule.
