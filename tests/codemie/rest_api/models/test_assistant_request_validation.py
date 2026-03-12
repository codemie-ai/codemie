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

from codemie.rest_api.a2a.types import AgentCard, AgentCapabilities, AgentSkill
from codemie.rest_api.models.assistant import AssistantRequest, AssistantType


def test_codemie_type_validation():
    # Test valid Codemie type with required fields
    valid_request = AssistantRequest(
        name="Test Assistant",
        description="Test Description",
        system_prompt="Test System Prompt",
        llm_model_type="gpt-4",
        type=AssistantType.CODEMIE,
    )
    assert valid_request.type == AssistantType.CODEMIE
    assert valid_request.system_prompt == "Test System Prompt"
    assert valid_request.llm_model_type == "gpt-4"

    # Test invalid Codemie type without system_prompt
    with pytest.raises(ValueError, match="system_prompt is required when type is Codemie"):
        AssistantRequest(
            name="Test Assistant",
            description="Test Description",
            llm_model_type="gpt-4",
            type=AssistantType.CODEMIE,
        )

    # Test invalid Codemie type without llm_model_type
    with pytest.raises(ValueError, match="llm_model_type is required when type is Codemie"):
        AssistantRequest(
            name="Test Assistant",
            description="Test Description",
            system_prompt="Test System Prompt",
            type=AssistantType.CODEMIE,
        )


def test_a2a_type_validation():
    # Create a valid agent card
    agent_card = AgentCard(
        name="Test Agent",
        description="Test Agent Description",
        url="https://example.com/agent",
        version="1.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="skill1",
                name="Test Skill",
                description="Test Skill Description",
            )
        ],
    )

    # Test valid A2A type with required fields
    valid_request = AssistantRequest(
        name="Test Assistant",
        type=AssistantType.A2A,
        agent_card=agent_card,
    )
    assert valid_request.type == AssistantType.A2A
    assert valid_request.agent_card == agent_card
    assert valid_request.system_prompt == ""
    assert valid_request.description == "Test Agent Description"


def test_auto_set_type_from_agent_card():
    # Create a valid agent card
    agent_card = AgentCard(
        name="Test Agent",
        description="Test Agent Description",
        url="https://example.com/agent",
        version="1.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="skill1",
                name="Test Skill",
                description="Test Skill Description",
            )
        ],
    )

    # Test that type is automatically set to A2A when agent_card is provided
    request = AssistantRequest(
        name="Test Assistant",
        agent_card=agent_card,
    )
    assert request.type == AssistantType.A2A
    assert request.agent_card == agent_card
    assert request.system_prompt == ""
    assert request.description == "Test Agent Description"

    # Test that providing both agent_card and explicit type=CODEMIE still sets type to A2A
    request = AssistantRequest(
        name="Test Assistant",
        type=AssistantType.CODEMIE,  # This will be overridden
        agent_card=agent_card,
        system_prompt="This will be overridden",
        description="This will be overridden",
    )
    assert request.type == AssistantType.A2A
    assert request.agent_card == agent_card
    assert request.system_prompt == ""
    assert request.description == "Test Agent Description"
