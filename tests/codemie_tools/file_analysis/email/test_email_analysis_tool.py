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

"""Unit tests for EmailAnalysisTool."""

import email.mime.base
import email.mime.multipart
import email.mime.text
from email import encoders as email_encoders
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.email.tools import EmailAnalysisTool
from codemie_tools.file_analysis.models import FileAnalysisConfig


def _build_eml(
    subject: str = "Test Subject",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str = "",
    body: str = "Hello World",
    attachments: list | None = None,
) -> bytes:
    """Build a minimal RFC-2822 EML message as bytes."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg.attach(email.mime.text.MIMEText(body, "plain"))
    for fname, data in attachments or []:
        part = email.mime.base.MIMEBase("application", "octet-stream")
        part.set_payload(data)
        email_encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)
    return msg.as_bytes()


def _make_tool(eml_bytes: bytes, filename: str = "test.eml") -> EmailAnalysisTool:
    file_obj = FileObject(name=filename, mime_type="message/rfc822", owner="test", content=eml_bytes)
    return EmailAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))


def _make_config() -> FileAnalysisConfig:
    file_obj = FileObject(name="x.eml", mime_type="message/rfc822", owner="test", content=_build_eml())
    return FileAnalysisConfig(input_files=[file_obj])


def test_supported_mime_types_includes_rfc822():
    assert "message/rfc822" in EmailAnalysisTool(_make_config())._get_supported_mime_types()


def test_supported_mime_types_includes_outlook():
    assert "application/vnd.ms-outlook" in EmailAnalysisTool(_make_config())._get_supported_mime_types()


def test_supported_extensions_includes_eml():
    assert ".eml" in EmailAnalysisTool(_make_config())._get_supported_extensions()


def test_supported_extensions_includes_msg():
    assert ".msg" in EmailAnalysisTool(_make_config())._get_supported_extensions()


def test_execute_raises_when_no_email_files():
    file_obj = FileObject(name="doc.pdf", mime_type="application/pdf", owner="test", content=b"data")
    tool = EmailAnalysisTool(config=FileAnalysisConfig(input_files=[file_obj]))
    with pytest.raises(ValueError, match="requires at least one EML or MSG file"):
        tool.execute()


def test_execute_eml_contains_from_header():
    result = _make_tool(_build_eml(sender="alice@example.com")).execute()
    assert "alice@example.com" in result


def test_execute_eml_contains_to_header():
    result = _make_tool(_build_eml(to="bob@example.com")).execute()
    assert "bob@example.com" in result


def test_execute_eml_contains_subject():
    result = _make_tool(_build_eml(subject="Project Alpha")).execute()
    assert "Project Alpha" in result


def test_execute_eml_contains_body():
    result = _make_tool(_build_eml(body="Please review the attached report.")).execute()
    assert "Please review the attached report." in result


def test_execute_eml_contains_cc_when_present():
    result = _make_tool(_build_eml(cc="charlie@example.com")).execute()
    assert "charlie@example.com" in result


def test_execute_eml_lists_attachment_filenames_by_default():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4"), ("data.xlsx", b"PK\x03\x04")])
    result = _make_tool(eml).execute()
    assert "report.pdf" in result
    assert "data.xlsx" in result


def test_execute_eml_shows_attachment_sizes():
    payload = b"%PDF-1.4 minimal"
    eml = _build_eml(attachments=[("doc.pdf", payload)])
    result = _make_tool(eml).execute()
    assert "bytes" in result


def test_execute_eml_does_not_extract_attachment_content_by_default():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    result = _make_tool(eml).execute()
    assert "### Attachment:" not in result


def test_execute_eml_analyzes_requested_attachment():
    eml = _build_eml(attachments=[("report.pdf", b"%PDF-1.4")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="PDF extracted text") as mock_analyze:
        result = tool.execute(attachment_names=["report.pdf"])
    mock_analyze.assert_called_once()
    assert "### Attachment: report.pdf" in result
    assert "PDF extracted text" in result


def test_execute_eml_attachment_name_match_is_case_insensitive():
    eml = _build_eml(attachments=[("Report.PDF", b"%PDF-1.4")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="content") as mock_analyze:
        tool.execute(attachment_names=["report.pdf"])
    mock_analyze.assert_called_once()


def test_execute_eml_skips_unlisted_attachments():
    eml = _build_eml(attachments=[("a.pdf", b"data"), ("b.xlsx", b"data")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment", return_value="text") as mock_analyze:
        tool.execute(attachment_names=["a.pdf"])
    assert mock_analyze.call_count == 1
    assert mock_analyze.call_args[0][1] == "a.pdf"


def test_execute_eml_no_analysis_when_attachment_names_empty():
    eml = _build_eml(attachments=[("doc.pdf", b"%PDF")])
    tool = _make_tool(eml)
    with patch.object(tool, "_analyze_attachment") as mock_analyze:
        tool.execute(attachment_names=[])
    mock_analyze.assert_not_called()


def test_analyze_attachment_dispatches_to_pdf_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pdf.tools.PDFTool") as mock_pdf_cls:
        mock_pdf_cls.return_value.execute.return_value = "pdf text"
        result = tool._analyze_attachment(b"%PDF-1.4", "contract.pdf")
    mock_pdf_cls.assert_called_once()
    assert result == "pdf text"


def test_analyze_attachment_dispatches_to_docx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.docx.tools.DocxTool") as mock_docx_cls:
        mock_docx_cls.return_value.execute.return_value = "docx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "letter.docx")
    mock_docx_cls.assert_called_once()
    assert result == "docx text"


def test_analyze_attachment_dispatches_to_xlsx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.xlsx.tools.XlsxTool") as mock_xlsx_cls:
        mock_xlsx_cls.return_value.execute.return_value = "xlsx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "budget.xlsx")
    mock_xlsx_cls.assert_called_once()
    assert result == "xlsx text"


def test_analyze_attachment_dispatches_to_xlsx_tool_for_xls():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.xlsx.tools.XlsxTool") as mock_xlsx_cls:
        mock_xlsx_cls.return_value.execute.return_value = "xls text"
        result = tool._analyze_attachment(b"\xd0\xcf\x11\xe0", "legacy.xls")
    mock_xlsx_cls.assert_called_once()
    assert result == "xls text"


def test_analyze_attachment_dispatches_to_pptx_tool():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pptx.tools.PPTXTool") as mock_pptx_cls:
        mock_pptx_cls.return_value.execute.return_value = "pptx text"
        result = tool._analyze_attachment(b"PK\x03\x04", "slides.pptx")
    mock_pptx_cls.assert_called_once()
    assert result == "pptx text"


def test_analyze_attachment_uses_file_analysis_tool_for_unknown_format():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.file_analysis_tool.FileAnalysisTool") as mock_fa_cls:
        mock_fa_cls.return_value._process_single_file.return_value = "plain text content"
        result = tool._analyze_attachment(b"plain content", "notes.txt")
    mock_fa_cls.assert_called_once()
    assert result == "plain text content"


def test_analyze_attachment_uses_file_analysis_tool_for_csv():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.file_analysis_tool.FileAnalysisTool") as mock_fa_cls:
        mock_fa_cls.return_value._process_single_file.return_value = "csv content"
        result = tool._analyze_attachment(b"a,b\n1,2", "data.csv")
    mock_fa_cls.assert_called_once()
    assert result == "csv content"


def test_analyze_attachment_returns_error_message_on_exception():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.pdf.tools.PDFTool") as mock_pdf_cls:
        mock_pdf_cls.return_value.execute.side_effect = RuntimeError("parse error")
        result = tool._analyze_attachment(b"%PDF", "broken.pdf")
    assert "Could not extract content from broken.pdf" in result


def test_analyze_image_attachment_calls_markitdown():
    tool = EmailAnalysisTool(config=_make_config())
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "A graph showing revenue trends"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md):
        result = tool._analyze_image_attachment(b"\x89PNG\r\n\x1a\n", "chart.png", ".png")
    assert result == "A graph showing revenue trends"


def test_analyze_image_attachment_passes_temp_file_path_not_bytes():
    """MarkItDown must receive a file path string so it can detect the image type by extension."""
    tool = EmailAnalysisTool(config=_make_config())
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "description"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md):
        tool._analyze_image_attachment(b"\x89PNG\r\n", "photo.png", ".png")
    called_arg = mock_md.convert.call_args[0][0]
    assert isinstance(called_arg, str)
    assert called_arg.endswith(".png")


def test_analyze_image_attachment_uses_root_client_for_llm():
    """root_client (full OpenAI client) must be passed — not client (Completions sub-object)."""
    mock_chat_model = MagicMock()
    mock_chat_model.root_client = MagicMock(name="root_openai_client")
    file_obj = FileObject(name="x.eml", mime_type="message/rfc822", owner="test", content=_build_eml())
    config = FileAnalysisConfig.model_construct(input_files=[file_obj], chat_model=mock_chat_model)
    tool = EmailAnalysisTool(config=config)
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "description"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md) as mock_md_cls:
        tool._analyze_image_attachment(b"\x89PNG", "img.png", ".png")
    _, kwargs = mock_md_cls.call_args
    assert kwargs["llm_client"] is mock_chat_model.root_client


def test_analyze_image_attachment_llm_client_none_when_no_chat_model():
    tool = EmailAnalysisTool(config=_make_config())  # no chat_model
    mock_md = MagicMock()
    mock_md.convert.return_value.text_content = "ok"
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown", return_value=mock_md) as mock_md_cls:
        tool._analyze_image_attachment(b"\x89PNG", "img.png", ".png")
    _, kwargs = mock_md_cls.call_args
    assert kwargs["llm_client"] is None


def test_analyze_image_attachment_returns_error_on_conversion_failure():
    tool = EmailAnalysisTool(config=_make_config())
    with patch("codemie_tools.file_analysis.email.tools.MarkItDown") as mock_md_cls:
        mock_md_cls.return_value.convert.side_effect = Exception("File conversion failed")
        result = tool._analyze_image_attachment(b"\x89PNG", "photo.png", ".png")
    assert "Could not extract content from photo.png" in result


def test_analyze_image_attachment_routes_png_via_image_handler():
    """PNG extension must reach _analyze_image_attachment, not the generic dispatcher."""
    tool = EmailAnalysisTool(config=_make_config())
    with patch.object(tool, "_analyze_image_attachment", return_value="image text") as mock_img:
        result = tool._analyze_attachment(b"\x89PNG", "diagram.png")
    mock_img.assert_called_once()
    assert result == "image text"


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"])
def test_analyze_attachment_routes_all_image_types_to_image_handler(ext):
    tool = EmailAnalysisTool(config=_make_config())
    with patch.object(tool, "_analyze_image_attachment", return_value="img") as mock_img:
        tool._analyze_attachment(b"imgdata", f"photo{ext}")
    mock_img.assert_called_once()


def test_execute_with_multiple_eml_files_returns_both():
    eml1 = _build_eml(subject="First Email")
    eml2 = _build_eml(subject="Second Email")
    f1 = FileObject(name="first.eml", mime_type="message/rfc822", owner="test", content=eml1)
    f2 = FileObject(name="second.eml", mime_type="message/rfc822", owner="test", content=eml2)
    tool = EmailAnalysisTool(config=FileAnalysisConfig(input_files=[f1, f2]))
    result = tool.execute()
    assert "First Email" in result
    assert "Second Email" in result
    assert "---" in result  # separator between files
