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

from unittest.mock import MagicMock, patch

import pytest
import requests

from codemie.datasource.exceptions import MissingIntegrationException, UnauthorizedException
from codemie.datasource.loader.sharepoint_loader import SharePointAuthConfig, SharePointLoader, _encode_url_path


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_auth_config():
    """SharePointAuthConfig using app (client-credentials) flow."""
    return SharePointAuthConfig(
        auth_type="app",
        tenant_id="test-tenant-id",
        client_id="test-client-id",
        client_secret="test-client-secret",
    )


@pytest.fixture
def oauth_auth_config():
    """SharePointAuthConfig using delegated OAuth flow with a stored token."""
    return SharePointAuthConfig(
        auth_type="oauth",
        access_token="stored-oauth-token",
        expires_at=9999999999,
    )


@pytest.fixture
def loader(app_auth_config):
    """SharePointLoader with app auth, pages+docs+lists enabled."""
    return SharePointLoader(
        site_url="https://tenant.sharepoint.com/sites/MySite",
        auth_config=app_auth_config,
        include_pages=True,
        include_documents=True,
        include_lists=True,
    )


@pytest.fixture
def oauth_loader(oauth_auth_config):
    """SharePointLoader with OAuth auth."""
    return SharePointLoader(
        site_url="https://tenant.sharepoint.com/sites/MySite",
        auth_config=oauth_auth_config,
        include_pages=True,
        include_documents=True,
        include_lists=True,
    )


@pytest.fixture
def single_drive(loader):
    """One-drive fixture: 'Shared Documents' with drive cache pre-populated."""
    drives = [{"id": "d1", "name": "Shared Documents"}]
    loader._drive_library_paths = {"d1": "Shared Documents"}
    return drives


@pytest.fixture
def two_drives(loader):
    """Two-drive fixture: 'Shared Documents' + 'Archive' with drive cache pre-populated."""
    drives = [
        {"id": "d1", "name": "Shared Documents"},
        {"id": "d2", "name": "Archive"},
    ]
    loader._drive_library_paths = {"d1": "Shared Documents", "d2": "Archive"}
    return drives


# ---------------------------------------------------------------------------
# TestSharePointAuthConfig
# ---------------------------------------------------------------------------


class TestSharePointAuthConfig:
    """Tests for the SharePointAuthConfig dataclass."""

    def test_default_values(self):
        """Default auth_type is 'app' and string fields are empty."""
        config = SharePointAuthConfig()

        assert config.auth_type == "app"
        assert config.tenant_id == ""
        assert config.client_id == ""
        assert config.client_secret == ""
        assert config.access_token == ""
        assert config.refresh_token == ""
        assert config.expires_at == 0
        assert config.setting_id is None

    def test_explicit_values_stored(self):
        """All fields are stored exactly as provided."""
        config = SharePointAuthConfig(
            auth_type="oauth",
            tenant_id="tid",
            client_id="cid",
            client_secret="secret",
            access_token="access",
            refresh_token="refresh",
            expires_at=12345,
            setting_id="sid",
        )

        assert config.auth_type == "oauth"
        assert config.tenant_id == "tid"
        assert config.client_id == "cid"
        assert config.client_secret == "secret"
        assert config.access_token == "access"
        assert config.refresh_token == "refresh"
        assert config.expires_at == 12345
        assert config.setting_id == "sid"


# ---------------------------------------------------------------------------
# TestParseSiteUrl
# ---------------------------------------------------------------------------


class TestParseSiteUrl:
    """Tests for __init__ / _parse_site_url."""

    def test_hostname_extracted(self, loader):
        """_site_hostname is set from URL netloc."""
        assert loader._site_hostname == "tenant.sharepoint.com"

    def test_path_extracted(self, loader):
        """_site_path is set from URL path."""
        assert loader._site_path == "/sites/MySite"

    def test_url_with_trailing_slash(self, app_auth_config):
        """Trailing slash in site_url is preserved in _site_path."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/MySite/",
            auth_config=app_auth_config,
        )

        assert loader._site_hostname == "tenant.sharepoint.com"
        assert loader._site_path == "/sites/MySite/"

    def test_default_flags_and_stats(self, loader):
        """Stats counters initialise to zero."""
        assert loader._total_files_found == 0
        assert loader._total_files_processed == 0
        assert loader._total_files_skipped == 0

    def test_default_auth_config_used_when_none(self):
        """Passing auth_config=None falls back to default SharePointAuthConfig."""
        loader = SharePointLoader(
            site_url="https://tenant.sharepoint.com/sites/MySite",
            auth_config=None,
        )

        assert loader.auth_type == "app"
        assert loader.tenant_id == ""


# ---------------------------------------------------------------------------
# TestGetAccessToken
# ---------------------------------------------------------------------------


class TestGetAccessToken:
    """Tests for _get_access_token routing logic."""

    def test_returns_cached_token(self, loader):
        """When _access_token is already set it is returned without any HTTP call."""
        loader._access_token = "cached-token"

        result = loader._get_access_token()

        assert result == "cached-token"

    def test_routes_to_app_token_for_app_auth(self, loader):
        """App auth delegates to _get_app_access_token."""
        loader._access_token = None
        loader.auth_type = "app"

        with patch.object(loader, '_get_app_access_token', return_value="app-token") as mock_app:
            result = loader._get_access_token()

        assert result == "app-token"
        mock_app.assert_called_once()

    def test_routes_to_oauth_token_for_oauth_auth(self, oauth_loader):
        """OAuth auth delegates to _get_oauth_access_token."""
        oauth_loader._access_token = None

        with patch.object(oauth_loader, '_get_oauth_access_token', return_value="oauth-token") as mock_oauth:
            result = oauth_loader._get_access_token()

        assert result == "oauth-token"
        mock_oauth.assert_called_once()


# ---------------------------------------------------------------------------
# TestGetAppAccessToken
# ---------------------------------------------------------------------------


class TestGetAppAccessToken:
    """Tests for _get_app_access_token."""

    @patch('codemie.datasource.loader.sharepoint_loader.requests.post')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_successful_token_acquisition(self, mock_config, mock_post, loader):
        """On 200 response the access_token is stored and returned."""
        mock_config.loader_timeout = 30
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "new-app-token"}
        mock_post.return_value = mock_response

        result = loader._get_app_access_token()

        assert result == "new-app-token"
        assert loader._access_token == "new-app-token"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert loader.tenant_id in call_kwargs[0][0]

    @patch('codemie.datasource.loader.sharepoint_loader.requests.post')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_raises_unauthorized_on_request_exception(self, mock_config, mock_post, loader):
        """requests.RequestException is wrapped as UnauthorizedException."""
        mock_config.loader_timeout = 30
        mock_post.side_effect = requests.exceptions.RequestException("connection error")

        with pytest.raises(UnauthorizedException):
            loader._get_app_access_token()

    @patch('codemie.datasource.loader.sharepoint_loader.requests.post')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_raises_unauthorized_on_http_error(self, mock_config, mock_post, loader):
        """HTTP 401 status (raise_for_status) raises UnauthorizedException."""
        mock_config.loader_timeout = 30
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_post.return_value = mock_response

        with pytest.raises(UnauthorizedException):
            loader._get_app_access_token()

    @patch('codemie.datasource.loader.sharepoint_loader.requests.post')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_post_uses_correct_tenant_url(self, mock_config, mock_post, loader):
        """Token URL contains the configured tenant_id."""
        mock_config.loader_timeout = 30
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_post.return_value = mock_response

        loader._get_app_access_token()

        posted_url = mock_post.call_args[0][0]
        assert "test-tenant-id" in posted_url
        assert "oauth2/v2.0/token" in posted_url


# ---------------------------------------------------------------------------
# TestGetOauthAccessToken
# ---------------------------------------------------------------------------


class TestGetOauthAccessToken:
    """Tests for _get_oauth_access_token."""

    def test_returns_stored_token(self, oauth_loader):
        """When _stored_access_token is present it is returned and cached."""
        result = oauth_loader._get_oauth_access_token()

        assert result == "stored-oauth-token"
        assert oauth_loader._access_token == "stored-oauth-token"

    def test_raises_unauthorized_when_no_stored_token(self, oauth_loader):
        """Empty stored token raises UnauthorizedException."""
        oauth_loader._stored_access_token = None

        with pytest.raises(UnauthorizedException):
            oauth_loader._get_oauth_access_token()

    def test_raises_unauthorized_when_stored_token_empty_string(self, oauth_loader):
        """Empty-string stored token raises UnauthorizedException."""
        oauth_loader._stored_access_token = ""

        with pytest.raises(UnauthorizedException):
            oauth_loader._get_oauth_access_token()


# ---------------------------------------------------------------------------
# TestGetHeaders
# ---------------------------------------------------------------------------


class TestGetHeaders:
    """Tests for _get_headers."""

    def test_returns_bearer_header(self, loader):
        """Authorization header uses Bearer scheme."""
        loader._access_token = "test-token"

        headers = loader._get_headers()

        assert headers["Authorization"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"

    def test_calls_get_access_token(self, loader):
        """_get_headers calls _get_access_token to obtain the token."""
        with patch.object(loader, '_get_access_token', return_value="fetched-token") as mock_get:
            headers = loader._get_headers()

        mock_get.assert_called_once()
        assert headers["Authorization"] == "Bearer fetched-token"


# ---------------------------------------------------------------------------
# TestGetSiteId
# ---------------------------------------------------------------------------


class TestGetSiteId:
    """Tests for _get_site_id."""

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_fetches_and_caches_site_id(self, mock_config, mock_get, loader):
        """Site ID is fetched from Graph API and cached."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        mock_config.loader_timeout = 30
        loader._access_token = "tok"
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "site-abc123"}
        mock_get.return_value = mock_response

        result = loader._get_site_id()

        assert result == "site-abc123"
        assert loader._site_id == "site-abc123"
        mock_get.assert_called_once()

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_returns_cached_site_id(self, mock_config, mock_get, loader):
        """If _site_id is already set, no HTTP call is made."""
        loader._site_id = "cached-site-id"

        result = loader._get_site_id()

        assert result == "cached-site-id"
        mock_get.assert_not_called()

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_raises_unauthorized_on_request_exception(self, mock_config, mock_get, loader):
        """RequestException raises UnauthorizedException."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        mock_config.loader_timeout = 30
        loader._access_token = "tok"
        mock_get.side_effect = requests.exceptions.RequestException("timeout")

        with pytest.raises(UnauthorizedException):
            loader._get_site_id()

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_url_includes_hostname_and_path(self, mock_config, mock_get, loader):
        """The Graph API URL encodes the site hostname and path."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        mock_config.loader_timeout = 30
        loader._access_token = "tok"
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "sid"}
        mock_get.return_value = mock_response

        loader._get_site_id()

        called_url = mock_get.call_args[0][0]
        assert "tenant.sharepoint.com" in called_url
        assert "/sites/MySite" in called_url


# ---------------------------------------------------------------------------
# TestValidateCreds
# ---------------------------------------------------------------------------


class TestValidateCreds:
    """Tests for _validate_creds."""

    def test_app_auth_missing_tenant_id_raises(self, loader):
        """Missing tenant_id raises MissingIntegrationException."""
        loader.tenant_id = ""

        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    def test_app_auth_missing_client_id_raises(self, loader):
        """Missing client_id raises MissingIntegrationException."""
        loader.client_id = ""

        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    def test_app_auth_missing_client_secret_raises(self, loader):
        """Missing client_secret raises MissingIntegrationException."""
        loader.client_secret = ""

        with pytest.raises(MissingIntegrationException):
            loader._validate_creds()

    def test_oauth_missing_token_raises(self, oauth_loader):
        """OAuth flow with no stored token raises MissingIntegrationException."""
        oauth_loader._stored_access_token = None

        with pytest.raises(MissingIntegrationException):
            oauth_loader._validate_creds()

    def test_app_auth_calls_get_site_id(self, loader):
        """Valid app auth credentials lead to _get_site_id being called."""
        with patch.object(loader, '_get_site_id', return_value="site-id") as mock_get_site:
            loader._validate_creds()

        mock_get_site.assert_called_once()

    def test_oauth_auth_calls_get_site_id(self, oauth_loader):
        """Valid OAuth token leads to _get_site_id being called."""
        with patch.object(oauth_loader, '_get_site_id', return_value="site-id") as mock_get_site:
            oauth_loader._validate_creds()

        mock_get_site.assert_called_once()


# ---------------------------------------------------------------------------
# TestMakeGraphRequest
# ---------------------------------------------------------------------------


class TestMakeGraphRequest:
    """Tests for _make_graph_request retry and error handling."""

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_successful_request_returns_json(self, mock_config, mock_get, loader):
        """200 response returns parsed JSON."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 3
        loader._access_token = "tok"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_get.return_value = mock_response

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/sites")

        assert result == {"value": []}

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_404_returns_none(self, mock_config, mock_get, loader):
        """404 response returns None without raising."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 3
        loader._access_token = "tok"
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/sites/bad")

        assert result is None

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_403_returns_none(self, mock_config, mock_get, loader):
        """403 response returns None without raising."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 3
        loader._access_token = "tok"
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        result = loader._make_graph_request("https://graph.microsoft.com/v1.0/sites/forbidden")

        assert result is None

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_401_clears_token_and_retries(self, mock_config, mock_get, loader):
        """401 clears _access_token and retries up to max_retries."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 1
        loader._access_token = "expired-token"

        first_response = MagicMock()
        first_response.status_code = 401

        second_response = MagicMock()
        second_response.status_code = 200
        second_response.json.return_value = {"value": ["ok"]}

        mock_get.side_effect = [first_response, second_response]

        with patch.object(loader, '_get_app_access_token', return_value="fresh-token"):
            result = loader._make_graph_request("https://example.com/resource")

        assert result == {"value": ["ok"]}
        assert mock_get.call_count == 2

    @patch('codemie.datasource.loader.sharepoint_loader.time.sleep')
    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_429_sleeps_and_retries(self, mock_config, mock_get, mock_sleep, loader):
        """429 reads Retry-After header, sleeps, and retries."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 1
        loader._access_token = "tok"

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "10"}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {"value": []}

        mock_get.side_effect = [rate_limited, ok_response]

        result = loader._make_graph_request("https://example.com/resource")

        mock_sleep.assert_called_once_with(10)
        assert result == {"value": []}

    @patch('codemie.datasource.loader.sharepoint_loader.time.sleep')
    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_429_caps_retry_after_at_max(self, mock_config, mock_get, mock_sleep, loader):
        """Retry-After value is capped at _MAX_RETRY_AFTER_SECONDS (60)."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 1
        loader._access_token = "tok"

        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "999"}

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.json.return_value = {}

        mock_get.side_effect = [rate_limited, ok_response]

        loader._make_graph_request("https://example.com/resource")

        mock_sleep.assert_called_once_with(60)

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_request_exception_retries_then_returns_none(self, mock_config, mock_get, loader):
        """RequestException triggers retry; after max_retries exhausted returns None."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 2
        loader._access_token = "tok"
        mock_get.side_effect = requests.exceptions.RequestException("network error")

        result = loader._make_graph_request("https://example.com/resource")

        assert result is None
        assert mock_get.call_count == 3  # initial + 2 retries

    @patch('codemie.datasource.loader.sharepoint_loader.requests.get')
    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_401_exceeds_max_retries_stops_retrying(self, mock_config, mock_get, loader):
        """401 responses beyond max_retries do not retry indefinitely."""
        mock_config.loader_timeout = 30
        mock_config.max_retries = 0
        loader._access_token = "tok"

        response_401 = MagicMock()
        response_401.status_code = 401
        response_401.raise_for_status.side_effect = requests.exceptions.HTTPError("401")
        mock_get.return_value = response_401

        # HTTPError is caught by the RequestException handler; returns None when retries exhausted
        result = loader._make_graph_request("https://example.com/resource")

        assert result is None
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# TestShouldProcessPage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestFetchPageDetails
# ---------------------------------------------------------------------------


class TestFetchPageDetails:
    """Tests for _fetch_page_details."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_returns_detailed_page_when_available(self, mock_config, loader):
        """When _make_graph_request returns data the detail is returned."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        page_detail = {"id": "p1", "title": "My Page", "canvasLayout": {}}

        with patch.object(loader, '_make_graph_request', return_value=page_detail):
            result = loader._fetch_page_details("site-id", "p1", {"id": "p1", "title": "Fallback"})

        assert result == page_detail

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_falls_back_to_original_page_on_none(self, mock_config, loader):
        """When _make_graph_request returns None the original page dict is returned."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        original = {"id": "p1", "title": "Fallback"}

        with patch.object(loader, '_make_graph_request', return_value=None):
            result = loader._fetch_page_details("site-id", "p1", original)

        assert result is original


# ---------------------------------------------------------------------------
# TestCreatePageDict
# ---------------------------------------------------------------------------


class TestCreatePageDict:
    """Tests for _create_page_dict."""

    def test_builds_correct_dict(self, loader):
        """All expected fields are populated from page_data."""
        page_data = {
            "id": "pid",
            "title": "My Title",
            "webUrl": "https://tenant.sharepoint.com/SitePages/Home.aspx",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "lastModifiedDateTime": "2024-06-01T00:00:00Z",
        }

        result = loader._create_page_dict(page_data, "Some content")

        assert result["type"] == "page"
        assert result["id"] == "pid"
        assert result["title"] == "My Title"
        assert result["content"] == "Some content"
        assert result["url"] == "https://tenant.sharepoint.com/SitePages/Home.aspx"
        assert result["created"] == "2024-01-01T00:00:00Z"
        assert result["modified"] == "2024-06-01T00:00:00Z"

    def test_missing_fields_use_defaults(self, loader):
        """Missing page_data fields default to empty strings / None."""
        result = loader._create_page_dict({}, "content")

        assert result["id"] is None
        assert result["title"] == ""
        assert result["url"] == ""


# ---------------------------------------------------------------------------
# TestStripHtmlToText
# ---------------------------------------------------------------------------


class TestStripHtmlToText:
    """Tests for _strip_html_to_text."""

    def test_removes_html_tags(self, loader):
        """HTML tags are stripped leaving text."""
        result = loader._strip_html_to_text("<p>Hello <b>World</b></p>")

        assert "<" not in result
        assert "Hello" in result
        assert "World" in result

    def test_normalises_whitespace(self, loader):
        """Multiple whitespace characters are collapsed to a single space."""
        result = loader._strip_html_to_text("<p>Hello   World</p>")

        assert "  " not in result

    def test_empty_string_returns_empty(self, loader):
        """Empty input returns empty string."""
        assert loader._strip_html_to_text("") == ""

    def test_plain_text_unchanged(self, loader):
        """Input without HTML tags is returned stripped."""
        result = loader._strip_html_to_text("  plain text  ")

        assert result == "plain text"


# ---------------------------------------------------------------------------
# TestExtractWebpartText
# ---------------------------------------------------------------------------


class TestExtractWebpartText:
    """Tests for _extract_webpart_text."""

    def test_extracts_text_from_inner_html(self, loader):
        """innerHtml is stripped to plain text."""
        webpart = {"innerHtml": "<p>Hello World</p>"}

        result = loader._extract_webpart_text(webpart)

        assert result == "Hello World"

    def test_returns_none_when_no_inner_html(self, loader):
        """Missing innerHtml key returns None."""
        assert loader._extract_webpart_text({}) is None

    def test_returns_none_when_inner_html_empty(self, loader):
        """Empty innerHtml string returns None."""
        assert loader._extract_webpart_text({"innerHtml": ""}) is None

    def test_returns_none_when_inner_html_only_tags(self, loader):
        """innerHtml that strips to empty string returns None."""
        assert loader._extract_webpart_text({"innerHtml": "<br/><br/>"}) is None


# ---------------------------------------------------------------------------
# TestExtractCanvasSections
# ---------------------------------------------------------------------------


class TestExtractCanvasSections:
    """Tests for _extract_canvas_sections."""

    def test_traverses_sections_columns_webparts(self, loader):
        """Text from nested webparts is collected."""
        canvas = {
            "horizontalSections": [
                {
                    "columns": [
                        {
                            "webparts": [
                                {"innerHtml": "<p>Section 1</p>"},
                                {"innerHtml": "<p>Section 2</p>"},
                            ]
                        }
                    ]
                }
            ]
        }

        result = loader._extract_canvas_sections(canvas)

        assert "Section 1" in result
        assert "Section 2" in result

    def test_empty_canvas_returns_empty_list(self, loader):
        """Canvas with no sections returns empty list."""
        result = loader._extract_canvas_sections({})

        assert result == []

    def test_webparts_without_inner_html_skipped(self, loader):
        """Webparts with no innerHtml are not added to text_parts."""
        canvas = {"horizontalSections": [{"columns": [{"webparts": [{}]}]}]}

        result = loader._extract_canvas_sections(canvas)

        assert result == []

    def test_multiple_sections_all_collected(self, loader):
        """Text from multiple sections is collected in order."""
        canvas = {
            "horizontalSections": [
                {"columns": [{"webparts": [{"innerHtml": "<p>A</p>"}]}]},
                {"columns": [{"webparts": [{"innerHtml": "<p>B</p>"}]}]},
            ]
        }

        result = loader._extract_canvas_sections(canvas)

        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestExtractPageContent
# ---------------------------------------------------------------------------


class TestExtractPageContent:
    """Tests for _extract_page_content."""

    def test_includes_title_as_h1(self, loader):
        """Title is prepended as '# Title'."""
        page = {"title": "My Page", "canvasLayout": {}}

        result = loader._extract_page_content(page)

        assert "# My Page" in result

    def test_uses_canvas_content(self, loader):
        """Canvas layout text is included in output."""
        page = {
            "title": "Title",
            "canvasLayout": {
                "horizontalSections": [{"columns": [{"webparts": [{"innerHtml": "<p>Canvas text</p>"}]}]}]
            },
        }

        result = loader._extract_page_content(page)

        assert "Canvas text" in result

    def test_falls_back_to_description_when_only_title(self, loader):
        """When only a title exists, description is appended."""
        page = {
            "title": "My Page",
            "canvasLayout": {},
            "description": "Page description",
        }

        result = loader._extract_page_content(page)

        assert "Page description" in result

    def test_no_description_fallback_when_canvas_has_content(self, loader):
        """Description is not added when canvas already provides content."""
        page = {
            "title": "My Page",
            "canvasLayout": {
                "horizontalSections": [{"columns": [{"webparts": [{"innerHtml": "<p>Rich content</p>"}]}]}]
            },
            "description": "Should not appear",
        }

        result = loader._extract_page_content(page)

        assert "Should not appear" not in result

    def test_empty_page_returns_empty_string(self, loader):
        """Page with no title, canvas, or description yields empty string."""
        result = loader._extract_page_content({})

        assert result == ""


# ---------------------------------------------------------------------------
# TestLoadSitePages
# ---------------------------------------------------------------------------


class TestLoadSitePages:
    """Tests for _load_site_pages pagination and filtering."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_yields_pages_with_content(self, mock_config, loader):
        """Pages that have content are yielded."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        page = {"id": "p1", "title": "Home", "webUrl": "https://tenant/Home"}

        def fake_graph(url):
            if "?$expand" in url:
                return {
                    "id": "p1",
                    "title": "Home",
                    "webUrl": "https://tenant/Home",
                    "canvasLayout": {
                        "horizontalSections": [{"columns": [{"webparts": [{"innerHtml": "<p>Content</p>"}]}]}]
                    },
                }
            return {"value": [page]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            results = list(loader._load_site_pages())

        assert len(results) == 1
        assert results[0]["title"] == "Home"

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_pagination_follows_next_link(self, mock_config, loader):
        """@odata.nextLink triggers subsequent requests."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        page1 = {"id": "p1", "title": "Page1", "webUrl": "https://t/p1"}
        page2 = {"id": "p2", "title": "Page2", "webUrl": "https://t/p2"}

        call_count = [0]

        def fake_graph(url):
            if "?$expand" in url:
                pid = "p1" if "p1" in url else "p2"
                return {
                    "id": pid,
                    "title": f"Page{pid[1]}",
                    "webUrl": f"https://t/{pid}",
                    "canvasLayout": {
                        "horizontalSections": [{"columns": [{"webparts": [{"innerHtml": f"<p>Content {pid}</p>"}]}]}]
                    },
                }
            call_count[0] += 1
            if call_count[0] == 1:
                return {"value": [page1], "@odata.nextLink": "https://graph.microsoft.com/next"}
            return {"value": [page2]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            results = list(loader._load_site_pages())

        assert len(results) == 2

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_stops_when_graph_returns_none(self, mock_config, loader):
        """None from _make_graph_request breaks the pagination loop."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        with patch.object(loader, '_make_graph_request', return_value=None):
            results = list(loader._load_site_pages())

        assert results == []

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_pages_without_content_are_not_yielded(self, mock_config, loader):
        """Pages that produce empty content (no title, no canvas, no description) are skipped."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        # No title, no canvasLayout, no description → _extract_page_content returns ""
        page = {"id": "p1", "webUrl": "https://t/p1"}

        def fake_graph(url):
            if "?$expand" in url:
                return {"id": "p1", "webUrl": "https://t/p1"}
            return {"value": [page]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            results = list(loader._load_site_pages())

        assert results == []


# ---------------------------------------------------------------------------
# TestGetAllDrives
# ---------------------------------------------------------------------------


class TestGetAllDrives:
    """Tests for _get_all_drives drive enumeration and library path caching."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_returns_list_of_drives(self, mock_config, loader):
        """Drive list is returned with id, name, and web_url."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        drives_response = {
            "value": [
                {
                    "id": "d1",
                    "name": "Shared Documents",
                    "webUrl": "https://tenant.sharepoint.com/sites/MySite/Shared Documents",
                },
            ]
        }

        with patch.object(loader, '_make_graph_request', return_value=drives_response):
            result = loader._get_all_drives()

        assert len(result) == 1
        assert result[0]["id"] == "d1"
        assert result[0]["name"] == "Shared Documents"

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_caches_library_path(self, mock_config, loader):
        """_drive_library_paths is populated from drive webUrl."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        drives_response = {
            "value": [
                {
                    "id": "d1",
                    "name": "Shared Documents",
                    "webUrl": "https://tenant.sharepoint.com/sites/MySite/Shared%20Documents",
                },
            ]
        }

        with patch.object(loader, '_make_graph_request', return_value=drives_response):
            loader._get_all_drives()

        assert "d1" in loader._drive_library_paths
        assert loader._drive_library_paths["d1"] == "Shared Documents"

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_empty_drives_returns_empty_list(self, mock_config, loader):
        """Empty value list results in empty drives list."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        with patch.object(loader, '_make_graph_request', return_value={"value": []}):
            result = loader._get_all_drives()

        assert result == []

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_pagination_follows_next_link(self, mock_config, loader):
        """Pagination follows @odata.nextLink for drives."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        call_count = [0]

        def fake_graph(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "value": [
                        {"id": "d1", "name": "Lib1", "webUrl": "https://tenant.sharepoint.com/sites/MySite/Lib1"}
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/next",
                }
            return {
                "value": [{"id": "d2", "name": "Lib2", "webUrl": "https://tenant.sharepoint.com/sites/MySite/Lib2"}],
            }

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            result = loader._get_all_drives()

        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestBuildFolderUrl
# ---------------------------------------------------------------------------


class TestBuildFolderUrl:
    """Tests for _build_folder_url."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_root_path_builds_root_children_url(self, mock_config, loader):
        """'root' folder_path yields /root/children endpoint."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        url = loader._build_folder_url("site-id", "drive-id", "root")

        assert url.endswith("/root/children")

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_non_root_path_builds_items_url(self, mock_config, loader):
        """Non-root folder_path yields /items/{path}/children endpoint."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        url = loader._build_folder_url("site-id", "drive-id", "folder-item-id")

        assert "/items/folder-item-id/children" in url

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_url_contains_site_and_drive_ids(self, mock_config, loader):
        """URL includes both site_id and drive_id."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        url = loader._build_folder_url("my-site", "my-drive", "root")

        assert "my-site" in url
        assert "my-drive" in url


# ---------------------------------------------------------------------------
# TestExtractDriveFolder
# ---------------------------------------------------------------------------


class TestExtractDriveFolder:
    """Tests for _extract_drive_folder."""

    def test_parses_drive_id_and_folder(self, loader):
        """drive_id and folder_in_drive are parsed from parentReference.path."""
        item = {
            "parentReference": {
                "driveId": "drive-abc",
                "path": "/drives/drive-abc/root:/folder/subfolder",
            }
        }

        drive_id, folder = loader._extract_drive_folder(item)

        assert drive_id == "drive-abc"
        assert folder == "folder/subfolder"

    def test_empty_folder_when_root(self, loader):
        """Library root path yields empty folder string."""
        item = {
            "parentReference": {
                "driveId": "drive-abc",
                "path": "/drives/drive-abc/root:",
            }
        }

        drive_id, folder = loader._extract_drive_folder(item)

        assert drive_id == "drive-abc"
        assert folder == ""

    def test_empty_strings_when_no_parent_reference(self, loader):
        """Missing parentReference yields empty strings."""
        drive_id, folder = loader._extract_drive_folder({})

        assert drive_id == ""
        assert folder == ""

    def test_url_encoded_folder_decoded(self, loader):
        """URL-encoded characters in path are decoded."""
        item = {
            "parentReference": {
                "driveId": "d1",
                "path": "/drives/d1/root:/folder%20name",
            }
        }

        _, folder = loader._extract_drive_folder(item)

        assert folder == "folder name"


# ---------------------------------------------------------------------------
# TestGetFileRelativePath
# ---------------------------------------------------------------------------


class TestGetFileRelativePath:
    """Tests for _get_file_relative_path."""

    def test_builds_full_path_with_library_and_folder(self, loader):
        """Full path = library/folder/filename."""
        loader._drive_library_paths = {"d1": "Shared Documents"}
        item = {
            "name": "doc.pdf",
            "parentReference": {
                "driveId": "d1",
                "path": "/drives/d1/root:/SubFolder",
            },
        }

        result = loader._get_file_relative_path(item)

        assert result == "Shared Documents/SubFolder/doc.pdf"

    def test_builds_path_without_folder(self, loader):
        """File at library root: library/filename."""
        loader._drive_library_paths = {"d1": "Shared Documents"}
        item = {
            "name": "doc.pdf",
            "parentReference": {
                "driveId": "d1",
                "path": "/drives/d1/root:",
            },
        }

        result = loader._get_file_relative_path(item)

        assert result == "Shared Documents/doc.pdf"

    def test_falls_back_to_filename_when_drive_not_cached(self, loader):
        """Unknown drive_id falls back to filename only."""
        loader._drive_library_paths = {}
        item = {
            "name": "doc.pdf",
            "parentReference": {
                "driveId": "unknown-drive",
                "path": "/drives/unknown-drive/root:",
            },
        }

        result = loader._get_file_relative_path(item)

        assert result == "doc.pdf"


# ---------------------------------------------------------------------------
# TestGetFileLibraryRelativePath
# ---------------------------------------------------------------------------


class TestGetFileLibraryRelativePath:
    """Tests for _get_file_library_relative_path."""

    def test_includes_folder_and_filename(self, loader):
        """Returns folder/filename without the library name prefix."""
        item = {
            "name": "report.xlsx",
            "parentReference": {
                "driveId": "d1",
                "path": "/drives/d1/root:/Team/Q1",
            },
        }

        result = loader._get_file_library_relative_path(item)

        assert result == "Team/Q1/report.xlsx"

    def test_returns_just_filename_at_root(self, loader):
        """File at root of library returns filename only."""
        item = {
            "name": "report.xlsx",
            "parentReference": {
                "driveId": "d1",
                "path": "/drives/d1/root:",
            },
        }

        result = loader._get_file_library_relative_path(item)

        assert result == "report.xlsx"


# ---------------------------------------------------------------------------
# TestShouldSkipFile
# ---------------------------------------------------------------------------


class TestShouldSkipFile:
    """Tests for _should_skip_file."""

    def test_skips_oversized_file(self, loader):
        """File exceeding max_file_size_bytes is skipped with reason 'size'."""
        loader.max_file_size_bytes = 1024
        item = {"name": "big.pdf", "size": 2048, "webUrl": ""}

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is True
        assert reason == "size"

    def test_skips_file_with_skip_extension(self, loader):
        """File with extension in SKIP_EXTENSIONS is skipped."""
        item = {"name": "photo.jpg", "size": 100, "webUrl": ""}

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is True
        assert reason == "extension"

    def test_does_not_skip_normal_file(self, loader):
        """Normal file within size limit and allowed extension is not skipped."""
        item = {"name": "document.pdf", "size": 100, "webUrl": "https://example.com/document.pdf"}

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is False
        assert reason is None

    def test_skips_executable_file(self, loader):
        """Executable file (.exe) is skipped."""
        item = {"name": "installer.exe", "size": 100, "webUrl": ""}

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is True
        assert reason == "extension"

    @patch('codemie.datasource.loader.sharepoint_loader._create_pathspec_from_filter')
    def test_skips_excluded_by_files_filter(self, mock_pathspec, loader):
        """File excluded by files_filter returns skip reason 'files_filter'."""
        loader.files_filter = "*.txt"
        loader._drive_library_paths = {}

        mock_include = MagicMock()
        mock_include.match_file.return_value = False
        mock_exclude = MagicMock()
        mock_exclude.match_file.return_value = True  # excluded
        mock_pathspec.return_value = (mock_include, mock_exclude, False)

        item = {
            "name": "notes.txt",
            "size": 100,
            "webUrl": "https://example.com/notes.txt",
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:"},
        }

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is True
        assert reason == "files_filter"

    def test_folder_scope_only_filter_does_not_skip_file(self, loader):
        """Folder-scope lines (/path/*) must not be fed to pathspec — files should pass through."""
        loader.files_filter = "/Shared Documents/Team/*"
        loader._drive_library_paths = {}

        item = {
            "name": "report.pdf",
            "size": 100,
            "webUrl": "https://example.com/report.pdf",
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:/Shared Documents/Team"},
        }

        should_skip, reason = loader._should_skip_file(item)

        assert should_skip is False
        assert reason is None

    def test_typed_wildcard_in_scope_line_filters_by_extension(self, loader):
        """Per-scope *.json filtering: when files_filter is set to "*.json" (as _load_and_yield_all_documents
        does during a scoped traversal for "/Shared Documents/Team/*.json"), only .json files pass."""
        # Simulate what _load_and_yield_all_documents sets for a scope with type_wildcard "*.json"
        loader.files_filter = "*.json"
        loader._drive_library_paths = {}

        item_json = {
            "name": "data.json",
            "size": 100,
            "webUrl": "https://example.com/data.json",
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:/Shared Documents/Team"},
        }
        item_pdf = {
            "name": "report.pdf",
            "size": 100,
            "webUrl": "https://example.com/report.pdf",
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:/Shared Documents/Team"},
        }

        should_skip_json, _ = loader._should_skip_file(item_json)
        should_skip_pdf, reason_pdf = loader._should_skip_file(item_pdf)

        assert should_skip_json is False
        assert should_skip_pdf is True
        assert reason_pdf == "files_filter"


# ---------------------------------------------------------------------------
# TestShouldSkipList
# ---------------------------------------------------------------------------


class TestShouldSkipList:
    """Tests for _should_skip_list."""

    def test_skips_document_library(self, loader):
        """documentLibrary template is always skipped."""
        list_info = {"displayName": "Docs", "list": {"template": "documentLibrary"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True

    def test_skips_hidden_list(self, loader):
        """Hidden list is skipped."""
        list_info = {"displayName": "HiddenList", "list": {"template": "genericList"}, "hidden": True}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True

    def test_skips_catalog_list(self, loader):
        """List whose name starts with underscore is skipped."""
        list_info = {"displayName": "_HiddenCatalog", "list": {"template": "genericList"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True

    def test_skips_system_list_by_name(self, loader):
        """Known system list names are skipped."""
        list_info = {"displayName": "Site Assets", "list": {"template": "genericList"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True

    def test_does_not_skip_user_list(self, loader):
        """Regular user-created list is not skipped."""
        list_info = {"displayName": "My Custom List", "list": {"template": "genericList"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is False
        assert reason is None

    def test_skips_form_templates(self, loader):
        """List starting with 'Form Templates' is skipped."""
        list_info = {"displayName": "Form Templates", "list": {"template": "genericList"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True

    def test_skips_list_with_catalogs_in_name(self, loader):
        """List with '_catalogs' anywhere in name is skipped."""
        list_info = {"displayName": "some_catalogs_list", "list": {"template": "genericList"}, "hidden": False}

        should_skip, reason = loader._should_skip_list(list_info)

        assert should_skip is True


# ---------------------------------------------------------------------------
# TestBuildListItemContent
# ---------------------------------------------------------------------------


class TestBuildListItemContent:
    """Tests for _build_list_item_content."""

    def test_builds_content_string(self, loader):
        """Fields are formatted as Key: Value lines."""
        fields = {"Title": "My Item", "Status": "Active"}

        result = loader._build_list_item_content("Tasks", fields)

        assert "List: Tasks" in result
        assert "Title: My Item" in result
        assert "Status: Active" in result

    def test_skips_odata_fields(self, loader):
        """Fields starting with '@' are excluded."""
        fields = {"@odata.etag": "abc", "Title": "Item"}

        result = loader._build_list_item_content("Tasks", fields)

        assert "@odata.etag" not in result
        assert "Title: Item" in result

    def test_skips_falsy_field_values(self, loader):
        """Fields with falsy values (None, '', 0) are excluded."""
        fields = {"Title": "Item", "Description": None, "Count": 0}

        result = loader._build_list_item_content("Tasks", fields)

        assert "Description" not in result
        assert "Count" not in result
        assert "Title: Item" in result


# ---------------------------------------------------------------------------
# TestTransformToDoc
# ---------------------------------------------------------------------------


class TestTransformToDoc:
    """Tests for _transform_to_doc."""

    def test_creates_document_with_correct_content(self, loader):
        """page_content is taken from item['content']."""
        item = {
            "content": "Hello World",
            "url": "https://t/page",
            "title": "T",
            "type": "page",
            "id": "1",
            "created": "",
            "modified": "",
        }

        doc = loader._transform_to_doc(item)

        assert doc.page_content == "Hello World"

    def test_metadata_fields_populated(self, loader):
        """source, title, type, id, created, modified are in metadata."""
        item = {
            "content": "Content",
            "url": "https://t/page",
            "title": "My Title",
            "type": "page",
            "id": "p1",
            "created": "2024-01-01",
            "modified": "2024-06-01",
        }

        doc = loader._transform_to_doc(item)

        assert doc.metadata["source"] == "https://t/page"
        assert doc.metadata["title"] == "My Title"
        assert doc.metadata["type"] == "page"
        assert doc.metadata["id"] == "p1"
        assert doc.metadata["created"] == "2024-01-01"
        assert doc.metadata["modified"] == "2024-06-01"

    def test_pre_existing_metadata_is_merged(self, loader):
        """metadata field in item is preserved and enriched."""
        item = {
            "content": "text",
            "url": "https://t/f",
            "title": "F",
            "type": "document",
            "id": "f1",
            "created": "",
            "modified": "",
            "metadata": {"source": "original", "page": 1},
        }

        doc = loader._transform_to_doc(item)

        assert doc.metadata["page"] == 1
        # url overrides original source
        assert doc.metadata["source"] == "https://t/f"

    def test_empty_item_returns_document_with_defaults(self, loader):
        """Empty item yields Document with empty strings in metadata."""
        doc = loader._transform_to_doc({})

        assert doc.page_content == ""
        assert doc.metadata["title"] == ""
        assert doc.metadata["type"] == ""


# ---------------------------------------------------------------------------
# TestProcessListItems
# ---------------------------------------------------------------------------


class TestProcessListItems:
    """Tests for _process_list_items pagination and item yield."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_yields_items_with_fields(self, mock_config, loader):
        """Items with non-empty fields are yielded."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        items_data = {
            "value": [
                {
                    "id": "i1",
                    "fields": {"Title": "Task 1"},
                    "webUrl": "https://t/l/i1",
                    "createdDateTime": "2024-01-01",
                    "lastModifiedDateTime": "2024-06-01",
                }
            ]
        }

        with patch.object(loader, '_make_graph_request', return_value=items_data):
            results = list(loader._process_list_items("site-id", "list-id", "Tasks"))

        assert len(results) == 1
        assert results[0]["type"] == "list_item"
        assert "Tasks" in results[0]["content"]

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_skips_items_with_no_fields(self, mock_config, loader):
        """Items with empty fields dict are skipped."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        items_data = {"value": [{"id": "i1", "fields": {}}]}

        with patch.object(loader, '_make_graph_request', return_value=items_data):
            results = list(loader._process_list_items("site-id", "list-id", "Tasks"))

        assert results == []

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_pagination_follows_next_link(self, mock_config, loader):
        """Pagination follows @odata.nextLink until exhausted."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        call_count = [0]

        def fake_graph(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "value": [{"id": "i1", "fields": {"Title": "T1"}}],
                    "@odata.nextLink": "https://graph.microsoft.com/next",
                }
            return {"value": [{"id": "i2", "fields": {"Title": "T2"}}]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            results = list(loader._process_list_items("site-id", "list-id", "Tasks"))

        assert len(results) == 2

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_stops_on_none_response(self, mock_config, loader):
        """None from _make_graph_request breaks the loop."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        with patch.object(loader, '_make_graph_request', return_value=None):
            results = list(loader._process_list_items("site-id", "list-id", "Tasks"))

        assert results == []


# ---------------------------------------------------------------------------
# TestWouldSkipFileForCount
# ---------------------------------------------------------------------------


class TestWouldSkipFileForCount:
    """Tests for _would_skip_file_for_count."""

    def test_skips_large_file(self, loader):
        """File larger than max_file_size_bytes is skipped."""
        loader.max_file_size_bytes = 100
        assert loader._would_skip_file_for_count({"name": "big.pdf", "size": 200}) is True

    def test_skips_skip_extension(self, loader):
        """File with SKIP_EXTENSIONS extension is skipped."""
        assert loader._would_skip_file_for_count({"name": "pic.jpg", "size": 10}) is True

    def test_does_not_skip_normal_file(self, loader):
        """Normal file within limits is not skipped."""
        loader.max_file_size_bytes = 1000
        assert loader._would_skip_file_for_count({"name": "doc.pdf", "size": 100}) is False

    def test_file_without_extension_not_skipped(self, loader):
        """File with no extension is not flagged as skip extension."""
        loader.max_file_size_bytes = 1000
        assert loader._would_skip_file_for_count({"name": "README", "size": 10}) is False

    def test_skips_when_files_filter_excludes(self, loader):
        """File excluded by the active files_filter is counted as skipped."""
        loader.files_filter = "*.json"
        loader._drive_library_paths = {}
        item = {
            "name": "report.pdf",
            "size": 100,
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:"},
        }
        assert loader._would_skip_file_for_count(item) is True

    def test_not_skipped_when_files_filter_matches(self, loader):
        """File matching the active files_filter include pattern is not skipped."""
        loader.files_filter = "*.json"
        loader._drive_library_paths = {}
        item = {
            "name": "data.json",
            "size": 100,
            "parentReference": {"driveId": "d1", "path": "/drives/d1/root:"},
        }
        assert loader._would_skip_file_for_count(item) is False


# ---------------------------------------------------------------------------
# TestCountPages
# ---------------------------------------------------------------------------


class TestCountPages:
    """Tests for _count_pages."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_counts_pages_single_page(self, mock_config, loader):
        """Single page of results counted correctly."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        with patch.object(loader, '_make_graph_request', return_value={"value": [{}, {}, {}]}):
            count = loader._count_pages("site-id")

        assert count == 3

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_counts_pages_with_pagination(self, mock_config, loader):
        """Pages across multiple API pages are summed."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        call_count = [0]

        def fake_graph(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"value": [{}, {}], "@odata.nextLink": "https://graph.microsoft.com/next"}
            return {"value": [{}]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            count = loader._count_pages("site-id")

        assert count == 3

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_returns_zero_on_none_response(self, mock_config, loader):
        """None response returns 0."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        with patch.object(loader, '_make_graph_request', return_value=None):
            count = loader._count_pages("site-id")

        assert count == 0


# ---------------------------------------------------------------------------
# TestCountFilesRecursive
# ---------------------------------------------------------------------------


class TestCountFilesRecursive:
    """Tests for _count_files_recursive."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_counts_files_in_flat_folder(self, mock_config, loader):
        """Files in the root folder are counted."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        items = {
            "value": [
                {"name": "doc.pdf", "size": 100, "file": {}},
                {"name": "photo.jpg", "size": 50, "file": {}},
            ]
        }

        with patch.object(loader, '_make_graph_request', return_value=items):
            total, skipped = loader._count_files_recursive("site-id", "drive-id")

        assert total == 2
        assert skipped == 1  # .jpg is in SKIP_EXTENSIONS

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_recurses_into_subfolders(self, mock_config, loader):
        """Folder items trigger recursive counting."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        call_count = [0]

        def fake_graph(url):
            call_count[0] += 1
            if call_count[0] == 1:
                # Root contains one folder and one file
                return {
                    "value": [
                        {"id": "sub-folder-id", "folder": {}},
                        {"name": "root-file.txt", "size": 10, "file": {}},
                    ]
                }
            # Subfolder contains one file
            return {"value": [{"name": "sub-file.pdf", "size": 20, "file": {}}]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            total, skipped = loader._count_files_recursive("site-id", "drive-id")

        assert total == 2  # root-file + sub-file
        assert skipped == 0


# ---------------------------------------------------------------------------
# TestShouldSkipListForCount
# ---------------------------------------------------------------------------


class TestShouldSkipListForCount:
    """Tests for _should_skip_list_for_count."""

    def test_skips_document_library(self, loader):
        """documentLibrary template returns True."""
        assert (
            loader._should_skip_list_for_count(
                {"displayName": "Docs", "list": {"template": "documentLibrary"}, "hidden": False}
            )
            is True
        )

    def test_skips_hidden_list(self, loader):
        """Hidden list returns True."""
        assert (
            loader._should_skip_list_for_count(
                {"displayName": "H", "list": {"template": "genericList"}, "hidden": True}
            )
            is True
        )

    def test_skips_system_list_name(self, loader):
        """Known system list name returns True."""
        assert (
            loader._should_skip_list_for_count(
                {"displayName": "Style Library", "list": {"template": "genericList"}, "hidden": False}
            )
            is True
        )

    def test_does_not_skip_regular_list(self, loader):
        """Regular user list returns False."""
        assert (
            loader._should_skip_list_for_count(
                {"displayName": "My List", "list": {"template": "genericList"}, "hidden": False}
            )
            is False
        )


# ---------------------------------------------------------------------------
# TestCountListItems
# ---------------------------------------------------------------------------


class TestCountListItems:
    """Tests for _count_list_items."""

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_counts_items_with_pagination(self, mock_config, loader):
        """Items across pages are summed."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        call_count = [0]

        def fake_graph(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"value": [{}, {}], "@odata.nextLink": "https://graph.microsoft.com/next"}
            return {"value": [{}]}

        with patch.object(loader, '_make_graph_request', side_effect=fake_graph):
            count = loader._count_list_items("site-id", "list-id")

        assert count == 3

    @patch('codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG')
    def test_returns_zero_on_none(self, mock_config, loader):
        """None response returns 0."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        with patch.object(loader, '_make_graph_request', return_value=None):
            count = loader._count_list_items("site-id", "list-id")

        assert count == 0


# ---------------------------------------------------------------------------
# TestValidateConnection
# ---------------------------------------------------------------------------


class TestValidateConnection:
    """Tests for validate_connection."""

    def test_delegates_to_validate_creds(self, loader):
        """validate_connection calls _validate_creds."""
        with patch.object(loader, '_validate_creds') as mock_validate:
            loader.validate_connection()

        mock_validate.assert_called_once()

    def test_propagates_missing_integration_exception(self, loader):
        """MissingIntegrationException propagates from validate_connection."""
        with patch.object(loader, '_validate_creds', side_effect=MissingIntegrationException("SharePoint")):
            with pytest.raises(MissingIntegrationException):
                loader.validate_connection()

    def test_propagates_unauthorized_exception(self, loader):
        """UnauthorizedException propagates from validate_connection."""
        with patch.object(loader, '_validate_creds', side_effect=UnauthorizedException("SharePoint")):
            with pytest.raises(UnauthorizedException):
                loader.validate_connection()


# ---------------------------------------------------------------------------
# TestFetchRemoteStats
# ---------------------------------------------------------------------------


class TestFetchRemoteStats:
    """Tests for fetch_remote_stats."""

    def test_returns_correct_keys(self, loader):
        """Result dict contains the three required keys."""
        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', return_value=5):
                    with patch.object(loader, '_count_documents', return_value=(10, 2)):
                        with patch.object(loader, '_count_lists', return_value=3):
                            result = loader.fetch_remote_stats()

        assert "documents_count_key" in result
        assert "total_documents" in result
        assert "skipped_documents" in result

    def test_totals_are_summed_correctly(self, loader):
        """pages + docs + lists total and skipped counts are computed."""
        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', return_value=5):
                    with patch.object(loader, '_count_documents', return_value=(10, 2)):
                        with patch.object(loader, '_count_lists', return_value=3):
                            result = loader.fetch_remote_stats()

        assert result["total_documents"] == 18  # 5 + 10 + 3
        assert result["skipped_documents"] == 2
        assert result["documents_count_key"] == 16  # 18 - 2

    def test_pages_skipped_when_include_pages_false(self, loader):
        """_count_pages not called when include_pages=False."""
        loader.include_pages = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages') as mock_pages:
                    with patch.object(loader, '_count_documents', return_value=(0, 0)):
                        with patch.object(loader, '_count_lists', return_value=0):
                            loader.fetch_remote_stats()

        mock_pages.assert_not_called()

    def test_documents_skipped_when_include_documents_false(self, loader):
        """_count_documents not called when include_documents=False."""
        loader.include_documents = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', return_value=0):
                    with patch.object(loader, '_count_documents') as mock_docs:
                        with patch.object(loader, '_count_lists', return_value=0):
                            loader.fetch_remote_stats()

        mock_docs.assert_not_called()

    def test_lists_skipped_when_include_lists_false(self, loader):
        """_count_lists not called when include_lists=False."""
        loader.include_lists = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', return_value=0):
                    with patch.object(loader, '_count_documents', return_value=(0, 0)):
                        with patch.object(loader, '_count_lists') as mock_lists:
                            loader.fetch_remote_stats()

        mock_lists.assert_not_called()

    def test_count_pages_exception_is_swallowed(self, loader):
        """Exception in _count_pages does not propagate; total falls back to 0."""
        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', side_effect=Exception("boom")):
                    with patch.object(loader, '_count_documents', return_value=(0, 0)):
                        with patch.object(loader, '_count_lists', return_value=0):
                            result = loader.fetch_remote_stats()

        assert result["total_documents"] == 0

    def test_validate_creds_called(self, loader):
        """fetch_remote_stats always calls _validate_creds first."""
        with patch.object(loader, '_validate_creds') as mock_validate:
            with patch.object(loader, '_get_site_id', return_value="site-id"):
                with patch.object(loader, '_count_pages', return_value=0):
                    with patch.object(loader, '_count_documents', return_value=(0, 0)):
                        with patch.object(loader, '_count_lists', return_value=0):
                            loader.fetch_remote_stats()

        mock_validate.assert_called_once()


# ---------------------------------------------------------------------------
# TestLazyLoad
# ---------------------------------------------------------------------------


class TestLazyLoad:
    """Tests for lazy_load."""

    def test_calls_validate_creds(self, loader):
        """lazy_load always calls _validate_creds."""
        with patch.object(loader, '_validate_creds') as mock_validate:
            with patch.object(loader, '_load_and_yield_pages', return_value=iter([])):
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([])):
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([])):
                        list(loader.lazy_load())

        mock_validate.assert_called_once()

    def test_yields_pages_documents_lists(self, loader):
        """All three load paths are traversed when all include flags are True."""
        from langchain_core.documents import Document as LCDoc

        page_doc = LCDoc(page_content="page", metadata={})
        doc_doc = LCDoc(page_content="document", metadata={})
        list_doc = LCDoc(page_content="list", metadata={})

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages', return_value=iter([page_doc])):
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([doc_doc])):
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([list_doc])):
                        results = list(loader.lazy_load())

        assert len(results) == 3

    def test_skips_pages_when_disabled(self, loader):
        """_load_and_yield_pages not called when include_pages=False."""
        loader.include_pages = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages') as mock_pages:
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([])):
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([])):
                        list(loader.lazy_load())

        mock_pages.assert_not_called()

    def test_skips_documents_when_disabled(self, loader):
        """_load_and_yield_all_documents not called when include_documents=False."""
        loader.include_documents = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages', return_value=iter([])):
                with patch.object(loader, '_load_and_yield_all_documents') as mock_docs:
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([])):
                        list(loader.lazy_load())

        mock_docs.assert_not_called()

    def test_skips_lists_when_disabled(self, loader):
        """_load_and_yield_lists not called when include_lists=False."""
        loader.include_lists = False

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages', return_value=iter([])):
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([])):
                    with patch.object(loader, '_load_and_yield_lists') as mock_lists:
                        list(loader.lazy_load())

        mock_lists.assert_not_called()

    def test_stats_reset_before_loading(self, loader):
        """Statistics counters are reset to 0 at start of each lazy_load call."""
        loader._total_files_found = 99
        loader._total_files_processed = 99
        loader._total_files_skipped = 99

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages', return_value=iter([])):
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([])):
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([])):
                        list(loader.lazy_load())

        assert loader._total_files_found == 0
        assert loader._total_files_processed == 0
        assert loader._total_files_skipped == 0

    def test_page_load_exception_does_not_stop_documents(self, loader):
        """Exception during page loading is swallowed; document loading still runs."""
        from langchain_core.documents import Document as LCDoc

        doc = LCDoc(page_content="doc", metadata={})

        with patch.object(loader, '_validate_creds'):
            with patch.object(loader, '_load_and_yield_pages', side_effect=Exception("pages failed")):
                with patch.object(loader, '_load_and_yield_all_documents', return_value=iter([doc])):
                    with patch.object(loader, '_load_and_yield_lists', return_value=iter([])):
                        results = list(loader.lazy_load())

        assert len(results) == 1
        assert results[0].page_content == "doc"

    def test_propagates_missing_integration_exception_from_validate(self, loader):
        """MissingIntegrationException from _validate_creds propagates immediately."""
        with patch.object(loader, '_validate_creds', side_effect=MissingIntegrationException("SharePoint")):
            with pytest.raises(MissingIntegrationException):
                list(loader.lazy_load())


# ---------------------------------------------------------------------------
# TestEncodeUrlPath
# ---------------------------------------------------------------------------


class TestEncodeUrlPath:
    """Tests for the module-level _encode_url_path helper."""

    def test_encodes_spaces(self):
        """Spaces are percent-encoded as %20."""

        result = _encode_url_path("My Folder/Sub Folder")

        assert result == "My%20Folder/Sub%20Folder"

    def test_encodes_special_characters(self):
        """Characters like # and ? are encoded."""

        result = _encode_url_path("Folder#1/File?name")

        assert "%23" in result
        assert "%3F" in result

    def test_preserves_slash_separator(self):
        """Slashes between segments are preserved as literal /."""

        result = _encode_url_path("A/B/C")

        assert result == "A/B/C"

    def test_strips_leading_slash(self):
        """Leading slash is stripped so the result never starts with /."""

        result = _encode_url_path("/Team/Project")

        assert not result.startswith("/")
        assert result == "Team/Project"

    def test_empty_string_returns_empty(self):
        """Empty input returns empty string."""

        assert _encode_url_path("") == ""


# ---------------------------------------------------------------------------
# TestNormalizePathFilter
# ---------------------------------------------------------------------------


class TestNormalizePathFilter:
    """Tests for _normalize_path_filter."""

    def test_plain_path_returned_unchanged(self, loader):
        """A plain path filter (not a URL) is returned as-is."""
        result = loader._normalize_path_filter("/Shared Documents/folder/*")

        assert result == "/Shared Documents/folder/*"

    def test_wildcard_returned_unchanged(self, loader):
        """'*' is returned unchanged."""
        assert loader._normalize_path_filter("*") == "*"

    def test_empty_string_returned_unchanged(self, loader):
        """Empty string is returned unchanged."""
        assert loader._normalize_path_filter("") == ""

    def test_copy_link_url_without_wildcard(self, loader):
        """SharePoint copy-link URL is converted; /* is appended automatically."""
        url = "https://tenant.sharepoint.com/:f:/r/sites/MySite" "/Shared%20Documents/test%20folder?csf=1&web=1&e=abc"

        result = loader._normalize_path_filter(url)

        assert result == "/Shared Documents/test folder/*"

    def test_copy_link_url_with_wildcard(self, loader):
        """Existing /* suffix on a copy-link URL is preserved."""
        url = "https://tenant.sharepoint.com/:f:/r/sites/MySite" "/Shared%20Documents/test%20folder/*"

        result = loader._normalize_path_filter(url)

        assert result == "/Shared Documents/test folder/*"

    def test_copy_link_url_with_extension_wildcard(self, loader):
        """/*.pdf suffix on a copy-link URL is preserved."""
        url = "https://tenant.sharepoint.com/:f:/r/sites/MySite" "/Shared%20Documents/test%20folder/*.pdf"

        result = loader._normalize_path_filter(url)

        assert result == "/Shared Documents/test folder/*.pdf"

    def test_direct_url_without_wildcard(self, loader):
        """Plain SharePoint folder URL gets /* appended."""
        url = "https://tenant.sharepoint.com/sites/MySite/Shared%20Documents/folder"

        result = loader._normalize_path_filter(url)

        assert result == "/Shared Documents/folder/*"

    def test_url_not_matching_site_path_returned_as_is(self, loader):
        """URL for a different site is returned unchanged with a warning."""
        url = "https://tenant.sharepoint.com/sites/OtherSite/Shared%20Documents/folder"

        result = loader._normalize_path_filter(url)

        assert result == url

    def test_query_params_stripped(self, loader):
        """Query parameters are stripped from the URL."""
        url = "https://tenant.sharepoint.com/sites/MySite/Shared%20Documents/folder" "?csf=1&web=1&e=XYZ"

        result = loader._normalize_path_filter(url)

        assert "csf" not in result
        assert result == "/Shared Documents/folder/*"

    def test_plain_path_with_whitespace_is_stripped(self, loader):
        """Leading/trailing whitespace in a non-URL path_filter is stripped."""
        result = loader._normalize_path_filter("  /Shared Documents/folder/*  ")

        assert result == "/Shared Documents/folder/*"

    def test_site_root_url_produces_wildcard_glob(self, loader):
        """A URL pointing at the site root itself normalizes to '/*', not '//*'."""
        url = "https://tenant.sharepoint.com/sites/MySite"

        result = loader._normalize_path_filter(url)

        assert result == "/*"
        assert "//*" not in result

    def test_cross_tenant_url_returned_as_is(self, loader):
        """URL from a different SharePoint tenant hostname is returned unchanged."""
        url = "https://other-tenant.sharepoint.com/sites/MySite/Shared%20Documents/folder"

        result = loader._normalize_path_filter(url)

        assert result == url


# ---------------------------------------------------------------------------
# TestParseFolderScope
# ---------------------------------------------------------------------------


class TestParseFolderScope:
    """Tests for _parse_folder_scope (static method)."""

    def test_wildcard_returns_none(self):
        """'*' returns None (no scoping)."""
        assert SharePointLoader._parse_folder_scope("*") is None

    def test_empty_returns_none(self):
        """Empty string returns None."""
        assert SharePointLoader._parse_folder_scope("") is None

    def test_simple_library_with_wildcard(self):
        """'/Shared Documents/*' returns library name and empty sub_path."""
        assert SharePointLoader._parse_folder_scope("/Shared Documents/*") == ("Shared Documents", "")

    def test_library_and_subfolder(self):
        """'/Shared Documents/Team/Project/*' returns correct library and sub_path."""
        assert SharePointLoader._parse_folder_scope("/Shared Documents/Team/Project/*") == (
            "Shared Documents",
            "Team/Project",
        )

    def test_extension_wildcard_stripped_from_sub_path(self):
        """'*.pdf' at the end is stripped from sub_path."""
        assert SharePointLoader._parse_folder_scope("/Shared Documents/folder/*.pdf") == ("Shared Documents", "folder")

    def test_intermediate_wildcard_falls_back_to_library_root(self):
        """An intermediate '*' segment makes sub_path empty (library root fallback)."""
        assert SharePointLoader._parse_folder_scope("/Shared Documents/*/Project/*") == ("Shared Documents", "")

    def test_encoded_path_is_decoded(self):
        """Percent-encoded characters are decoded before parsing."""
        assert SharePointLoader._parse_folder_scope("/Shared%20Documents/My%20Folder/*") == (
            "Shared Documents",
            "My Folder",
        )

    def test_bare_wildcard_library_segment_returns_none(self):
        """A path whose only non-empty segment is '*' returns None."""
        assert SharePointLoader._parse_folder_scope("/*") is None

    def test_library_name_only_no_wildcard(self):
        """'/Shared Documents' (no wildcard, no sub-path) returns library and empty sub_path."""
        assert SharePointLoader._parse_folder_scope("/Shared Documents") == ("Shared Documents", "")


# ---------------------------------------------------------------------------
# TestResolveFolderStart
# ---------------------------------------------------------------------------


class TestResolveFolderStart:
    """Tests for _resolve_folder_start."""

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_returns_root_when_sub_path_empty(self, mock_config, loader):
        """Empty sub_path returns 'root' without any API call."""
        with patch.object(loader, "_make_graph_request") as mock_req:
            result = loader._resolve_folder_start("drive-id", "")

        assert result == "root"
        mock_req.assert_not_called()

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_returns_folder_item_id_on_success(self, mock_config, loader):
        """Graph API response with 'id' returns that folder item ID."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        with patch.object(loader, "_make_graph_request", return_value={"id": "folder-abc"}):
            result = loader._resolve_folder_start("drive-id", "Team/Project")

        assert result == "folder-abc"

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_returns_root_when_folder_not_found(self, mock_config, loader):
        """None response (404) falls back to 'root'."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        with patch.object(loader, "_make_graph_request", return_value=None):
            result = loader._resolve_folder_start("drive-id", "Missing/Folder")

        assert result == "root"

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_uses_provided_site_id(self, mock_config, loader):
        """Explicitly provided site_id is used; _get_site_id is not called."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"

        with patch.object(loader, "_get_site_id") as mock_get_site:
            with patch.object(loader, "_make_graph_request", return_value={"id": "fid"}):
                loader._resolve_folder_start("drive-id", "Folder", site_id="explicit-site")

        mock_get_site.assert_not_called()

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_url_contains_encoded_path(self, mock_config, loader):
        """Spaces in sub_path are percent-encoded in the Graph API URL."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"
        captured = {}

        def capture(url):
            captured["url"] = url
            return {"id": "fid"}

        with patch.object(loader, "_make_graph_request", side_effect=capture):
            loader._resolve_folder_start("drive-id", "My Folder/Sub Folder")

        assert "My%20Folder" in captured["url"]
        assert "Sub%20Folder" in captured["url"]

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_calls_get_site_id_when_site_id_is_none(self, mock_config, loader):
        """When site_id=None, _get_site_id() is called to resolve the site ID."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = None

        with patch.object(loader, "_get_site_id", return_value="resolved-site") as mock_get_site:
            with patch.object(loader, "_make_graph_request", return_value={"id": "fid"}):
                loader._resolve_folder_start("drive-id", "Team/Project")

        mock_get_site.assert_called_once()

    @patch("codemie.datasource.loader.sharepoint_loader.SHAREPOINT_CONFIG")
    def test_warning_includes_sub_path(self, mock_config, loader):
        """Warning log on resolution failure includes the sub_path value."""
        mock_config.graph_base_url = "https://graph.microsoft.com"
        mock_config.graph_api_version = "v1.0"
        loader._site_id = "site-id"

        with patch.object(loader, "_make_graph_request", return_value=None):
            with patch("codemie.datasource.loader.sharepoint_loader.logger") as mock_logger:
                loader._resolve_folder_start("drive-id", "folder")

        warning_msg = mock_logger.warning.call_args[0][0]
        assert "folder" in warning_msg


# ---------------------------------------------------------------------------
# TestHasGlobalPatterns
# ---------------------------------------------------------------------------


class TestHasGlobalPatterns:
    """Tests for _has_global_patterns."""

    def test_empty_returns_false(self):
        """Empty string has no global patterns."""
        assert SharePointLoader._has_global_patterns("") is False

    def test_only_path_lines_returns_false(self):
        """/…-prefixed lines are folder scopes, not global patterns."""
        assert SharePointLoader._has_global_patterns("/Shared Documents/*\n/Archive/*") is False

    def test_extension_pattern_returns_true(self):
        """'*.xlsx' is a global pattern."""
        assert SharePointLoader._has_global_patterns("*.xlsx") is True

    def test_mixed_returns_true(self):
        """Global pattern alongside a path line → True."""
        assert SharePointLoader._has_global_patterns("/folder/*\n*.pdf") is True

    def test_negation_only_returns_false(self):
        """Negation lines starting with '!' are not global include patterns."""
        assert SharePointLoader._has_global_patterns("!*.tmp") is False

    def test_comment_only_returns_false(self):
        """Comment lines starting with '#' are not patterns."""
        assert SharePointLoader._has_global_patterns("# just a comment") is False

    def test_blank_lines_ignored(self):
        """Lines that are only whitespace are skipped."""
        assert SharePointLoader._has_global_patterns("  \n  \n/folder/*") is False

    def test_url_line_returns_false(self):
        """A SharePoint URL line is a folder scope, not a global pattern."""
        url = "https://tenant.sharepoint.com/sites/MySite/Shared%20Documents/folder"
        assert SharePointLoader._has_global_patterns(url) is False


# ---------------------------------------------------------------------------
# TestParseFolderRules
# ---------------------------------------------------------------------------


class TestParseFolderRules:
    """Tests for _parse_folder_rules."""

    def test_empty_returns_empty(self, loader):
        """Empty files_filter returns no rules."""
        assert loader._parse_folder_rules("") == []

    def test_single_path_line(self, loader):
        """'/Shared Documents/folder/*' produces one rule (no type wildcard)."""
        rules = loader._parse_folder_rules("/Shared Documents/folder/*")
        assert rules == [("Shared Documents", "folder", "")]

    def test_typed_wildcard_captured(self, loader):
        """'/Shared Documents/folder/*.json' produces one rule with type_wildcard."""
        rules = loader._parse_folder_rules("/Shared Documents/folder/*.json")
        assert rules == [("Shared Documents", "folder", "*.json")]

    def test_global_pattern_excluded(self, loader):
        """'*.xlsx' is a global pattern and not included in folder rules."""
        assert loader._parse_folder_rules("*.xlsx") == []

    def test_multiple_path_lines(self, loader):
        """Multiple path lines produce multiple rules."""
        ff = "/Shared Documents/folder/*\n/Archive/*"
        rules = loader._parse_folder_rules(ff)
        assert ("Shared Documents", "folder", "") in rules
        assert ("Archive", "", "") in rules

    def test_negation_excluded(self, loader):
        """Negation lines are skipped."""
        assert loader._parse_folder_rules("!*.tmp") == []

    def test_duplicate_deduplicated(self, loader):
        """Duplicate path lines produce a single rule."""
        ff = "/Shared Documents/folder/*\n/Shared Documents/folder/*"
        rules = loader._parse_folder_rules(ff)
        assert rules == [("Shared Documents", "folder", "")]

    def test_url_line_normalised(self, loader):
        """SharePoint copy-link URL is normalised to a folder rule."""
        url = "https://tenant.sharepoint.com/:f:/r/sites/MySite/Shared%20Documents/team?csf=1&web=1"
        rules = loader._parse_folder_rules(url)
        assert rules == [("Shared Documents", "team", "")]

    def test_mismatched_hostname_url_skipped(self, loader):
        """A URL from a different tenant that can't be normalised is silently skipped."""
        url = "https://other-tenant.sharepoint.com/sites/Other/Shared%20Documents/folder"
        rules = loader._parse_folder_rules(url)
        assert rules == []


# ---------------------------------------------------------------------------
# TestResolveAllScopes
# ---------------------------------------------------------------------------


class TestResolveAllScopes:
    """Tests for _resolve_all_scopes."""

    def test_empty_filter_returns_all_drives(self, loader, two_drives):
        """Empty files_filter → traverse all drives, scope_filter is empty."""
        loader.files_filter = ""
        result = loader._resolve_all_scopes(two_drives)
        assert result == [(two_drives, "", "")]

    def test_global_pattern_returns_all_drives(self, loader, two_drives):
        """A global pattern (*.xlsx) → traverse all drives, scope_filter is original ff."""
        loader.files_filter = "*.xlsx"
        result = loader._resolve_all_scopes(two_drives)
        assert result == [(two_drives, "", "*.xlsx")]

    def test_single_folder_rule_scopes_drives(self, loader, two_drives):
        """/Shared Documents/Team/* → only Shared Documents drive, empty scope_filter."""
        loader.files_filter = "/Shared Documents/Team/*"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1
        drives, sub_path, scope_filter = result[0]
        assert len(drives) == 1
        assert drives[0]["id"] == "d1"
        assert sub_path == "Team"
        assert scope_filter == ""

    def test_typed_wildcard_scope_filter(self, loader, two_drives):
        """/Shared Documents/Team/*.json → scope_filter is '*.json'."""
        loader.files_filter = "/Shared Documents/Team/*.json"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1
        _, sub_path, scope_filter = result[0]
        assert sub_path == "Team"
        assert scope_filter == "*.json"

    def test_global_negation_carried_into_scope_filter(self, loader, two_drives):
        """Global negation !*.pptx + scope URL → scope_filter includes negation."""
        loader.files_filter = "/Shared Documents/Team/*\n!*.pptx"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1
        _, _, scope_filter = result[0]
        assert "!*.pptx" in scope_filter

    def test_multiple_rules_multiple_scopes(self, loader, two_drives):
        """/Shared Documents/* + /Archive/* → two separate scopes."""
        loader.files_filter = "/Shared Documents/*\n/Archive/*"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 2
        drive_ids = {r[0][0]["id"] for r in result}
        assert drive_ids == {"d1", "d2"}

    def test_unknown_library_fallback_all_drives(self, loader, two_drives):
        """Unknown library name falls back to all drives with empty sub_path."""
        loader.files_filter = "/NonExistent/*"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1
        drives, sub_path, scope_filter = result[0]
        assert drives == two_drives
        assert sub_path == ""

    def test_duplicates_deduplicated(self, loader, two_drives):
        """Duplicate folder rules produce a single scope entry."""
        loader.files_filter = "/Shared Documents/*\n/Shared Documents/*"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1

    def test_mixed_global_and_path_returns_all_drives(self, loader, two_drives):
        """Mixed filter with at least one global pattern → all drives, full ff as scope_filter."""
        ff = "/Shared Documents/*\n*.pdf"
        loader.files_filter = ff
        result = loader._resolve_all_scopes(two_drives)
        assert result == [(two_drives, "", ff)]

    def test_multiple_unknown_libraries_produce_single_fallback(self, loader, two_drives):
        """Two different unknown library names both fall back to all drives, but only one entry."""
        loader.files_filter = "/NonExistent1/*\n/NonExistent2/*"
        result = loader._resolve_all_scopes(two_drives)
        assert len(result) == 1
        drives, sub_path, scope_filter = result[0]
        assert drives == two_drives
        assert sub_path == ""


# ---------------------------------------------------------------------------
# TestLoadAndYieldAllDocuments
# ---------------------------------------------------------------------------


class TestLoadAndYieldAllDocuments:
    """Integration tests verifying the call chain inside _load_and_yield_all_documents."""

    def test_no_drives_yields_nothing(self, loader):
        """When _get_all_drives returns empty, no documents are yielded."""
        with patch.object(loader, "_get_all_drives", return_value=[]):
            result = list(loader._load_and_yield_all_documents())

        assert result == []

    def test_unscoped_filter_uses_root_start_folder(self, loader, single_drive):
        """When files_filter is empty, start_folder='root' is passed to the drive loader."""
        loader.files_filter = ""

        with (
            patch.object(loader, "_get_all_drives", return_value=single_drive),
            patch.object(loader, "_resolve_all_scopes", return_value=[(single_drive, "", "")]) as mock_scope,
            patch.object(loader, "_resolve_folder_start") as mock_resolve,
            patch.object(loader, "_load_and_yield_documents_from_drive", return_value=iter([])) as mock_load,
        ):
            list(loader._load_and_yield_all_documents())

        mock_scope.assert_called_once_with(single_drive)
        mock_resolve.assert_not_called()
        mock_load.assert_called_once_with("d1", "Shared Documents", "root")

    def test_scoped_filter_resolves_start_folder(self, loader, single_drive):
        """When sub_path is non-empty, _resolve_folder_start is called with pre-resolved site_id."""
        loader.files_filter = "/Shared Documents/Team/*"

        with (
            patch.object(loader, "_get_all_drives", return_value=single_drive),
            patch.object(loader, "_resolve_all_scopes", return_value=[(single_drive, "Team", "")]),
            patch.object(loader, "_get_site_id", return_value="site-abc") as mock_site_id,
            patch.object(loader, "_resolve_folder_start", return_value="folder-item-id") as mock_resolve,
            patch.object(loader, "_load_and_yield_documents_from_drive", return_value=iter([])) as mock_load,
        ):
            list(loader._load_and_yield_all_documents())

        mock_site_id.assert_called_once()
        mock_resolve.assert_called_once_with("d1", "Team", "site-abc")
        mock_load.assert_called_once_with("d1", "Shared Documents", "folder-item-id")


# ---------------------------------------------------------------------------
# TestCountDocuments
# ---------------------------------------------------------------------------


class TestCountDocuments:
    """Integration tests verifying the call chain inside _count_documents."""

    def test_no_drives_returns_zeros(self, loader):
        """When _get_all_drives returns empty, (0, 0) is returned without error."""
        with patch.object(loader, "_get_all_drives", return_value=[]):
            total, skipped = loader._count_documents("site-id")

        assert total == 0
        assert skipped == 0

    def test_unscoped_filter_uses_root_start_folder(self, loader, single_drive):
        """When sub_path is empty, _resolve_folder_start is not called and 'root' is used."""
        loader.files_filter = ""

        with (
            patch.object(loader, "_get_all_drives", return_value=single_drive),
            patch.object(loader, "_resolve_all_scopes", return_value=[(single_drive, "", "")]),
            patch.object(loader, "_resolve_folder_start") as mock_resolve,
            patch.object(loader, "_count_files_recursive", return_value=(5, 1)) as mock_count,
        ):
            total, skipped = loader._count_documents("site-id")

        mock_resolve.assert_not_called()
        mock_count.assert_called_once_with("site-id", "d1", "root")
        assert total == 5
        assert skipped == 1

    def test_scoped_filter_passes_site_id_to_resolve(self, loader, single_drive):
        """site_id is forwarded to _resolve_folder_start and the returned ID used for counting."""
        loader.files_filter = "/Shared Documents/Team/*"

        with (
            patch.object(loader, "_get_all_drives", return_value=single_drive),
            patch.object(loader, "_resolve_all_scopes", return_value=[(single_drive, "Team", "")]),
            patch.object(loader, "_resolve_folder_start", return_value="folder-item-id") as mock_resolve,
            patch.object(loader, "_count_files_recursive", return_value=(3, 0)) as mock_count,
        ):
            total, skipped = loader._count_documents("site-xyz")

        mock_resolve.assert_called_once_with("d1", "Team", "site-xyz")
        mock_count.assert_called_once_with("site-xyz", "d1", "folder-item-id")
        assert total == 3
        assert skipped == 0

    def test_aggregates_counts_across_drives(self, loader, two_drives):
        """Counts from multiple drives are summed correctly."""
        loader.files_filter = ""

        with (
            patch.object(loader, "_get_all_drives", return_value=two_drives),
            patch.object(loader, "_resolve_all_scopes", return_value=[(two_drives, "", "")]),
            patch.object(loader, "_count_files_recursive", side_effect=[(10, 2), (5, 1)]),
        ):
            total, skipped = loader._count_documents("site-id")

        assert total == 15
        assert skipped == 3

    def test_deduplicates_same_drive_across_scopes(self, loader, two_drives):
        """Same (drive_id, sub_path) pair from two scopes is counted only once."""
        loader.files_filter = "/NonExistent1/*\n/NonExistent2/*"
        # Both unknown libraries fall back to all_drives with sub_path=""
        scopes = [(two_drives, "", ""), (two_drives, "", "")]

        with (
            patch.object(loader, "_get_all_drives", return_value=two_drives),
            patch.object(loader, "_resolve_all_scopes", return_value=scopes),
            patch.object(loader, "_count_files_recursive", side_effect=[(10, 2), (5, 1)]) as mock_count,
        ):
            total, skipped = loader._count_documents("site-id")

        # Each of the two drives is counted once, not twice
        assert mock_count.call_count == 2
        assert total == 15
        assert skipped == 3

    def test_scope_filter_applied_during_count(self, loader, single_drive):
        """files_filter is temporarily set to scope_filter so _would_skip_file_for_count
        picks up files_filter skips (not just extension/size), matching actual indexing behaviour."""
        loader.files_filter = "/Shared Documents/Team/*.json"
        # _resolve_all_scopes returns scope_filter="*.json" for this folder rule
        scope_filter = "*.json"
        observed: list[str] = []

        def capture_count(*_args, **_kwargs):
            observed.append(loader.files_filter)  # capture what files_filter is during count
            return (0, 0)

        with (
            patch.object(loader, "_get_all_drives", return_value=single_drive),
            patch.object(loader, "_resolve_all_scopes", return_value=[(single_drive, "", scope_filter)]),
            patch.object(loader, "_count_files_recursive", side_effect=capture_count),
        ):
            loader._count_documents("site-id")

        # During counting the scope_filter must have been active
        assert observed == [scope_filter]
        # After counting the original files_filter is restored
        assert loader.files_filter == "/Shared Documents/Team/*.json"
