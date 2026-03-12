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

from codemie_tools.data_management.elastic.elastic_wrapper import SearchElasticIndexResults
from codemie_tools.data_management.elastic.tools import SearchElasticIndex
from codemie_tools.data_management.elastic.models import ElasticConfig


class TestSearchElasticIndex:
    @pytest.fixture
    def search_elastic_index(self):
        # Mocking elastic_config with minimal required data for initialization
        mock_elastic_config = ElasticConfig(url='http://localhost:9200', api_key=('mock', 'api_key'))
        return SearchElasticIndex(config=mock_elastic_config)

    @pytest.mark.parametrize(
        "index, query, expected_output",
        [
            ("test-index", "{}", SearchElasticIndexResults()),
            ("", "{\"query\": {\"match_all\": {}}}", SearchElasticIndexResults()),
            ("test-index", "{\"query\": {\"match_none\": {}}}", SearchElasticIndexResults()),
        ],
    )
    def test_execute(self, search_elastic_index, index, query, expected_output):
        with patch('codemie_tools.data_management.elastic.tools.SearchElasticIndexResults.search') as mock_search:
            mock_search.return_value = expected_output
            result = search_elastic_index.execute(index=index, query=query)
            mock_search.assert_called_once_with(
                index=index, query=eval(query), elastic_config=search_elastic_index.config
            )
            assert result == expected_output
