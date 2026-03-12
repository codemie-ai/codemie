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

import os
import pathlib
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from pptx import Presentation, presentation
from pydantic import ValidationError

from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.pptx.processor import PptxProcessor
from codemie_tools.file_analysis.pptx.tools import PPTXToolInput, PPTXTool, QueryType


@pytest.fixture
def samples_dir():
    """Get the path to the samples directory."""
    return pathlib.Path(__file__).parent


def test_pptx_tool_input_valid():
    """
    Test valid inputs for PPTXToolInput.
    """
    # Valid case with specific slides and query
    valid_input = {"slides": [1, 2, 3], "query": "Text"}
    tool_input = PPTXToolInput(**valid_input)
    assert tool_input.slides == [1, 2, 3]
    assert tool_input.query == "Text"

    # Valid case with empty slides and query
    valid_input = {"slides": [], "query": "Text_with_Metadata"}
    tool_input = PPTXToolInput(**valid_input)
    assert tool_input.slides == []
    assert tool_input.query == "Text_with_Metadata"


def test_pptx_tool_input_invalid_slides():
    """
    Test invalid inputs for the slides field in PPTXToolInput.
    """
    # Invalid slides: non-integer values
    invalid_input = {"slides": ["a", "b", "c"], "query": "Text"}
    with pytest.raises(ValidationError):
        PPTXToolInput(**invalid_input)

    # Invalid slides: mixed types
    invalid_input = {"slides": [1, "two", 3], "query": "Text"}
    with pytest.raises(ValidationError):
        PPTXToolInput(**invalid_input)

    # Invalid slides: non-list
    invalid_input = {"slides": "not_a_list", "query": "Text"}
    with pytest.raises(ValidationError):
        PPTXToolInput(**invalid_input)


def test_pptx_tool_input_edge_cases():
    """
    Test edge cases for PPTXToolInput.
    """
    # Edge case: slides with duplicates
    valid_input = {"slides": [1, 2, 2, 3], "query": "Text"}
    tool_input = PPTXToolInput(**valid_input)
    assert tool_input.slides == [1, 2, 2, 3]


def create_valid_pptx():
    """
    Helper function to create an in-memory valid .pptx file.
    """
    pptx = BytesIO()
    presentation = Presentation()
    slide_layout = presentation.slide_layouts[0]
    slide = presentation.slides.add_slide(slide_layout)
    slide.shapes.title.text = "Test Slide"
    presentation.save(pptx)
    pptx.seek(0)
    return pptx


def test_pptx_tool_load_valid_presentation():
    """
    Test that PPTXTool correctly loads a valid .pptx file into a Presentation object.
    """
    from codemie_tools.base.file_object import FileObject

    valid_pptx = create_valid_pptx()
    file_obj = FileObject(
        name="test.pptx",
        content=valid_pptx.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    pptx_tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Get the presentation by using open_pptx_document
    pptx_presentation = pptx_tool.pptx_processor.open_pptx_document(file_obj.content)

    # Assertions
    assert isinstance(pptx_presentation, presentation.Presentation)
    assert len(pptx_presentation.slides) == 1
    assert pptx_presentation.slides[0].shapes.title.text == "Test Slide"


def test_pptx_tool_load_invalid_presentation():
    """
    Test that PPTXTool raises an exception when loading an invalid .pptx file.
    """
    from codemie_tools.base.file_object import FileObject

    invalid_pptx = BytesIO(b"This is not a valid PPTX file.")

    # Create invalid file object
    file_obj = FileObject(
        name="invalid.pptx",
        content=invalid_pptx.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    pptx_tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Try to open the invalid PPTX content
    with pytest.raises(Exception) as exc_info:
        # This should raise an exception when trying to open the invalid file
        pptx_tool.pptx_processor.open_pptx_document(file_obj.content)

    # Assertions
    assert "zipfile.BadZipFile" in str(exc_info.value) or "Failed to open PPTX document" in str(exc_info.value)


def test_pptx_tool_load_empty_presentation():
    """
    Test that PPTXTool raises an exception when loading an empty file.
    """
    from codemie_tools.base.file_object import FileObject

    empty_pptx = BytesIO()

    # Create empty file object
    file_obj = FileObject(
        name="empty.pptx",
        content=empty_pptx.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    pptx_tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Try to open the empty PPTX content
    with pytest.raises(Exception) as exc_info:
        # This should raise an exception when trying to open the empty file
        pptx_tool.pptx_processor.open_pptx_document(file_obj.content)

    # Assertions
    assert "zipfile.BadZipFile" in str(exc_info.value) or "Failed to open PPTX document" in str(exc_info.value)


def create_test_presentation(slide_texts):
    """
    Helper function to create an in-memory presentation with given slide texts.
    """
    pptx = BytesIO()
    presentation = Presentation()
    slide_layout = presentation.slide_layouts[0]

    for text in slide_texts:
        slide = presentation.slides.add_slide(slide_layout)
        slide.shapes.title.text = text

    presentation.save(pptx)
    pptx.seek(0)
    return pptx


def test_pptx_tool_process_all_slides():
    """
    Test that PPTXTool processes all slides when `slides` is empty.
    """
    from codemie_tools.base.file_object import FileObject

    slide_texts = ["Slide 1", "Slide 2", "Slide 3"]
    pptx = create_test_presentation(slide_texts)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Process all slides
    output = tool.execute(slides=[], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert len(output["slides"]) == 3
    assert output["slides"][0]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 1"
    assert output["slides"][1]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 2"
    assert output["slides"][2]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 3"


def test_pptx_tool_process_specific_slides():
    """
    Test that PPTXTool processes a subset of slides based on input slide numbers.
    """
    from codemie_tools.base.file_object import FileObject

    slide_texts = ["Slide 1", "Slide 2", "Slide 3"]
    pptx = create_test_presentation(slide_texts)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Process specific slides
    output = tool.execute(slides=[1, 3], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert len(output["slides"]) == 2
    assert output["slides"][0]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 1"
    assert output["slides"][1]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 3"


def test_pptx_tool_process_invalid_slides():
    """
    Test that PPTXTool handles invalid slide numbers gracefully.
    """
    from codemie_tools.base.file_object import FileObject

    slide_texts = ["Slide 1", "Slide 2", "Slide 3"]
    pptx = create_test_presentation(slide_texts)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Process invalid slides (out-of-range indices)
    output = tool.execute(slides=[4, 5], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert len(output["slides"]) == 0


def test_pptx_tool_query_text():
    """
    Test that PPTXTool converts slides to Markdown for query='Text'.
    """
    from codemie_tools.base.file_object import FileObject

    slide_texts = ["Slide 1", "Slide 2", "Slide 3"]
    pptx = create_test_presentation(slide_texts)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Query for Markdown output
    output = tool.execute(slides=[], query=QueryType.TEXT)

    # Assertions
    assert isinstance(output, str)
    assert "# Slide 1" in output
    assert "# Slide 2" in output
    assert "# Slide 3" in output


def test_pptx_tool_query_text_with_metadata():
    """
    Test that PPTXTool converts slides to JSON with metadata for query='Text_with_Metadata'.
    """
    from codemie_tools.base.file_object import FileObject

    slide_texts = ["Slide 1", "Slide 2", "Slide 3"]
    pptx = create_test_presentation(slide_texts)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Query for JSON output with metadata
    output = tool.execute(slides=[], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert isinstance(output, dict)
    assert "slides" in output
    assert len(output["slides"]) == 3
    assert output["slides"][0]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 1"
    assert output["slides"][1]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 2"
    assert output["slides"][2]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 3"


def create_test_presentation_with_metadata(slides_metadata):
    """
    Helper function to create an in-memory presentation with metadata on slides.
    """
    pptx = BytesIO()
    presentation = Presentation()
    slide_layout = presentation.slide_layouts[0]

    for metadata in slides_metadata:
        slide = presentation.slides.add_slide(slide_layout)
        if "title" in metadata:
            slide.shapes.title.text = metadata["title"]
        if "author" in metadata:
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = f"Author: {metadata['author']}"

    presentation.save(pptx)
    pptx.seek(0)
    return pptx


def test_pptx_tool_metadata_extraction():
    """
    Test that PPTXTool extracts metadata correctly.
    """
    from codemie_tools.base.file_object import FileObject

    slides_metadata = [
        {"title": "Slide 1", "author": "Author 1"},
        {"title": "Slide 2", "author": "Author 2"},
        {"title": "Slide 3"},
    ]
    pptx = create_test_presentation_with_metadata(slides_metadata)
    file_obj = FileObject(
        name="test.pptx",
        content=pptx.read(),
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))

    # Extract metadata
    output = tool.execute(slides=[], query=QueryType.TEXT_WITH_METADATA)

    # Assertions
    assert len(output["slides"]) == 3
    assert output["slides"][0]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 1"
    assert "notes" in output["slides"][0]
    assert "Author: Author 1" in output["slides"][0]["notes"][0]["runs"][0]["text"]

    assert output["slides"][1]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 2"
    assert "notes" in output["slides"][1]
    assert "Author: Author 2" in output["slides"][1]["notes"][0]["runs"][0]["text"]

    assert output["slides"][2]["shapes"][0]["text_frame"][0]["runs"][0]["text"] == "Slide 3"
    assert "notes" in output["slides"][2]
    assert len(output["slides"][2]["notes"]) == 1


def test_pptx_tool_query_text_with_text_with_metadata(samples_dir):
    from codemie_tools.base.file_object import FileObject

    filepath = os.path.join(samples_dir, "test.pptx")
    with open(filepath, "rb") as f:
        pptx_bytes = f.read()
    file_obj = FileObject(
        name="test.pptx",
        content=pptx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))
    dict_output = tool.execute(slides=[], query=QueryType.TEXT_WITH_METADATA)
    filepath = os.path.join(samples_dir, "test.pptx.json")
    with open(filepath, "rb") as fstr:
        json_output = fstr.read().decode()

    assert str(dict_output) == json_output


def test_pptx_tool_query_text_with_text(samples_dir):
    from codemie_tools.base.file_object import FileObject

    filepath = os.path.join(samples_dir, "test.pptx")
    with open(filepath, "rb") as f:
        pptx_bytes = f.read()
    file_obj = FileObject(
        name="test.pptx",
        content=pptx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        owner="test",
    )
    tool = PPTXTool(config=FileAnalysisConfig(input_files=[file_obj]))
    md_output = tool.execute(slides=[], query=QueryType.TEXT)

    filepath = os.path.join(samples_dir, "test.pptx.md")
    with open(filepath, "rb") as fstr:
        json_output = fstr.read().decode()

    assert md_output == json_output


@pytest.fixture
def mock_text_frame():
    """Fixture to create a mock text frame object."""
    text_frame = MagicMock()
    return text_frame


def test_parse_text_frame_empty(mock_text_frame):
    """Test parsing an empty text frame."""
    mock_text_frame.paragraphs = []

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [], "Expected empty list for an empty text frame."


def test_parse_text_frame_single_paragraph_single_run(mock_text_frame):
    """Test parsing a text frame with one paragraph and one run."""
    paragraph = MagicMock()
    run = MagicMock()
    run.text = "Test text"

    paragraph.runs = [run]
    mock_text_frame.paragraphs = [paragraph]

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [
        {"paragraph_index": 1, "runs": [{"run_index": 1, "text": "Test text"}]}
    ], "Expected a single paragraph with one run."


def test_parse_text_frame_multiple_paragraphs_and_runs(mock_text_frame):
    """Test parsing a text frame with multiple paragraphs and runs."""
    paragraph1 = MagicMock()
    run1_1 = MagicMock()
    run1_1.text = "Paragraph 1, Run 1"
    run1_2 = MagicMock()
    run1_2.text = "Paragraph 1, Run 2"
    paragraph1.runs = [run1_1, run1_2]

    paragraph2 = MagicMock()
    run2_1 = MagicMock()
    run2_1.text = "Paragraph 2, Run 1"
    paragraph2.runs = [run2_1]

    mock_text_frame.paragraphs = [paragraph1, paragraph2]

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [
        {
            "paragraph_index": 1,
            "runs": [{"run_index": 1, "text": "Paragraph 1, Run 1"}, {"run_index": 2, "text": "Paragraph 1, Run 2"}],
        },
        {"paragraph_index": 2, "runs": [{"run_index": 1, "text": "Paragraph 2, Run 1"}]},
    ], "Expected multiple paragraphs with respective runs."


def test_parse_text_frame_paragraph_with_empty_run(mock_text_frame):
    """Test parsing a text frame with a paragraph containing an empty run."""
    paragraph = MagicMock()
    run = MagicMock()
    run.text = ""

    paragraph.runs = [run]
    mock_text_frame.paragraphs = [paragraph]

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [
        {"paragraph_index": 1, "runs": [{"run_index": 1, "text": ""}]}
    ], "Expected paragraph with an empty run to be handled."


def test_parse_text_frame_multiple_empty_paragraphs(mock_text_frame):
    """Test parsing a text frame with multiple empty paragraphs."""
    paragraph1 = MagicMock()
    paragraph1.runs = []

    paragraph2 = MagicMock()
    paragraph2.runs = []

    mock_text_frame.paragraphs = [paragraph1, paragraph2]

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [
        {"paragraph_index": 1, "runs": []},
        {"paragraph_index": 2, "runs": []},
    ], "Expected empty paragraphs to be handled."


def test_parse_text_frame_paragraph_with_none_run_text(mock_text_frame):
    """Test parsing a text frame with a paragraph containing a run with None text."""
    paragraph = MagicMock()
    run = MagicMock()
    run.text = None

    paragraph.runs = [run]
    mock_text_frame.paragraphs = [paragraph]

    result = PptxProcessor._parse_text_frame(mock_text_frame)

    assert result == [
        {"paragraph_index": 1, "runs": [{"run_index": 1, "text": None}]}
    ], "Expected run with None text to be handled."
