# EPMCDME-16530 (Backend): Keep unbranched proxy tokens in CLI Repositories analytics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /v1/analytics/cli-repositories` stops discarding LLM-proxy token usage recorded without a branch when the same repository and user have a real CLI session on a branch.

**Architecture:** Read-time fix in `CLIHandler._parse_cli_repositories_result` (`src/codemie/service/analytics/handlers/cli/handler.py:863-920`). Today a `(repository, branch, user)` bucket with no `cli_tool_usage_total` document is skipped. A proxy token record with `branch == ""` (CLI sent no `X-CodeMie-Branch`; `proxy_router.py:472` stores `""`) therefore vanishes while the session row on the real branch shows 0 tokens. The fix folds such `""`-branch token-only buckets into the same repository/user's session row. No ES query, mapping, or ingest change, so historical data from old CLI versions is repaired as well. Companion CLI plan: `codemie-code` repo, `docs/superpowers/plans/2026-10-30-epmcdme-16530-cli-unborn-head-branch.md` (stops new data from splitting).

**Tech Stack:** Python 3, Elasticsearch aggregations, pytest (`.venv/bin/pytest`), ruff (`make ruff`).

## Global Constraints

- Keep the noise filter added in EPMCDME-12687: a token-only bucket whose repository/user has **no** CLI session row stays dropped (Claude Desktop pings).
- A token-only bucket on a **real** branch (non-empty) stays dropped (unchanged; not part of this defect).
- Row shape (keys and types) is unchanged; `_get_cli_repositories_columns()` is untouched.
- Pagination: the pipeline paginates whole repositories and slices flattened rows; folding only merges rows within one repository bucket, so it never needs data from another page.
- Commit format: `EPMCDME-16530: <Description>` (`.ai-run/guides/standards/git-workflow.md`).
- Tests are written/run because this plan was explicitly requested with TDD steps.

## Background (verified 2026-10-30)

Reporter's A/B (`ab-token-attribution.txt`, CLI 0.16.2, Backend 2.62.0):

| Repo | Proxy `X-CodeMie-Branch` | Session branch | Local in/out/cc/cr | API in/out/cc/cr |
|---|---|---|---|---|
| `qa-h1/tok-commit` | `master` | `master` | 2/4/17615/10608 | 2/4/17615/10608 |
| `qa-h1/tok-nocommit` | none → `""` | `master` (from Claude transcript) | 2/4/28222/0 | 0/0/0/0, row branch `master` |

ES buckets for `tok-nocommit`: `("", user)` = tokens, `session_data.doc_count == 0` → skipped at `handler.py:897`; `("master", user)` = session, tokens 0 → returned.

The same split happens for `codemie-claude` on a detached HEAD (session branch `"HEAD"` from the transcript, proxy branch `""`) and for any session started with a CLI older than the companion fix.

Fold rule, per repository bucket and user:

| Token-only bucket (`doc_count == 0`) | Session rows for same repo + user | Result |
|---|---|---|
| branch `""` | exactly 1 | tokens added to that row |
| branch `""` | 2 or more | kept as its own row, branch `""`, duration/lines 0 (ambiguous; better than losing usage) |
| branch `""` | 0 | dropped (proxy-only noise, unchanged) |
| real branch | any | dropped (unchanged) |

## File Structure

- Modify: `src/codemie/service/analytics/handlers/cli/handler.py:863-920` — `_parse_cli_repositories_result`, plus two small static helpers next to it.
- Modify: `tests/codemie/service/analytics/handlers/test_cli_handler.py` — new tests in `class TestCLIRepositories` (starts line 389).

---

### Task 0: Precondition and branch

- [ ] **Step 1: Confirm the stored proxy branch value is `""` (not a missing field)**

In Kibana Dev Tools on the preview or prod cluster, for a no-commit session (reporter's repo `qa-h1/tok-nocommit`, 2026-10-29 16:12-16:14Z):

```
GET <cli metrics index>/_search
{
  "size": 3,
  "query": { "bool": { "filter": [
    { "term": { "metric_name.keyword": "codemie_litellm_proxy_usage" } },
    { "term": { "attributes.repository.keyword": "qa-h1/tok-nocommit" } }
  ] } },
  "_source": ["attributes.branch", "attributes.cli_request", "attributes.input_tokens"]
}
```

Expected: `attributes.branch: ""`. If the field is **absent**, the `terms` agg on `attributes.branch.keyword` produces no `""` bucket at all; then also add `"missing": ""` to `branch_agg` in `_build_cli_repositories_aggregation` (via `AggregationBuilder.build_terms_agg` sub-params) and adjust Task 1's tests. Use the field names from `src/codemie/service/analytics/handlers/cli/constants.py` (`METRIC_NAME_KEYWORD_FIELD`, `REPOSITORY_KEYWORD_FIELD`) if they differ from the above.

- [ ] **Step 2: Branch from the latest `origin/main`**

```bash
cd /Users/Sviatoslav_Likhtarchyk/mdtu_gpt/codemie
git fetch origin
git checkout -b EPMCDME-16530_cli-repositories-unbranched-tokens origin/main
```

### Task 1: Fold unbranched token-only buckets into the session row

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli/handler.py:863-920`
- Test: `tests/codemie/service/analytics/handlers/test_cli_handler.py` (`class TestCLIRepositories`)

**Interfaces:**
- Consumes: existing `handler` fixture (`test_cli_handler.py:48`), ES result shape `aggregations.paginated_results.buckets[].branches.buckets[].users.buckets[]` with `token_data` / `session_data`.
- Produces: `CLIHandler._parse_cli_repositories_result(result: dict) -> list[dict]` (unchanged signature); new private statics `_extract_cli_repository_tokens(user_bucket: dict) -> dict[str, int]` and `_attach_unbranched_tokens(rows: list[dict], user_rows: list[dict], repository: str, user_name: str, tokens: dict[str, int]) -> None`.

- [ ] **Step 1: Write the failing tests**

Add at module level in `test_cli_handler.py` (below the fixtures, above `class TestCLISummary`):

```python
def _repo_user_bucket(user: str, tokens: tuple[int, int, int, int], session_docs: int, duration: int = 0) -> dict:
    """ES user bucket for cli-repositories; tokens = (input, output, cache_creation, cache_read)."""
    input_tokens, output_tokens, cache_creation, cache_read = tokens
    return {
        "key": user,
        "token_data": {
            "input_tokens": {"value": input_tokens},
            "output_tokens": {"value": output_tokens},
            "cache_creation_tokens": {"value": cache_creation},
            "cache_read_tokens": {"value": cache_read},
        },
        "session_data": {
            "doc_count": session_docs,
            "session_duration": {"value": duration},
            "total_lines_added": {"value": 0},
        },
    }


def _repo_result(repository: str, branches: dict[str, list[dict]]) -> dict:
    return {
        "aggregations": {
            "paginated_results": {
                "buckets": [
                    {
                        "key": repository,
                        "branches": {
                            "buckets": [
                                {"key": branch, "users": {"buckets": users}} for branch, users in branches.items()
                            ]
                        },
                    }
                ]
            }
        }
    }
```

Add to `class TestCLIRepositories`:

```python
    def test_parse_cli_repositories_folds_unbranched_tokens_into_session_row(self, handler):
        """EPMCDME-16530: unborn-HEAD repo — proxy tokens have branch "", the session has "master"."""
        result = _repo_result(
            "qa-h1/tok-nocommit",
            {
                "": [_repo_user_bucket("u1", (2, 4, 28222, 0), session_docs=0)],
                "master": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=1, duration=9947)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert rows == [
            {
                "repository": "qa-h1/tok-nocommit",
                "branch": "master",
                "user_name": "u1",
                "input_tokens": 2,
                "cache_creation_tokens": 28222,
                "cache_read_tokens": 0,
                "output_tokens": 4,
                "session_duration": 9947,
                "total_lines_added": 0,
            }
        ]

    def test_parse_cli_repositories_folds_unbranched_tokens_into_detached_head_row(self, handler):
        """Detached HEAD: Claude transcript reports "HEAD", the proxy sends no branch."""
        result = _repo_result(
            "org/repo",
            {
                "": [_repo_user_bucket("u1", (10, 20, 30, 40), session_docs=0)],
                "HEAD": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=2, duration=500)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert len(rows) == 1
        assert rows[0]["branch"] == "HEAD"
        assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (10, 20)
        assert (rows[0]["cache_creation_tokens"], rows[0]["cache_read_tokens"]) == (30, 40)

    def test_parse_cli_repositories_adds_to_existing_session_row_tokens(self, handler):
        """Branched and unbranched proxy tokens for the same session row are summed."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (100, 50, 0, 0), session_docs=1)],
                "": [_repo_user_bucket("u1", (1, 2, 3, 4), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert len(rows) == 1
        assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (101, 52)
        assert (rows[0]["cache_creation_tokens"], rows[0]["cache_read_tokens"]) == (3, 4)

    def test_parse_cli_repositories_keeps_ambiguous_unbranched_tokens_as_own_row(self, handler):
        """Two session branches for the user: do not guess, keep the tokens visible on branch ""."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (5, 5, 0, 0), session_docs=1, duration=10)],
                "dev": [_repo_user_bucket("u1", (7, 7, 0, 0), session_docs=1, duration=20)],
                "": [_repo_user_bucket("u1", (1, 2, 3, 4), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 5), ("dev", 7), ("", 1)]
        unbranched = rows[2]
        assert (unbranched["session_duration"], unbranched["total_lines_added"]) == (0, 0)
        assert (unbranched["output_tokens"], unbranched["cache_creation_tokens"], unbranched["cache_read_tokens"]) == (
            2,
            3,
            4,
        )

    def test_parse_cli_repositories_drops_unbranched_tokens_without_any_session(self, handler):
        """EPMCDME-12687 noise filter: proxy-only usage (e.g. Claude Desktop pings) stays hidden."""
        result = _repo_result("org/repo", {"": [_repo_user_bucket("u1", (9, 9, 9, 9), session_docs=0)]})

        assert handler._parse_cli_repositories_result(result) == []

    def test_parse_cli_repositories_does_not_fold_across_users(self, handler):
        """u2's unbranched tokens must not land on u1's session row."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (0, 0, 0, 0), session_docs=1)],
                "": [_repo_user_bucket("u2", (9, 9, 9, 9), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["user_name"], r["input_tokens"]) for r in rows] == [("u1", 0)]

    def test_parse_cli_repositories_still_drops_token_only_bucket_on_real_branch(self, handler):
        """Only branch "" is treated as missing attribution; a real branch without sessions is unchanged."""
        result = _repo_result(
            "org/repo",
            {
                "main": [_repo_user_bucket("u1", (1, 1, 0, 0), session_docs=1)],
                "feature": [_repo_user_bucket("u1", (50, 50, 0, 0), session_docs=0)],
            },
        )

        rows = handler._parse_cli_repositories_result(result)

        assert [(r["branch"], r["input_tokens"]) for r in rows] == [("main", 1)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/codemie/service/analytics/handlers/test_cli_handler.py -k "parse_cli_repositories" -v`
Expected: FAIL — `folds_unbranched_tokens_into_session_row` (tokens 0), `folds_..._detached_head_row`, `adds_to_existing_session_row_tokens`, `keeps_ambiguous_unbranched_tokens_as_own_row`. PASS — `drops_unbranched_tokens_without_any_session`, `does_not_fold_across_users`, `still_drops_token_only_bucket_on_real_branch`, and the two existing parser tests.

- [ ] **Step 3: Write the minimal implementation**

Replace `_parse_cli_repositories_result` in `handler.py` (lines 863-921, up to and including `return rows`, just before `_get_cli_repositories_columns`) with:

```python
    def _parse_cli_repositories_result(self, result: dict) -> list[dict]:
        """Parse 3-level nested result and flatten into tabular rows.

        Note: Sorting is now handled by query_pipeline for consistent pagination.
        Extracts token data from CLI_LLM_USAGE_TOTAL and session data from CLI_TOOL_USAGE_TOTAL.
        """
        rows = []
        repo_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for repo_bucket in repo_buckets:
            repo_name = repo_bucket["key"]
            branch_buckets = repo_bucket.get("branches", {}).get("buckets", [])
            session_rows_by_user: dict[str, list[dict]] = {}
            unbranched_tokens_by_user: dict[str, dict[str, int]] = {}

            for branch_bucket in branch_buckets:
                branch_name = branch_bucket["key"]
                user_buckets = branch_bucket.get("users", {}).get("buckets", [])

                for user_bucket in user_buckets:
                    user_name = user_bucket["key"]
                    tokens = self._extract_cli_repository_tokens(user_bucket)

                    # Extract session data from CLI_TOOL_USAGE_TOTAL (filtered aggregation)
                    session_data = user_bucket.get("session_data", {})
                    session_doc_count = int(session_data.get("doc_count", 0))

                    # Rows with no real CLI session are noise from LLM proxy metrics (e.g. Claude
                    # Desktop ping requests) — except proxy tokens recorded without a branch, which a
                    # CLI session on a real branch may own (no X-CodeMie-Branch sent: unborn or
                    # detached HEAD, older CLI). Those are attached after the repository is parsed.
                    if session_doc_count == 0:
                        if branch_name == "":
                            unbranched_tokens_by_user[user_name] = tokens
                        continue

                    row = {
                        "repository": repo_name,
                        "branch": branch_name,
                        "user_name": user_name,
                        **tokens,
                        "session_duration": int(session_data.get("session_duration", {}).get("value", 0)),
                        "total_lines_added": int(session_data.get("total_lines_added", {}).get("value", 0)),
                    }
                    rows.append(row)
                    session_rows_by_user.setdefault(user_name, []).append(row)

            for user_name, tokens in unbranched_tokens_by_user.items():
                self._attach_unbranched_tokens(
                    rows, session_rows_by_user.get(user_name, []), repo_name, user_name, tokens
                )

        # Sorting removed - now handled by pipeline for consistent pagination across pages

        logger.debug(
            f"Parsed cli-repositories result: total_repo_buckets={len(repo_buckets)}, total_rows_flattened={len(rows)}"
        )
        return rows

    @staticmethod
    def _extract_cli_repository_tokens(user_bucket: dict) -> dict[str, int]:
        """Token sums from CLI_LLM_USAGE_TOTAL (filtered aggregation) for one user bucket."""
        token_data = user_bucket.get("token_data", {})
        return {
            "input_tokens": int(token_data.get("input_tokens", {}).get("value", 0)),
            "cache_creation_tokens": int(token_data.get("cache_creation_tokens", {}).get("value", 0)),
            "cache_read_tokens": int(token_data.get("cache_read_tokens", {}).get("value", 0)),
            "output_tokens": int(token_data.get("output_tokens", {}).get("value", 0)),
        }

    @staticmethod
    def _attach_unbranched_tokens(
        rows: list[dict], user_rows: list[dict], repository: str, user_name: str, tokens: dict[str, int]
    ) -> None:
        """Attach branch-less proxy tokens to the user's only session row in this repository.

        With no session row the tokens are proxy-only noise and stay dropped. With several session
        branches the owner is ambiguous, so the tokens are kept on their own branch-less row rather
        than guessed or lost.
        """
        if not user_rows:
            return
        if len(user_rows) == 1:
            for field, value in tokens.items():
                user_rows[0][field] += value
            return
        rows.append(
            {
                "repository": repository,
                "branch": "",
                "user_name": user_name,
                **tokens,
                "session_duration": 0,
                "total_lines_added": 0,
            }
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/codemie/service/analytics/handlers/test_cli_handler.py -v`
Expected: all PASS, including the existing `TestCLIRepositories` pagination/sorting tests.

- [ ] **Step 5: Lint**

Run: `make ruff`
Expected: no remaining violations.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/analytics/handlers/cli/handler.py tests/codemie/service/analytics/handlers/test_cli_handler.py
git commit -m "EPMCDME-16530: Keep unbranched proxy tokens in CLI repositories analytics

Proxy token records without X-CodeMie-Branch are stored with branch \"\".
cli-repositories skipped that bucket because it has no CLI session document,
so sessions in repositories without commits (or on a detached HEAD) showed
zero tokens on their real-branch row. Attach those tokens to the user's
session row in the same repository; keep the proxy-only noise filter.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
```

### Task 2: Gates and live verification

- [ ] **Step 1: Full gates**

Run: `make ruff && make test`
Expected: both succeed.

- [ ] **Step 2: Live check (optional; needs ES access)**

Run: `make test-cli-analytics-live` if configured, or on the preview environment query `GET /v1/analytics/cli-repositories?time_period=last_7_days` and confirm a known unborn-HEAD session (reproduce with CLI 0.16.2: `git init` → `codemie-claude --model haiku -p "Reply with OK"`) now shows non-zero tokens on its `master`/`main` row.

### Task 3: Merge request

- [ ] **Step 1: Open the MR via the `sdlc-factory:mr-creator` skill** (only when the user asks): title `EPMCDME-16530: Keep unbranched proxy tokens in CLI repositories analytics`; body with the A/B table and fold-rule table from "Background", and a link to the companion CLI PR.

## Out of scope (follow-up candidates, not verified)

- `CLIClassificationEngine._build_cli_repository_classifications` (CLI user-detail repository breakdown) merges only `"HEAD"` with `""` (`_normalize_branch`) and then filters `sessions > 0`. An unborn-HEAD session (proxy `""`, session `master`) may lose cost there in the same way. Check before raising a separate ticket.
