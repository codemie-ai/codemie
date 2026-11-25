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

"""A flat GitLab tool payload (no ``query`` wrapper) is accepted instead of failing schema validation (EPMCDME-17166)."""

import json
from unittest.mock import patch

import pytest

FOREIGN_URL = "https://example.com/api/v4/projects"
FLAT = {"method": "GET", "url": FOREIGN_URL, "method_arguments": {}}


class TestFlatInput:
    def test_flat_dict_with_foreign_origin_returns_rejection(self, gitlab_tool):
        with patch("requests.request") as mock_request:
            result = gitlab_tool.invoke(dict(FLAT))

        mock_request.assert_not_called()
        assert "absolute URL rejected" in str(result)
        assert "does not match the configured GitLab origin" in str(result)

    def test_flat_dict_with_relative_url_is_requested(self, gitlab_tool, mock_response):
        with patch("requests.request", return_value=mock_response) as mock_request:
            gitlab_tool.invoke({**FLAT, "url": "/api/v4/projects"})

        assert mock_request.call_args.kwargs["url"] == "https://gitlab.example.com/api/v4/projects"

    def test_flat_dict_keeps_custom_headers(self, gitlab_tool, mock_response):
        with patch("requests.request", return_value=mock_response) as mock_request:
            gitlab_tool.invoke({**FLAT, "url": "/api/v4/projects", "custom_headers": {"X-Test": "1"}})

        assert mock_request.call_args.kwargs["headers"]["X-Test"] == "1"

    def test_flat_json_string_is_accepted(self, gitlab_tool):
        with patch("requests.request") as mock_request:
            result = gitlab_tool.invoke(json.dumps(FLAT))

        mock_request.assert_not_called()
        assert "absolute URL rejected" in str(result)

    @pytest.mark.parametrize("wrapped", [{"query": dict(FLAT)}, {"query": json.dumps(FLAT)}])
    def test_wrapped_input_is_unchanged(self, gitlab_tool, wrapped):
        with patch("requests.request") as mock_request:
            result = gitlab_tool.invoke(wrapped)

        mock_request.assert_not_called()
        assert "absolute URL rejected" in str(result)

    @pytest.mark.parametrize("payload", [{}, {"foo": "bar"}])
    def test_dict_without_method_gets_tool_message(self, gitlab_tool, payload):
        with patch("requests.request") as mock_request:
            result = gitlab_tool.invoke(payload)

        mock_request.assert_not_called()
        assert "'method' is required" in str(result)
        assert "Error parsing tool input" not in str(result)
