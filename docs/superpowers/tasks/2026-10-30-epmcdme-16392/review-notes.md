# EPMCDME-16392 — review notes (MR !4678), companion to the generated `code-review.md`

The machine-readable report is `code-review.md` in this directory, rendered by `sdlc-review-report render` from
`code-review-final.local.json` (the inherited round-1 request-changes baseline, local) and `code-review-check.local.json`
(published byte-for-byte as `code-review-check.json`), with the pin set to the reviewed production source
`4e6d3ed8d6fba55a3fff899ff85b02c6cab8b714`. Its Coverage table repeats the round-1 grades (e.g. AC3 `partial`, live DB
`pending`): those belong to the final round, and CR-001, which caused the AC3 grade, is closed by the check round. This file
holds the provenance the report does not carry. The earlier round-2 note is
`../2026-10-30-epmcdme-16392-inline-skill-timeline-r2/code-review.md` (superseded).

Produced by the installed `sdlc-factory:code-review` skill (plugin 0.9.13) through `code-review-orchestrator`.

## Reviewed source

- **Reviewed production-source head: `2878f99a18ae2c24c0ae843b6e6c152d50f670bf`** (on `main` `06df4db9f`).
  The commit that adds this record comes after it and changes only files under `docs/superpowers/tasks/`; the pushed
  branch head is therefore that later artifact-only commit, not `2878f99a1`, and no source differs between them.
- **Rebased onto `main` `d2d59a21e` (published equivalent):** `84d1e837b` -> `e1702ac4d`, `704da36ed` -> `fa0684855`,
  `2878f99a1` -> `4e6d3ed8d` (production source). The only change from the reviewed source is one import-block conflict
  in `reader.py` taken from `main` (`CLASSIFIER_NAMES_SQL` replaces `SKILL_NAMES_SQL`, `SESSION_PAGE_SORTS` kept, our
  rollups helper imports kept). `git range-diff` shows every other patch `=`; the added and removed lines of the delta
  (`src`, `tests`) are identical before and after; `rollups.py` is unchanged on `main`, so the pairing helpers this
  change imports are the reviewed ones. After the rebase only ruff, `test_reader.py` and `test_session_derived.py`
  (246 passed) were run; no new review round and no live rerun, since the production logic is unchanged.
- Check baseline `reviewed_head`: `84d1e837bbed5331dc498d005a921274a7a5a721` (the rebased original fourth patch; its diff
  against `main` is byte-identical to the round-1 frozen diff, which was taken on the pre-rebase `98c5c5cad`).
- Fix-up diff reviewed: `git diff 84d1e837b..2878f99a1 -- src tests`, 3 files, +66/-2, sha256
  `6d69b3f7c74f96eef1e4ac7a44c9b340252bce5443321d5a9647a56358cb4053` (byte-compared with the frozen
  `code-review-check.local.diff`, which stays local). It is the whole accumulated CR-001 correction (`704da36ed` and
  `2878f99a1`), not only the last commit.

## Rounds

| Round | Gate | Head | Result |
|---|---|---|---|
| 1 | `code-review.final` (full, compact profile, S) | `98c5c5cad` = `84d1e837b` rebased | request-changes, **CR-001** (major): a duplicated inline slash dispatch doubled the inline cost and tokens. This verdict is the inherited baseline (`code-review-final.local.json` here, sha256 `0d8b6384…f5451`, not published as current) |
| 2 | `code-review.final` (fresh) | `1ccba7d82` = `704da36ed` rebased | approve, high, 0 findings, on the first fix (`DISTINCT` in `inline_window`). Superseded: it did not cover the duplicate public rows found afterwards |
| 3 | `code-review.check` (targeted verifier, `check_scope: code`) | `2878f99a1` | **approve, high; `finding_status`: CR-001 `resolved`** |

`code-review-check.json` is the check round's own output, copied byte-for-byte from `code-review-check.local.json`
(sha256 `ff1a3d9d719d9afa2040781f5a3ff15cff69301036bd6843142086c0da34afe0`). It was not edited. Its `findings` array
carries the historical CR-001 entry from the baseline and its `business_review` / `standards_review` rows are carried
forward from it: the check result for CR-001 is `finding_status[0].status = resolved`, not a new finding. The check round
read the current source and did not rerun tests; the live results below come from the author's own runs.

## CR-001 resolution

`dispatch_fork` (`reader.py`) is `SELECT DISTINCT d.ts, d.skill_name, d.prompt_id, df.agent_id, df.ts AS fork_ts`: rows
identical in those columns are one dispatch, so `inline_window` and the final slash `UNION` branch each get one row.
`hook_events` has no unique key, so a dispatch differing in a byte or a raw second row does reach the table (a re-sent
identical or same-batch dispatch is dropped by the ingest ledger). Fork, agent and Skill-call rows and session totals do not
read the collapsed duplicates.

## Test evidence (not rerun for the check round)

- New live regression `test_live_a_duplicated_inline_slash_dispatch_is_one_row_and_counts_its_requests_once` (`resent`,
  `same_batch`, `other_bytes`, `raw_row`) on Postgres 17: red on `other_bytes` and `raw_row` with the `704da36ed`
  reader, green on `2878f99a1`. It asserts one command row, 170/30 tokens, the visible cost sum equal to a single-dispatch
  control, and unchanged session totals.
- `test_live_dispatch_gate.py` + `test_reader_live_dispatches.py`, serial: 111 passed, 1 failed (the Windows calendar
  `OSError` test, reproduced on the exact baseline earlier). `test_reader.py`: 209 passed. Ruff clean.
- Earlier live proxy / HTTP / timing evidence is from the pre-rebase head `21e76792`; the original four patches are
  unchanged. The full 200-test `sanity-api` gate has not run.
- Limits: compact review profile (no standards, blind or verification-gap lens).
