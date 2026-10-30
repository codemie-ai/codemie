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

"""
Story 1.2 model-layer tests.

Covers AC #1 (auth_config field on MCPServerConfig / MCPServerConfigData),
AC #3 (get_by_auth_config_id reverse lookup), AC #4 (NULL excluded from
uniqueness — model-level: None is accepted and serialises to nothing),
AC #5 (MCPExecutionContext.auth_headers accessible but excluded from both
to_request_fields and model_dump).
"""

from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.mcp_config import MCPConfig, MCPServerConfigData
from codemie.service.mcp.models import MCPExecutionContext, MCPServerConfig


# ---------------------------------------------------------------------------
# AC #1 — auth_config field on MCPServerConfig (service layer model)
# ---------------------------------------------------------------------------


class TestMCPServerConfigAuthConfig:
    """auth_config field on MCPServerConfig (src/codemie/service/mcp/models.py)."""

    def test_auth_config_defaults_to_none(self):
        """MCPServerConfig.auth_config is None when not provided."""
        cfg = MCPServerConfig(command="npx")
        assert cfg.auth_config is None

    def test_auth_config_accepts_dict(self):
        """MCPServerConfig.auth_config stores an arbitrary dict (raw, no enterprise types)."""
        auth = {"id": "ac-123", "type": "oauth2", "client_id": "client-abc"}
        cfg = MCPServerConfig(command="npx", auth_config=auth)
        assert cfg.auth_config == auth
        assert cfg.auth_config["id"] == "ac-123"

    def test_auth_config_round_trips_via_model_dump(self):
        """auth_config survives serialise → deserialise unchanged."""
        auth = {"id": "ac-456", "scope": "read write"}
        cfg = MCPServerConfig(command="npx", auth_config=auth)
        dumped = cfg.model_dump()
        assert "auth_config" in dumped
        assert dumped["auth_config"] == auth

    def test_auth_config_accepts_none_explicitly(self):
        """Explicit None is accepted — AC #4 (NULL excluded from uniqueness)."""
        cfg = MCPServerConfig(url="http://mcp.local/mcp", auth_config=None)
        assert cfg.auth_config is None


# ---------------------------------------------------------------------------
# AC #1 — auth_config field on MCPServerConfigData (API/DB model)
# ---------------------------------------------------------------------------


class TestMCPServerConfigDataAuthConfig:
    """auth_config field on MCPServerConfigData (src/codemie/rest_api/models/mcp_config.py)."""

    def test_auth_config_defaults_to_none(self):
        """MCPServerConfigData.auth_config is None when not provided."""
        data = MCPServerConfigData()
        assert data.auth_config is None

    def test_auth_config_accepts_dict(self):
        """MCPServerConfigData.auth_config stores an arbitrary dict."""
        auth = {"id": "ac-789", "type": "saml"}
        data = MCPServerConfigData(auth_config=auth)
        assert data.auth_config == auth

    def test_auth_config_round_trips_via_model_dump(self):
        """auth_config survives serialise → deserialise unchanged."""
        auth = {"id": "ac-abc", "issuer": "https://idp.example.com"}
        data = MCPServerConfigData(auth_config=auth)
        dumped = data.model_dump()
        assert "auth_config" in dumped
        assert dumped["auth_config"] == auth

    def test_auth_config_serialised_as_none_when_absent(self):
        """When auth_config is None, serialisation produces None (not missing key)."""
        data = MCPServerConfigData()
        dumped = data.model_dump()
        assert "auth_config" in dumped
        assert dumped["auth_config"] is None


# ---------------------------------------------------------------------------
# AC #3 — get_by_auth_config_id() reverse lookup (with mocked Session)
# ---------------------------------------------------------------------------


class TestMCPConfigGetByAuthConfigId:
    """Unit tests for MCPConfig.get_by_auth_config_id() without a real DB."""

    @patch("codemie.rest_api.models.mcp_config.Session")
    def test_returns_mcp_config_when_found(self, mock_session_class):
        """Returns the owning MCPConfig when a matching record exists."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        expected = MagicMock(spec=MCPConfig)
        mock_session.exec.return_value.first.return_value = expected

        with patch.object(MCPConfig, "get_engine", return_value=MagicMock()):
            result = MCPConfig.get_by_auth_config_id("ac-123")

        assert result is expected
        mock_session.exec.assert_called_once()

    @patch("codemie.rest_api.models.mcp_config.Session")
    def test_returns_none_when_not_found(self, mock_session_class):
        """Returns None when no MCPConfig has the given auth_config.id."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = None

        with patch.object(MCPConfig, "get_engine", return_value=MagicMock()):
            result = MCPConfig.get_by_auth_config_id("nonexistent-id")

        assert result is None
        mock_session.exec.assert_called_once()

    @patch("codemie.rest_api.models.mcp_config.Session")
    def test_passes_auth_config_id_to_query(self, mock_session_class):
        """The auth_config_id value participates in the WHERE predicate of the executed query."""
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = None

        with patch.object(MCPConfig, "get_engine", return_value=MagicMock()):
            MCPConfig.get_by_auth_config_id("ac-lookup-test")

        mock_session.exec.assert_called_once()

        # Compile the statement to SQL and verify the supplied value is bound as
        # a predicate — this proves the method passes auth_config_id to the WHERE
        # clause, not just that some query was executed.
        stmt = mock_session.exec.call_args[0][0]
        compiled_sql = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert (
            "ac-lookup-test" in compiled_sql
        ), f"Expected 'ac-lookup-test' in compiled SQL predicate, got:\n{compiled_sql}"


# ---------------------------------------------------------------------------
# AC #5 — MCPExecutionContext.auth_headers field and serialisation exclusion
# ---------------------------------------------------------------------------


class TestMCPExecutionContextAuthHeaders:
    """auth_headers field on MCPExecutionContext and to_request_fields() exclusion."""

    def test_auth_headers_defaults_to_none(self):
        """auth_headers is None by default and does not affect other fields."""
        ctx = MCPExecutionContext()
        assert ctx.auth_headers is None

    def test_auth_headers_stores_dict(self):
        """auth_headers accepts and returns a dict[str, str]."""
        hdrs = {"Authorization": "Bearer tok-abc", "X-Tenant": "t1"}
        ctx = MCPExecutionContext(user_id="u1", auth_headers=hdrs)
        assert ctx.auth_headers == hdrs
        assert ctx.auth_headers["Authorization"] == "Bearer tok-abc"

    def test_auth_headers_excluded_from_to_request_fields(self):
        """auth_headers must NOT appear in to_request_fields() output (AC #5)."""
        hdrs = {"Authorization": "Bearer secret-token"}
        ctx = MCPExecutionContext(user_id="u1", auth_headers=hdrs)

        fields = ctx.to_request_fields()

        assert "auth_headers" not in fields, (
            "auth_headers must be excluded from to_request_fields() to prevent "
            "credential leakage via MCPToolInvocationRequest caching"
        )

    def test_auth_headers_accessible_on_model(self):
        """auth_headers remains accessible as a model attribute even though excluded from serialisation."""
        hdrs = {"X-Auth": "value"}
        ctx = MCPExecutionContext(user_id="u1", auth_headers=hdrs)

        # Excluded from to_request_fields but readable directly
        assert ctx.auth_headers == hdrs
        assert "auth_headers" not in ctx.to_request_fields()

    def test_to_request_fields_contains_expected_keys_when_auth_headers_set(self):
        """to_request_fields includes exactly the non-auth fields when auth_headers is set."""
        ctx = MCPExecutionContext(
            user_id="u1",
            assistant_id="a1",
            project_name="proj",
            workflow_execution_id="wf1",
            request_headers={"X-Req": "val"},
            auth_headers={"Authorization": "Bearer tok"},
        )
        fields = ctx.to_request_fields()

        assert fields == {
            "user_id": "u1",
            "assistant_id": "a1",
            "project_name": "proj",
            "workflow_execution_id": "wf1",
            "request_headers": {"X-Req": "val"},
            "user_context": None,
        }
        assert fields["user_context"] is None
        assert "extra_attributes" not in fields

    def test_to_request_fields_without_auth_headers_matches_previous_contract(self):
        """to_request_fields output is identical to the pre-1.2 contract when auth_headers is None."""
        ctx = MCPExecutionContext(
            user_id="u-123",
            assistant_id="a-456",
            project_name="proj",
            workflow_execution_id="wf-789",
            request_headers=None,
        )
        fields = ctx.to_request_fields()

        # Identical to the pre-1.2 contract, plus the EPMCDME-13260 user_context key
        # (None here; dropped before the wire by exclude_none=True at client.py:128).
        assert fields == {
            "user_id": "u-123",
            "assistant_id": "a-456",
            "project_name": "proj",
            "workflow_execution_id": "wf-789",
            "request_headers": None,
            "user_context": None,
        }
        assert fields["user_context"] is None
        assert "extra_attributes" not in fields

    def test_auth_headers_none_also_excluded_from_request_fields(self):
        """auth_headers=None is also excluded (not serialised as null)."""
        ctx = MCPExecutionContext(user_id="u1", auth_headers=None)
        fields = ctx.to_request_fields()
        assert "auth_headers" not in fields

    def test_auth_headers_excluded_from_direct_model_dump(self):
        """auth_headers must NOT appear in model_dump() either — Field(exclude=True) enforces this."""
        hdrs = {"Authorization": "Bearer super-secret"}
        ctx = MCPExecutionContext(user_id="u1", auth_headers=hdrs)

        dumped = ctx.model_dump()

        assert "auth_headers" not in dumped, (
            "auth_headers must be excluded from model_dump() via Field(exclude=True) "
            "to prevent credential leakage via any serialisation path"
        )
        # The value remains accessible as a Python attribute
        assert ctx.auth_headers == hdrs
