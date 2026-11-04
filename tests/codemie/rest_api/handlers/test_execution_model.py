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

"""RequestExecutionModel: the model a request runs on, before and after the agent is built."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from codemie.core.exceptions import ModelNotWhitelistedException, NoDefaultModelException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.handlers.execution_model import RequestExecutionModel
from codemie.rest_api.models.assistant import AssistantType

MODULE = "codemie.rest_api.handlers.execution_model"


@pytest.fixture
def assistant() -> Mock:
    return Mock(llm_model_type="own-model", type=AssistantType.CODEMIE)


@pytest.fixture
def request_() -> AssistantChatRequest:
    return AssistantChatRequest(text="Hello", llm_model="picked-model")


def _tracker(assistant: Mock, bedrock: bool = False):
    patcher = patch(f"{MODULE}.BedrockOrchestratorService.is_bedrock_assistant", return_value=bedrock)
    patcher.start()
    return RequestExecutionModel(assistant), patcher


def test_resolves_through_the_policy_once_per_request(assistant, request_):
    tracker, bedrock = _tracker(assistant)
    try:
        with patch(f"{MODULE}.AssistantService.resolve_execution_model", return_value="resolved") as resolve:
            assert tracker.for_request(request_) == "resolved"
            assert tracker.for_request(request_) == "resolved"
        resolve.assert_called_once_with(assistant, request_)
    finally:
        bedrock.stop()


def test_built_agent_model_replaces_the_pre_build_one(assistant, request_):
    tracker, bedrock = _tracker(assistant)
    try:
        with patch(f"{MODULE}.AssistantService.resolve_execution_model", return_value="pre-build"):
            tracker.for_request(request_)
        tracker.record_agent(Mock(llm_model="agent-model"))
        assert tracker.for_request(request_) == "agent-model"
    finally:
        bedrock.stop()


@pytest.mark.parametrize("agent_model", ["", None, 7])
def test_agent_without_a_model_is_not_recorded(assistant, request_, agent_model):
    tracker, bedrock = _tracker(assistant)
    try:
        tracker.record_agent(Mock(llm_model=agent_model))
        with patch(f"{MODULE}.AssistantService.resolve_execution_model", return_value="resolved"):
            assert tracker.for_request(request_) == "resolved"
    finally:
        bedrock.stop()


@pytest.mark.parametrize("error", [ModelNotWhitelistedException("m", "p", details="d"), NoDefaultModelException("p")])
def test_falls_back_to_the_raw_pick_when_the_project_allows_no_model(assistant, request_, error):
    tracker, bedrock = _tracker(assistant)
    try:
        with patch(f"{MODULE}.AssistantService.resolve_execution_model", side_effect=error):
            assert tracker.for_request(request_) == "picked-model"
    finally:
        bedrock.stop()


@pytest.mark.parametrize("remote", ["a2a", "bedrock"])
def test_remote_assistants_keep_the_raw_pick_without_the_policy(assistant, request_, remote):
    if remote == "a2a":
        assistant.type = AssistantType.A2A
    tracker, bedrock = _tracker(assistant, bedrock=remote == "bedrock")
    try:
        with patch(f"{MODULE}.AssistantService.resolve_execution_model") as resolve:
            assert tracker.for_request(request_) == "picked-model"
        resolve.assert_not_called()
    finally:
        bedrock.stop()


def test_raw_pick_falls_back_to_the_assistants_own_model(assistant):
    tracker, bedrock = _tracker(assistant, bedrock=True)
    try:
        assert tracker.for_request(AssistantChatRequest(text="Hello")) == "own-model"
    finally:
        bedrock.stop()
