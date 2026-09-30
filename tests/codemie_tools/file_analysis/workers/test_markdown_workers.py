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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.file_analysis.workers.markdown_workers import convert_file_to_markdown
from codemie_tools.file_analysis.workers.utf8_safe_plain_text_converter import Utf8SafePlainTextConverter


XLSB_BYTES = b"fake xlsb content"


class TestXlsbPreconversion:
    def test_xlsb_short_circuits_before_markitdown(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            return_value="## Sheet1\n| A |\n| val |",
        ) as mock_conv:
            result = convert_file_to_markdown(XLSB_BYTES, "report.xlsb")
        mock_conv.assert_called_once_with(XLSB_BYTES, sheet_names=None, visible_only=False, file_ext=".xlsb")
        assert "Sheet1" in result

    def test_xlsb_short_circuit_does_not_call_markitdown(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            return_value="md",
        ):
            # Patch the name in the module where it's used (from markitdown import MarkItDown),
            # NOT the source module — otherwise the real MarkItDown still runs.
            with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md:
                convert_file_to_markdown(XLSB_BYTES, "report.xlsb")
        mock_md.assert_not_called()

    def test_corrupt_xlsb_preconversion_raises(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            side_effect=Exception("Cannot detect file format"),
        ):
            with pytest.raises(Exception, match="Cannot detect file format"):
                convert_file_to_markdown(XLSB_BYTES, "broken.xlsb")

    def test_xlsx_file_not_affected_by_xlsb_short_circuit(self):
        mock_result = MagicMock()
        mock_result.text_content = "xlsx content"
        with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md_cls:
            mock_md = mock_md_cls.return_value
            mock_md.convert.return_value = mock_result
            with patch("codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown") as mock_xlsb:
                convert_file_to_markdown(b"xlsx bytes", "report.xlsx")
        mock_xlsb.assert_not_called()


class TestUtf8SafePlainTextConverter:
    """Unit tests for Utf8SafePlainTextConverter."""

    def _make_stream_info(self, charset=None):
        from markitdown import StreamInfo

        return StreamInfo(charset=charset)

    def test_ascii_charset_with_utf8_cyrillic_falls_back_to_utf8(self):
        """Simulates the production failure: charset='ascii' but content is UTF-8 Cyrillic."""
        import io
        from markitdown import DocumentConverterResult

        cyrillic = "123ууу test строка"
        data = cyrillic.encode("utf-8")
        converter = Utf8SafePlainTextConverter()
        result = converter.convert(io.BytesIO(data), self._make_stream_info(charset="ascii"))
        assert isinstance(result, DocumentConverterResult)
        assert "ууу" in result.markdown
        assert "строка" in result.markdown

    def test_valid_ascii_charset_with_ascii_content_passes_through(self):
        """ASCII charset with ASCII-only content works normally."""
        import io

        data = b"hello world"
        converter = Utf8SafePlainTextConverter()
        result = converter.convert(io.BytesIO(data), self._make_stream_info(charset="ascii"))
        assert "hello world" in result.markdown

    def test_utf8_charset_with_cyrillic_content_works(self):
        """UTF-8 charset with Cyrillic content decodes correctly."""
        import io

        cyrillic = "привет мир"
        data = cyrillic.encode("utf-8")
        converter = Utf8SafePlainTextConverter()
        result = converter.convert(io.BytesIO(data), self._make_stream_info(charset="utf-8"))
        assert "привет" in result.markdown

    def test_no_charset_cyrillic_uses_detection(self):
        """No charset provided: full-content detection handles Cyrillic."""
        import io

        cyrillic = "ууу123 content"
        data = cyrillic.encode("utf-8")
        converter = Utf8SafePlainTextConverter()
        result = converter.convert(io.BytesIO(data), self._make_stream_info(charset=None))
        assert "ууу" in result.markdown

    def test_undecodable_bytes_replaced_not_raised(self):
        """Bytes that cannot be decoded by any codec produce replacement characters, not an exception."""
        import io

        # Construct invalid UTF-8 bytes
        garbage = b"\x80\x81\x82\x83 text"
        converter = Utf8SafePlainTextConverter()
        result = converter.convert(io.BytesIO(garbage), self._make_stream_info(charset="ascii"))
        assert result.markdown is not None


class TestConvertFileToMarkdownCyrillic:
    """Integration tests: convert_file_to_markdown handles UTF-8 Cyrillic without crashing.

    These tests use realistic markdown content so that Magika (MarkItDown's file-type detector)
    correctly identifies the file as text without needing a filename hint. That mirrors the
    production scenario: a real .md file is uploaded, Magika marks it as text, charset_normalizer
    reads only the first 4 KiB (all ASCII), returns 'ascii', and PlainTextConverter then fails
    decoding the full content that contains Cyrillic bytes at position >4096.
    """

    @staticmethod
    def _build_realistic_md_cyrillic_after_4k() -> bytes:
        """Build a realistic markdown table where Cyrillic first appears past byte 4096.

        Magika can identify this as text without a filename hint because it looks like
        real markdown content, not repeated filler bytes.
        """
        header = "# API Handler Test\n\n| User | ID | Token | Value | Active | Role |\n|---|---|---|---|---|---|\n"
        # ~150 ASCII rows push the file past 4096 bytes before the Cyrillic row
        ascii_rows = "".join(f"|user{i}@epam.com|{i}|tok{i}|value{i}|true|Owner|\n" for i in range(150))
        cyrillic_row = "|Ivan_Nikitin1@epam.com|981|foh49wbsv|123ууу|false|SimulationOwner|\n"
        return (header + ascii_rows + cyrillic_row).encode("utf-8")

    def test_cyrillic_past_4096_does_not_raise(self):
        """Regression: UnicodeDecodeError must not propagate for UTF-8 Cyrillic at position >4096."""
        file_bytes = self._build_realistic_md_cyrillic_after_4k()
        assert next((i for i, b in enumerate(file_bytes) if b >= 128), None) > 4096, "precondition"
        result = convert_file_to_markdown(file_bytes, "data.md")
        assert result is not None

    def test_cyrillic_past_4096_content_preserved(self):
        """Cyrillic content in the file is present in the converted output."""
        file_bytes = self._build_realistic_md_cyrillic_after_4k()
        result = convert_file_to_markdown(file_bytes, "data.md")
        assert "ууу" in result

    def test_plain_ascii_file_unaffected(self):
        """Plain ASCII text files continue to convert correctly after the fix."""
        file_bytes = b"# Hello\n\nThis is plain ASCII text.\n"
        result = convert_file_to_markdown(file_bytes, "readme.md")
        assert "Hello" in result

    def test_mixed_ascii_cyrillic_file_works(self):
        """A short file with both ASCII and Cyrillic characters is fully converted."""
        mixed = "# Header\n\n123ууу mixed content строка\n".encode("utf-8")
        result = convert_file_to_markdown(mixed, "mixed.md")
        assert "ууу" in result


class TestDocxPptxRouting:
    """Regression tests for EPMCDME-13887: DOCX must never be routed to PptxConverter.

    MarkItDown falls back to magika's content-based type guess when no stream_info hint is
    given. Because DOCX and PPTX are both OOXML zip containers, magika can misclassify a real
    DOCX as PPTX-like, and MarkItDown then tries PptxConverter first, which fails opening the
    real (docx) bytes with python-pptx. These tests force that exact magika misclassification
    by patching markitdown._markitdown.magika.Magika (class-level, since convert_file_to_markdown
    builds its own MarkItDown() instance whose __init__ does self._magika = magika.Magika()) and
    verify the real DocxConverter/PptxConverter pipeline routes correctly once a stream_info
    extension hint is supplied.
    """

    DOCX_PATH = Path(__file__).parent.parent / "docx" / "test.docx"
    PPTX_PATH = Path(__file__).parent.parent / "pptx" / "test.pptx"

    @staticmethod
    def _read(path):
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _make_pptx_like_magika_result():
        """A magika identify_stream() result shaped like a real PPTX-content prediction."""
        result = MagicMock()
        result.status = "ok"
        result.prediction.output.label = "pptx"
        result.prediction.output.is_text = False
        result.prediction.output.extensions = ["pptx"]
        result.prediction.output.mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        return result

    def test_docx_misclassified_by_magika_still_converts_after_fix(self):
        """Core regression test: a real .docx must convert even when magika guesses pptx."""
        docx_bytes = self._read(self.DOCX_PATH)
        with patch("markitdown._markitdown.magika.Magika") as mock_magika_cls:
            mock_magika_cls.return_value.identify_stream.return_value = self._make_pptx_like_magika_result()
            result = convert_file_to_markdown(docx_bytes, "report.docx")
        assert result is not None
        assert len(result) > 0

    def test_pptx_conversion_unaffected_by_fix(self):
        """No PPTX regression: the real .pptx fixture must still convert, unmocked."""
        pptx_bytes = self._read(self.PPTX_PATH)
        result = convert_file_to_markdown(pptx_bytes, "slides.pptx")
        assert result is not None
        assert len(result) > 0

    def test_plain_docx_happy_path_still_works(self):
        """Guard against regressions in the normal (unmocked) docx conversion path."""
        docx_bytes = self._read(self.DOCX_PATH)
        result = convert_file_to_markdown(docx_bytes, "report.docx")
        assert result is not None
        assert len(result) > 0

    def test_extensionless_filename_passes_none_not_empty_string(self):
        """ext == '' must become stream_info.extension=None, not '' (see spec pitfall note)."""
        docx_bytes = b"placeholder"
        with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md_cls:
            mock_result = MagicMock()
            mock_result.text_content = "converted"
            mock_md = mock_md_cls.return_value
            mock_md.convert.return_value = mock_result
            convert_file_to_markdown(docx_bytes, "noext")
        _, kwargs = mock_md.convert.call_args
        assert kwargs["stream_info"].extension is None
