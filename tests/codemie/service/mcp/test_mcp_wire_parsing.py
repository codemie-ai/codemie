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

"""MCP tool results parsed from wire JSON shaped like the MCP spec (EmbeddedResource, ResourceLink)."""

import json
from pathlib import Path

import pytest

from codemie.service.mcp.models import MCPToolContentItem, MCPToolInvocationResponse

_FIXTURES = Path(__file__).parent / "fixtures"
_WIRE = json.loads((_FIXTURES / "mcp_wire_results.json").read_text())
_CAPTURED = json.loads((_FIXTURES / "mcp_server_everything_results.json").read_text())


def _item(key: str, source: dict = _WIRE, index: int = 0) -> MCPToolContentItem:
    return MCPToolInvocationResponse.model_validate(source[key]).content[index]


def test_embedded_blob_keeps_uri_and_blob():
    item = _item("embedded_blob", _CAPTURED, index=1)
    assert item.resource.uri == "test://static/resource/2"
    assert item.resource.blob == "UmVzb3VyY2UgMjogVGhpcyBpcyBhIGJhc2U2NCBibG9i"
    assert item.resource.mimeType == "application/octet-stream"
    assert item.file_name() == "2.bin"


def test_embedded_text_keeps_text_and_adds_mime_extension():
    item = _item("embedded_text", _CAPTURED, index=1)
    assert item.resource.text == "Resource 1: This is a plaintext resource"
    assert item.file_name() == "1.txt"


def test_resource_link_keeps_top_level_uri_and_name():
    item = _item("resource_link", _CAPTURED, index=1)
    assert item.type == "resource_link"
    assert item.uri == "test://static/resource/1"
    assert item.name == "Resource 1"
    assert item.file_name() == "1.txt"
    assert item.resource is None


def test_flat_resource_data_is_not_deliverable():
    item = MCPToolContentItem.model_validate({"type": "resource", "data": "YQ==", "mimeType": "text/csv"})
    assert item.resource is None
    assert not hasattr(item, "is_resource")


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"resource": {"uri": "file:///a/..%2F..%2Fetc%2Fpasswd"}}, "passwd"),
        ({"resource": {"uri": "file:///a/b/My%20File.csv"}}, "My File.csv"),
        ({"uri": "file:///dir/", "name": "from-name.pdf"}, "from-name.pdf"),
        ({"name": "n", "mimeType": "image/png"}, "n.png"),
        ({}, "artifact"),
        ({"resource": {"uri": "https://h/x.bin?sig=a/b"}}, "x.bin"),
    ],
)
def test_file_name(payload, expected):
    assert MCPToolContentItem(type="resource", **payload).file_name() == expected
