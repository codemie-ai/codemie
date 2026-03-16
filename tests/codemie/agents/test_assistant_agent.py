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

from pydantic import BaseModel

from codemie.agents.assistant_agent import TaskResult
from codemie.chains.base import GenerationResult


class SampleOutput(BaseModel):
    field: str
    value: int


def make_generation_result(generated=None, success=True):
    return GenerationResult(
        generated=generated,
        time_elapsed=0.1,
        input_tokens_used=None,
        tokens_used=None,
        success=success,
    )


def test_from_agent_response_with_string_result():
    response = make_generation_result(generated="some output", success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == "some output"
    assert result.success is True


def test_from_agent_response_success_mirrors_response():
    response = make_generation_result(generated="output", success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == "output"
    assert result.success is False


def test_from_agent_response_none_generated_returns_empty_string():
    response = make_generation_result(generated=None, success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False


def test_from_agent_response_empty_string_generated_returns_empty_string():
    response = make_generation_result(generated="", success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False


def test_from_agent_response_dict_generated_is_json_serialized():
    payload = {"key": "value", "count": 3}
    response = make_generation_result(generated=payload, success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == json.dumps(payload)
    assert result.success is True


def test_from_agent_response_pydantic_generated_is_json_serialized():
    model = SampleOutput(field="test", value=42)
    response = make_generation_result(generated=model, success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == model.model_dump_json()
    assert result.success is True


def test_from_agent_response_dict_with_output_key():
    response = {"output": "agent answer", "intermediate_steps": [("step1", "obs1")]}

    result = TaskResult.from_agent_response(response)

    assert result.result == "agent answer"
    assert result.success is True
    assert result.intermediate_steps == [("step1", "obs1")]


def test_from_agent_response_dict_with_generated_key():
    response = {"generated": "chain answer"}

    result = TaskResult.from_agent_response(response)

    assert result.result == "chain answer"
    assert result.success is True


def test_from_agent_response_dict_with_no_known_key():
    response = {"unknown_key": "some value"}

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False
