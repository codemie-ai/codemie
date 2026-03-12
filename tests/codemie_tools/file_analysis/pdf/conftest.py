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

"""
Test fixtures for PDF processor tests.
"""

import os
from unittest.mock import Mock

import pytest

from codemie_tools.file_analysis.pdf.processor import PdfProcessor


@pytest.fixture
def mock_chat_model():
    """Create a mock chat model for testing."""
    return Mock(name="mock_chat_model")


@pytest.fixture
def pdf_processor():
    """Create a PDF processor with a mock image processor for testing."""
    mock_chat_model = Mock()
    return PdfProcessor(chat_model=mock_chat_model)


@pytest.fixture
def sample_pdf_path():
    """Provide a path to a sample PDF file for testing."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples", "sample.pdf")


@pytest.fixture
def mock_image_processor():
    """Create a mock image processor for testing."""
    mock = Mock()
    mock.extract_text_from_image_bytes.return_value = "Mocked image text"
    return mock


@pytest.fixture
def mock_pdf_document():
    """Create a mock PDF document for testing."""
    mock_doc = Mock()

    # Set up mock pages
    mock_page1 = Mock()
    mock_page1.extract_text.return_value = "Page 1 content"
    mock_page1.extract_tables.return_value = []

    mock_page2 = Mock()
    mock_page2.extract_text.return_value = "Page 2 content"
    mock_page2.extract_tables.return_value = []

    mock_page3 = Mock()
    mock_page3.extract_text.return_value = "Page 3 content"
    mock_page3.extract_tables.return_value = []

    # Configure document pages list
    mock_doc.pages = [mock_page1, mock_page2, mock_page3]
    mock_doc.close = Mock()

    return mock_doc
