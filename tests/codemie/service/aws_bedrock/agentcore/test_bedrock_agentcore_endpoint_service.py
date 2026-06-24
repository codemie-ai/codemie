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

import json

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock, patch

from codemie.service.aws_bedrock.agentcore.bedrock_agentcore_endpoint_service import (
    BedrockAgentCoreEndpointService,
)
from codemie.service.aws_bedrock.exceptions import (
    AgentcoreEndpointNotFoundError,
    AgentcoreEndpointValidationError,
)
from codemie.core.exceptions import ExtendedHTTPException


_VALID_CONFIG = json.dumps({"response": {"streaming": False, "body": {"text_path": "output"}}})


def _make_client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetAgentRuntimeEndpoint")


def _base_kwargs(endpoint_name: str = "ep1", config_json: str = _VALID_CONFIG) -> dict:
    return {
        "user": MagicMock(),
        "setting": MagicMock(),
        "aws_creds": MagicMock(region="us-east-1", access_key_id="k", secret_access_key="s", session_token=None),
        "existing_entities_map": {},
        "input_runtime_id": "runtime-1",
        "input_endpoint_name": endpoint_name,
        "configuration_json": config_json,
    }


def test_process_endpoint_import_raises_validation_error_on_bad_json():
    with pytest.raises(AgentcoreEndpointValidationError):
        BedrockAgentCoreEndpointService._process_endpoint_import(**_base_kwargs(config_json="{bad json"))


@patch.object(BedrockAgentCoreEndpointService, "_bedrock_get_runtime_endpoint")
def test_process_endpoint_import_raises_not_found_on_resource_not_found(mock_get):
    mock_get.side_effect = _make_client_error("ResourceNotFoundException")

    with pytest.raises(AgentcoreEndpointNotFoundError):
        BedrockAgentCoreEndpointService._process_endpoint_import(**_base_kwargs())


@patch.object(BedrockAgentCoreEndpointService, "_bedrock_get_runtime_endpoint")
def test_process_endpoint_import_raises_not_found_on_access_denied(mock_get):
    """AWS returns AccessDeniedException for non-existent endpoints — must map to 404."""
    mock_get.side_effect = _make_client_error("AccessDeniedException")

    with pytest.raises(AgentcoreEndpointNotFoundError):
        BedrockAgentCoreEndpointService._process_endpoint_import(**_base_kwargs(endpoint_name="nonexistent-ep"))


@patch.object(BedrockAgentCoreEndpointService, "_bedrock_get_runtime_endpoint")
def test_process_endpoint_import_raises_409_when_not_ready(mock_get):
    mock_get.return_value = {"id": "ep-id", "agentRuntimeEndpointArn": "arn:aws:...", "status": "CREATING"}

    with pytest.raises(ExtendedHTTPException) as exc_info:
        BedrockAgentCoreEndpointService._process_endpoint_import(**_base_kwargs())

    assert exc_info.value.code == 409


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        ("", None),
        (json.dumps({"response": {"streaming": False, "body": {"text_path": "output"}}}), None),
        (json.dumps({"response": {"streaming": True, "chunk": {"text_path": "delta"}}}), None),
        (
            "{bad json",
            "Invalid JSON template: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
        ),
        (
            json.dumps({"message": "__QUERY_PLACEHOLDER__"}),
            "Invalid configuration: missing required 'response' key",
        ),
        (
            json.dumps({"response": {}}),
            "Invalid response configuration: Value error, body is required when streaming is False",
        ),
        (
            json.dumps({"response": {"streaming": False}}),
            "Invalid response configuration: Value error, body is required when streaming is False",
        ),
        (
            json.dumps({"response": {"streaming": True}}),
            "Invalid response configuration: Value error, chunk is required when streaming is True",
        ),
        (
            json.dumps({"response": {"streaming": False, "body": {}}}),
            "Invalid response configuration: body.text_path field required",
        ),
        (
            json.dumps({"response": {"streaming": True, "chunk": {}}}),
            "Invalid response configuration: chunk.text_path field required",
        ),
    ],
)
def test_validate_configuration_json(raw, expected):
    assert BedrockAgentCoreEndpointService._validate_configuration_json(raw) == expected
