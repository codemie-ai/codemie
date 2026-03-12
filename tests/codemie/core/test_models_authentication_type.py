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

"""
Tests for AuthenticationType enum in core.models module.
"""

import pytest

from codemie.core.models import AuthenticationType


class TestAuthenticationType:
    """Test suite for AuthenticationType enum."""

    def test_enum_values_and_attributes(self):
        """Test that enum values have correct key and display_value attributes."""
        # Test BASIC
        assert AuthenticationType.BASIC.key == "basic"
        assert AuthenticationType.BASIC.display_value == "Basic"

        # Test APIKEY
        assert AuthenticationType.APIKEY.key == "apikey"
        assert AuthenticationType.APIKEY.display_value == "ApiKey"

        # Test BEARER
        assert AuthenticationType.BEARER.key == "bearer"
        assert AuthenticationType.BEARER.display_value == "Bearer"

    def test_eq_case_insensitive_string_comparison(self):
        """Test case-insensitive comparison with string values."""
        # Test lowercase
        assert AuthenticationType.BASIC == "basic"
        assert AuthenticationType.APIKEY == "apikey"
        assert AuthenticationType.BEARER == "bearer"

        # Test uppercase
        assert AuthenticationType.BASIC == "BASIC"
        assert AuthenticationType.APIKEY == "APIKEY"
        assert AuthenticationType.BEARER == "BEARER"

        # Test mixed case
        assert AuthenticationType.BASIC == "Basic"
        assert AuthenticationType.BASIC == "bAsIc"
        assert AuthenticationType.APIKEY == "ApiKey"
        assert AuthenticationType.APIKEY == "aPiKeY"
        assert AuthenticationType.BEARER == "Bearer"
        assert AuthenticationType.BEARER == "BeArEr"

    def test_eq_with_non_matching_strings(self):
        """Test comparison with non-matching string values."""
        assert AuthenticationType.BASIC != "invalid"
        assert AuthenticationType.BASIC != "bearer"
        assert AuthenticationType.APIKEY != "basic"
        assert AuthenticationType.BEARER != "apikey"

    def test_from_string_valid_values(self):
        """Test from_string method with valid string values."""
        # Test lowercase
        assert AuthenticationType.from_string("basic") == AuthenticationType.BASIC
        assert AuthenticationType.from_string("apikey") == AuthenticationType.APIKEY
        assert AuthenticationType.from_string("bearer") == AuthenticationType.BEARER

        # Test uppercase
        assert AuthenticationType.from_string("BASIC") == AuthenticationType.BASIC
        assert AuthenticationType.from_string("APIKEY") == AuthenticationType.APIKEY
        assert AuthenticationType.from_string("BEARER") == AuthenticationType.BEARER

        # Test mixed case
        assert AuthenticationType.from_string("Basic") == AuthenticationType.BASIC
        assert AuthenticationType.from_string("ApiKey") == AuthenticationType.APIKEY
        assert AuthenticationType.from_string("Bearer") == AuthenticationType.BEARER

    def test_from_string_with_enum_input(self):
        """Test from_string method when input is already an enum value."""
        assert AuthenticationType.from_string(AuthenticationType.BASIC) == AuthenticationType.BASIC
        assert AuthenticationType.from_string(AuthenticationType.APIKEY) == AuthenticationType.APIKEY
        assert AuthenticationType.from_string(AuthenticationType.BEARER) == AuthenticationType.BEARER

    def test_from_string_invalid_values(self):
        """Test from_string method with invalid string values."""
        with pytest.raises(ValueError, match="Unknown authentication type: invalid"):
            AuthenticationType.from_string("invalid")

        with pytest.raises(ValueError, match="Unknown authentication type: oauth"):
            AuthenticationType.from_string("oauth")

        with pytest.raises(ValueError, match="Unknown authentication type: digest"):
            AuthenticationType.from_string("digest")

    def test_from_string_empty_and_none_values(self):
        """Test from_string method with empty and None values."""
        with pytest.raises(ValueError, match="Unknown authentication type: "):
            AuthenticationType.from_string("")

        with pytest.raises(ValueError, match="Unknown authentication type: None"):
            AuthenticationType.from_string(None)
