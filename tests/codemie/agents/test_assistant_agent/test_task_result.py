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

from codemie.agents.assistant_agent import TaskResult
from codemie.chains.base import GenerationResult


class TestTaskResult:
    def test_failed_result(self):
        result = TaskResult.failed_result("Error occurred", original_exc=None)
        assert result.result == "Error occurred"
        assert result.success is False
        assert result.intermediate_steps == []

    def test_from_agent_response_with_output(self):
        response = {'output': 'Successful output', 'intermediate_steps': ['step1', 'step2']}
        result = TaskResult.from_agent_response(response)
        assert result.result == 'Successful output'
        assert result.success is True
        assert result.intermediate_steps == ['step1', 'step2']

    def test_from_agent_response_with_generated(self):
        result = TaskResult.from_agent_response({"generated": "Generated output"})
        assert result.result == 'Generated output'
        assert result.success is True
        assert result.intermediate_steps == []

    def test_from_agent_response_empty(self):
        response = {}
        result = TaskResult.from_agent_response(response)
        assert result.result == ''
        assert result.success is False
        assert result.intermediate_steps == []

    def test_from_agent_response_no_output_no_generated(self):
        result = TaskResult.from_agent_response({})
        assert result.result == ''
        assert result.success is False
        assert result.intermediate_steps == []

    def test_from_agent_response(self):
        response = {'output': 'Successful output', 'intermediate_steps': ['step1', 'step2']}
        result = TaskResult.from_agent_response(response)
        assert result.result == 'Successful output'
        assert result.success is True
        assert result.intermediate_steps == ['step1', 'step2']

        response = GenerationResult(
            generated="Generated output",
            time_elapsed=None,
            input_tokens_used=None,
            tokens_used=100,
            success=True,
        )

        result = TaskResult.from_agent_response(response)
        assert result.result == 'Generated output'
        assert result.success is True
