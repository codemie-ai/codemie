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

import dataclasses
import io
import zipfile

import pytest

from codemie_tools.base.artifact_publishing import (
    ZIP_CONTAINER_EXTENSIONS,
    PublishContext,
    PublishResult,
    zip_container_error,
)


def test_publish_context_is_frozen():
    ctx = PublishContext(user_id="u1", conversation_id=None, turn_id="t1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.turn_id = "t2"  # type: ignore[misc]


@pytest.mark.parametrize("name", sorted(f"f{ext}" for ext in ZIP_CONTAINER_EXTENSIONS))
def test_zip_container_error_names_the_format(name):
    assert zip_container_error(name, b"not a zip") == f"its content is not a valid {name[1:]} file"


def test_zip_container_error_accepts_real_zip_and_ignores_other_formats():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "a")
    assert zip_container_error("a.docx", buffer.getvalue()) is None
    assert zip_container_error("a.csv", b"not a zip") is None


def test_publish_result_ok_only_with_link():
    assert PublishResult(link="sandbox:/a", name="a", size=1, mime="text/plain").ok
    assert not PublishResult(name="a", reason="a stays on the MCP server and was not delivered").ok


def test_for_turn_uses_the_request_uuid_as_turn_id():
    ctx = PublishContext.for_turn("u1", "c1", "req-1")

    assert ctx == PublishContext("u1", "c1", "req-1")


@pytest.mark.parametrize("missing", [None, ""])
def test_for_turn_without_request_uuid_gives_each_call_a_fresh_hex_turn(missing):
    first = PublishContext.for_turn("u1", "c1", missing)
    second = PublishContext.for_turn("u1", "c1", missing)

    assert first.turn_id != second.turn_id
    assert len(first.turn_id) == 32 and int(first.turn_id, 16) >= 0
