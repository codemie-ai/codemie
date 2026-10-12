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

"""Tests for the transport-agnostic enrichment step."""

from unittest.mock import patch

from codemie_tools.ms_exchange import enrichment
from codemie_tools.ms_exchange.enrichment import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_TOTAL_BYTES,
    enrich,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _ocr(data):
    return "ocr:" + data.decode()


def test_image_without_any_model_is_skipped_with_fixed_reason():
    out = enrich("body", [("a.png", "image/png", b"x")], None)
    assert out == "body\n\n[skipped: a.png] no OCR model provided"


def test_image_without_ocr_is_not_downloaded():
    fetched = []

    def lazy():
        fetched.append(1)
        return b"x"

    out = enrich("", [("a.png", "image/png", lazy)], None)
    assert out == "\n\n[skipped: a.png] no OCR model provided"
    assert fetched == []


def test_shape_body_then_sections_in_message_order():
    with (
        patch.object(enrichment, "extract_docx_text", return_value="D"),
        patch.object(enrichment.PdfProcessor, "extract_text_as_markdown", return_value="P"),
    ):
        out = enrich(
            "body",
            [("a.png", "image/png", b"img"), ("b.pdf", "application/pdf", b"p"), ("c.docx", DOCX, b"d")],
            _ocr,
        )
    assert out == (
        "body\n\n--- Attachment: a.png ---\nocr:img\n\n--- Attachment: b.pdf ---\nP\n\n--- Attachment: c.docx ---\nD"
    )


def test_pdf_and_docx_extract_without_ocr():
    with (
        patch.object(enrichment, "extract_docx_text", return_value="D"),
        patch.object(enrichment.PdfProcessor, "extract_text_as_markdown", return_value="P"),
    ):
        out = enrich("", [("b.pdf", "application/pdf", b"p"), ("c.docx", DOCX, b"d")], None)
    assert "P" in out and "D" in out and "skipped" not in out


def test_not_a_file_unsupported_and_failure_lines():
    with patch.object(enrichment.PdfProcessor, "extract_text_as_markdown", side_effect=RuntimeError("secret")):
        out = enrich(
            "b",
            [("item", "message/rfc822", None), ("z.zip", "application/zip", b"z"), ("b.pdf", "application/pdf", b"p")],
            None,
        )
    assert "[skipped: item] attachment is not a file" in out
    assert "[skipped: z.zip] unsupported attachment type" in out
    assert "[skipped: b.pdf] text extraction failed" in out
    assert "secret" not in out


def test_count_cap_does_not_fetch_extra():
    fetched = []

    def lazy(i):
        def f():
            fetched.append(i)
            return b"x"

        return f

    atts = [(f"f{i}.png", "image/png", lazy(i)) for i in range(MAX_ATTACHMENTS + 2)]
    out = enrich("", atts, _ocr)
    assert fetched == list(range(MAX_ATTACHMENTS))
    assert out.count("attachment limit exceeded") == 2


def test_per_file_cap():
    big = b"x" * (MAX_ATTACHMENT_BYTES + 1)
    out = enrich("", [("big.png", "image/png", big)], _ocr)
    assert out == "\n\n[skipped: big.png] attachment too large"


def test_total_cap():
    chunk = b"x" * MAX_ATTACHMENT_BYTES
    n = MAX_TOTAL_BYTES // MAX_ATTACHMENT_BYTES + 1
    out = enrich("", [(f"f{i}.png", "image/png", chunk) for i in range(n)], _ocr)
    assert out.count("total attachment size limit exceeded") == 1
    assert out.count("--- Attachment:") == n - 1


def _spy(data=b"x"):
    calls = []

    def content():
        calls.append(1)
        return data

    return content, calls


def test_oversized_declared_size_is_never_fetched():
    content, calls = _spy()
    out = enrich("body", [("big.pdf", "application/pdf", content, MAX_ATTACHMENT_BYTES + 1)], None)
    assert calls == []
    assert out == "body\n\n[skipped: big.pdf] attachment too large"


def test_declared_sizes_exceeding_total_are_not_fetched():
    first, first_calls = _spy(b"p")
    second, second_calls = _spy(b"p")
    third, third_calls = _spy(b"p")
    size = MAX_ATTACHMENT_BYTES
    with patch.object(enrichment.PdfProcessor, "extract_text_as_markdown", return_value="P"):
        out = enrich(
            "",
            [
                ("a.pdf", "application/pdf", first, size),
                ("b.pdf", "application/pdf", second, size),
                ("c.pdf", "application/pdf", third, size),
            ],
            None,
        )
    assert (len(first_calls), len(second_calls), third_calls) == (1, 1, [])
    assert out.endswith("[skipped: c.pdf] total attachment size limit exceeded")


def test_unknown_size_is_fetched_then_checked_after_fetch():
    content, calls = _spy(b"x" * (MAX_ATTACHMENT_BYTES + 1))
    out = enrich("", [("big.pdf", "application/pdf", content, None)], None)
    assert len(calls) == 1
    assert out.strip() == "[skipped: big.pdf] attachment too large"
