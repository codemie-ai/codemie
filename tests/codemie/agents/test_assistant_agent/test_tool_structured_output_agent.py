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

import json
import pytest

from unittest.mock import Mock
from pydantic import BaseModel

from langchain_core.prompts.chat import ChatPromptTemplate
from langchain_core.messages import AIMessage
from langchain_core.language_models.chat_models import BaseChatModel

# Import your functions here
from codemie.agents.structured_tool_agent import middleware, create_structured_tool_calling_agent


class DummyStructuredOutput(BaseModel):
    foo: str


@pytest.fixture
def input_dict():
    return {"input": "Hello", "agent_scratchpad": [], "chat_history": []}


@pytest.fixture
def fake_prompt():
    prompt = Mock(spec=ChatPromptTemplate)
    prompt.format.return_value = "formatted_prompt"
    prompt.input_variables = ["input", "agent_scratchpad", "chat_history"]
    prompt.partial_variables = []
    return prompt


@pytest.fixture
def fake_llm_with_tools():
    llm = Mock(spec=BaseChatModel)
    llm.invoke = Mock()
    return llm


@pytest.fixture
def fake_llm_with_structured_output():
    llm = Mock(spec=BaseChatModel)
    llm.invoke = Mock()
    return llm


def test_middleware_tool_call(input_dict, fake_prompt, fake_llm_with_tools, fake_llm_with_structured_output):
    # LLM returns tool_calls
    llm_output = Mock()
    llm_output.additional_kwargs = {"tool_calls": True}
    fake_llm_with_tools.invoke.return_value = llm_output

    result = middleware(input_dict, fake_llm_with_tools, fake_llm_with_structured_output, fake_prompt)
    assert result == llm_output


def test_middleware_structured_output_basemodel(
    input_dict, fake_prompt, fake_llm_with_tools, fake_llm_with_structured_output
):
    # LLM does NOT return tool_calls
    llm_output = Mock()
    llm_output.additional_kwargs = {}
    fake_llm_with_tools.invoke.return_value = llm_output

    # Structured output is a BaseModel
    structured_output = DummyStructuredOutput(foo="bar")
    fake_llm_with_structured_output.invoke.return_value = structured_output

    result = middleware(input_dict, fake_llm_with_tools, fake_llm_with_structured_output, fake_prompt)
    assert isinstance(result, AIMessage)
    assert result.content == structured_output.model_dump_json()


def test_middleware_structured_output_dict(
    input_dict, fake_prompt, fake_llm_with_tools, fake_llm_with_structured_output
):
    # LLM does NOT return tool_calls
    llm_output = Mock()
    llm_output.additional_kwargs = {}
    fake_llm_with_tools.invoke.return_value = llm_output

    # Structured output is a dict
    structured_output = {"foo": "bar"}
    fake_llm_with_structured_output.invoke.return_value = structured_output

    result = middleware(input_dict, fake_llm_with_tools, fake_llm_with_structured_output, fake_prompt)
    assert isinstance(result, AIMessage)
    assert result.content == json.dumps(structured_output)


def test_create_structured_tool_calling_agent_missing_vars(fake_prompt):
    fake_prompt.input_variables = ["input", "chat_history"]  # missing agent_scratchpad
    fake_prompt.partial_variables = []
    llm = Mock()
    tools = []
    structured_output = DummyStructuredOutput

    with pytest.raises(ValueError):
        create_structured_tool_calling_agent(llm, tools, fake_prompt, structured_output)


def test_create_structured_tool_calling_agent_no_bind_tools(fake_prompt):
    llm = Mock()
    del llm.bind_tools  # remove bind_tools
    tools = []
    structured_output = DummyStructuredOutput

    with pytest.raises(ValueError):
        create_structured_tool_calling_agent(llm, tools, fake_prompt, structured_output)


def test_create_structured_tool_calling_agent_success(fake_prompt):
    class DummyLLM(Mock):
        def bind_tools(self, tools):
            return self

        def with_structured_output(self, structured_output):
            return self

    llm = DummyLLM()
    tools = []
    structured_output = DummyStructuredOutput

    agent = create_structured_tool_calling_agent(llm, tools, fake_prompt, structured_output)
    # The agent should be a Runnable
    from langchain_core.runnables.base import Runnable

    assert isinstance(agent, Runnable)
