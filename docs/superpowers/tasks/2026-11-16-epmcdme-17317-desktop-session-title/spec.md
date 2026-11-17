# EPMCDME-17317 — Desktop-hosted sessions must not show their auto-generated chat name as the served prompt

## Intent

Claude Desktop auto-generates a chat name for every session it hosts (Code tab and Cowork) and
sends it as `session_title`. The backend's `_served_prompt` (`cli_analytics_handler.py:142`)
currently treats any non-blank `session_title` as an explicit user rename and always serves it
over the first user prompt — correct for a real `--name`/`/rename`, wrong for Desktop's own
auto-name. Users see "Hello world text and PDF" instead of their actual first prompt.

Fix: `_served_prompt` ignores `session_title` specifically when the session's `entrypoint`
indicates it was hosted inside Desktop, falling through to its existing chain
(`first_prompt → repository → branch`) unchanged. Every other session (native CLI, `--name`d,
`codemie-claude`) keeps today's behavior exactly.

## Decisions

- Desktop-entrypoint membership check as a module-level constant in `cli_analytics_handler.py`
  (where all four consuming call sites already live) over a new enum/shared-vocabulary module —
  no existing enum/constant for entrypoint values exists anywhere in this repo or in the sibling
  codemie-code repo (technical-analysis.md confirms this by search), so introducing a new shared
  type would be inventing a convention with no second consumer, not reusing one.
- Blank or missing `entrypoint` is treated as "not Desktop" (title still wins if present) over
  treating it as ambiguous/Desktop-leaning — every session ingested before `entrypoint` started
  being populated, or from any client that doesn't set it, must keep exactly today's behavior; the
  new rule can only narrow when it fires, never widen onto rows it has no evidence for.
- `entrypoint` is compared trimmed and lower-cased (`.strip(BLANK_CHARS).lower()` in
  `_title_is_eligible`; `lower(btrim(...))` in the SQL search arm), not by plain raw-value
  equality — **superseding this decision's original "raw equality" choice**. The first code-review
  round (that round's own CR-002) found that a client sending the three canonical literals with
  stray whitespace or different casing would bypass the exclusion entirely, reproducing the exact
  bug this change fixes; normalizing the comparison closes that gap while leaving `entrypoint`
  itself stored exactly as received. `entrypoint` is still a controlled identifier, not
  user-authored free text — this is deliberate normalization of a known, bounded set of literals,
  not adoption of the `BLANK_CHARS` convention used for user-authored text.
- The Python-side `_DESKTOP_ENTRYPOINTS` (`cli_analytics_handler.py`) and the SQL-side
  `_DESKTOP_ENTRYPOINTS_SQL` (`reader.py`) are a deliberately duplicated literal set, not a gap —
  SQL cannot import the Python constant, and the non-goal above already rules out a new shared
  module for a set with no second consumer. Divergence is guarded by
  `test_the_sql_desktop_entrypoint_list_matches_the_handler_constant` (`test_reader.py`), which
  asserts the two sets stay equal. A reviewer re-flagging the duplication itself (as opposed to
  proposing a stronger guard than the equality test) is re-litigating this decision, not finding a
  new gap.
- `named_first_prompt`'s search-by-title fallback (`cli_analytics_handler.py:972-976`) applies the
  exact same Desktop-entrypoint exclusion as the display rule, over a separately-tuned rule for
  search — search results must stay consistent with what the session list actually shows; a
  session excluded from the title path for display but still searchable by its (hidden) title would
  be a visible inconsistency.
- The three literal entrypoint values (`claude-desktop`, `claude-desktop-3p`, `local-agent`) are
  implemented as currently understood and carried as an open risk to confirm during
  implementation, rather than blocking the spec on that confirmation — per the ticket's own
  instruction; see Open Risks.

## Approach

1. Add `_DESKTOP_ENTRYPOINTS` next to `_served_prompt` in `cli_analytics_handler.py`:
   ```python
   _DESKTOP_ENTRYPOINTS = frozenset({"claude-desktop", "claude-desktop-3p", "local-agent"})
   ```
2. Extend `_served_prompt` with an `entrypoint` parameter; when `entrypoint in _DESKTOP_ENTRYPOINTS`,
   skip `title` and start the existing fallthrough at `first_prompt`:
   ```python
   def _served_prompt(title, first_prompt, repository, branch, entrypoint) -> str:
       effective_title = None if entrypoint in _DESKTOP_ENTRYPOINTS else title
       return (
           _s(effective_title).strip(BLANK_CHARS)
           or strip_pasted_content(_s(first_prompt))
           or _s(repository)
           or _s(branch)
       )
   ```
3. Pass `entrypoint` from each of the three callers' row/meta dict: `_session_row` (843-845),
   `get_session_detail` (1073-1075), `get_efficiency`'s worst_session_prompt (call-graph edge
   confirmed, near line 1300 — re-check the exact current line before editing per
   technical-analysis.md's risk note).
4. Apply the same `entrypoint in _DESKTOP_ENTRYPOINTS` exclusion to `named_first_prompt`'s guard
   condition (972-976): a Desktop-entrypoint row is skipped from that dict the same way a blank
   title is today.
5. `reader.py` — add `entrypoint` alongside `session_title` in the three queries, mirroring each
   site's own existing null-handling idiom for `session_title`:
   - `_sessions_cte`'s `sel` CTE (426): `coalesce(d.entrypoint, '') AS entrypoint`.
   - `get_session_cost_facts` (872): `max(s.entrypoint) AS entrypoint` — lands together with the
     `sel` CTE change above; the two are coupled (same alias).
   - `get_session_detail_meta` (1051, independent, reads `session_dims` as `d` directly):
     `nullif(d.entrypoint, '') AS entrypoint`.
6. Docs — one sentence each:
   - `.ai-run/guides/integration/cli-analytics-events.md` (session_title common-field row, ~line
     132): note that Desktop-entrypoint sessions' `session_title` is stored but not used to build
     the served `prompt`.
   - `.ai-run/guides/integration/cli-analytics-storage.md` (latest-session-name rule, ~line 155):
     same exception, stated against the storage/merge rule.

## Acceptance Criteria

- AC1: Code tab session `f17aca9c-8d00-43c8-8682-a14ccd196bda` (entrypoint `claude-desktop` or
  `claude-desktop-3p`) serves its first prompt ("Hey Desktop claude hey, run a few skill and
  subagents…"), not its Desktop auto-title ("Hello world text and PDF"), in the sessions list.
- AC2: Cowork session `c9edfb02-e43f-4ac1-8122-720c3d9c24c5` (entrypoint `local-agent`) serves its
  first prompt ("hey cowork, run a few skill and subagents…"), not its auto-title
  ("Helloworld.txt to PDF").
- AC3: The same exclusion applies identically in `get_session_detail` and in `get_efficiency`'s
  `worst_session_prompt`.
- AC4: `named_first_prompt`'s search-by-title fallback excludes Desktop-entrypoint sessions the
  same way, so search-by-title stays consistent with what the session list displays.
- AC5: A native `claude` session using `--name`/`/rename` and a `codemie-claude` session (both
  non-Desktop entrypoints) keep showing their explicit `session_title` unchanged.
- AC6: A session with blank or missing `entrypoint` keeps today's behavior unchanged (title wins
  when non-blank).
- AC7: `session_title` remains stored and is not newly exposed as its own field in any response —
  no REST/OpenAPI contract change.
- AC8: `cli-analytics-events.md` and `cli-analytics-storage.md` each gain the one-sentence Desktop
  exception noted in Approach step 6.

## Non-goals

- No change to the codemie-code CLI repo (e.g. stopping Cowork from sending its auto-title as
  `session_title`) — a separate, independently-scoped cleanup in a different repository.
- No new REST/OpenAPI field or response-model change; `prompt` continues to be the only field this
  logic feeds.
- No schema migration — `entrypoint` already exists as a `session_dims` column.
- No new enum/shared-vocabulary module for entrypoint values beyond the local constant in
  `cli_analytics_handler.py`.
- Does not address the accepted trade-off that a deliberate rename made inside Desktop itself is
  indistinguishable from Desktop's own auto-name and will also fall through to the first prompt.
- No change to the "last non-null wins" merge rule for `entrypoint` in rollups write path
  (`rollups.py`).

## Testing Notes

- Unit, `_served_prompt` (extend the existing ~10-case parametrized suite at
  `test_cli_analytics_handler.py:1805-1879`): each of the three Desktop literals ignores a
  non-blank title; a non-Desktop entrypoint (e.g. `cli`, `codemie-cli`) with a title still shows
  the title; blank/missing entrypoint with a title still shows the title (AC6).
  `hook_event_builders.py:39`'s existing `"entrypoint": "cli"` default is the pattern to follow
  for setting a Desktop value in new fixtures.
- Integration/unit for the three callers and for `named_first_prompt`'s exclusion (AC3, AC4).
- Regression fixtures for the two repro sessions (AC1, AC2) and the two control sessions (AC5),
  per the ticket's exact session ids and expected strings above.

## Open Risks

- **Accepted, not fixed — `entrypoint`'s "latest value wins" merge (`rollups.py`, unchanged by this
  non-goal above) makes the exclusion reactive to whatever `entrypoint` is current, not the
  entrypoint the session started with. Two symmetric consequences, both accepted at the
  architecture-review gate (`spec.approved`, finding AR-1) as the same trade-off as "a deliberate
  Desktop rename is indistinguishable from Desktop's own auto-name":**
  - A session renamed explicitly outside Desktop, then continued inside Desktop or Cowork under the
    same session id, has that explicit rename hidden once `entrypoint` flips to a Desktop value
    (AR-1's original direction).
  - A Desktop-hosted session's auto-title can resurface if the same session is later resumed from a
    non-Desktop entrypoint (e.g. a terminal, `entrypoint` flips to `cli`) — `_title_is_eligible`
    only ever sees the current `entrypoint`, so the stored Desktop auto-title becomes "eligible"
    again and is served over the first prompt.

  Both directions are the same mechanism and the same accepted scope boundary — fixing either would
  mean pinning eligibility to the entrypoint at session start, which is explicitly out of scope
  (see the `rollups.py` non-goal above). Re-flagging either direction as a new finding without this
  context re-litigates an already-decided trade-off.
- **Resolved, post-merge-prep, via direct live testing — not a live DB spot-check of the two ticket
  session ids (still not done), but live capture off a real running Desktop build, which is
  stronger evidence for the literals themselves:**
  - `claude-desktop-3p` (Code tab): confirmed by running `echo $CLAUDE_CODE_ENTRYPOINT` inside a real
    Code tab session connected through the CodeMie gateway. The plain `claude-desktop` (no `-3p`)
    form was never observed on this build — tested both gateway-connected and disconnected
    (personal Anthropic sign-in), both produced `claude-desktop-3p`. It stays in the set as a
    harmless, possibly-unused-on-current-builds entry (older Desktop version, different OS, or a
    mode not exercised here) — removing it risks a false negative for zero benefit.
  - `local-agent` (local-VM Cowork): confirmed by reading the real transcript JSONL Desktop wrote
    for a local Cowork session (`.claude/projects/session/*.jsonl`, `entrypoint` field on the user
    message line) — present even though the session's own API call was unreachable, confirming the
    field is written during session bootstrap, not tied to a successful model response.
  - **New, previously unknown, added to the set as a result of this testing: `remote_cowork`**
    (cloud-hosted Cowork — a remote workspace linked back to the local machine, distinct from the
    local-VM `local-agent` mode). Discovered live, after disconnecting Desktop from the CodeMie
    gateway and starting a new session, via the same `CLAUDE_CODE_ENTRYPOINT` check. Zero prior
    reference anywhere in either repo before this. This is a materially different Desktop surface
    from the three literals carried since Stage 3 — it was never named in the ticket, the
    cross-repo investigation, or the EPMCDME-17128 fixtures, and would have silently fallen through
    this fix's exclusion (served and searchable by its Desktop auto-title) had it shipped without
    this live test.
  - The two ticket session ids themselves (`f17aca9c-...`, `c9edfb02-...`) were never spot-checked
    against live `session_dims` — this gap is superseded, not closed: direct literal confirmation
    from a real Desktop build is stronger evidence than confirming two specific historical rows
    would have been.
- `get_efficiency`'s exact `_served_prompt` call-site line was confirmed only by call-graph edge,
  not re-read verbatim — re-check its current line before editing.
- `_sessions_cte`'s `sel` CTE change and `get_session_cost_facts`'s `SELECT`-list change are
  coupled (same alias) and must land in the same change, or `get_session_cost_facts` would
  reference a nonexistent `s.entrypoint`.
