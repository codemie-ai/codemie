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

import uuid
from unittest.mock import patch, MagicMock
import pytest

from codemie.rest_api.models.logs import LogEntry, LogRetrieveRequest
from codemie.service.logs import LogService  # Adjust import as needed


@pytest.fixture
def sample_uuid():
    return uuid.uuid4()


@pytest.fixture
def sample_log_hits():
    return [
        {"_source": {"message": "test message 1", "@timestamp": "2023-09-01T12:00:00"}},
        {"_source": {"message": "test message 2", "@timestamp": "2023-09-01T13:00:00"}},
    ]


@patch("codemie.service.logs.ElasticSearchClient")
@patch("codemie.service.logs.config")
def test_get_logs_by_target_field_success(mock_config, mock_es_client, sample_uuid, sample_log_hits):
    # Arrange
    mock_config.ELASTIC_LOGS_INDEX = "logs-index"
    mock_search = MagicMock()
    mock_search.search.return_value = {"hits": {"hits": sample_log_hits}}
    mock_es_client.get_client.return_value = mock_search

    target = LogRetrieveRequest(field="conversation_id", value=str(sample_uuid))

    # Act
    result = LogService.get_logs_by_target_field(target)

    # Assert
    mock_es_client.get_client.assert_called_once()
    mock_search.search.assert_called_once_with(
        index="logs-index",
        body={"query": {"term": {"conversation_id.keyword": str(sample_uuid)}}},
        size=LogService.MAX_RESPONSE_SIZE,
    )
    assert isinstance(result, list)
    assert all(isinstance(entry, LogEntry) for entry in result)
    assert result[0].message == "test message 1"
    assert result[1].timestamp == "2023-09-01T13:00:00"


def test_log_retrieve_request_field_constraint():
    # Should work for allowed values
    for valid_field in ["conversation_id", "execution_id", "request_uuid"]:
        req = LogRetrieveRequest(field=valid_field, value="some_value")
        assert req.field == valid_field

    # Should raise for an invalid value
    with pytest.raises(ValueError):
        LogRetrieveRequest(field="not_allowed_field", value="some_value")


@patch("codemie.service.logs.ElasticSearchClient")
@patch("codemie.service.logs.config")
def test_get_logs_by_target_field_empty_result(mock_config, mock_es_client, sample_uuid):
    # Arrange
    mock_config.ELASTIC_LOGS_INDEX = "logs-index"
    mock_search = MagicMock()
    mock_search.search.return_value = {"hits": {"hits": []}}
    mock_es_client.get_client.return_value = mock_search

    target = LogRetrieveRequest(field="conversation_id", value=str(sample_uuid))

    # Act
    result = LogService.get_logs_by_target_field(target)

    # Assert
    assert result == []


@patch("codemie.service.logs.ElasticSearchClient")
@patch("codemie.service.logs.config")
def test_get_logs_by_target_field_missing_timestamp_raises(mock_config, mock_es_client, sample_uuid):
    # Hit without "@timestamp"
    sample_hit = [{"_source": {"message": "has no timestamp"}}]
    mock_config.ELASTIC_LOGS_INDEX = "logs-index"
    mock_search = MagicMock()
    mock_search.search.return_value = {"hits": {"hits": sample_hit}}
    mock_es_client.get_client.return_value = mock_search

    target = LogRetrieveRequest(field="conversation_id", value=str(sample_uuid))

    # Should raise ValueError due to missing @timestamp
    with pytest.raises(ValueError, match="Each log document must contain 'message' and '@timestamp' fields."):
        LogService.get_logs_by_target_field(target)


@patch("codemie.service.logs.ElasticSearchClient")
@patch("codemie.service.logs.config")
def test_get_logs_by_target_field_missing_message_raises(mock_config, mock_es_client, sample_uuid):
    # Hit without "message"
    sample_hit = [{"_source": {"@timestamp": "2023-09-01T13:00:00"}}]
    mock_config.ELASTIC_LOGS_INDEX = "logs-index"
    mock_search = MagicMock()
    mock_search.search.return_value = {"hits": {"hits": sample_hit}}
    mock_es_client.get_client.return_value = mock_search

    target = LogRetrieveRequest(field="conversation_id", value=str(sample_uuid))

    # Should raise ValueError due to missing message
    with pytest.raises(ValueError, match="Each log document must contain 'message' and '@timestamp' fields."):
        LogService.get_logs_by_target_field(target)
