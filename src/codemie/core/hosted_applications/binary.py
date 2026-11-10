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

"""Bounded signature and container checks; no media decoding or executable loading."""

from __future__ import annotations

from codemie.core.hosted_applications.errors import HostedApplicationError

EXECUTABLE_MAGIC = (
    b"MZ",
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)
_WOFF = ".woff"


def png(data):
    import zlib

    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    pos, count = 8, 0
    while pos + 12 <= len(data) and count < 100000:
        size = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + size
        if end > len(data) or (count == 0 and (kind != b"IHDR" or size != 13)):
            return False
        if zlib.crc32(data[pos + 4 : end - 4]) != int.from_bytes(data[end - 4 : end], "big"):
            return False
        if kind == b"IEND":
            return size == 0 and end == len(data)
        pos, count = end, count + 1
    return False


def image(data, suffix):
    if suffix == '.png':
        return png(data)
    if suffix in ('.jpg', '.jpeg'):
        return len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if suffix == '.gif':
        return (
            len(data) >= 14
            and data[:6] in (b'GIF87a', b'GIF89a')
            and data[-1:] == b';'
            and int.from_bytes(data[6:8], 'little') > 0
            and int.from_bytes(data[8:10], 'little') > 0
        )
    if suffix == '.webp':
        return (
            len(data) >= 20
            and data[:4] == b'RIFF'
            and data[8:12] == b'WEBP'
            and int.from_bytes(data[4:8], 'little') == len(data) - 8
            and data[12:16] in (b'VP8 ', b'VP8L', b'VP8X')
        )
    if suffix == '.ico':
        count = int.from_bytes(data[4:6], 'little')
        if len(data) < 22 or data[:4] != b'\0\0\1\0' or not 1 <= count <= 1024 or 6 + 16 * count > len(data):
            return False
        return all(
            6 + 16 * count <= int.from_bytes(data[i + 12 : i + 16], 'little') < len(data)
            and int.from_bytes(data[i + 12 : i + 16], 'little') + int.from_bytes(data[i + 8 : i + 12], 'little')
            <= len(data)
            for i in range(6, 6 + 16 * count, 16)
        )
    return iso_media(data, {b'avif', b'avis'})


def iso_media(data, brands):
    if len(data) < 16 or data[4:8] != b'ftyp':
        return False
    end = int.from_bytes(data[:4], 'big')
    return (
        16 <= end <= min(len(data), 4096)
        and end % 4 == 0
        and (data[8:12] in brands or any(data[i : i + 4] in brands for i in range(16, end, 4)))
    )


def font(data, suffix):
    if suffix in (_WOFF, '.woff2'):
        minimum, magic = (44, b'wOFF') if suffix == _WOFF else (48, b'wOF2')
        return (
            len(data) >= minimum
            and data[:4] == magic
            and int.from_bytes(data[8:12], 'big') == len(data)
            and int.from_bytes(data[12:14], 'big') > 0
        )
    count = int.from_bytes(data[4:6], 'big')
    if (
        len(data) < 12
        or data[:4] not in (b'\0\1\0\0', b'OTTO', b'true')
        or not 1 <= count <= 4096
        or 12 + 16 * count > len(data)
    ):
        return False
    return all(
        12 + 16 * count <= int.from_bytes(data[i + 8 : i + 12], 'big') <= len(data)
        and int.from_bytes(data[i + 8 : i + 12], 'big') + int.from_bytes(data[i + 12 : i + 16], 'big') <= len(data)
        for i in range(12, 12 + 16 * count, 16)
    )


def media(data, suffix):
    if suffix == '.mp4':
        return iso_media(data, {b'isom', b'iso2', b'mp41', b'mp42', b'avc1', b'dash', b'M4V ', b'M4A ', b'qt  '})
    if suffix == '.mp3':
        return len(data) >= 10 and (data.startswith(b'ID3') or data[0] == 255 and data[1] & 224 == 224)
    if suffix == '.ogg':
        return (
            len(data) >= 28
            and data[:5] == b'OggS\0'
            and len(data) >= 27 + data[26]
            and 27 + data[26] + sum(data[27 : 27 + data[26]]) <= len(data)
        )
    return len(data) >= 12 and data[:4] == b'\x1a\x45\xdf\xa3' and b'webm' in data[:4096]


def inspect_binary(data, suffix):
    if data.startswith(EXECUTABLE_MAGIC):
        raise HostedApplicationError('UNSUPPORTED_ARTIFACT', 'Executable files are not static application assets')
    if suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.ico'):
        valid = image(data, suffix)
    elif suffix in (_WOFF, '.woff2', '.ttf', '.otf'):
        valid = font(data, suffix)
    else:
        valid = media(data, suffix)
    if not valid:
        raise HostedApplicationError('UNSUPPORTED_ARTIFACT', 'A binary asset does not match its declared static format')
