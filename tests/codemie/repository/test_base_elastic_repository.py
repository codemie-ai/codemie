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
from unittest.mock import MagicMock

from codemie.repository.base_elastic_repository import BaseElasticRepository
from codemie.core.models import GitRepo


class DummyElasticRepository(BaseElasticRepository):
    def to_entity(self, source):
        return MagicMock()


@pytest.fixture
def mock_es():
    """Fixture to mock Elasticsearch instance."""
    mock_es_instance = MagicMock()
    return mock_es_instance


@pytest.fixture
def repo(mock_es):
    """Fixture to create a DummyElasticRepository instance with mocked ES."""
    return DummyElasticRepository(mock_es, "test_index")


@pytest.fixture
def git_repo():
    return GitRepo(
        name="test_repo",
        link="http://test-repo.com",
        branch="main",
        indexType="code",
        appId="test_app",
        description="description",
    )


def test_base_get_by_id(mock_es, repo):
    mock_es.get.return_value = {"_source": {"id": "1", "name": "Test"}}
    result = repo.get_by_id("1")
    assert result is not None


def test_base_get_all(mock_es, repo):
    mock_es.search.return_value = {"hits": {"hits": [{"_source": {"id": "1", "name": "Test"}}]}}
    results = repo.get_all()
    assert len(results) > 0


def test_base_save(mock_es, repo):
    entity = MagicMock()
    entity.get_identifier.return_value = "1"
    entity.model_dump.return_value = {"id": "1", "name": "Test"}

    result = repo.save(entity)
    assert result is not None


def test_base_update(mock_es, repo):
    entity = MagicMock()
    entity.get_identifier.return_value = "1"
    entity.model_dump.return_value = {"id": "1", "name": "Test"}

    result = repo.update(entity)
    assert result is not None
