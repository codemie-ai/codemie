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

import base64

import pytest

from codemie_tools.base.file_object import FileObject, MimeType
from codemie_tools.base.string_serializer import StringSerializer


def test_is_csv():
    mime_type = "text/csv"
    result = MimeType(mime_type).is_csv
    assert result

    mime_type = "text/plain"
    result = MimeType(mime_type).is_csv
    assert not result


def test_is_png():
    mime_type = "image/png"
    result = MimeType(mime_type).is_png
    assert result

    mime_type = "text/plain"
    result = MimeType(mime_type).is_png
    assert not result


def test_is_image():
    # Regular image should be detected as image
    mime_type = "image/png"
    result = MimeType(mime_type).is_image
    assert result

    # SVG should not be detected as image
    mime_type = "image/svg+xml"
    result = MimeType(mime_type).is_image
    assert not result

    # Non-image types should not be detected as image
    mime_type = "text/plain"
    result = MimeType(mime_type).is_image
    assert not result


@pytest.fixture
def file_object():
    return FileObject(
        name="test-file.txt",
        content=b"This is a test file.",
        mime_type="text/plain",
        owner="user",
        path="/path/to/file",
    )


def test_to_encoded_url(file_object):
    result = file_object.to_encoded_url()
    expected_result = "MTB+dGV4dC9wbGFpbjR+dXNlcjEzfnRlc3QtZmlsZS50eHQ="
    assert result == expected_result


def test_base64_content(file_object):
    result = file_object.base64_content()
    expected_result = "data:text/plain;base64,VGhpcyBpcyBhIHRlc3QgZmlsZS4="
    assert result == expected_result


def test_bytes_content(file_object):
    file_object.content = b'test'
    expected_result = b'test'
    assert file_object.bytes_content() == expected_result

    file_object.content = 'test'
    assert file_object.bytes_content() == expected_result


def test_from_encoded_url(file_object):
    encoded_url = "dGV4dC9wbGFpbl91c2VyX3Rlc3QtZmlsZS50eHQ="
    result = FileObject.from_encoded_url(encoded_url)
    assert result.name == file_object.name
    assert result.mime_type == file_object.mime_type
    assert result.owner == file_object.owner


@pytest.mark.parametrize(
    "encoded_url, given_len, expected_deserialized",
    [
        ("invalid_base64", 0, []),
        (StringSerializer.serialize([]), 0, []),
        (StringSerializer.serialize(["mime_type"]), 1, ['mime_type']),
        (
            base64.b64encode("mime_type_owner_name".encode("utf-8")).decode("utf-8"),
            4,
            ["mime", "type", "owner", "name"],
        ),
        (
            StringSerializer.serialize(["mime_type", "owner", "name", "extra"]),
            4,
            ["mime_type", "owner", "name", "extra"],
        ),
    ],
    ids=[
        "invalid base string",
        "valid but empty list",
        "valid base64 but 1 instead of 3 values",
        "legacy format data",
        "valid but more than 3 values",
    ],
)
def test_from_encoded_url_with_invalid_data(encoded_url: str, given_len: int, expected_deserialized: list) -> None:
    expected_error_msg = (
        f"Invalid encoded URL data: {encoded_url}, expected 3 values but got {given_len}: {expected_deserialized}"
    )

    with pytest.raises(ValueError) as exc_info:
        FileObject.from_encoded_url(encoded_url)
    assert str(exc_info.value) == expected_error_msg


def test_from_encoded_url_with_valid_data() -> None:
    mime_type = "mime_type"
    owner = "owner"
    name = "name"
    encoded_url = StringSerializer.serialize([mime_type, owner, name])

    file_object = FileObject.from_encoded_url(encoded_url)

    assert file_object.mime_type == mime_type
    assert file_object.owner == owner
    assert file_object.name == name


def test_repr(file_object):
    result = repr(file_object)
    expected_result = "<File: name=test-file.txt, mime_type = text/plain, owner=user, path=/path/to/file>"
    assert result == expected_result


def test_is_image_file_object(file_object):
    file_object.mime_type = "image/png"
    result = file_object.is_image()
    assert result

    file_object.mime_type = "text/plain"
    result = file_object.is_image()
    assert not result
