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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx.shared import Inches

from codemie_tools.file_analysis.docx.exceptions import (
    DocumentReadError,
)
from codemie_tools.file_analysis.docx.models import (
    HeaderInfo,
    ParagraphInfo,
    Position,
    TableData,
    QueryType,
)
from codemie_tools.file_analysis.docx.reader import DocxReader

expected_text_parts = [
    "Context",
    "",
    "Scenario:\xa0",
    "Travelers planning international trips often need to compare accommodation prices in their local currency to better understand overall costs. Currently, the search functionality in the travel booking system allows filtering only by destination, travel dates, and the number of passengers. However, it does not provide an option to view prices in different currencies. This limitation can lead to confusion and make budgeting difficult, as travelers must manually convert prices using external tools.",
    "\xa0",
    "Challenges:\xa0",
    "Inability to filter prices by currency:  ",
    "Users cannot view accommodation prices in their preferred or local currency. This gap results in confusion and increases the potential for errors in cost estimation.  ",
    "Manual Currency Conversions:  ",
    "Without built-in currency conversion, users must rely on external tools or websites. These extra steps waste time and increase the risk of conversion mistakes.",
    "\xa0",
    "Requirements for User Story Generation:\xa0",
    "Add an advanced filtering option that allows users to view and compare accommodation prices in the following currencies:  ",
    "US Dollar ",
    "Euro  ",
    "Japanese Yen ",
    "British Pound Sterling  ",
    "Swiss Franc  ",
    "Canadian Dollar ",
    "Australian Dollar   ",
    "Chinese Yuan Renminbi   ",
    "The system should display costs in the chosen currency, along with the currency code and numeric identifier, to help travelers more accurately budget and decide on accommodations. For example, AUD 36 Australian dollar.",
]


@pytest.fixture
def docx_reader():
    """Provides an instance of the DocxReader."""
    return DocxReader()


@pytest.fixture
def temp_docx_file():
    """
    Fixture to copy the static 'test.docx' to a temporary location
    and yields the path to the temporary file, ensuring its cleanup.
    """
    yield Path(__file__).parent / "test.docx"


@pytest.fixture
def mock_docx_document():
    doc = MagicMock()

    # Mock paragraphs
    p1 = MagicMock(text="This is a title.")
    p1.style = MagicMock(name="Heading 1")
    p1.runs = [MagicMock(text="This is a title.", bold=True)]

    p2 = MagicMock(text="Normal paragraph text.")
    p2.runs = [MagicMock(text="Normal paragraph text.", bold=False)]

    p3 = MagicMock(text="Another paragraph.")
    p3.runs = [MagicMock(text="Another paragraph.", bold=False)]
    doc.paragraphs = [p1, p2, p3]

    # Mock table
    table = MagicMock()
    table.rows = [
        MagicMock(cells=[MagicMock(text="Header 1"), MagicMock(text="Header 2")]),
        MagicMock(cells=[MagicMock(text="Data 1"), MagicMock(text="Data 2")]),
    ]
    doc.tables = [table]

    # Mock sections for page dimensions
    section = MagicMock()
    section.page_width = Inches(8.5)
    section.page_height = Inches(11.0)
    section.top_margin = Inches(1.0)
    section.right_margin = Inches(1.25)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.25)
    doc.sections = [section]

    # Mock core properties
    doc.core_properties = MagicMock(
        title="Test Document",
        author="Test Author",
        keywords="test, docx",
    )
    yield doc


def test_extract_text_from_document(mock_docx_document):
    """Tests the static _extract_text method."""
    text = DocxReader._extract_text(mock_docx_document)
    expected_text = "This is a title.\nNormal paragraph text.\nAnother paragraph.\nHeader 1 | Header 2\nData 1 | Data 2"
    assert text == expected_text


def test_process_paragraphs_from_document(mock_docx_document):
    """Tests the static _process_paragraphs method."""
    paragraphs, headers, _ = DocxReader._process_paragraphs(mock_docx_document.paragraphs)

    assert len(paragraphs) == 3
    assert paragraphs[0].text == "This is a title."
    assert paragraphs[1].text == "Normal paragraph text."

    assert len(headers) == 3
    assert headers[0].text == "This is a title."
    assert headers[0].level == 1


def test_create_sections():
    """Tests the static _create_sections method."""
    p1 = ParagraphInfo(text="Heading 1", style="Heading 1", position=Position(page=1, x=0, y=0))
    p2 = ParagraphInfo(text="Content 1", style="Normal", position=Position(page=1, x=0, y=1))
    p3 = ParagraphInfo(text="Heading 2", style="Heading 2", position=Position(page=1, x=0, y=2))
    p4 = ParagraphInfo(text="Content 2", style="Normal", position=Position(page=1, x=0, y=3))

    headers = [
        HeaderInfo(text="Heading 1", level=1, position=Position(page=1, x=0, y=0)),
        HeaderInfo(text="Heading 2", level=2, position=Position(page=1, x=0, y=2)),
    ]
    paragraphs = [p1, p2, p3, p4]

    sections = DocxReader._create_sections(paragraphs, headers)

    assert len(sections) == 2
    assert sections[0].title == "Heading 1"
    assert sections[0].content[0].text == "Content 1"
    assert sections[1].title == "Heading 2"
    assert sections[1].content[0].text == "Content 2"


def test_extract_page_dimensions(mock_docx_document):
    """Tests the static _extract_page_dimensions method."""
    width, height, margins = DocxReader._extract_page_dimensions(mock_docx_document)
    assert width == 8.5
    assert height == 11.0
    assert margins == {"top": 1.0, "right": 1.25, "bottom": 1.0, "left": 1.25}

    mock_document_no_sections = MagicMock(sections=[])
    width, height, margins = DocxReader._extract_page_dimensions(mock_document_no_sections)
    assert width == 8.5
    assert height == 11.0
    assert margins == {"top": 1.0, "right": 1.0, "bottom": 1.0, "left": 1.0}


def test_extract_metadata(mock_docx_document):
    """Tests the static _extract_metadata method."""
    metadata = DocxReader._extract_metadata(mock_docx_document)
    assert metadata["title"] == "Test Document"
    assert metadata["author"] == "Test Author"
    assert metadata["keywords"] == "test, docx"
    assert metadata["paragraph_count"] == 3
    assert metadata["table_count"] == 1
    assert metadata["word_count"] > 0


def test_extract_tables(mock_docx_document):
    """Tests the static _extract_tables method."""
    tables = DocxReader._extract_tables(mock_docx_document)
    assert len(tables) == 1
    assert isinstance(tables[0], TableData)
    assert tables[0].rows == [["Header 1", "Header 2"], ["Data 1", "Data 2"]]
    assert tables[0].headers == ["Header 1", "Header 2"]
    assert tables[0].metadata["table_index"] == 0


def test_read_with_markitdown_read_error(docx_reader):
    """Tests error handling for general read errors."""
    with patch("codemie_tools.file_analysis.docx.reader.Document", side_effect=IOError("File not found")):
        with pytest.raises(DocumentReadError):
            docx_reader.read_with_markitdown("non_existent.docx", QueryType.TEXT)


@patch("codemie_tools.file_analysis.docx.reader.DocxReader._extract_images")
def test_read_with_actual_docx_file(
    docx_reader,
    temp_docx_file,
):
    """
    Tests DocxReader.read_with_markitdown using an actual, temporary DOCX file.
    Image extraction related functions are mocked to avoid real filesystem interactions.
    """
    content = docx_reader.read_with_markitdown(str(temp_docx_file), QueryType.TEXT)

    for part in content.text.split("\n"):
        assert part in expected_text_parts
