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


"""Detect, read and wrap GitLab image responses as base64 multimodal artifacts."""

from __future__ import annotations

import base64
import logging
import mimetypes
from collections.abc import Mapping
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from codemie_tools.base.file_object import normalise_mime
from codemie_tools.base.image_constants import IMAGE_MIME_TYPES, MAX_IMAGE_SIZE_BYTES
from codemie_tools.base.script_result import ScriptResult

logger = logging.getLogger(__name__)

IMAGE_STREAM_CHUNK_BYTES = 65536
# Content types that say nothing about the body, so an upload's file extension may decide instead.
UNINFORMATIVE_CONTENT_TYPES = frozenset({"application/octet-stream", "text/plain"})
# Built-in table only (no system mime.types), with .webp, which Python < 3.13 lacks, so results do not vary by host.
_UPLOAD_MIME_TYPES = mimetypes.MimeTypes(filenames=())
_UPLOAD_MIME_TYPES.add_type("image/webp", ".webp")


class GitlabMultimodalResponse(BaseModel):
    """GitLab response whose body is an image, carried as base64 artifacts beside the status text."""

    text: str
    image_artifacts: list[dict[str, str]]

    def __str__(self) -> str:
        return self.text

    def to_script_result(self, max_bytes: int | None = None) -> ScriptResult:
        """Script-facing result: the status text only, never the image bytes."""
        return ScriptResult(result=self.text)


def detect_image_mime(response: requests.Response, url: str) -> str | None:
    """Detect an image from the Content-Type, or from an upload's extension when the Content-Type says nothing."""
    headers = response.headers if isinstance(response.headers, Mapping) else {}
    content_type = headers.get("Content-Type")
    mime = normalise_mime(content_type) if isinstance(content_type, str) else ""
    if mime in IMAGE_MIME_TYPES:
        return mime
    if mime and mime not in UNINFORMATIVE_CONTENT_TYPES:
        return None
    path = urlparse(url).path
    if "/uploads/" not in path:
        return None
    guessed = _UPLOAD_MIME_TYPES.guess_type(path)[0]
    guessed = normalise_mime(guessed) if guessed else None
    return guessed if guessed in IMAGE_MIME_TYPES else None


def read_image_bounded(response: requests.Response) -> bytes | None:
    """Read the body up to the image size limit; ``None`` as soon as it is known to exceed the limit."""
    headers = response.headers if isinstance(response.headers, Mapping) else {}
    content_length = headers.get("Content-Length")
    if isinstance(content_length, str) and content_length.isdigit() and int(content_length) > MAX_IMAGE_SIZE_BYTES:
        return None
    body = bytearray()
    for chunk in response.iter_content(chunk_size=IMAGE_STREAM_CHUNK_BYTES):
        body.extend(chunk)
        if len(body) > MAX_IMAGE_SIZE_BYTES:
            return None
    return bytes(body)


def matches_image_signature(body: bytes, mime: str) -> bool:
    """Whether the leading bytes of ``body`` are the file signature of the ``mime`` image format."""
    if mime == "image/png":
        return body.startswith(b"\x89PNG")
    if mime in {"image/jpeg", "image/jpg"}:
        return body.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return body.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    return False


def _skipped(status_line: str, reason: str) -> GitlabMultimodalResponse:
    return GitlabMultimodalResponse(text=f"{status_line} {reason}", image_artifacts=[])


def build_image_response(status_line: str, response: requests.Response, mime: str) -> GitlabMultimodalResponse:
    """Wrap an image body as a base64 artifact, or a skip notice when it is oversized, empty or not that image.

    The streamed response is always closed, also when reading the body raises a network error.
    """
    try:
        body = read_image_bounded(response)
    finally:
        response.close()
    if body is None:
        logger.warning("GitLab image response exceeds %d bytes; skipped", MAX_IMAGE_SIZE_BYTES)
        return _skipped(status_line, "Image exceeds 5 MB limit; skipped.")
    if not body:
        logger.warning("GitLab image response has an empty body; skipped")
        return _skipped(status_line, "Empty image body; skipped.")
    if not matches_image_signature(body, mime):
        logger.warning("GitLab image response body does not match %s; skipped", mime)
        return _skipped(status_line, f"Body is not a valid {mime} image; skipped.")
    artifact = {"data": base64.b64encode(body).decode("ascii"), "mime_type": mime}
    logger.info("Downloaded GitLab image artifact (%s, %d bytes)", mime, len(body))
    return GitlabMultimodalResponse(
        text=f"{status_line} Image downloaded ({mime}, {len(body)} bytes)", image_artifacts=[artifact]
    )
