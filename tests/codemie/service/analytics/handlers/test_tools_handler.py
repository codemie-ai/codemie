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

"""Unit tests for ToolsHandler."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.tools_handler import ToolsHandler


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return ToolsHandler(mock_user, mock_repository)


class TestBuildToolsUsageAggregation:
    """AD-29 obligation 1: the unique-assistants cardinality must key off the stable id."""

    def test_unique_assistants_cardinality_uses_assistant_id(self, handler):
        agg_body = handler._build_tools_usage_aggregation(query={"match_all": {}}, fetch_size=20)

        sub_aggs = agg_body["aggs"]["paginated_results"]["aggs"]
        # 4-bucket/4-metric is the "unique assistants" cardinality consumed by
        # _parse_tools_usage_result. Grouping/counting on assistant_name would let a
        # rename inflate the count by splitting one assistant's history across two
        # keyword values - the same mutable-name defect AD-29 fixes in assistant_handler.py.
        cardinality_field = sub_aggs["4-bucket"]["aggs"]["4-metric"]["cardinality"]["field"]
        assert cardinality_field == "attributes.assistant_id.keyword"
