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

"""ZIP archive extraction utilities for file datasource uploads."""

from __future__ import annotations

import io
import os
import struct
import zipfile
import zlib

from codemie.configs import logger
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes

_ZIP_MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_ZIP_MAX_FILE_COUNT = 1000
_ZIP_MAX_ENTRY_COUNT = 10 * _ZIP_MAX_FILE_COUNT


class ZipExtractionError(ValueError):
    """Raised when a ZIP archive cannot be extracted due to a content or size problem."""

    def __init__(self, message: str, detail: str = "", help_text: str = "") -> None:
        super().__init__(message)
        self.detail = detail
        self.help_text = help_text


def _read_zip_entry(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    current_total: int,
) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    try:
        with zf.open(info) as entry:
            while True:
                chunk = entry.read(65536)
                if not chunk:
                    break
                current_total += len(chunk)
                if current_total > _ZIP_MAX_UNCOMPRESSED_BYTES:
                    raise ZipExtractionError(
                        message="ZIP archive too large",
                        detail=(
                            f"Uncompressed content exceeds the "
                            f"{_ZIP_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB limit."
                        ),
                        help_text="Upload a smaller archive or split the content into multiple files.",
                    )
                chunks.append(chunk)
    except ZipExtractionError:
        raise
    except (RuntimeError, zlib.error, struct.error) as exc:
        raise ZipExtractionError(
            message=f"Cannot read entry '{info.filename}' from ZIP",
            detail=str(exc),
            help_text="The entry may be encrypted, corrupted, or use an unsupported compression method.",
        ) from exc
    return b"".join(chunks), current_total


def _flat_name_for_entry(info: zipfile.ZipInfo) -> str | None:
    """Return the flat basename for a ZIP entry, or None if the entry should be skipped."""
    if info.is_dir():
        return None
    normalised = info.filename.replace("\\", "/")  # normalise Windows backslash separators
    flat_name = os.path.basename(normalised)
    if not flat_name or flat_name in (".", ".."):
        return None
    if flat_name.startswith("._"):  # Apple Double resource forks — metadata, not content
        return None
    return flat_name


def expand_zip_file(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Extract all files from a ZIP archive, returning (filename, content) pairs.

    Raises ZipExtractionError for invalid, oversized, or too-many-files archives.
    Skips directories, nested ZIPs, Apple Double resource forks, and duplicate filenames.
    """
    result: list[tuple[str, bytes]] = []
    seen_names: set[str] = set()
    total_size = 0

    try:
        zf_ctx = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ZipExtractionError(
            message="Invalid ZIP archive",
            detail="The uploaded file could not be opened as a ZIP archive.",
            help_text="Ensure the file is a valid ZIP archive and try again.",
        ) from exc

    with zf_ctx as zf:
        all_entries = zf.infolist()
        if len(all_entries) > _ZIP_MAX_ENTRY_COUNT:
            raise ZipExtractionError(
                message="ZIP archive contains too many entries",
                detail=f"Archives are limited to {_ZIP_MAX_ENTRY_COUNT} total entries.",
                help_text="Reduce the number of files and directories in the archive.",
            )
        for info in all_entries:
            flat_name = _flat_name_for_entry(info)
            if flat_name is None:
                continue
            ext = os.path.splitext(flat_name)[1].lstrip(".").lower()
            if ext == IndexKnowledgeBaseFileTypes.ZIP.value:
                logger.warning(f"Skipping nested ZIP in archive: {info.filename}")
                continue
            if flat_name in seen_names:
                logger.warning(f"Skipping duplicate filename in ZIP: {info.filename}")
                continue
            if len(result) >= _ZIP_MAX_FILE_COUNT:
                raise ZipExtractionError(
                    message="ZIP archive contains too many files",
                    detail=f"Archives are limited to {_ZIP_MAX_FILE_COUNT} files.",
                    help_text="Split the archive into smaller batches and upload separately.",
                )
            # Read in chunks to track actual decompressed bytes; info.file_size is metadata and
            # can be set to 0 by a crafted archive to bypass the size guard (zip bomb).
            file_content, total_size = _read_zip_entry(zf, info, total_size)
            seen_names.add(flat_name)
            result.append((flat_name, file_content))

    return result
