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

from unittest.mock import Mock

import pytest
from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from codemie_tools.utils.image_processor import ImageProcessor


@pytest.fixture
def mock_chat_model():
    """Fixture providing a mock chat model for testing."""
    mock = Mock(spec=BaseChatModel)
    mock.invoke.return_value = AIMessage(content="Mocked extracted text")
    return mock


@pytest.fixture
def image_processor(mock_chat_model):
    """Fixture providing an ImageProcessor instance with a mock chat model."""
    return ImageProcessor(chat_model=mock_chat_model)


@pytest.fixture
def pdf_processor(mock_chat_model):
    """Fixture providing a PdfProcessor instance with a mock chat model."""
    return PdfProcessor(chat_model=mock_chat_model)
