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

import pytest

from codemie.datasource.loader.util import AssistantKBGoogleDocToJsonParser


class TestAssistantKBGoogleDocToJsonParser:
    @pytest.fixture
    def mock_parser(self):
        return AssistantKBGoogleDocToJsonParser(document_id="test_id1")

    @pytest.fixture
    def mock_elements(self):
        return [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "1.1.2. Title"}}],
                    "paragraphStyle": {"namedStyleType": "heading_1"},
                }
            },
            {"paragraph": {"elements": [{"textRun": {"content": "content1"}}]}},
        ]

    def test_get_element_text(self, mock_parser, mock_elements):
        result = mock_parser.get_element_text(mock_elements[0])

        assert result == "1.1.2. Title"

    def test_get_element_style(self, mock_parser, mock_elements):
        result = mock_parser.get_element_style(mock_elements[0])

        assert result == "heading_1"

    def test_get_articles(self, mock_parser, mock_elements):
        result = mock_parser.get_articles(mock_elements)

        assert result == [{"title": "Title", "content": "content1", "instructions": "", "reference": "1.1.2."}]

    def test_get_titles(self, mock_parser, mock_elements):
        result = mock_parser.get_titles(mock_elements)

        assert result == ["1.1.2. Title"]
