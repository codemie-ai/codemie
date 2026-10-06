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

from unittest.mock import MagicMock, patch

from codemie.service.assistant_evaluation_service import AssistantEvaluationService


def _make_context(system_prompt):
    """Assemble mocks and invoke _run_evaluation_task. Returns the mock handler."""
    mock_response = MagicMock()
    mock_response.generated = "response text"

    mock_handler = MagicMock()
    mock_handler.process_request.return_value = mock_response

    root_span = MagicMock()
    item = MagicMock()
    item.input = "test query"
    # MagicMock supports __enter__/__exit__; configure the context manager return value
    item.run.return_value.__enter__ = MagicMock(return_value=root_span)
    item.run.return_value.__exit__ = MagicMock(return_value=False)

    dataset = MagicMock()
    dataset.items = [item]

    def _run_experiment(name, task):
        mock_result = MagicMock()
        mock_result.item_results = [task(item=item) for item in dataset.items]
        return mock_result

    dataset.run_experiment = _run_experiment

    mock_langfuse = MagicMock()
    mock_langfuse.get_dataset.return_value = dataset

    with (
        patch(
            "codemie.service.assistant_evaluation_service.get_request_handler",
            return_value=mock_handler,
        ),
        patch(
            "codemie.service.assistant_evaluation_service.require_langfuse_client",
            return_value=mock_langfuse,
        ),
    ):
        AssistantEvaluationService._run_evaluation_task(
            assistant=MagicMock(),
            dataset_id="ds-1",
            experiment_name="exp-1",
            system_prompt=system_prompt,
        )

    return mock_handler


class TestRunEvaluationTask:
    def test_without_system_prompt_does_not_raise(self):
        """Omitting system_prompt must not raise a ValidationError."""
        # Before the fix, this raises ValidationError because None is passed
        # to AssistantChatRequest.system_prompt which requires a str.
        mock_handler = _make_context(system_prompt=None)
        chat_request = mock_handler.process_request.call_args[0][0]
        assert chat_request.system_prompt == ""

    def test_with_system_prompt_forwards_value(self):
        """Providing system_prompt must forward it to AssistantChatRequest."""
        mock_handler = _make_context(system_prompt="custom override")
        chat_request = mock_handler.process_request.call_args[0][0]
        assert chat_request.system_prompt == "custom override"

    def test_conversation_id_is_unique_uuid_per_item(self):
        """Each item gets its own UUID as conversation_id so its trace links to a unique session."""
        import uuid as _uuid

        mock_handler = _make_context(system_prompt=None)
        chat_request = mock_handler.process_request.call_args[0][0]
        _uuid.UUID(chat_request.conversation_id)  # raises if not a valid UUID
        assert chat_request.conversation_id != "exp-1"

    def test_propagate_attributes_receives_same_id_as_conversation_id(self):
        """propagate_attributes session_id must equal the conversation_id sent to CodeMie."""
        import uuid as _uuid

        mock_response = MagicMock()
        mock_response.generated = "response text"
        mock_handler = MagicMock()
        mock_handler.process_request.return_value = mock_response

        item = MagicMock()
        item.input = "test query"
        dataset = MagicMock()
        dataset.items = [item]

        def _run_experiment(name, task):
            return MagicMock(item_results=[task(item=item)])

        dataset.run_experiment = _run_experiment

        mock_langfuse = MagicMock()
        mock_langfuse.get_dataset.return_value = dataset

        with (
            patch("codemie.service.assistant_evaluation_service.get_request_handler", return_value=mock_handler),
            patch("codemie.service.assistant_evaluation_service.require_langfuse_client", return_value=mock_langfuse),
        ):
            AssistantEvaluationService._run_evaluation_task(
                assistant=MagicMock(),
                dataset_id="ds-1",
                experiment_name="exp-1",
                system_prompt=None,
            )

        chat_request = mock_handler.process_request.call_args[0][0]
        _, prop_kwargs = mock_langfuse.propagate_attributes.call_args
        assert prop_kwargs["session_id"] == chat_request.conversation_id
        _uuid.UUID(prop_kwargs["session_id"])
