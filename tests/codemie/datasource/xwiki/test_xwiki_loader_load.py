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

from unittest.mock import patch

import pytest

from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
from codemie.datasource.loader.xwiki_loader import (
    Metadata,
    CONTENT_FORMAT_RAW,
    CONTENT_FORMAT_RENDERED,
    XWikiLoader,
)
from codemie_tools.core.project_management.xwiki.models import XWikiConfig

PAGE = {
    "id": "xwiki:KB.Formatting",
    "name": "Formatting",
    "title": "Formatting",
    "content": "= Heading =\n\nSome **bold** text",
    "version": "1.1",
    "modified": 1785836295000,
    "author": "XWiki.AdminAdmin",
    "xwikiAbsoluteUrl": "http://xwiki:8080/bin/view/KB/Formatting",
}


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
    )


def test_rendered_html_is_converted_to_markdown(loader):
    html = "<h1>Heading</h1><p>Some <strong>bold</strong> text</p>"
    with patch.object(loader, "_get_text", return_value=html):
        text, fmt = loader._resolve_content("KB", PAGE)
    assert fmt == CONTENT_FORMAT_RENDERED
    assert "# Heading" in text
    assert "**bold**" in text


def test_falls_back_to_raw_when_rendering_is_unavailable(loader):
    with patch.object(loader, "_get_text", return_value=None):
        text, fmt = loader._resolve_content("KB", PAGE)
    assert fmt == CONTENT_FORMAT_RAW
    assert text == PAGE["content"]


def test_document_carries_all_nine_metadata_keys(loader):
    with patch.object(loader, "_get_text", return_value=None):
        doc = loader._to_document("KB", PAGE)
    assert doc.metadata[Metadata.SOURCE.value] == PAGE["xwikiAbsoluteUrl"]
    assert doc.metadata[Metadata.MODIFIED.value] == 1785836295000
    assert doc.metadata[Metadata.CONTENT_FORMAT.value] == CONTENT_FORMAT_RAW
    for key in ("page_id", "space", "wiki", "title", "version", "author"):
        assert key in doc.metadata


def test_empty_pages_are_skipped_and_counted(loader):
    empty = {**PAGE, "content": "", "name": "Empty"}
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "Empty"}])),
        patch.object(loader, "_fetch_page", return_value=empty),
        patch.object(loader, "_get_text", return_value=None),
    ):
        docs = list(loader.lazy_load())
    assert docs == []
    assert loader.get_load_stats()[XWikiLoader.SKIPPED_DOCUMENTS_KEY] == 1


def test_a_single_failing_page_does_not_kill_the_run(loader):
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}, {"name": "B"}])),
        patch.object(loader, "_fetch_page", side_effect=[ConnectionException("xWiki", "boom"), PAGE]),
        patch.object(loader, "_get_text", return_value=None),
    ):
        docs = list(loader.lazy_load())
    assert len(docs) == 1
    assert loader.get_load_stats()[XWikiLoader.FAILED_DOCUMENTS_KEY] == 1


def test_auth_failure_aborts_the_whole_crawl(loader):
    """A 403 is a configuration problem for the crawl, not one bad page."""
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}])),
        patch.object(loader, "_fetch_page", side_effect=UnauthorizedException(datasource_type="xWiki")),
    ):
        with pytest.raises(UnauthorizedException):
            list(loader.lazy_load())


def test_failure_rate_above_the_threshold_fails_the_run(loader):
    loader.max_failed_pages_floor = 1
    loader.max_failed_pages_ratio = 0.1
    summaries = [{"name": f"P{i}"} for i in range(4)]
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)),
        patch.object(loader, "_fetch_page", side_effect=ConnectionException("xWiki", "boom")),
    ):
        with pytest.raises(ConnectionException):
            list(loader.lazy_load())


def test_max_pages_marks_the_result_truncated(loader):
    loader.max_pages = 1
    summaries = [{"name": "A"}, {"name": "B"}]
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)),
        patch.object(loader, "_fetch_page", return_value=PAGE),
        patch.object(loader, "_get_text", return_value=None),
    ):
        docs = list(loader.lazy_load())
    assert len(docs) == 1
    assert loader.get_load_stats()["truncated"] is True


def test_pages_from_descendant_spaces_get_distinct_sources(loader):
    """KB.Onboarding exists as a page in KB AND as a space whose home is KB.Onboarding.WebHome.

    Both must be indexed, and their `source` values must differ: _split_documents keys chunk
    identity on source, so a collision silently overwrites the other page's chunks.
    """
    page_in_kb = {
        **PAGE,
        "name": "Onboarding",
        "id": "xwiki:KB.Onboarding",
        "xwikiAbsoluteUrl": "http://xwiki:8080/bin/view/KB/Onboarding",
    }
    space_home = {
        **PAGE,
        "name": "WebHome",
        "id": "xwiki:KB.Onboarding.WebHome",
        "xwikiAbsoluteUrl": "http://xwiki:8080/bin/view/KB/Onboarding/",
    }
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB", "KB.Onboarding"])),
        patch.object(
            loader,
            "_iter_page_summaries",
            side_effect=[iter([{"name": "Onboarding"}]), iter([{"name": "WebHome"}])],
        ),
        patch.object(loader, "_fetch_page", side_effect=[page_in_kb, space_home]),
        patch.object(loader, "_get_text", return_value=None),
    ):
        docs = list(loader.lazy_load())

    assert len(docs) == 2
    sources = [d.metadata[Metadata.SOURCE.value] for d in docs]
    assert len(set(sources)) == 2, f"page and space home collided on source: {sources}"
    assert {d.metadata["space"] for d in docs} == {"KB", "KB.Onboarding"}


def test_fetch_remote_stats_propagates_instead_of_reporting_zero(loader):
    with patch.object(loader, "_iter_spaces", side_effect=ConnectionException("xWiki", "boom")):
        with pytest.raises(ConnectionException):
            loader.fetch_remote_stats()


def test_fetch_remote_stats_counts_pages_across_descendant_spaces(loader):
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB", "KB.Onboarding"])),
        patch.object(
            loader, "_iter_page_summaries", side_effect=[iter([{"name": "A"}, {"name": "B"}]), iter([{"name": "C"}])]
        ),
    ):
        stats = loader.fetch_remote_stats()
    assert stats[XWikiLoader.DOCUMENTS_COUNT_KEY] == 3
    assert stats["truncated"] is False


def test_fetch_page_uses_the_encoded_rest_path(loader):
    with patch.object(loader, "_get_json", return_value=PAGE) as get_json:
        loader._fetch_page("KB", "Vacation Policy")
    assert get_json.call_args.args[0] == "/rest/wikis/xwiki/spaces/KB/pages/Vacation%20Policy"


def test_truncation_detected_while_counting_survives_into_load_stats(loader):
    """The base processor calls fetch_remote_stats before lazy_load; lazy_load must not clear it."""
    loader.max_pages = 1
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}, {"name": "B"}])),
    ):
        stats = loader.fetch_remote_stats()
    assert stats["truncated"] is True

    with (
        patch.object(loader, "_iter_spaces", return_value=iter([])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([])),
    ):
        list(loader.lazy_load())
    assert loader.get_load_stats()["truncated"] is True


def test_a_space_where_every_page_fails_aborts_even_below_the_floor(loader):
    """Five failures against a floor of five never exceeded the budget, so the run continued to
    zero documents - after reprocess() had already deleted the previous index."""
    summaries = [{"name": f"P{i}"} for i in range(5)]
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)),
        patch.object(loader, "_fetch_page", side_effect=ConnectionException("xWiki", "boom")),
    ):
        with pytest.raises(ConnectionException):
            list(loader.lazy_load())


def test_failure_budget_uses_the_total_from_stats_not_pages_seen_so_far(loader):
    loader.max_failed_pages_floor = 0
    loader.max_failed_pages_ratio = 0.5
    loader._total_pages = 100  # as fetch_remote_stats would have set it
    summaries = [{"name": f"P{i}"} for i in range(4)]
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)),
        patch.object(
            loader,
            "_fetch_page",
            side_effect=[
                ConnectionException("xWiki", "boom"),
                PAGE,
                PAGE,
                PAGE,
            ],
        ),
        patch.object(loader, "_get_text", return_value=None),
    ):
        docs = list(loader.lazy_load())
    # 1 failure against a budget of 50 (0.5 * 100) must not abort; with `seen` it would have been 2.
    assert len(docs) == 3
    assert loader.get_load_stats()[XWikiLoader.FAILED_DOCUMENTS_KEY] == 1


def test_end_of_crawl_budget_catches_a_crawl_much_smaller_than_the_counted_total(loader):
    """Mid-crawl the budget uses the total from stats so early failures do not abort a big run.
    If the crawl then turns out far smaller than that total, the lenient budget must not let most
    of the space fail unnoticed."""
    loader.max_failed_pages_floor = 0
    loader.max_failed_pages_ratio = 0.5
    loader._total_pages = 100  # stats counted 100, but spaces disappeared before the crawl
    summaries = [{"name": f"P{i}"} for i in range(10)]
    fetches = [ConnectionException("xWiki", "boom")] * 9 + [PAGE]
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter(summaries)),
        patch.object(loader, "_fetch_page", side_effect=fetches),
        patch.object(loader, "_get_text", return_value=None),
    ):
        with pytest.raises(ConnectionException):
            list(loader.lazy_load())


def test_truncation_flag_is_cleared_when_a_later_count_fits(loader):
    """fetch_remote_stats assigns the flag in both branches, so a reused loader cannot report a
    stale truncated=True."""
    loader.max_pages = 1
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}, {"name": "B"}])),
    ):
        assert loader.fetch_remote_stats()["truncated"] is True

    loader.max_pages = 5000
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "A"}])),
    ):
        assert loader.fetch_remote_stats()["truncated"] is False
    assert loader.get_load_stats()["truncated"] is False
