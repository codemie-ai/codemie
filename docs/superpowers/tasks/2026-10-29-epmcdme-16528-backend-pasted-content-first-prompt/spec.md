# EPMCDME-16528 (backend): readable session title in CLI Analytics

## Problem

Session List and Session Detail serve `prompt` = `session_dims.first_prompt`, taken verbatim from the
first qualifying `hook_events.prompt_body`. Claude Code wraps pasted text as
`<pasted_content id="x">\n...\n</pasted_content id="x">\n`, so the title shows raw markup. Old CLIs
(<= 0.16.2) still send the markup; new CLIs send a cleaned `prompt_body` plus `prompt_original`. The
backend must therefore clean on its own and must not depend on the new field. Separately, `--name`
(arrives as `session_title` in the residual `hook_events.attrs`) never reaches `session_dims` and is
never served.

## Approach

1. **Clean in the rollup SQL (source of truth).** The `first_prompt` derivation
   (`postgres/rollups.py`, `_PROMPT_OK`, `_earliest("prompt_body", ...)`, the `session_dims` upsert) works
   on cleaned text: remove the tag text only, matching the ARE built once in `vocabulary.py`
   (`PASTED_CONTENT_TAG_SQL`, shared by the SQL and the Python regex) with flag `g`, keep the enclosed text
   byte-for-byte, then trim the blank set at both ends (default `btrim` does not trim newlines). The blank
   set is ONE set on the SQL and Python side: ASCII space, tab, CR, LF and the no-break space U+00A0.
   Tag grammar: `<` or `</`, `pasted_content`, zero or more COMPLETE quoted attributes (ASCII whitespace,
   name `[A-Za-z0-9_-]+`, `="`, value `[^"<>\n]*`, `"`), then `>`; only when it ends the text, a truncated
   final piece (old-CLI cut-off: optional name, optional `=`, optional `"` and a partial value without
   whitespace) may end the tag. Whitespace in the pattern is the explicit ASCII class `[ \t\n\r\f\v]`, never
   `\s`, which drifts between Postgres and Python (locale/Unicode). A stray `<pasted_content` followed by
   prose, an unquoted value or a bare attribute is not a tag and stays in the text. Covered shapes: whole-prompt, mid-prompt, several blocks, orphan tag,
   tag cut inside its attributes by the old CLI's 200-char cut. A cut inside the tag name is not
   recoverable and is left as is.
   - Sentinel tests (`SENTINEL_*`, `_PROMPT_OK`) apply to the cleaned text; the two raw continuation and
     caveat prefixes are tested on the prompt as sent and on the cleaned text.
   - A prompt that cleans to empty does not qualify; the next qualifying prompt becomes `first_prompt`.
   - `_earliest_merged` guards `first_prompt` with `nullif(..., '')` so an empty result never overwrites a
     stored good value.
2. **Python safety net (read time).** Add a pure `strip_pasted_content(text) -> str` to
   `repository/cli_analytics/vocabulary.py` with identical semantics (same pattern, same blank set). The
   handler applies it to every `first_prompt` it serves, so every stored row, including rows past the
   90-day raw retention that no recompute can reach, is served clean. Python `$` matches before a
   trailing newline while ARE `$` does not; use `\Z` (or an equivalent) so the two stay identical.
3. **Parity test.** A live-Postgres test runs one corpus of strings through the SQL expression and the
   Python helper and asserts equal output. It is version-agnostic: it runs against whatever database
   `TEST_LOCAL_PG_URL` points at and skips without one; no multi-version infrastructure is added. Corpus:
   whole, mid-prompt, multiple, unterminated, orphan, attribute-truncated (`<pasted_content id="trunc`),
   bare `<pasted_content`, plain typed prompt, lookalike `<pasted_contentx id="q">...</pasted_contentx>`
   (left unchanged); the cases are in `/tmp/repro-16528/regex-corpus.sql`. Verified fact: the regex with
   flag `g` plus the newline-aware trim was run by hand on PG 14.24 and PG 17.11 over these 9 cases with
   byte-identical output.
4. **Explicit session name: `session_dims.session_title`.** New nullable `text` column:
   - Meaning: `session_title` is the explicit `--name`, taken from the attr key of the same name. The
     existing `session_dims.title` is the auto-generated transcript ai-title; it stays unserved, and only
     `session_title` feeds the served `prompt`.
   - A third alembic revision, schema-only (metadata-only `ADD COLUMN`). `test_migrations._declared_columns()`
     is extended to read it, and the column is added to `_SESSION_DIMS_COLUMNS`.
   - Feed: reuse the existing latest-non-empty-value mechanism (`_COMMON_TEXT` -> `_LATEST_FIELD` /
     `_LATEST_VALUES` -> `_SESSION_DIMS_VALUES` as `lv.v->>'<col>'`, rollups.py ~780-905, ~1312-1314), with
     the column named like the key, so no name mapping is needed. Adding the key to `_COMMON_TEXT` means:
     any event may carry it (string values only); being in `_VALUE_KEYS` it is removed from
     `session_dims.attrs`; being in `_SESSION_DIMS_COLUMNS` (hence `_NOT_ATTRS`) it is never copied into
     `session_dims.attrs` by the `_ATTRS_EVENTS` clause either. `_COMMON_TEXT` and `_VALUE_KEYS` are
     used only in rollups.py. The generic entry at line 1312 is special-cased for this column to apply
     the bound below.
   - Latest wins by (ts, ingest_seq), so a new name replaces the old one. Merge into the stored row is
     `_session_dims_set` (rollups.py:1339-1340): "a NULL never overwrites", so a recompute that finds no
     name keeps the stored value and a non-null value replaces it.
   - Bound: trimmed of the blank set and cut to 200 characters when the column is filled (e.g. `left(btrim(...), 200)`,
     empty -> NULL). This is new server-side logic with its own test: the backend has no cap on `title`
     (the 200 cap on the ai-title is CLI-side only, `session-signals.ts:188`), and the CLI forwards
     `session_title` unbounded.
5. **Serving.** The reader returns `session_title` next to `first_prompt` (list/cost-facts query, session
   detail meta). The handler computes the served `prompt` = `session_title` if non-empty, else
   `strip_pasted_content(first_prompt)`, else the repository, else the branch, else `""` (a first prompt that
   cleans to empty, e.g. an old-CLI prompt cut inside the open tag, falls through to the repository or
   branch). `prompt: str` is unchanged in the REST models; no OpenAPI change.
   `worst_session_prompt` (efficiency) uses the same label as the Sessions list: `session_title`, else the
   cleaned first prompt, else the repository, else the branch (`null` when all are empty).
6. **Search.** The sessions-list search (handler, Python, over built rows) matches the case-insensitive
   term against the served `prompt`, the cleaned first prompt, repository and branch. The handler carries
   the cleaned first prompt alongside the served prompt for this; it is not added to the response.
7. **`prompt_original` is ignored**: not read, served, searched or persisted. It lives in unindexed,
   weekly-partitioned `hook_events.attrs` and dies with the 90-day raw retention. It exists only for
   new-CLI, pasted prompts, and is masked by pattern only, so using it would add a privacy and size
   decision for no gain.

## Acceptance criteria

1. A multi-line pasted prompt yields a readable `first_prompt`: tags removed, pasted text preserved
   byte-for-byte, outer whitespace trimmed.
2. No `pasted_content` tag appears in `prompt` in Session List or Session Detail, for whole, mid-prompt,
   multiple, orphan and attribute-truncated shapes, from both new- and old-CLI input.
3. A typed prompt is unchanged (trimmed only); a lookalike tag (`<pasted_contentx ...>`) is left alone.
4. A prompt that cleans to empty is skipped and the next qualifying prompt is the first prompt; a stored
   `first_prompt` is never overwritten by an empty value.
5. A sentinel wrapped in a paste block is recognised on the cleaned text.
6. A named session serves `session_title` as `prompt` in List and Detail; the latest non-empty name wins
   when the session is resumed with a new `--name`; unnamed sessions serve the cleaned first prompt; a
   recompute without a name keeps the stored one.
7. `session_title` is cut to 200 characters and trimmed when filled; it never appears in
   `session_dims.attrs`.
8. `worst_session_prompt` uses the same label as the Sessions list: `session_title`, else the cleaned first
   prompt, else the repository, else the branch.
9. A stored `first_prompt` that still contains markup (pre-fix row) is served clean by the read-time net.
10. Search finds a named session by a word of its name and by a word of its real first prompt, and still
    by repository and branch.
11. Turn classification output is unchanged (it reads `hook_events.prompt_body` directly).
12. The SQL expression and the Python helper agree on the whole parity corpus on the database the live
    tests are pointed at.
13. Old-CLI events (no `prompt_original`) and new-CLI events both ingest and roll up; the field is never
    required.
14. Regression tests exist for typed, pasted-only, typed+pasted, mid-prompt, multiple, unterminated,
    orphan and `--name` sessions: live-PG tests on the `_live` + `hook_event_builders` scaffolding, handler
    tests (prompt precedence, search, worst prompt), and the migration test.
15. `.ai-run/guides/integration/cli-analytics-events.md` (prompt cleaning, `session_title` ->
    `session_dims.session_title`, `prompt_original` ignored) and `cli-analytics-storage.md`
    (`first_prompt` derivation, `session_title` as a latest-value column next to the unserved ai-title
    `title`, how stored rows are served clean) are updated.

## Non-goals

- No removal of whole `<pasted_content>` blocks and no discarding of pasted text; no sentinel-list
  entry for the tag.
- No reading, serving, searching or persisting `prompt_original`.
- No new REST field and no change to REST models or the OpenAPI schema.
- No use of `session_dims.title` (ai-title) as a name or title source.
- No change to turn classification, `_IS_AUTO_PROMPT`, or `prompt_body` at ingest (stored raw).
- No data-repair UPDATE of stored `first_prompt` rows and no backfill change: the alembic revision is
  schema-only. The Python read-time net already serves every stored row clean, including rows past the
  90-day window, and a data migration would be the repo's first and hard to undo.
- No multi-version Postgres test infrastructure.
- No change to the platform conversation store or the CLI conversation sync (outside this repo's
  analytics path).
- No recovery of tags cut inside their name by old CLIs.

## Decisions after the spec

Deliberate changes made after this spec was written, during review of MR !4680. They are intended;
a review against this spec should not report them as deviations. The acceptance criteria and approach
above are amended to match.

1. **Efficiency label (AC8).** `worst_session_prompt` uses the same label as the Sessions list: the
   `session_title`, else the cleaned first prompt, else the repository, else the branch. Reason: the same
   trace was shown under two different labels (Sessions list vs Efficiency). Owner decision.
2. **Served-prompt fallback (approach 5).** When there is no title and the stored first prompt cleans to
   nothing (an old-CLI prompt cut inside the open tag), the repository and then the branch are served
   instead of an empty string. No reader column, response field or migration is added. Reason: such a row
   has no recoverable text and never recomputes after the raw window. Owner decision.
3. **One blank set, ASCII tag whitespace.** The blank set (space, tab, CR, LF and the no-break space
   U+00A0) is defined once for SQL and once for Python. The tag pattern uses the explicit class
   `[ \t\n\r\f\v]` and `[A-Za-z0-9_-]` instead of `\s` and `\w`, because Postgres and Python disagree on
   those two for non-ASCII input. Reason: a whitespace-only `session_title` (NBSP) must not overwrite a
   real name, and the stored and the served prompt must stay identical.
4. **Stricter tag grammar.** A tag is `<` or `</`, `pasted_content`, complete `name="value"` attributes,
   then `>`, or only at the end of the text a truncated final piece (a cut-off by an old CLI). Bare
   attributes and an unterminated quote followed by prose are not tags. Reason: a stray opener typed in a
   prompt (`... show <pasted_content id=b23a instead of my text?`) must not delete the rest of the prompt.
   The CLI side uses the same rule (quoted attributes, `>` required).
5. **Empty never replaces a real value.** `_earliest_merged` keeps `nullif(first_prompt, '')` in both
   coalesce operands, as in the approach above.
6. **Turn classification untouched.** `_IS_AUTO_PROMPT` keeps its original trim set (space, tab, CR, LF),
   not the shared blank set, so turn counting does not change.
7. **Migration id.** The `session_title` migration is `a1c1a0000009` with `down_revision =
   a1c1a0000006` (the head on main), because `a1c1a0000007` and `a1c1a0000008` are claimed by other
   unmerged branches. Whichever branch merges later repoints `down_revision` to the head on main.

## Open items

- An idempotent in-migration `UPDATE session_dims` (rows containing the marker; cleaned-to-empty set to
  NULL) can be added later if a direct consumer of `session_dims.first_prompt` (BI, ad-hoc SQL) turns
  up. None was found among the 3 reader queries and 4 handler sites.

## Open risks

- Mid-session interactive `/rename`: verified that resuming with `claude --resume <id> --name <new>`
  puts the NEW `session_title` in both SessionStart and UserPromptSubmit hook payloads of the same
  session id and writes new `custom-title` / `agent-name` transcript lines (claude 2.1.312). An
  interactive mid-session `/rename` could not be exercised non-interactively, so that exact path is
  untested.
- The production Postgres version is unknown (docker-compose uses pgvector pg17; no CI pin found). The
  regex was hand-verified identical on 14.24 and 17.11, but the automated parity test runs on one
  version only.
- SQL (ARE) and Python regexes can drift on Unicode edge cases beyond the corpus; the explicit ASCII
  whitespace class and the single blank set remove the known whitespace drift, and the parity test is the
  guard for the rest.
- A whitespace-only latest `session_title` is picked as the latest non-empty raw value and then trims to
  NULL, so it will not replace an earlier real name (the stored value is kept).
- Rows past the raw window stay raw in storage and are cleaned only at read time.
