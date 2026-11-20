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

"""
Tests for MCP toolkit post-processing functions.
"""

import base64
import copy
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote

import pytest

from codemie.service.artifact_publisher import ArtifactPublisherService
from codemie.service.mcp.models import MCPToolContentItem, MCPToolInvocationResponse
from codemie.service.mcp.toolkit import _convert_mcp_response_to_tool_message, _format_content_item
from codemie_tools.base.artifact_publishing import PublishContext

_WIRE = json.loads((Path(__file__).parent / "fixtures" / "mcp_wire_results.json").read_text())
_CAPTURED = json.loads((Path(__file__).parent / "fixtures" / "mcp_server_everything_results.json").read_text())
_CTX = PublishContext(user_id="user-1", conversation_id="conv-1", turn_id="turn-1")


@pytest.fixture
def storage():
    """Real publisher over a fake workspace: links echo the workspace path they were written to."""
    workspace = MagicMock()
    workspace.repository.get_by_conversation_for_user.return_value = None
    workspace.publish_artifact_file.side_effect = lambda conv, user, path, data, mime: f"sandbox:/v1/files/{path}"
    repo = MagicMock()
    with (
        patch(
            "codemie.service.mcp.toolkit.ArtifactPublisherService",
            side_effect=lambda file_repository: ArtifactPublisherService(file_repository, workspace),
        ),
        patch("codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository", return_value=repo),
    ):
        yield workspace, repo


def _convert(payload: dict, ctx: PublishContext = _CTX) -> str:
    return _convert_mcp_response_to_tool_message(MCPToolInvocationResponse.model_validate(payload), ctx)


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # A fixed ZIP entry timestamp keeps pytest parameter IDs stable across xdist workers.
        archive.writestr(zipfile.ZipInfo("[Content_Types].xml"), "<Types/>")
    return buffer.getvalue()


def test_embedded_blob_becomes_workspace_link(storage):
    workspace, _ = storage
    payload = copy.deepcopy(_WIRE["fake_xlsx_embedded_blob"])
    payload["content"][0]["resource"]["blob"] = base64.b64encode(_xlsx_bytes()).decode()
    result = _convert(payload)
    assert result == "[report.xlsx](sandbox:/v1/files/artifacts/turn-1/report.xlsx)"
    assert workspace.publish_artifact_file.call_args.args[3] == _xlsx_bytes()


@pytest.mark.parametrize(
    ("file_name", "mime", "data"),
    [
        ("bundle.zip", "application/zip", _xlsx_bytes()),
        ("summary.pdf", "application/pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"),
    ],
    ids=("zip", "pdf"),
)
def test_zip_and_pdf_blobs_round_trip(storage, file_name, mime, data):
    workspace, _ = storage
    payload = copy.deepcopy(_WIRE["fake_xlsx_embedded_blob"])
    payload["content"][0]["resource"] = {
        "uri": f"file:///home/codemie/s1/{file_name}",
        "mimeType": mime,
        "blob": base64.b64encode(data).decode(),
    }

    assert _convert(payload) == f"[{file_name}](sandbox:/v1/files/artifacts/turn-1/{file_name})"
    _conv, _user, path, stored, stored_mime = workspace.publish_artifact_file.call_args.args
    assert (path, stored, stored_mime) == (f"artifacts/turn-1/{file_name}", data, mime)


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("data[1].csv", r"data\[1\].csv"),
        ("x](javascript:alert)[y.csv", r"x\](javascript:alert)\[y.csv"),
    ],
)
def test_published_link_label_cannot_end_or_splice_the_link(storage, name, label):
    uri = f"file:///home/codemie/s1/{quote(name)}"
    payload = {"content": [{"type": "resource", "resource": {"uri": uri, "mimeType": "text/csv", "text": "a\n"}}]}
    assert _convert(payload) == f"[{label}](sandbox:/v1/files/artifacts/turn-1/{name})"


def test_backticks_in_a_name_cannot_break_out_of_the_reason_code_span(storage):
    name = quote("x`[click](javascript:alert)`\n.xlsx")
    payload = {"content": [{"type": "resource", "resource": {"uri": f"file:///s/{name}", "blob": "UEsDBAoAAAAA"}}]}
    refused = _convert(payload)
    payload["content"][0]["resource"].pop("blob")
    not_delivered = _convert(payload)

    assert refused == "`x[click](javascript:alert).xlsx` was not saved: its content is not a valid .xlsx file"
    assert not_delivered == "`x[click](javascript:alert).xlsx` stays on the MCP server and was not delivered"


def test_publisher_setup_failure_keeps_the_rest_of_the_result():
    payload = copy.deepcopy(_CAPTURED["embedded_text"])
    payload["content"].insert(0, {"type": "image", "data": "aGk=", "mimeType": "image/png"})
    with patch(
        "codemie.service.mcp.toolkit.FileRepositoryFactory.get_current_repository", side_effect=RuntimeError("down")
    ):
        result = _convert(payload)

    assert "`image` could not be saved because of a storage error" in result
    assert "`1.txt` could not be saved because of a storage error" in result
    assert "Returning resource reference for Resource 1:" in result
    assert "base64" not in result and "aGk=" not in result


def test_fake_xlsx_blob_gives_reason(storage):
    workspace, _ = storage
    assert (
        _convert(_WIRE["fake_xlsx_embedded_blob"])
        == "`report.xlsx` was not saved: its content is not a valid .xlsx file"
    )
    workspace.publish_artifact_file.assert_not_called()


def test_embedded_text_becomes_workspace_link(storage):
    workspace, _ = storage
    result = _convert(_CAPTURED["embedded_text"])
    assert "[1.txt](sandbox:/v1/files/artifacts/turn-1/1.txt)" in result
    assert workspace.publish_artifact_file.call_args.args[3] == b"Resource 1: This is a plaintext resource"


def test_embedded_blob_from_a_real_server_becomes_workspace_link(storage):
    workspace, _ = storage
    result = _convert(_CAPTURED["embedded_blob"])
    assert "[2.bin](sandbox:/v1/files/artifacts/turn-1/2.bin)" in result
    assert workspace.publish_artifact_file.call_args.args[3] == b"Resource 2: This is a base64 blob"


def test_resource_link_is_not_delivered(storage):
    workspace, repo = storage
    result = _convert(_CAPTURED["resource_link"])
    assert "`1.txt` stays on the MCP server and was not delivered" in result
    assert "`2.bin` stays on the MCP server and was not delivered" in result
    workspace.publish_artifact_file.assert_not_called()
    repo.write_file.assert_not_called()


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            {"name": "Q3 [final] report", "uri": "https://s3.example.com/r.xlsx?X-Sig=a b(1)"},
            r"[Q3 \[final\] report](https://s3.example.com/r.xlsx?X-Sig=a%20b%281%29)",
        ),
        ({"uri": "HTTP://example.com/data/report.csv"}, "[report.csv](HTTP://example.com/data/report.csv)"),
        ({"name": "x", "uri": "javascript:alert(1)"}, "`alert(1)` stays on the MCP server and was not delivered"),
        ({"name": "x", "uri": "http://[bad"}, "`x` stays on the MCP server and was not delivered"),
    ],
)
def test_web_resource_link_is_passed_on_as_a_plain_link(storage, link, expected):
    workspace, repo = storage
    assert _convert({"content": [{"type": "resource_link", **link}]}) == expected
    workspace.publish_artifact_file.assert_not_called()
    repo.write_file.assert_not_called()


def test_web_uri_in_an_embedded_resource_without_content_is_not_delivered(storage):
    payload = {"content": [{"type": "resource", "resource": {"uri": "https://example.com/report.pdf"}}]}
    assert _convert(payload) == "`report.pdf` stays on the MCP server and was not delivered"


def test_malformed_resource_uri_does_not_abort_the_result(storage):
    workspace, _ = storage
    payload = {
        "content": [
            {"type": "resource", "name": "notes.txt", "resource": {"uri": "http://[bad", "text": "hello"}},
            {"type": "text", "text": "kept"},
        ]
    }
    result = _convert(payload)
    assert "[notes.txt](" in result
    assert "kept" in result
    assert workspace.publish_artifact_file.call_args.args[3] == b"hello"


def test_mcp_connect_explicit_export_result_is_published_item_by_item(storage):
    """A real MCP-Connect `codemie_export_files` result (MR !314): each file becomes a link, the 0-byte one a reason."""
    workspace, _ = storage
    export_result = json.loads((Path(__file__).parent / "fixtures" / "mcp_connect_export_result.json").read_text())

    result = _convert(export_result["result"])

    assert "done" in result
    assert "[closed positions.csv](sandbox:/v1/files/artifacts/turn-1/closed positions.csv)" in result
    assert "[report.xlsx](sandbox:/v1/files/artifacts/turn-1/report.xlsx)" in result
    assert "[data.json](sandbox:/v1/files/artifacts/turn-1/data.json)" in result
    assert "`empty.txt` was not saved: it is empty" in result
    assert "missing.pdf: file not found" in result
    published = {call.args[2]: call.args[3] for call in workspace.publish_artifact_file.call_args_list}
    assert published["artifacts/turn-1/closed positions.csv"] == b"a,b\n1,2\n"
    assert published["artifacts/turn-1/data.json"] == b'{"k": 1}'
    assert "artifacts/turn-1/empty.txt" not in published


def test_empty_blob_gives_the_empty_file_reason(storage):
    workspace, _ = storage
    payload = copy.deepcopy(_WIRE["fake_xlsx_embedded_blob"])
    payload["content"][0]["resource"]["blob"] = ""
    assert _convert(payload) == "`report.xlsx` was not saved: it is empty"
    workspace.publish_artifact_file.assert_not_called()


def test_empty_resource_is_not_delivered(storage):
    workspace, _ = storage
    assert _convert(_WIRE["empty_resource"]) == "`data.csv` stays on the MCP server and was not delivered"
    workspace.publish_artifact_file.assert_not_called()


def test_flat_resource_is_not_deliverable(storage):
    workspace, repo = storage
    assert "was not delivered" in _convert(_WIRE["legacy_flat_resource"])
    workspace.publish_artifact_file.assert_not_called()
    repo.write_file.assert_not_called()


def test_image_publishes_as_image_with_extension(storage):
    workspace, repo = storage
    result = _convert(_WIRE["image_jpeg"])
    assert "![Screenshot](sandbox:/v1/files/artifacts/turn-1/image.jpg)" in result
    repo.write_file.assert_not_called()  # never the shared mcp_images namespace


def test_image_failure_gives_reason_not_data_url(storage):
    result = _convert(_WIRE["image_invalid_base64"])
    assert "data:" not in result
    assert "`image.png` was not saved" in result


def test_failed_resource_gives_reason(storage):
    result = _convert(_WIRE["fake_xlsx_blob_without_mime"])
    assert "`fake.xlsx` was not saved" in result
    assert "sandbox:" not in result


def test_no_user_stores_nothing(storage):
    workspace, repo = storage
    ctx = PublishContext(user_id="", conversation_id=None, turn_id="turn-1")
    result = _convert(_WIRE["image_png"], ctx)
    assert "no user is known" in result
    workspace.publish_artifact_file.assert_not_called()
    repo.write_file.assert_not_called()


def test_text_item_unchanged(storage):
    assert _convert(_WIRE["text"]) == "hello"


class TestFormatContentItem(unittest.TestCase):
    def test_resource_url_returns_text(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = "[Download](sandbox:/v1/files/abc)"
        result = _format_content_item(item)
        self.assertEqual(result, "[Download](sandbox:/v1/files/abc)")

    def test_image_url_branch_unchanged(self):
        item = MCPToolContentItem(type="image_url")
        item.image_url = {"url": "sandbox:/v1/files/img", "detail": "high"}
        result = _format_content_item(item)
        self.assertIn("sandbox:/v1/files/img", result)

    def test_unrecognized_type_serializes_via_json_catch_all(self):
        item = MCPToolContentItem(type="unrecognized_type")
        item.text = "structured payload"
        item.mimeType = "application/x-custom"
        result = _format_content_item(item)
        parsed = json.loads(result)
        self.assertEqual(
            parsed,
            {"type": "unrecognized_type", "text": "structured payload", "mimeType": "application/x-custom"},
        )


class TestMCPToolContentItemModel(unittest.TestCase):
    def test_str_resource_url_returns_text(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = "[Download](sandbox:/v1/files/abc)"
        self.assertEqual(str(item), "[Download](sandbox:/v1/files/abc)")

    def test_str_resource_url_no_text_returns_fallback(self):
        item = MCPToolContentItem(type="resource_url")
        item.text = None
        result = str(item)
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, "")


def _text_resources(*names_and_text: tuple[str, str]) -> dict:
    return {
        "content": [
            {"type": "resource", "resource": {"uri": f"file:///s/{name}", "mimeType": "text/csv", "text": text}}
            for name, text in names_and_text
        ]
    }


def test_files_over_the_per_result_count_cap_are_refused_with_a_reason(storage, monkeypatch):
    monkeypatch.setattr("codemie.service.mcp.toolkit.MAX_ARTIFACTS_PER_RESULT", 2)
    workspace, _ = storage

    result = _convert(_text_resources(("a.csv", "1"), ("b.csv", "2"), ("c.csv", "3")))

    assert workspace.publish_artifact_file.call_count == 2
    assert "[a.csv](" in result and "[b.csv](" in result
    assert "`c.csv` was not saved: a result may carry at most 2 files" in result


def test_files_over_the_per_result_byte_cap_are_refused_with_a_reason(storage, monkeypatch):
    monkeypatch.setattr("codemie.service.mcp.toolkit.MAX_ARTIFACT_BYTES_PER_RESULT", 10)
    workspace, _ = storage

    result = _convert(_text_resources(("a.csv", "123456"), ("b.csv", "123456")))

    assert workspace.publish_artifact_file.call_count == 1
    assert "[a.csv](" in result
    assert "`b.csv` was not saved: a result may carry at most" in result


def test_items_that_store_nothing_do_not_use_up_the_cap(storage, monkeypatch):
    monkeypatch.setattr("codemie.service.mcp.toolkit.MAX_ARTIFACTS_PER_RESULT", 1)
    payload = {"content": [{"type": "resource_link", "uri": "https://example.com/r.csv", "name": "r.csv"}]}
    payload["content"] += _text_resources(("a.csv", "1"))["content"]

    result = _convert(payload)

    assert "[r.csv](https://example.com/r.csv)" in result and "[a.csv](" in result
