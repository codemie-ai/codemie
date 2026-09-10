# Confluence Loader Retry/Backoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a single transient 504 (or 502/503) from aborting an entire Confluence indexing run by retrying `_search_content_by_cql` with exponential backoff before propagating failure.

**Architecture:** Add a module-level `_is_transient_http_error` predicate and a `@retry` decorator (tenacity, already installed) to `ConfluenceDatasourceLoader._search_content_by_cql`. Retry configuration comes from the existing `STORAGE_CONFIG` knobs. Non-retryable errors (4xx) fail immediately. Three mock-based tests are appended to the existing test class file.

**Tech Stack:** Python, tenacity (`retry`, `stop_after_attempt`, `wait_exponential`, `retry_if_exception`, `before_sleep_log`), pytest, `unittest.mock`

**Spec:** `docs/superpowers/tasks/2026-09-09-EPMCDME-14732-confluence-retry-backoff/spec.md`

## Global Constraints

- No new dependencies — `tenacity` and `requests` are already declared.
- No new config fields — use `STORAGE_CONFIG.indexing_max_retries`, `indexing_error_retry_wait_min_seconds`, `indexing_error_retry_wait_max_seconds`.
- Retry only HTTP 502, 503, 504 — not 4xx, not network-level exceptions unrelated to gateway timeouts.
- `reraise=True` is required — original `HTTPError` must propagate after retries are exhausted.
- Tests are mock-based — no live Confluence instance.
- `make ruff` must pass after all changes.

---

### Task 1: Write failing retry tests

**Test-first: yes — three tests in `TestSearchContentByCqlRetry` that fail because no retry logic exists yet**

**Files:**
- Modify: `tests/codemie/datasource/loader/test_confluence_loader.py` (append after line 422)

**Interfaces:**
- Consumes: `ConfluenceDatasourceLoader._search_content_by_cql` (existing)
- Produces: `TestSearchContentByCqlRetry` test class (three tests), consumed by Task 2 for RED→GREEN verification

- [ ] **Step 1: Add `requests` import to the test file**

Open `tests/codemie/datasource/loader/test_confluence_loader.py`. The current imports block (lines 15–17) is:

```python
from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader
```

Change to:

```python
from unittest.mock import MagicMock, call, patch
from unittest.mock import patch as _patch

import pytest
import requests

from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader
```

Wait — `call` may or may not be needed. Keep it minimal. The import block becomes:

```python
from unittest.mock import MagicMock, patch

import pytest
import requests

from codemie.datasource.loader.confluence_loader import ConfluenceDatasourceLoader
```

- [ ] **Step 2: Append `TestSearchContentByCqlRetry` class at the end of the file**

Add the following block after the last line (line 422) of `test_confluence_loader.py`:

```python


class TestSearchContentByCqlRetry:
    """Retry behaviour of _search_content_by_cql for transient gateway errors."""

    @staticmethod
    def _http_error(status_code: int) -> requests.exceptions.HTTPError:
        response = MagicMock()
        response.status_code = status_code
        err = requests.exceptions.HTTPError(response=response)
        return err

    def test_retries_on_504_then_succeeds(self, confluence_loader, mock_confluence_client):
        """A single 504 followed by a 200 resolves successfully after one retry."""
        success_response = {"results": [{"id": "1"}], "_links": {}}
        mock_confluence_client.get.side_effect = [
            self._http_error(504),
            success_response,
        ]

        with patch("tenacity.nap.time.sleep"):
            results, next_url = confluence_loader._search_content_by_cql(cql="type=page")

        assert results == [{"id": "1"}]
        assert next_url == ""
        assert mock_confluence_client.get.call_count == 2

    def test_exhausted_retries_raises(self, confluence_loader, mock_confluence_client):
        """Continuous 504 responses exhaust the retry limit and raise HTTPError."""
        mock_confluence_client.get.side_effect = self._http_error(504)

        with patch("tenacity.nap.time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError) as exc_info:
                confluence_loader._search_content_by_cql(cql="type=page")

        assert exc_info.value.response.status_code == 504

    def test_non_retryable_error_fails_immediately(self, confluence_loader, mock_confluence_client):
        """A 401 Unauthorized fails on the first attempt without retrying."""
        mock_confluence_client.get.side_effect = self._http_error(401)

        with patch("tenacity.nap.time.sleep"):
            with pytest.raises(requests.exceptions.HTTPError) as exc_info:
                confluence_loader._search_content_by_cql(cql="type=page")

        assert exc_info.value.response.status_code == 401
        assert mock_confluence_client.get.call_count == 1
```

- [ ] **Step 3: Run the tests to confirm RED**

```bash
cd /c/epam/codemie-dev/codemie
poetry run pytest tests/codemie/datasource/loader/test_confluence_loader.py::TestSearchContentByCqlRetry -v
```

Expected: all three tests **FAIL** — `test_retries_on_504_then_succeeds` fails because `HTTPError` propagates on the first 504; `test_exhausted_retries_raises` passes accidentally or fails; `test_non_retryable_error_fails_immediately` may pass by accident but the retry count assertion will fail once retry logic exists. The important signal is that `test_retries_on_504_then_succeeds` fails.

If the test file has a syntax error, fix it before moving on.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/codemie/datasource/loader/test_confluence_loader.py
git commit -m "EPMCDME-14732: Add failing retry tests for _search_content_by_cql"
```

---

### Task 2: Implement retry logic in the Confluence loader

**Test-first: yes — tests from Task 1 go GREEN**

**Files:**
- Modify: `src/codemie/datasource/loader/confluence_loader.py`

**Interfaces:**
- Consumes: `STORAGE_CONFIG` from `codemie.datasource.datasources_config` (fields: `indexing_max_retries: int`, `indexing_error_retry_wait_min_seconds: int`, `indexing_error_retry_wait_max_seconds: int`)
- Produces: `_is_transient_http_error(exc: BaseException) -> bool` (module-level predicate); `_search_content_by_cql` decorated with `@retry`

- [ ] **Step 1: Update the imports block**

Current imports (lines 15–21):

```python
from typing import Any, List, Dict, Optional, Iterator

from langchain_community.document_loaders import ConfluenceLoader
from langchain_core.documents import Document

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import logger
```

Replace with:

```python
import logging
from typing import Any, List, Dict, Optional, Iterator

import requests
from langchain_community.document_loaders import ConfluenceLoader
from langchain_core.documents import Document
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import logger
from codemie.datasource.datasources_config import STORAGE_CONFIG
```

- [ ] **Step 2: Add the `_is_transient_http_error` predicate before the class**

After the imports block (after line 21, before `class ConfluenceDatasourceLoader`), insert:

```python

_TRANSIENT_HTTP_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})


def _is_transient_http_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and getattr(exc.response, "status_code", None) in _TRANSIENT_HTTP_STATUS_CODES
    )

```

- [ ] **Step 3: Add the `@retry` decorator to `_search_content_by_cql`**

Current method signature (line 37):

```python
    def _search_content_by_cql(
```

Insert the decorator immediately before it:

```python
    @retry(
        stop=stop_after_attempt(STORAGE_CONFIG.indexing_max_retries),
        wait=wait_exponential(
            multiplier=2,
            min=STORAGE_CONFIG.indexing_error_retry_wait_min_seconds,
            max=STORAGE_CONFIG.indexing_error_retry_wait_max_seconds,
        ),
        retry=retry_if_exception(_is_transient_http_error),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _search_content_by_cql(
```

The complete updated file should look like:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from typing import Any, List, Dict, Optional, Iterator

import requests
from langchain_community.document_loaders import ConfluenceLoader
from langchain_core.documents import Document
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import logger
from codemie.datasource.datasources_config import STORAGE_CONFIG

_TRANSIENT_HTTP_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})


def _is_transient_http_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and getattr(exc.response, "status_code", None) in _TRANSIENT_HTTP_STATUS_CODES
    )


class ConfluenceDatasourceLoader(ConfluenceLoader, BaseDatasourceLoader):
    def fetch_remote_stats(self) -> dict[str, Any]:
        response = self.confluence.cql(self.cql, start=0, limit=1)
        if not isinstance(response, dict):
            raise ValueError("Cannot retrieve data with provided configuration")
        pages_count = response['totalSize']
        total_documents = pages_count  # No extra logic for now
        return {
            self.DOCUMENTS_COUNT_KEY: pages_count,
            self.TOTAL_DOCUMENTS_KEY: total_documents,
            self.SKIPPED_DOCUMENTS_KEY: total_documents - pages_count,
        }

    @retry(
        stop=stop_after_attempt(STORAGE_CONFIG.indexing_max_retries),
        wait=wait_exponential(
            multiplier=2,
            min=STORAGE_CONFIG.indexing_error_retry_wait_min_seconds,
            max=STORAGE_CONFIG.indexing_error_retry_wait_max_seconds,
        ),
        retry=retry_if_exception(_is_transient_http_error),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _search_content_by_cql(
        self,
        cql: str,
        include_archived_spaces: Optional[bool] = None,
        next_url: str = "",
        **kwargs: Any,
    ) -> tuple[List[dict], str]:
        """Overriden to fix the bug.
        See https://github.com/langchain-ai/langchain/commit/0d20c314dd0508ea956482fbdd6ce7854b85fc01
        (!) IMPORTANT. Remove once underlying langchain_community is updated
        """
        if next_url:
            response = self.confluence.get(next_url)
        else:
            url = "rest/api/content/search"

            params: Dict[str, Any] = {"cql": cql}
            params.update(kwargs)
            if include_archived_spaces is not None:
                params["includeArchivedSpaces"] = include_archived_spaces

            response = self.confluence.get(url, params=params)

        return response.get("results", []), response.get("_links", {}).get("next", "")

    def lazy_load(self) -> Iterator[Document]:
        """Stream the CQL result set, following the `_links.next` URL Confluence returns.

        Yields:
            Document: one per page produced by `process_pages`.
        """
        expand = ",".join(
            [
                self.content_format.value,
                "version",
                *(["metadata.labels"] if self.include_labels else []),
            ]
        )
        next_url = ""
        loaded = 0

        while True:
            pages, next_url = self._search_content_by_cql(
                cql=self.cql,
                include_archived_spaces=self.include_archived_content,
                next_url=next_url,
                limit=self.limit,
                expand=expand,
            )

            if not pages:
                logger.info(f"Confluence loader: empty response, total pages loaded={loaded}")
                break

            yield from self.process_pages(
                pages,
                include_restricted_content=self.include_restricted_content,
                include_attachments=self.include_attachments,
                include_comments=self.include_comments,
                include_labels=self.include_labels,
                content_format=self.content_format,
                ocr_languages=self.ocr_languages,
                keep_markdown_format=self.keep_markdown_format,
                keep_newlines=self.keep_newlines,
            )
            loaded += len(pages)

            if not next_url:
                logger.info(f"Confluence loader: no next link, total pages loaded={loaded}")
                break
```

- [ ] **Step 4: Run the new tests to confirm GREEN**

```bash
cd /c/epam/codemie-dev/codemie
poetry run pytest tests/codemie/datasource/loader/test_confluence_loader.py::TestSearchContentByCqlRetry -v
```

Expected: all three tests **PASS**.

- [ ] **Step 5: Run the full loader test suite to confirm no regressions**

```bash
poetry run pytest tests/codemie/datasource/loader/test_confluence_loader.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run lint**

```bash
make ruff
```

Expected: no violations. If ruff reports import ordering issues, it will auto-fix — re-run `make ruff` once to confirm clean.

- [ ] **Step 7: Commit the implementation**

```bash
git add src/codemie/datasource/loader/confluence_loader.py
git commit -m "EPMCDME-14732: Retry _search_content_by_cql on transient HTTP 502/503/504"
```
