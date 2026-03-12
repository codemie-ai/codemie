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

import unittest
from pathlib import Path
from unittest import mock

import pytest
from pptx.presentation import Presentation

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.pptx.processor import PptxProcessor


class TestPptxProcessor(unittest.TestCase):
    def setUp(self):
        self.sample_path = Path(__file__).parent / "test.pptx"
        self.processor = PptxProcessor()
        self.sample_content = self._load_sample_pptx()
        self.file_object = FileObject(
            content=self.sample_content,
            name="test.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            owner="tester",
        )
        self.pptx_presentation = mock.Mock()

    def _load_sample_pptx(self):
        with open(self.sample_path, 'rb') as f:
            return f.read()

    def test_open_pptx_document(self):
        # Test that the document opens correctly
        pptx_doc = self.processor.open_pptx_document(self.sample_content)
        assert isinstance(pptx_doc, Presentation)

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.open_pptx_document(b'not a pptx')

    def test_get_total_slides(self):
        pptx_doc = self.processor.open_pptx_document(self.sample_content)
        result = self.processor.get_total_slides(pptx_doc)
        expected_slides = str(len(pptx_doc.slides))
        assert result == expected_slides

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.get_total_slides(None)

    def test_get_total_slides_from_files(self):
        result = self.processor.get_total_slides_from_files([self.file_object])
        assert "Total slides:" in result
        assert "test.pptx" in result

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.get_total_slides_from_files([])

    def test_extract_text_as_markdown(self):
        pptx_doc = self.processor.open_pptx_document(self.sample_content)
        markdown = self.processor.extract_text_as_markdown(pptx_doc)

        # Basic checks - should have some markdown content
        assert "# Slide" in markdown
        assert "---" in markdown  # Slide separators

        # Test with specific slides
        specific_slide = self.processor.extract_text_as_markdown(pptx_doc, [1])
        assert "# Slide 1" in specific_slide
        assert "# Slide 2" not in specific_slide

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.extract_text_as_markdown(None)

    def test_extract_text_as_json(self):
        pptx_doc = self.processor.open_pptx_document(self.sample_content)
        json_data = self.processor.extract_text_as_json(pptx_doc)

        # Check basic structure
        assert "slides" in json_data
        assert isinstance(json_data["slides"], list)

        # Check slide data structure
        if json_data["slides"]:
            slide = json_data["slides"][0]
            assert "slide_index" in slide
            assert "shapes" in slide

        # Test with specific slides
        specific_json = self.processor.extract_text_as_json(pptx_doc, [1])
        if specific_json["slides"]:
            assert len(specific_json["slides"]) == 1
            assert specific_json["slides"][0]["slide_index"] == 1

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.extract_text_as_json(None)

    def test_process_pptx_files(self):
        # Test with a single file
        result = self.processor.process_pptx_files([self.file_object])
        assert "# Slide" in result

        # Test with specific slides
        result_specific = self.processor.process_pptx_files([self.file_object], [1])
        assert "# Slide 1" in result_specific

        # Test with multiple files
        result_multi = self.processor.process_pptx_files([self.file_object, self.file_object])
        assert "ANALYZED FILE" in result_multi
        assert result_multi.count("# Slide 1") >= 2  # Should appear for each file

        # Test error handling
        with pytest.raises(ValueError):
            self.processor.process_pptx_files([])
