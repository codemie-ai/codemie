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

from unittest.mock import MagicMock, patch

from codemie.service.workflow_execution.workflow_output_change_request_service import WorkflowOutputChangeRequestService


@patch("codemie.chains.pure_chat_chain.PureChatChain.generate")
def test_run(mock_generate):
    mock_generate.return_value = MagicMock(generated="This is the changed output.")

    original_output = "This is the original output."
    changes_request = "Please change the output to be more concise."

    result = WorkflowOutputChangeRequestService.run(original_output, changes_request)

    assert result == "This is the changed output."
