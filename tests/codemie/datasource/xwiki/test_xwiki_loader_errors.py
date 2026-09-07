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

from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_core.documents import Document

from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
    )


def _response(status_code=200, json_data=None, text="", raise_on_json=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if raise_on_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


def test_basic_auth_header_is_built_from_credentials(loader):
    assert loader._auth_headers()["Authorization"].startswith("Basic ")


def test_basic_is_the_only_auth_path_even_when_use_bearer_is_set():
    """XWIKI_FIELDS never persists use_bearer, so a Bearer branch would be unreachable code that
    reads as supported. Spec section 9: do not build a second auth path."""
    loader = XWikiLoader(config=XWikiConfig(url="http://x", username="u", token="t", use_bearer=True), space="KB")
    assert loader._auth_headers()["Authorization"].startswith("Basic ")


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failures_raise_unauthorized(loader, status_code):
    with patch("httpx.Client.get", return_value=_response(status_code=status_code)):
        with pytest.raises(UnauthorizedException):
            loader._get_json("/rest/wikis")


def test_unexpected_status_raises_connection_error_hinting_at_the_base_url(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=404, text="Not Found")):
        with pytest.raises(ConnectionException) as exc:
            loader._get_json("/rest/wikis")
    assert "/xwiki" in str(exc.value)


def test_html_body_on_200_raises_connection_error(loader):
    with patch("httpx.Client.get", return_value=_response(raise_on_json=True, text="<html>")):
        with pytest.raises(ConnectionException):
            loader._get_json("/rest/wikis")


def test_transport_failure_becomes_a_connection_error(loader):
    with patch("httpx.Client.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(ConnectionException):
            loader._get_json("/rest/wikis")


def test_get_text_returns_none_on_non_200_instead_of_raising(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=302, text="")):
        assert loader._get_text("/bin/get/KB/Formatting") is None


def test_get_text_returns_none_on_an_empty_body(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=200, text="   ")):
        assert loader._get_text("/bin/get/KB/Formatting") is None


def test_get_text_returns_html_on_success(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=200, text="<h1>Hi</h1>")):
        assert loader._get_text("/bin/get/KB/Formatting") == "<h1>Hi</h1>"


def test_timeout_is_retried_once_then_raises(loader):
    with patch("httpx.Client.get", side_effect=httpx.ConnectTimeout("slow")) as get:
        with pytest.raises(ConnectionException):
            loader._get_json("/rest/wikis")
    assert get.call_count == 2


def test_a_transient_timeout_recovers_on_the_retry(loader):
    ok = _response(status_code=200, json_data={"spaces": []})
    with patch("httpx.Client.get", side_effect=[httpx.ConnectTimeout("slow"), ok]) as get:
        assert loader._get_json("/rest/wikis") == {"spaces": []}
    assert get.call_count == 2


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_retryable_statuses_are_retried_once(loader, status_code):
    ok = _response(status_code=200, json_data={"ok": True})
    with patch("httpx.Client.get", side_effect=[_response(status_code=status_code), ok]) as get:
        assert loader._get_json("/rest/wikis") == {"ok": True}
    assert get.call_count == 2


@pytest.mark.parametrize("status_code", [400, 404])
def test_client_errors_are_not_retried(loader, status_code):
    """A 4xx will not change on a second attempt; retrying only doubles the latency."""
    with patch("httpx.Client.get", return_value=_response(status_code=status_code)) as get:
        with pytest.raises(ConnectionException):
            loader._get_json("/rest/wikis")
    assert get.call_count == 1


def test_auth_failures_are_not_retried(loader):
    with patch("httpx.Client.get", return_value=_response(status_code=403)) as get:
        with pytest.raises(UnauthorizedException):
            loader._get_json("/rest/wikis")
    assert get.call_count == 1


def test_one_client_is_reused_across_requests_and_closed_after_the_entry_point(loader):
    """The loader must build a single httpx.Client and reuse it for every request of an entry
    point, then close it - not open a fresh connection per request."""
    client = MagicMock()
    client.is_closed = False
    client.get.side_effect = [
        _response(status_code=200, json_data={"spaces": [{"id": "xwiki:KB"}]}),  # /spaces listing
        _response(status_code=200, json_data={"pageSummaries": []}),  # KB pages listing
    ]
    with patch("codemie.datasource.loader.xwiki_loader.httpx.Client", return_value=client) as ctor:
        loader.fetch_remote_stats()

    assert ctor.call_count == 1  # one client for the whole crawl...
    assert client.get.call_count == 2  # ...reused across every request...
    client.close.assert_called_once()  # ...and closed when the entry point finished.


def test_abandoning_the_lazy_load_generator_closes_the_client(loader):
    """A consumer that walks away mid-crawl (GeneratorExit) must not leak the socket."""
    client = MagicMock()
    client.is_closed = False
    loader._client = client  # simulate a client already opened by the crawl
    doc = Document(page_content="body", metadata={})
    with (
        patch.object(loader, "_iter_spaces", return_value=iter(["KB"])),
        patch.object(loader, "_iter_page_summaries", return_value=iter([{"name": "P1"}, {"name": "P2"}])),
        patch.object(loader, "_load_page", return_value=doc),
    ):
        gen = loader.lazy_load()
        assert next(gen) is doc  # first page emitted, crawl still in progress
        gen.close()  # consumer abandons iteration

    client.close.assert_called_once()
