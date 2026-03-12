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
from unittest.mock import Mock, patch

from codemie.core.dependecies import LLMClientWrapper  # Replace 'your_module' with the actual module name


# Mock logger for testing
@pytest.fixture
def mock_logger():
    with patch('codemie.core.dependecies.logger') as mock_log:
        yield mock_log


class TestLLMClientWrapper:
    @pytest.fixture
    def wrapped_class(self):
        return Mock()

    @pytest.fixture
    def wrapper(self, wrapped_class):
        return LLMClientWrapper(wrapped_class)

    def test_getattr_returns_wrapper(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock()
        assert callable(wrapper.some_method)
        assert wrapper.some_method is not wrapped_class.some_method

    def test_wrapper_calls_original_function(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock(return_value="test_result")
        result = wrapper.some_method()
        assert result == "test_result"
        wrapped_class.some_method.assert_called_once()

    def test_wrapper_logs_body_when_present(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        wrapper.some_method(body="test_body")
        mock_logger.debug.assert_called_once_with("Call LLM with the following body:\ntest_body")

    def test_wrapper_logs_kwargs_when_no_body(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        wrapper.some_method(arg1="value1", arg2="value2")
        expected_log = "Call LLM with the following body:\n" + json.dumps({"arg1": "value1", "arg2": "value2"})
        mock_logger.debug.assert_called_once_with(expected_log)

    def test_wrapper_handles_logging_exception(self, wrapper, wrapped_class, mock_logger):
        wrapped_class.some_method = Mock()
        mock_logger.debug.side_effect = Exception("Test exception")
        wrapper.some_method(body="test_body")
        mock_logger.warning.assert_called_once_with(
            "Exception has been occurred during the logging request to LLM: \nTest exception"
        )

    def test_wrapper_passes_args_and_kwargs(self, wrapper, wrapped_class):
        wrapped_class.some_method = Mock()
        wrapper.some_method("arg1", "arg2", kwarg1="value1", kwarg2="value2")
        wrapped_class.some_method.assert_called_once_with("arg1", "arg2", kwarg1="value1", kwarg2="value2")
