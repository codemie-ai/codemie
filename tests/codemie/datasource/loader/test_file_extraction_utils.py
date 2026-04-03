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

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes, is_binary_extractable


class TestIsBinaryExtractable(unittest.TestCase):
    # --- supported binary extensions ---

    def test_returns_true_for_pdf(self):
        self.assertTrue(is_binary_extractable("report.pdf"))

    def test_returns_true_for_docx(self):
        self.assertTrue(is_binary_extractable("document.docx"))

    def test_returns_true_for_xlsx(self):
        self.assertTrue(is_binary_extractable("spreadsheet.xlsx"))

    def test_returns_true_for_pptx(self):
        self.assertTrue(is_binary_extractable("slides.pptx"))

    def test_returns_true_for_msg(self):
        self.assertTrue(is_binary_extractable("email.msg"))

    def test_returns_true_for_jpg(self):
        self.assertTrue(is_binary_extractable("photo.jpg"))

    def test_returns_true_for_jpeg(self):
        self.assertTrue(is_binary_extractable("photo.jpeg"))

    def test_returns_true_for_png(self):
        self.assertTrue(is_binary_extractable("screenshot.png"))

    # --- text / unsupported extensions ---

    def test_returns_false_for_txt(self):
        self.assertFalse(is_binary_extractable("readme.txt"))

    def test_returns_false_for_py(self):
        self.assertFalse(is_binary_extractable("script.py"))

    def test_returns_false_for_js(self):
        self.assertFalse(is_binary_extractable("app.js"))

    def test_returns_false_for_unknown_extension(self):
        self.assertFalse(is_binary_extractable("archive.xyz"))

    # --- case insensitivity ---

    def test_case_insensitive_pdf_uppercase(self):
        self.assertTrue(is_binary_extractable("REPORT.PDF"))

    def test_case_insensitive_png_uppercase(self):
        self.assertTrue(is_binary_extractable("IMAGE.PNG"))

    def test_case_insensitive_docx_mixed(self):
        self.assertTrue(is_binary_extractable("Doc.Docx"))

    # --- full paths ---

    def test_full_path_pdf_returns_true(self):
        self.assertTrue(is_binary_extractable("/some/path/report.pdf"))

    def test_full_path_txt_returns_false(self):
        self.assertFalse(is_binary_extractable("/some/path/readme.txt"))

    def test_full_path_png_returns_true(self):
        self.assertTrue(is_binary_extractable("/var/data/image.png"))


class TestExtractDocumentsFromBytes(unittest.TestCase):
    """Tests for extract_documents_from_bytes — verifies loader selection by file extension."""

    def _make_mock_loader(self, doc: Document) -> MagicMock:
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.return_value = iter([doc])
        return mock_loader_instance

    def test_csv_loader_selected_for_csv_file(self):
        # Arrange — patch LOADERS so the mock class is resolved at call time
        mock_csv_loader_class = MagicMock()
        mock_doc = Document(page_content="col1,col2", metadata={"source": "data.csv"})
        mock_csv_loader_class.return_value = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["csv"] = mock_csv_loader_class
        try:
            # Act
            result = extract_documents_from_bytes(b"col1,col2\nval1,val2", "data.csv")
        finally:
            utils.LOADERS.update(original_loaders)

        # Assert
        mock_csv_loader_class.assert_called_once()
        self.assertEqual(len(result), 1)

    @patch("codemie.datasource.loader.file_extraction_utils._build_pdf_images_parser")
    def test_pdf_loader_selected_for_pdf_file(self, mock_build_parser):
        # Arrange — replace LOADERS entry for pdf with a mock loader class
        mock_build_parser.return_value = MagicMock()
        mock_pdf_loader_class = MagicMock()
        mock_doc = Document(page_content="page content", metadata={"source": "report.pdf", "file_path": "/tmp/x.pdf"})
        mock_pdf_loader_class.return_value = self._make_mock_loader(mock_doc)

        import codemie.datasource.loader.file_extraction_utils as utils

        original_loaders = dict(utils.LOADERS)
        utils.LOADERS["pdf"] = mock_pdf_loader_class
        try:
            # Act
            result = extract_documents_from_bytes(b"%PDF-1.4 content", "report.pdf")
        finally:
            utils.LOADERS.update(original_loaders)

        # Assert
        mock_pdf_loader_class.assert_called_once()
        # source metadata should be rewritten to the original file_name
        self.assertEqual(result[0].metadata["source"], "report.pdf")

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_plain_text_loader_used_for_unknown_extension(self, mock_plain_loader_class):
        # Arrange — unknown extension not in LOADERS, so PlainTextLoader is the default
        mock_doc = Document(page_content="plain content", metadata={"source": "file.xyz"})
        mock_plain_loader_class.return_value = self._make_mock_loader(mock_doc)

        # Act
        result = extract_documents_from_bytes(b"plain content", "file.xyz")

        # Assert
        mock_plain_loader_class.assert_called_once()
        self.assertEqual(len(result), 1)

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_source_metadata_rewritten_to_file_name(self, mock_plain_loader_class):
        # Arrange — loader returns a doc whose source comes from the temp path
        mock_doc = Document(page_content="data", metadata={"source": "/tmp/tmpXXX.xyz"})
        mock_plain_loader_class.return_value = self._make_mock_loader(mock_doc)

        # Act
        result = extract_documents_from_bytes(b"data", "file.xyz")

        # Assert — source must be overwritten with the original file name
        self.assertEqual(result[0].metadata["source"], "file.xyz")

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_returns_empty_list_when_loader_raises_unicode_error(self, mock_plain_loader_class):
        # Arrange
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "reason")
        mock_plain_loader_class.return_value = mock_loader_instance

        # Act
        result = extract_documents_from_bytes(b"\xff\xfe bad bytes", "bad.xyz")

        # Assert
        self.assertEqual(result, [])

    @patch("codemie.datasource.loader.file_extraction_utils.PlainTextLoader")
    def test_returns_empty_list_when_loader_raises_value_error(self, mock_plain_loader_class):
        # Arrange
        mock_loader_instance = MagicMock()
        mock_loader_instance.lazy_load.side_effect = ValueError("Unsupported file type")
        mock_plain_loader_class.return_value = mock_loader_instance

        # Act
        result = extract_documents_from_bytes(b"content", "file.xyz")

        # Assert
        self.assertEqual(result, [])
