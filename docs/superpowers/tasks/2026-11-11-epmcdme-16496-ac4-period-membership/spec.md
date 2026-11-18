# EPMCDME-16496 (AC4): Tighten CLI Analytics period-membership admission (rewrite 2)

## Problem

AC4 requires a session to be listed and counted for a period only when it has *genuine* activity
(a real prompt, an API call, or a tool call) in that period. `_SESSION_ADMITTED_IN_WINDOW`
(`reader.py:259`), read by the single CTE `sel` every dashboard endpoint shares, currently
over-admits on four paths:

1. **Zero-value lines metric point.** `_raw_activity_in_window`'s `metric_points` clause
   (`reader.py:217`) filters only by metric name, not value, so a zero-delta
   `claude_code.lines_of_code.count` point (final export at shutdown) counts as activity.
2. **Zero-value `lines_daily` row.** Independently of (1), `_activity_exists` (`reader.py:183-194`,
   used by both `_has_any_activity` and `_active_in_window`'s day-row checks) admits on row
   *existence* in `lines_daily`, and "a `lines_daily` row means the lines are known, zero included"
   (`rollups.py:1856`). A session whose only record anywhere in scope is a zero-delta lines point
   still has a `lines_daily` row and is admitted by every arm except the pure raw-probe one.
   `turns_daily`, `tool_facts_daily`, `cost_daily` and `session_usage` do not share this: each is
   built only from genuine prompt/tool/cost/usage rows (`rollups.py`'s rollup-family builders), never
   from an exit-time zero row — confirmed by reading their build queries; no change needed for them.
3. **Housekeeping/synthetic rows of the same kind as real ones.** `log_events`/`spans` admit on
   `event_kind`/`span_kind <> OTHER` alone (`reader.py:215-216`). A `/exit`-emitted `user_prompt` log
   row carries `command_name = 'exit'` (`BUILTIN_COMMAND_NAMES`, `vocabulary.py:218`, already
   confirms `"exit"` is a recognized slash command) and is indistinguishable, by kind alone, from a
   genuine typed prompt.
4. **The `resumed` proxy over-admits for any multi-day session.** `_has_any_activity`
   (`reader.py:248-256`) is a lifetime, no-day-bound check gating the unconditional
   `_STARTED_IN_WINDOW` arm: "a session that truly started in the window ... all its activity
   postdates `started_at`." That holds for a session whose activity sits on its start day. It does
   **not** hold for a `resumed` session — `session_activity.py:22-23` defines `resumed` as "a row
   exists on a UTC day **other than** the session's start day," true of *any* multi-day session, not
   only a dormant one someone reopens. If that session's only record on the day it started was
   housekeeping, and its real activity sits on a different day entirely, the lifetime check still
   admits it for the queried window. This, not any movement of `started_at`, is the EPMCDME-16993
   gap: the one `started_at` writer found in this codebase (`rollups.py:2069-2092`, `ON CONFLICT ...
   started_at = LEAST(sd.started_at, EXCLUDED.started_at)`) can only lower a stored value or set it
   once — it never raises an existing one, so this spec does not rely on `started_at` moving forward.

## Fix

**Metric points (raw probe).** Add `r.value <> 0` to the `metric_points` clause of
`_RAW_ACTIVITY_SOURCES` (`reader.py:217`) — narrows an existing per-row EXISTS, no new probe.

**`lines_daily` existence check.** Give `_activity_exists` (`reader.py:183-194`) a
`lines_daily`-specific extra condition, `lines_added <> 0 OR lines_removed <> 0` — the same test
already named `_LINES_NONZERO` at `reader.py:369` for a different purpose; reuse it rather than
duplicate it. This closes the gap for both `_has_any_activity` and `_active_in_window`'s day-row
checks. The other four `_ACTIVITY_TABLES` keep plain existence (see Problem #2).

**Genuine vs. synthetic prompts, without narrowing the kind list.** `rollups.py`'s own turn rule
(`_REAL_TURN`, `1761-1776`) already tells a genuine `user_prompt` from a command-triggered one by
`command_name`, not by kind, and that check is not gated to any one harness. Apply the same signal
to the raw-activity `log_events` clause: keep `event_kind <> OTHER` and add
`AND (r.event_kind <> {USER_PROMPT} OR coalesce(btrim(r.command_name), '') = '')`. A genuine prompt
(including one Claude Code injects without firing its own hook — `command_name` is blank either
way) still independently proves membership; only a slash-command-triggered row (`/exit`, `/model`,
`/quit`) stops doing so on its own. `SKILL_ACTIVATED`/`TOOL_RESULT`/`TOOL_DECISION` log rows and a
real API call's own `API_REQUEST`/`API_ERROR` row are untouched by this filter and keep admitting
unconditionally, exactly as today.

For `spans`, `command_name` does not exist as a column, so the same trick does not apply directly.
Every `INTERACTION` span pairs 1:1, by `span_id`, with the very `log_events` `user_prompt` row the
turn rule reads (`rollups.py`'s `_SPAN_PROMPT` join; also the basis of the "counter fallback" note at
`rollups.py:1708-1713`). Drop `INTERACTION` from the unconditionally-admitting span kinds (keep
`TOOL`, `TOOL_EXECUTION`, `LLM_REQUEST`): the paired `log_events` row, filtered as above, already
decides membership for that same logical event, so dropping `INTERACTION` opens no gap and adds no
join. This generalizes past Claude Code without per-tool code (`vocabulary.py`'s `SPAN_KIND` maps
Cursor's `cursor.interaction` to the same `INTERACTION` kind, paired with its own `user_prompt` log
row the same way).

**The `resumed` proxy (EPMCDME-16993).** Gate the unconditional, lifetime `_has_any_activity` arm on
`NOT resumed`, and give the `resumed` + started-in-window cohort its own arm using the stricter
`_active_in_window` day+raw check — a sixth arm in `_admitted_arms` (`reader.py:270-301`), not an
edit of the existing one in place:
- Arm "started, not resumed" (today's arm 1, `reader.py:294`, now with `AND NOT a.resumed` added):
  unchanged population minus the resumed cohort. This is the one necessary touch to the index-only
  arm — a column read, not a new EXISTS probe; `resumed` is outside `session_dims_active_shared_cov`'s
  current `INCLUDE (session_id)` list (`a1c1a0000011...py:85-86`), so this may cost a heap read per
  row where today there is none, but it adds no correlated subquery.
- New arm "started, resumed" (`has_activity AND resumed`): `_active_in_window(a, fenced=True)` — the
  fenced form arm 4 already uses, so this stays a per-row index probe rather than a window-wide hash
  (AR-4's point).
- Arm "started, underived" (today's arm 2, `reader.py:295`): today's `d.has_activity IS NULL AND
  d.resumed` guard is a no-op — `has_activity`/`last_activity_day`/`resumed` are written together by
  one `UPDATE` (`session_activity.py:79-85`), so `resumed IS NULL` for every row this arm can ever
  see. Replace the stored-column read with a live equivalent of `resumed`'s own definition ("a row
  exists on a UTC day other than the session's start day," mirroring `DERIVE_SQL`,
  `session_activity.py:72-73`) computed per candidate, fenced, then apply the same split: not-live-
  resumed keeps today's `_has_any_activity`; live-resumed uses `_active_in_window`. This, not a
  blanket switch to `_active_in_window` for the whole underived cohort, avoids a second regression:
  for a custom window older than `CLI_ANALYTICS_RAW_RETENTION_DAYS` (90 days) but within
  `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (365), `_active_in_window`'s partial-day branch needs raw
  rows that may already be purged, where `_has_any_activity` does not — the storage guide already
  names this as an accepted divergence for the non-resumed case and this fix must not widen it.
- Arms "outside, underived / outside, resumed / outside, one day" (today's arms 3-5): unchanged
  structure; they inherit the narrowed `_active_in_window`/`_has_any_activity` automatically.
- The single-predicate form `_SESSION_ADMITTED_IN_WINDOW` (`reader.py:259`) needs only `AND NOT
  d.resumed` added to its first (`_STARTED_IN_WINDOW`) branch — its second branch,
  `_active_in_window('d')`, already runs unconditionally and already handles a resumed,
  started-in-window session correctly on its own, independent of where it started.

**Performance.** Moving the resumed-started cohort out of the index-only arm and into `sel_rest`
means `_sel_ctes`'s split assumption ("`sel_rest`, few rows," `reader.py:304-309`) now has to hold
for *every multi-day session*, not a handful of literal reopens — the dev benchmark's count of 10
`resumed` sessions (`cli-analytics-storage.md`'s "Scan cost" section) was never measured against
"started in window and resumed" specifically, only against today's arm 4 (started outside the
window). This must be measured before merge with `scripts/cli_analytics_bench/run_sessions_bench.py`
against a representative `resumed`-population size, not assumed safe. If the measured regression is
unacceptable, the documented fallback is to add `resumed` to `session_dims_active_shared_cov`'s
`INCLUDE` list (a small migration) so the new arm's routing stays index-only too — revisiting the "no
migration" non-goal below, decided then from the numbers, not now.

## Acceptance Criteria

- A session whose only records in a window are lifecycle hooks (`session.start`/`session.env`/
  `session.end`, `git.snapshot`) is excluded from sessions, overview, users, repositories, cost, and
  activity for that window.
- A session whose real activity sits entirely on a day other than the one queried (a multi-day /
  `resumed` session) and whose only record in the queried window is housekeeping is excluded from
  that window across all six endpoints, whether its `started_at` lies inside or outside the window.
- A session whose only record in a window is a zero-delta lines-of-code point (raw metric point *or*
  its `lines_daily` rollup row) is excluded from that window across all six endpoints.
- A session whose only `log_events`/`spans` record in a window is a slash-command-triggered
  (non-blank `command_name`) `user_prompt` row or its paired `INTERACTION` span, with no real
  prompt/API request/tool call, is excluded from that window across all six endpoints.
- Hour-based presets (`last_hour`, `last_6_hours`, `last_24_hours`) that cross UTC midnight require
  an in-window activity record for the partial UTC day, not merely a daily rollup row for it.
- For the same filter: `overview.total_sessions` = `sessions` total = `sum(users.session_count)` =
  `sum(repositories.session_count)` = `cost.total_sessions` = `sum(activity.by_weekday)`.
- A genuinely active session (real, non-housekeeping, non-zero activity recorded in the window) is
  still admitted, whether or not it is resumed, whether or not it started in the window.
- A genuinely active, non-resumed session that started in the window is admitted when it has real work
  inside the window. Work stamped after the window's end does not count (review of the AC4 MR: started
  10:00, first prompt 11:00, window 10:00-10:30 -> excluded). For a window that covers its days whole
  (and any window longer than a day) the day rows decide as before.

## Non-goals

- Clipping fact *values* (turns, cost, tool calls, lines shown per session) to the window — these
  remain all-time sums once a session is admitted (`cli-analytics-storage.md:75`). Only membership
  changes.
- Any DB migration or new stored column, **unless** the pre-merge benchmark of the new resumed-arm
  shows the index-only started page/count regresses unacceptably, in which case extending
  `session_dims_active_shared_cov`'s `INCLUDE` list is the named fallback, decided from that
  measurement.
- Per-tool special-casing. The log/span fix (`command_name`, kind dropping) and the `resumed` fix are
  both expressed in vocabulary every harness already shares.
- Guaranteeing period-membership correctness for a CLI tool whose hook pipeline does not cover the
  kinds this fix narrows as completely as Claude Code's does (e.g. an unconfirmed gap for Cursor).
  Deferred to a tracked follow-up, not fixed here (**user decision, unchanged from the prior round**)
  — a ticket and owner for that follow-up must be filed; none exists yet.
- Changing `resumed`'s own stored definition (existence-based, no value/kind filter) — only which
  arm applies which admission check changes, not what `resumed` means.

## Decisions

- Narrow `metric_points` with `value <> 0`, over replicating `rollups.py`'s delta/zero-vs-absent
  recompute at read time — unchanged from the prior round; sound.
- Narrow `_activity_exists`'s `lines_daily` existence check with the same `_LINES_NONZERO` test,
  reusing the existing constant, over leaving the day-row/lifetime arms blind to the raw check's own
  zero-value fix — closes a gap the prior round's `metric_points`-only fix left open.
- Leave `turns_daily`/`tool_facts_daily`/`cost_daily`/`session_usage` existence checks unfiltered —
  each is built only from genuine rows; confirmed by reading their rollup builders, not assumed.
- Filter `log_events`' raw-activity clause by `command_name` on `user_prompt` rows specifically,
  rather than narrowing the kind list to only `API_REQUEST`/`API_ERROR` — the prior round's
  kind-narrowing silently dropped a genuine, hookless-but-real prompt case the storage guide's own
  turn rule already treats as real; this revises that implementation while keeping the user's
  original tradeoff (zero added database cost over an unmeasured correlated join) — **user** (choice
  of "zero cost over correlated join" kept; "narrow the kind list" specifically was wrong and is
  corrected here).
- Drop `INTERACTION` from `spans`' unconditionally-admitting kinds, relying on the paired
  `log_events` row (same `span_id`) instead of a join — a column-list change, no added probe.
- Leave `hook_events`/`usage_requests` unchanged — neither is implicated.
- Close the EPMCDME-16993 gap by gating the lifetime `_has_any_activity` proxy on `NOT resumed` and
  giving the resumed cohort its own new arm (derived) and a live-computed equivalent (underived),
  over reading the stored `resumed`/`has_activity` columns together in the existing arm (they are
  written in lockstep and NULL in lockstep, so that reading is a no-op) and over widening the
  existing index-only arm's population in place (unmeasured, and risks the exact scan-cost
  regression this predicate has hit before).
- Describe the EPMCDME-16993 gap as the `resumed` proxy's own scope (any multi-day session), not as
  a `started_at`-forward-move mechanism — the only `started_at` writer found in this codebase cannot
  raise a stored value, so a fix premised on that movement would target an unconfirmed mechanism.
- Require a pre-merge benchmark of the new resumed-arm's cost against a representative `resumed`
  population, over asserting it is safe by analogy to the unrelated arm-4 measurement — the one
  figure on record (10 resumed sessions) was never sized for this population.

## Testing

- Live-Postgres integration tests (existing skip-when-unset pattern): (a) lifecycle-hooks-only
  session excluded across all six endpoints; (b) multi-day/`resumed` session with real activity on
  one day and only housekeeping in the queried window, excluded, both with `started_at` inside and
  outside that window; (c) exit-time-records-only session (zero-delta lines point, zero-delta
  `lines_daily` row, slash-command-triggered `user_prompt`/`INTERACTION` pair) excluded the same way.
- Unit tests, semantic (seeded-row, not SQL-string-shape): UTC-midnight-crossing hour presets; the
  corrected arm routing for a resumed-but-underived session (the live-resumed path, not the NULL
  stored column); the `lines_daily` zero-value existence check. Existing SQL-shape literals in
  `test_reader.py:422-471` need updating for the new arm and narrowed clauses, not just new tests.
- One cross-endpoint contract test asserting the six totals in Acceptance Criteria agree.
- `scripts/cli_analytics_bench/run_sessions_bench.py` (or an `EXPLAIN (ANALYZE, BUFFERS)` of the
  default-sorted page/count) against a `resumed`-population sized at production scale, run and its
  numbers recorded in `cli-analytics-storage.md`'s "Scan cost" section before merge, per that
  section's own established convention.
- A doc update to `cli-analytics-storage.md`'s "Window Semantics" section reflecting the corrected
  rule and the new arm.

## Open Risks

- **Resolved 2026-11-12 (code review follow-up, CR-001).** The new resumed-arm's cost was unmeasured
  as of this spec; it has since been benchmarked at production scale (1.9M `session_dims` rows,
  `resumed` cohorts of 0.1%/1%/2% of the 950K started-in-window population, half cheap/half expensive
  per cohort) — see `cli-analytics-storage.md`'s "Scan cost of the admission rule", 2026-11-12 entry.
  The section 6.6 latency budget holds through 1% `resumed` and is crossed only past 2%, against the
  one real figure on record (≈0.001% `resumed`, two and a half orders of magnitude below the 0.1%
  point). Decision: the INCLUDE-list migration fallback is **not implemented** — no regression at any
  measured or currently-plausible scale.
- The hook-coverage assumption behind the `log_events`/`spans` narrowing remains unverified for CLI
  tools other than Claude Code; tracked as a follow-up, still with no ticket/owner named — filing one
  is outstanding, not resolved by this rewrite.
- `SKILL_ACTIVATED`/`TOOL_RESULT`/`TOOL_DECISION` log rows have no confirmed exit-time/synthetic
  analogue the way `user_prompt`/`exit` does; they are left unconditionally admitting on the
  assumption none exists today. If one is found, it needs the same `command_name`-style treatment.
- The "/exit synthetic `user_prompt`" claim's CLI-side mechanism remains unverified against this
  repository (the admission-side mechanism — `command_name = 'exit'` on that row — is now confirmed
  via `BUILTIN_COMMAND_NAMES`, but the event's own emission is CLI/plugin code outside this repo).
