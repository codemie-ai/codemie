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

"""Tests for project validation utilities."""

import pytest

from codemie.core.constants import HEADER_CODEMIE_INTEGRATION
from codemie.core.project_validator import (
    ProjectRequiredException,
    is_background_consumer,
    require_valid_project,
)


class TestRequireValidProject:
    """Test project validation logic."""

    def test_require_valid_project_with_valid_project(self):
        """Test validation succeeds with valid project."""
        result = require_valid_project("valid-project")
        assert result == "valid-project"

    def test_require_valid_project_rejects_none(self):
        """Test validation fails when project is None."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project(None)
        assert "project is required" in str(exc_info.value).lower()

    def test_require_valid_project_rejects_empty_string(self):
        """Test validation fails when project is empty string."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project("")
        assert "project is required" in str(exc_info.value).lower()

    def test_require_valid_project_rejects_whitespace_only(self):
        """Test validation fails when project is whitespace."""
        with pytest.raises(ProjectRequiredException) as exc_info:
            require_valid_project("   ")
        assert "project is required" in str(exc_info.value).lower()


class TestIsBackgroundConsumer:
    """Test background consumer identification."""

    def test_is_background_consumer_with_valid_header(self):
        """Test identification succeeds with integration header."""
        headers = {HEADER_CODEMIE_INTEGRATION: "integration-123"}
        assert is_background_consumer(headers) is True

    def test_is_background_consumer_without_header(self):
        """Test identification fails without integration header."""
        headers = {}
        assert is_background_consumer(headers) is False

    def test_is_background_consumer_with_empty_header(self):
        """Test identification fails with empty integration header."""
        headers = {HEADER_CODEMIE_INTEGRATION: ""}
        assert is_background_consumer(headers) is False

    def test_is_background_consumer_with_none_header(self):
        """Test identification fails with None integration header."""
        headers = {HEADER_CODEMIE_INTEGRATION: None}
        assert is_background_consumer(headers) is False

    def test_is_background_consumer_with_none_dict(self):
        """Test identification fails with None dict."""
        assert is_background_consumer(None) is False

    def test_is_background_consumer_case_insensitive(self):
        """Test identification works with lowercase header key."""
        headers = {HEADER_CODEMIE_INTEGRATION.lower(): "integration-123"}
        assert is_background_consumer(headers) is True

    def test_is_background_consumer_with_mixed_casing_variant(self):
        """Regression for CR-006: the old check only matched the original casing or the
        fully-lowercased form; a third casing (e.g. Title-Case from an HTTP client/proxy)
        used to be missed entirely."""
        headers = {"x-Codemie-integration": "integration-123"}
        assert is_background_consumer(headers) is True
