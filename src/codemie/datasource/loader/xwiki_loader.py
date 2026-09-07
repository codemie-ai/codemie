# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import base64
import logging
from enum import Enum
from typing import Any, Iterator
from urllib.parse import quote

import httpx
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from markdownify import markdownify

from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

logger = logging.getLogger(__name__)

DATASOURCE_TYPE = "xWiki"


class Metadata(str, Enum):
    """Keys of the metadata dict attached to every loaded page.

    Inherits from str so the values serialise as plain strings once the
    documents reach Elasticsearch.
    """

    SOURCE = "source"
    PAGE_ID = "page_id"
    SPACE = "space"
    WIKI = "wiki"
    TITLE = "title"
    MODIFIED = "modified"
    VERSION = "version"
    AUTHOR = "author"
    CONTENT_FORMAT = "content_format"


CONTENT_FORMAT_RENDERED = "rendered"
CONTENT_FORMAT_RAW = "raw"


class XWikiLoader(BaseLoader, BaseDatasourceLoader):
    """Loads pages of one xWiki space and all of its descendant spaces.

    Note on path building: the tools layer's ``build_spaces_path`` is deliberately not reused
    because it does no percent-encoding, and encoding is mandatory here — a page named
    "Vacation Policy" is unreachable without it.
    """

    _BASE_URL_HINT = (
        "Check the xWiki base URL: a 404 on /rest usually means the URL is missing "
        "the /xwiki prefix, or carries one the instance does not use."
    )
    _RETRYABLE_STATUS = (429, 500, 502, 503, 504)
    _MAX_ATTEMPTS = 2

    def __init__(
        self,
        config: XWikiConfig,
        space: str,
        wiki: str = "xwiki",
        page_size: int = 50,
        max_pages: int = 5000,
        timeout_seconds: int = 30,
        max_failed_pages_floor: int = 5,
        max_failed_pages_ratio: float = 0.1,
    ):
        self.config = config
        self.space = space
        self.wiki = wiki
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.max_failed_pages_floor = max_failed_pages_floor
        self.max_failed_pages_ratio = max_failed_pages_ratio
        self._base = (config.url or "").rstrip("/")
        self._skipped = 0
        self._failed = 0
        self._truncated = False
        self._total_pages = 0  # set by fetch_remote_stats; the failure budget's denominator
        self._client: httpx.Client | None = None  # one pooled client per entry point; see _http_client

    def lazy_load(self) -> Iterator[Document]:
        # _truncated is deliberately NOT reset: the base processor calls fetch_remote_stats first,
        # and truncation it detected while counting must survive into get_load_stats().
        self._skipped = self._failed = 0
        seen = emitted = 0

        try:
            for space_id in self._iter_spaces():
                for summary in self._iter_page_summaries(space_id):
                    if emitted >= self.max_pages:
                        self._truncated = True
                        logger.warning(
                            "xWiki space %s truncated at max_pages=%s; some pages are not indexed",
                            self.space,
                            self.max_pages,
                        )
                        return
                    seen += 1
                    document = self._load_page(space_id, summary, seen)
                    if document is None:
                        continue

                    emitted += 1
                    yield document

            # Both checks run only once the crawl is complete, because reprocess() has already deleted
            # the previous index by this point and a partial result would silently replace it.
            if seen > 0 and self._failed == seen:
                raise ConnectionException(
                    DATASOURCE_TYPE,
                    f"All {seen} pages of the space failed to load. "
                    "Refusing to replace the existing index with an empty one.",
                )
            # Re-check against the pages actually walked. Mid-crawl the budget uses the total from
            # fetch_remote_stats so a burst of early failures does not abort a large run; if that total
            # turned out to be much larger than the crawl (spaces removed between the two passes), the
            # lenient budget would otherwise let most of the space fail unnoticed.
            self._check_failure_budget_final(seen)
        finally:
            # Runs on normal completion, on the truncation return, on error, and on GeneratorExit
            # when the consumer abandons the generator mid-crawl - so the socket is never leaked.
            self.close()

    def fetch_remote_stats(self) -> dict[str, Any]:
        """xWiki returns no total-count field anywhere, so this enumerates.

        Deliberately does not swallow exceptions: reporting "0 documents" for a broken
        connection is what makes a failed datasource look merely empty.
        """
        count = 0
        try:
            for space_id in self._iter_spaces():
                for _ in self._iter_page_summaries(space_id):
                    count += 1
                    if count >= self.max_pages:
                        self._truncated = True
                        self._total_pages = count
                        return {
                            self.DOCUMENTS_COUNT_KEY: count,
                            self.TOTAL_DOCUMENTS_KEY: count,
                            "truncated": True,
                        }
            # Assigned in both branches so a reused loader instance cannot carry a stale True.
            self._truncated = False
            self._total_pages = count
            return {
                self.DOCUMENTS_COUNT_KEY: count,
                self.TOTAL_DOCUMENTS_KEY: count,
                "truncated": False,
            }
        finally:
            # This entry point may run alone (health check) or before lazy_load (indexing); either
            # way its client is closed here, and lazy_load reopens one via _http_client if it follows.
            self.close()

    def get_load_stats(self) -> dict[str, Any]:
        return {
            self.SKIPPED_DOCUMENTS_KEY: self._skipped,
            self.FAILED_DOCUMENTS_KEY: self._failed,
            "truncated": self._truncated,
        }

    @staticmethod
    def _segments(space: str) -> list[str]:
        return [p.strip() for p in space.split(".") if p.strip()]

    def _rest_space_path(self, space: str) -> str:
        """ "KB.Onboarding" -> "/spaces/KB/spaces/Onboarding" (REST repeats the segment)."""
        return "".join(f"/spaces/{quote(p, safe='')}" for p in self._segments(space))

    def _bin_page_path(self, space: str, name: str) -> str:
        """ "KB.Onboarding", "Checklist" -> "/bin/get/KB/Onboarding/Checklist" (plain slashes)."""
        parts = self._segments(space) + [name]
        return "/bin/get/" + "/".join(quote(p, safe="") for p in parts)

    def _auth_headers(self) -> dict[str, str]:
        """Basic only, by design.

        XWIKI_FIELDS persists url, token and username but not use_bearer, so credentials resolved
        through SettingsService always come back with use_bearer=False. Bearer additionally needs a
        server-side xWiki plugin. Implementing a second auth path here would be unreachable code
        that reads as supported - see spec section 9.

        Note: this sends base64(username:token) on every request. Over a http:// base URL that is
        cleartext on the wire. The scheme is deliberately not gated, because xWiki is typically
        self-hosted on an internal network where http is normal; https is strongly recommended and
        this is called out in the integration field help and the MR description.
        """
        raw = f"{self.config.username or ''}:{self.config.token}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def _http_client(self) -> httpx.Client:
        """One client reused across every request of an entry point, so the pooled connection
        survives the pagination and per-page fetches instead of reconnecting each time.

        Recreated on demand because the two entry points run on the same loader instance in
        sequence (fetch_remote_stats then lazy_load), and each closes the client in its own
        finally; the second entry point simply reopens one here.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=self.timeout_seconds, follow_redirects=False)
        return self._client

    def close(self) -> None:
        """Idempotent. Called from the entry points' finally blocks and by __exit__."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "XWikiLoader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _get(self, path: str, params: dict | None = None, accept: str = "application/json"):
        """One bounded retry on timeout, 429 and 5xx. 4xx is never retried - it will not change."""
        headers = {**self._auth_headers(), "Accept": accept}
        url = f"{self._base}{path}"
        last_error: Exception | None = None

        client = self._http_client()
        for attempt in range(self._MAX_ATTEMPTS):
            has_retry_left = attempt < self._MAX_ATTEMPTS - 1
            try:
                response = client.get(url, params=params, headers=headers)
            except httpx.RequestError as e:
                last_error = e
                if has_retry_left:
                    logger.warning("xWiki request to %s failed (%s); retrying once", path, e)
                    continue
                raise ConnectionException(DATASOURCE_TYPE, f"{e}. {self._BASE_URL_HINT}") from e

            if response.status_code in self._RETRYABLE_STATUS and has_retry_left:
                logger.warning("xWiki request to %s returned %s; retrying once", path, response.status_code)
                continue
            return response

        # Unreachable: the loop either returns a response or raises on the final attempt.
        raise ConnectionException(DATASOURCE_TYPE, f"{last_error}. {self._BASE_URL_HINT}")

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        response = self._get(path, params)
        if response.status_code in (401, 403):
            raise UnauthorizedException(datasource_type=DATASOURCE_TYPE)
        if response.status_code != 200:
            raise ConnectionException(DATASOURCE_TYPE, f"HTTP {response.status_code} for {path}. {self._BASE_URL_HINT}")
        try:
            return response.json()
        except ValueError as e:
            raise ConnectionException(
                DATASOURCE_TYPE,
                f"Expected JSON from {path} but got a non-JSON body. {self._BASE_URL_HINT}",
            ) from e

    def _get_text(self, path: str, params: dict | None = None) -> str | None:
        """Fetch rendered HTML. Returns None when unavailable - the caller falls back to raw."""
        response = self._get(path, params, accept="text/html")
        if response.status_code != 200 or not response.text.strip():
            return None
        return response.text

    def _paginate(self, path: str, result_key: str) -> Iterator[dict[str, Any]]:
        """Offset pagination. xWiki sends no total and no next link, so a short page ends it."""
        start = 0
        while True:
            payload = self._get_json(path, params={"number": self.page_size, "start": start})
            items = payload.get(result_key) or []
            for item in items:
                yield item
            if len(items) < self.page_size:
                return
            start += self.page_size

    def _iter_spaces(self) -> Iterator[str]:
        """Yield the target space and every descendant, as dotted ids without the wiki prefix.

        The flat /spaces listing is what makes this one pass instead of an N-deep walk, but it
        paginates like everything else - an unpaged call can silently drop whole subtrees.
        """
        prefix = f"{self.wiki}:"
        for item in self._paginate(f"/rest/wikis/{quote(self.wiki, safe='')}/spaces", "spaces"):
            space_id = item.get("id") or ""
            if not space_id.startswith(prefix):
                continue
            dotted = space_id[len(prefix) :]
            if dotted == self.space or dotted.startswith(f"{self.space}."):
                yield dotted

    def _iter_page_summaries(self, space_id: str) -> Iterator[dict[str, Any]]:
        path = f"/rest/wikis/{quote(self.wiki, safe='')}{self._rest_space_path(space_id)}/pages"
        yield from self._paginate(path, "pageSummaries")

    def _fetch_page(self, space_id: str, name: str) -> dict[str, Any]:
        """Page summaries carry neither content nor modified, so a per-page GET is mandatory."""
        path = f"/rest/wikis/{quote(self.wiki, safe='')}{self._rest_space_path(space_id)}/pages/{quote(name, safe='')}"
        return self._get_json(path)

    def _resolve_content(self, space_id: str, page: dict[str, Any]) -> tuple[str, str]:
        """Prefer rendered markdown; fall back to raw wiki syntax. Never fail on rendering."""
        html = self._get_text(self._bin_page_path(space_id, page.get("name") or ""), params={"xpage": "plain"})
        if html:
            return markdownify(html, heading_style="ATX").strip(), CONTENT_FORMAT_RENDERED
        logger.warning(
            "xWiki rendered content unavailable for %s in space %s; using raw wiki syntax",
            page.get("name"),
            space_id,
        )
        return (page.get("content") or "").strip(), CONTENT_FORMAT_RAW

    def _to_document(self, space_id: str, page: dict[str, Any]) -> Document:
        text, content_format = self._resolve_content(space_id, page)
        return Document(
            page_content=text,
            metadata={
                Metadata.SOURCE.value: page.get("xwikiAbsoluteUrl"),
                Metadata.PAGE_ID.value: page.get("id"),
                Metadata.SPACE.value: space_id,
                Metadata.WIKI.value: self.wiki,
                Metadata.TITLE.value: page.get("title"),
                Metadata.MODIFIED.value: page.get("modified"),
                Metadata.VERSION.value: page.get("version"),
                Metadata.AUTHOR.value: page.get("author"),
                Metadata.CONTENT_FORMAT.value: content_format,
            },
        )

    def _check_failure_budget(self, seen: int) -> None:
        """Abort rather than index a partial space as a success.

        The denominator is the total page count from fetch_remote_stats when it is known, so the
        ratio arm means what the spec says; `seen` is only a fallback for a bare lazy_load call.
        The all-failed guard exists because a space at or below the floor could otherwise lose
        every page without ever tripping the budget - and reprocess() has already deleted the
        previous index by then.
        """
        self._raise_if_over_budget(seen, self._total_pages or seen)

    def _check_failure_budget_final(self, seen: int) -> None:
        """End-of-crawl re-check whose denominator is what was actually walked."""
        self._raise_if_over_budget(seen, seen)

    def _raise_if_over_budget(self, seen: int, total: int) -> None:
        budget = max(self.max_failed_pages_floor, int(self.max_failed_pages_ratio * total))
        if self._failed > budget:
            raise ConnectionException(
                DATASOURCE_TYPE,
                f"{self._failed} of {seen} pages failed to load"
                + (f" (of {total} total)" if self._total_pages else "")
                + f", above the allowed {budget}. Refusing to index a partial space as a success.",
            )

    def _load_page(self, space_id: str, summary: dict[str, Any], seen: int) -> Document | None:
        """Fetch and convert one page. None means "do not emit" - already counted as failed or skipped."""
        try:
            page = self._fetch_page(space_id, summary.get("name") or "")
            document = self._to_document(space_id, page)
        except UnauthorizedException:
            raise
        except Exception as e:  # noqa: BLE001 - one bad page must not kill the crawl
            self._failed += 1
            logger.warning("Failed to load xWiki page %s in %s: %s", summary.get("name"), space_id, e)
            self._check_failure_budget(seen)
            return None

        if not document.page_content:
            self._skipped += 1
            return None
        return document
