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
Unit tests for UserContext model and request-scoped context variables
"""

import asyncio

import pytest

from codemie.rest_api.security.user import User, UserContext
from codemie.rest_api.security.user_context import (
    clear_current_auth_token,
    clear_current_client_access_token,
    get_current_auth_token,
    get_current_client_access_token,
    set_current_auth_token,
    set_current_client_access_token,
)


class TestUserContext:
    """Test cases for UserContext model"""

    def test_from_user_maps_all_fields(self):
        """from_user() maps all 13 non-sensitive fields from a fully-populated User."""
        user = User(
            id="u-001",
            username="jdoe",
            name="Jane Doe",
            email="jdoe@example.com",
            roles=["admin", "viewer"],
            is_admin=False,  # may be overridden by model_validator; compare against user.is_admin
            is_maintainer=False,
            user_type="internal",
            project_names=["proj-a", "proj-b"],
            admin_project_names=["proj-a"],
            knowledge_bases=["kb-1", "kb-2"],
            picture="https://example.com/avatar.png",
            auth_token="secret-token",
            tenant_id="tenant-1",
            project_limit=10,
        )
        ctx = UserContext.from_user(user)

        assert ctx.id == user.id
        assert ctx.username == user.username
        assert ctx.name == user.name
        assert ctx.email == user.email
        assert ctx.roles == user.roles
        assert ctx.is_admin == user.is_admin  # robust across ENV=local override
        assert ctx.is_maintainer == user.is_maintainer
        assert ctx.user_type == user.user_type
        assert ctx.project_names == user.project_names
        assert ctx.admin_project_names == user.admin_project_names
        assert ctx.knowledge_bases == user.knowledge_bases
        assert ctx.picture == user.picture
        assert ctx.extra_attributes == user.extra_attributes

    def test_extra_attributes_absent_from_dump_when_none(self):
        """extra_attributes=None must not appear in model_dump(exclude_none=True)."""
        user = User(id="u-no-extra")
        ctx = UserContext.from_user(user)
        dumped = ctx.model_dump(exclude_none=True)
        assert "extra_attributes" not in dumped

    def test_extra_attributes_propagates_through_from_user(self):
        """extra_attributes dict is copied verbatim by from_user()."""
        attrs = {"department": "eng", "team_id": "backend"}
        user = User(id="u-with-extra", extra_attributes=attrs)
        ctx = UserContext.from_user(user)
        assert ctx.extra_attributes == attrs

    def test_sensitive_fields_excluded_from_model_dump(self):
        """auth_token, tenant_id, project_limit must not appear in model_dump()."""
        user = User(
            id="u-sensitive",
            auth_token="secret-token",
            tenant_id="tenant-1",
            project_limit=5,
        )
        ctx = UserContext.from_user(user)
        dumped = ctx.model_dump()

        assert "auth_token" not in dumped
        assert "tenant_id" not in dumped
        assert "project_limit" not in dumped

    def test_sensitive_token_absent_from_model_dump_json(self):
        """The raw auth_token string must never appear in JSON serialization."""
        user = User(
            id="u-sensitive",
            auth_token="super-secret-token-xyz",
            tenant_id="tenant-abc",
            project_limit=5,
        )
        ctx = UserContext.from_user(user)
        json_str = ctx.model_dump_json()

        assert "super-secret-token-xyz" not in json_str
        assert "tenant-abc" not in json_str

    def test_all_defaults_user_produces_valid_context(self):
        """A User built with only id should produce a valid UserContext with no errors."""
        user = User(id="minimal-user")
        ctx = UserContext.from_user(user)

        assert ctx.id == "minimal-user"
        # All other fields derive from User defaults; none raise errors
        assert isinstance(ctx.username, str)
        assert isinstance(ctx.roles, list)
        assert isinstance(ctx.project_names, list)

    def test_roundtrip_equality(self):
        """UserContext(**ctx.model_dump()) == ctx."""
        user = User(
            id="u-roundtrip",
            username="rtrip",
            name="Round Trip",
            email="rtrip@example.com",
            roles=["viewer"],
            user_type="external",
            project_names=["p1"],
            admin_project_names=[],
            knowledge_bases=["kb-x"],
            picture="https://example.com/pic.jpg",
        )
        ctx = UserContext.from_user(user)
        ctx2 = UserContext(**ctx.model_dump())

        assert ctx2 == ctx


class TestMaskedForLogging:
    """Test cases for UserContext.masked_for_logging() (EPMCDME-13260)."""

    def _full_context(self) -> UserContext:
        return UserContext(
            id="u-001",
            username="johndoe",
            name="John Doe",
            email="john.doe@epam.com",
            roles=["admin", "viewer"],
            is_admin=True,
            is_maintainer=False,
            is_auditor=False,
            user_type="internal",
            project_names=["proj-a", "proj-b"],
            admin_project_names=["proj-a"],
            knowledge_bases=["kb1"],
            picture="https://example.com/avatar.png",
            extra_attributes={"a": 1, "b": 2},
        )

    def test_email_is_masked_to_first_char_and_domain(self):
        result = self._full_context().masked_for_logging()
        assert result["email"] == "j***@epam.com"

    def test_name_picture_username_are_masked(self):
        ctx = self._full_context()
        result = ctx.masked_for_logging()

        for key in ("name", "picture", "username"):
            assert result[key] != getattr(ctx, key), f"{key} must not appear verbatim"
            assert result[key], f"{key} mask must be non-empty"

    def test_readable_fields_pass_through_unchanged(self):
        ctx = self._full_context()
        result = ctx.masked_for_logging()

        assert result["id"] == "u-001"
        assert result["roles"] == ["admin", "viewer"]
        assert result["is_admin"] is True
        assert result["is_maintainer"] is False
        assert result["is_auditor"] is False
        assert result["user_type"] == "internal"
        assert result["project_names"] == ["proj-a", "proj-b"]
        assert result["admin_project_names"] == ["proj-a"]

    def test_unbounded_fields_collapse_to_count_only_placeholder(self):
        result = self._full_context().masked_for_logging()
        assert result["extra_attributes"] == "<redacted, 2 keys>"
        assert result["knowledge_bases"] == "<redacted, 1 items>"

    def test_no_raw_pii_or_attribute_values_in_string_form(self):
        rendered = str(self._full_context().masked_for_logging())
        assert "john.doe@epam.com" not in rendered
        assert "John Doe" not in rendered
        assert "https://example.com/avatar.png" not in rendered
        # raw extra_attributes contents never leak
        assert "'a': 1" not in rendered
        assert "'b': 2" not in rendered

    def test_none_fields_are_omitted(self):
        result = UserContext(id="only-id").masked_for_logging()
        assert result == {"id": "only-id"}


class TestMaskHelpers:
    """Module-level _mask_value / _mask_email helpers in user.py."""

    def test_mask_value_short_string_fully_masked(self):
        from codemie.rest_api.security.user import _mask_value

        assert _mask_value("ab") == "***"
        assert _mask_value("a") == "***"

    def test_mask_value_longer_string_keeps_two_char_prefix(self):
        from codemie.rest_api.security.user import _mask_value

        assert _mask_value("johndoe") == "jo***"

    def test_mask_value_passes_through_falsy(self):
        from codemie.rest_api.security.user import _mask_value

        assert _mask_value(None) is None
        assert _mask_value("") == ""

    def test_mask_email_keeps_first_local_char_and_domain(self):
        from codemie.rest_api.security.user import _mask_email

        assert _mask_email("john.doe@epam.com") == "j***@epam.com"

    def test_mask_email_without_at_falls_back_to_mask_value(self):
        from codemie.rest_api.security.user import _mask_email

        assert _mask_email("notanemail") == "no***"

    def test_mask_email_passes_through_falsy(self):
        from codemie.rest_api.security.user import _mask_email

        assert _mask_email(None) is None
        assert _mask_email("") == ""


class TestClientAccessTokenContextVar:
    """Test cases for the client access token ContextVar trio"""

    @pytest.fixture(autouse=True)
    def clean_context(self):
        clear_current_auth_token()
        clear_current_client_access_token()
        yield
        clear_current_auth_token()
        clear_current_client_access_token()

    def test_default_is_none(self):
        assert get_current_client_access_token() is None

    def test_set_and_get(self):
        set_current_client_access_token("client-token")
        assert get_current_client_access_token() == "client-token"

    def test_clear(self):
        set_current_client_access_token("client-token")
        clear_current_client_access_token()
        assert get_current_client_access_token() is None

    def test_independent_from_auth_token(self):
        """Client token and user auth token are stored in separate context vars."""
        set_current_auth_token("user-token")
        set_current_client_access_token("client-token")

        assert get_current_auth_token() == "user-token"
        assert get_current_client_access_token() == "client-token"

        clear_current_auth_token()
        assert get_current_auth_token() is None
        assert get_current_client_access_token() == "client-token"

    def test_isolation_between_async_tasks(self):
        """Each asyncio task sees its own client token."""

        async def set_and_read(token: str) -> str | None:
            set_current_client_access_token(token)
            await asyncio.sleep(0)
            return get_current_client_access_token()

        async def main():
            return await asyncio.gather(set_and_read("token-a"), set_and_read("token-b"))

        results = asyncio.run(main())
        assert results == ["token-a", "token-b"]
        assert get_current_client_access_token() is None
