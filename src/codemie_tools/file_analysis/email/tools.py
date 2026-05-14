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

"""Specialized tool for EML and MSG email file analysis, including attachment extraction."""

from __future__ import annotations

import base64
import binascii
import concurrent.futures
import contextlib
import datetime
import email as _email_lib
import email.message
import ipaddress
import logging
import os
import re
import socket
import struct
import tempfile
from typing import Any, List, Optional, Type
from urllib.parse import urlparse

import httpx
from markitdown import MarkItDown
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.tool_vars import EMAIL_TOOL

logger = logging.getLogger(__name__)

# OLE stream IDs for MSG files (MAPI property format)
_MSG_BODY_UNICODE = "__substg1.0_1000001F"
_MSG_BODY_ASCII = "__substg1.0_1000001E"
_MSG_BODY_HTML = "__substg1.0_10130102"  # PR_HTML — binary blob (PT_BINARY = 0x0102)
_MSG_BODY_RTF = "__substg1.0_10090102"  # RTF-compressed body (last resort)
_MSG_INTERNET_CPID = "__substg1.0_3FDE0003"  # PR_INTERNET_CPID — charset code page (PT_LONG)
_MSG_SUBJECT_UNICODE = "__substg1.0_0037001F"
_MSG_SUBJECT_ASCII = "__substg1.0_0037001E"
_MSG_SENDER_NAME = "__substg1.0_0C1A001F"  # Sender display name
_MSG_SENDER_EMAIL = "__substg1.0_0C1F001F"  # May be X.500 DN on Exchange
_MSG_SENDER_SMTP = "__substg1.0_5D01001F"  # SMTP address of the sender
_MSG_TO = "__substg1.0_0E04001F"
_MSG_CC = "__substg1.0_0E03001F"
_MSG_DATE = "__substg1.0_00390040"  # PR_CLIENT_SUBMIT_TIME (FILETIME, 8 bytes)
_MSG_TRANSPORT_HEADERS = "__substg1.0_007D001F"  # Full RFC transport headers
_MSG_ATTACH_DATA = "__substg1.0_37010102"
_MSG_ATTACH_FILENAME_SHORT = "__substg1.0_3704001F"
_MSG_ATTACH_FILENAME_LONG = "__substg1.0_3707001F"

# Extension → MIME type mapping for attachment FileObjects
_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}

# Windows code page → Python codec mapping used when decoding PR_HTML
_CPID_MAP: dict[int, str] = {1200: "utf-16-le", 1201: "utf-16-be", 65001: "utf-8", 28591: "iso-8859-1"}


_MAX_REMOTE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB hard cap for remote .eml files
_DNS_TIMEOUT_SECONDS = 5  # max time to wait for getaddrinfo before treating host as unsafe
_ALLOWED_URL_SCHEMES = {"http", "https"}
# RFC-1918 + loopback private networks — must not be reachable via user-supplied URLs (SSRF)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _normalize_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1 → 127.0.0.1).

    Without this, ipaddress.ip_address("::ffff:127.0.0.1") in ip_network("127.0.0.0/8")
    returns False because they are different address families.
    """
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_private_address(host: str) -> bool:
    """Return True if *host* resolves to a private/loopback address.

    Fails closed: unresolvable or empty-result hostnames are treated as private
    to prevent SSRF via DNS tricks (empty getaddrinfo result or resolution failure).
    """
    try:
        addr = _normalize_ip(ipaddress.ip_address(host))
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass
    try:
        # socket.getaddrinfo has no built-in timeout — run it in a thread so we can
        # enforce _DNS_TIMEOUT_SECONDS and avoid hanging indefinitely on slow resolvers.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(socket.getaddrinfo, host, None)
            resolved = future.result(timeout=_DNS_TIMEOUT_SECONDS)
        if not resolved:
            # Empty result — treat as unsafe to prevent bypass via unresolvable hostnames
            return True
        for _, _, _, _, sockaddr in resolved:
            addr = _normalize_ip(ipaddress.ip_address(sockaddr[0]))
            if any(addr in net for net in _PRIVATE_NETWORKS):
                return True
    except (OSError, concurrent.futures.TimeoutError):
        # DNS resolution failed or timed out — fail closed to prevent SSRF
        return True
    return False


def _normalize_github_url(url: str) -> str:
    """Convert a GitHub blob viewer URL to its raw.githubusercontent.com equivalent."""
    match = re.match(
        r"https?://github\.com/([^/]+/[^/]+)/blob/(.+)",
        url,
    )
    if match:
        return f"https://raw.githubusercontent.com/{match.group(1)}/{match.group(2)}"
    return url


def _assert_redirect_safe(response: httpx.Response) -> None:
    """Event hook called by httpx before following each redirect.

    Raises ValueError if the redirect target is a private/loopback address,
    preventing SSRF via open-redirect chains (e.g. public URL → 127.0.0.1).
    """
    if response.is_redirect:
        location = response.headers.get("location", "")
        parsed = urlparse(location)
        if parsed.scheme not in _ALLOWED_URL_SCHEMES:
            raise ValueError(f"Redirect to unsupported scheme '{parsed.scheme}' is not allowed.")
        redirect_host = parsed.hostname or ""
        if not redirect_host:
            raise ValueError("Redirect location contains no valid hostname.")
        if _is_private_address(redirect_host):
            raise ValueError(
                f"Redirect to '{redirect_host}' resolves to a private or loopback address and is not allowed."
            )


def _fetch_url_content(url: str) -> bytes:
    """Fetch raw bytes from a public URL pointing to an .eml file.

    Raises ValueError for invalid/unsafe URLs and IOError for network failures.
    """
    url = _normalize_github_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed.")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must contain a valid hostname.")
    if _is_private_address(host):
        raise ValueError(f"URL hostname '{host}' resolves to a private or loopback address and is not allowed.")

    try:
        with (
            httpx.Client(
                follow_redirects=True,
                timeout=30,
                event_hooks={"response": [_assert_redirect_safe]},
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code == 404:
                raise IOError(f"Remote resource not found (HTTP 404): {url}")
            if response.status_code != 200:
                raise IOError(f"Failed to fetch URL (HTTP {response.status_code}): {url}")

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_REMOTE_SIZE_BYTES:
                    raise ValueError(
                        f"Remote file exceeds the maximum allowed size of "
                        f"{_MAX_REMOTE_SIZE_BYTES // (1024 * 1024)} MB."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise IOError(f"Request timed out while fetching URL: {url}") from exc
    except httpx.RequestError as exc:
        raise IOError(f"Network error while fetching URL '{url}': {exc}") from exc


def _decode_inline_content(inline_content: str) -> bytes:
    """Decode Base64-encoded or raw .eml text provided as an inline string.

    Tries Base64 first; falls back to treating the input as raw RFC-5322 text.
    Raises ValueError if the result cannot be parsed as a valid email message.
    """
    raw: bytes | None = None

    # Try strict Base64 decode
    with contextlib.suppress(binascii.Error, ValueError):
        raw = base64.b64decode(inline_content, validate=True)

    # Fall back to URL-safe Base64
    if raw is None:
        try:
            # Strip existing padding before computing the correct amount — blindly
            # appending "==" produces invalid padding when the input is already padded.
            stripped = inline_content.rstrip("=")
            padding = (4 - len(stripped) % 4) % 4
            raw = base64.urlsafe_b64decode(stripped + "=" * padding)
            # Quick sanity check: decoded bytes should be printable-ish (ASCII range)
            if not raw or raw[0] > 0x7F:
                raw = None
        except (binascii.Error, ValueError):
            raw = None

    # Fall back to treating the input as raw RFC-5322 text
    if raw is None:
        raw = inline_content.encode("utf-8", errors="replace")

    # Validate that the bytes parse as a recognisable email message
    try:
        msg = _email_lib.message_from_bytes(raw)
        if not any(msg.get(h) for h in ("From", "To", "Subject", "Date", "Message-ID")):
            raise ValueError("Content does not appear to be a valid email message (no recognisable headers found).")
    except Exception as exc:
        raise ValueError(f"Inline content could not be parsed as a valid .eml: {exc}") from exc

    return raw


class EmailToolInput(BaseModel):
    attachment_names: list[str] = Field(
        default_factory=list,
        description=(
            "Filenames of attachments to analyze. "
            "Leave empty to receive only email metadata and the list of attached files."
        ),
    )
    url: str | None = Field(
        default=None,
        description=(
            "Publicly accessible URL pointing directly to a .eml file "
            "(e.g. https://raw.githubusercontent.com/.../email.eml). "
            "The tool fetches and analyzes the file as if it were uploaded. "
            "Only http and https schemes are accepted; private/internal addresses are blocked."
        ),
    )
    inline_content: str | None = Field(
        default=None,
        description=(
            "Inline .eml content provided as a Base64-encoded string or as raw RFC-5322 text. "
            "The tool detects the encoding automatically. "
            "Use this when the email data is available in memory rather than as a file or URL."
        ),
    )


class EmailAnalysisTool(CodeMieTool, FileToolMixin):
    """Specialized tool for EML and MSG email files.

    Default call returns email headers, body, and a list of attachment names/sizes.
    Pass attachment filenames via `attachment_names` to extract their content using
    the appropriate specialized tool (PDFTool, DocxTool, XlsxTool, PPTXTool, or
    FileAnalysisTool for other formats).
    """

    name: str = EMAIL_TOOL.name
    label: str = EMAIL_TOOL.label or ""
    description: str = EMAIL_TOOL.description or ""
    args_schema: Type[BaseModel] = EmailToolInput

    config: FileAnalysisConfig

    def __init__(self, config: FileAnalysisConfig) -> None:
        super().__init__(config=config)

    def _get_supported_mime_types(self) -> Optional[List[str]]:
        return ["message/rfc822", "application/vnd.ms-outlook"]

    def _get_supported_extensions(self) -> Optional[List[str]]:
        return [".eml", ".msg"]

    def execute(
        self,
        attachment_names: list[str] | None = None,
        url: str | None = None,
        inline_content: str | None = None,
    ) -> str:
        files: list[FileObject] = list(self._get_supported_files() or [])

        if url:
            try:
                raw = _fetch_url_content(url)
            except (ValueError, IOError) as exc:
                raise ValueError(f"Failed to fetch email from URL: {exc}") from exc
            filename = os.path.basename(urlparse(url).path) or "remote_email.eml"
            if not filename.lower().endswith((".eml", ".msg")):
                filename = filename + ".eml"
            files.append(FileObject(name=filename, mime_type="message/rfc822", owner="url", content=raw))

        if inline_content:
            # Base64 encodes 3 bytes as 4 chars, so the max encoded length for a
            # _MAX_REMOTE_SIZE_BYTES payload is ceil(limit * 4/3). Reject before
            # decoding to prevent memory exhaustion via oversized inline content.
            _max_encoded = int(_MAX_REMOTE_SIZE_BYTES * 4 / 3) + 4  # +4 for padding
            if len(inline_content) > _max_encoded:
                raise ValueError(
                    f"Inline content exceeds the maximum allowed size of "
                    f"{_MAX_REMOTE_SIZE_BYTES // (1024 * 1024)} MB (decoded equivalent)."
                )
            try:
                raw = _decode_inline_content(inline_content)
            except ValueError as exc:
                raise ValueError(f"Failed to decode inline email content: {exc}") from exc
            files.append(FileObject(name="inline_email.eml", mime_type="message/rfc822", owner="inline", content=raw))

        if not files:
            raise ValueError(
                f"{self.name} requires at least one email source: "
                "upload a .eml/.msg file, supply a 'url', or provide 'inline_content'."
            )

        results = []
        for file_obj in files:
            content = self._process_email_file(file_obj, attachment_names or [])
            results.append(f"## {file_obj.name}\n\n{content}")

        return "\n\n---\n\n".join(results)

    def _process_email_file(self, file_obj: FileObject, attachment_names: list[str]) -> str:
        ext = os.path.splitext(file_obj.name.lower())[1]
        raw = file_obj.bytes_content()
        if not raw:
            return f"Could not read file content: {file_obj.name}"
        if ext == ".eml":
            return self._process_eml(raw, file_obj.name, attachment_names)
        if ext == ".msg":
            return self._process_msg(raw, file_obj.name, attachment_names)
        return f"Unsupported email format: {file_obj.name}"

    def _process_eml(self, email_bytes: bytes, source_name: str, attachment_names: list[str]) -> str:
        msg = _email_lib.message_from_bytes(email_bytes)

        subject = msg.get("Subject", "")
        sender = msg.get("From", "")
        recipients = msg.get("To", "")
        date = msg.get("Date", "")
        cc = msg.get("Cc", "")

        header_lines = [
            line
            for line in [
                f"**From:** {sender}" if sender else "",
                f"**To:** {recipients}" if recipients else "",
                f"**Cc:** {cc}" if cc else "",
                f"**Subject:** {subject}" if subject else "",
                f"**Date:** {date}" if date else "",
            ]
            if line
        ]

        body = self._extract_eml_body(msg)
        parts = header_lines + (["", body] if body else [])

        if msg.is_multipart():
            attachments = self._list_eml_attachments(msg)
            if attachments:
                parts.append("\n**Attachments:**")
                for att_name, size in attachments:
                    parts.append(f"- {att_name} ({size} bytes)")

            if attachment_names:
                for att_name, data in self._get_eml_attachment_data(msg, attachment_names):
                    logger.info(f"EmailAnalysisTool: analyzing EML attachment '{att_name}' from {source_name}")
                    parts.append(f"\n### Attachment: {att_name}\n\n{self._analyze_attachment(data, att_name)}")

        return "\n".join(parts)

    def _extract_eml_body(self, msg: email.message.Message) -> str:
        plain_parts: list[str] = []
        html_parts: list[str] = []

        if msg.is_multipart():
            for part in msg.walk():
                disposition = part.get("Content-Disposition", "")
                if "attachment" in disposition:
                    continue
                content_type = part.get_content_type()
                decoded = self._decode_part(part)
                if decoded:
                    if content_type == "text/plain":
                        plain_parts.append(decoded)
                    elif content_type == "text/html":
                        html_parts.append(decoded)
        else:
            decoded = self._decode_part(msg)
            if decoded:
                if msg.get_content_type() == "text/html":
                    html_parts.append(decoded)
                else:
                    plain_parts.append(decoded)

        return "\n\n".join(plain_parts) if plain_parts else "\n\n".join(html_parts)

    @staticmethod
    def _decode_part(part: email.message.Message) -> str:
        try:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes) or not payload:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to decode email part: {e}")
            return ""

    def _list_eml_attachments(self, msg: email.message.Message) -> list[tuple[str, int]]:
        """Return (filename, byte_size) pairs for all EML attachments."""
        result: list[tuple[str, int]] = []
        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" not in disposition:
                continue
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else 0
            result.append((filename, size))
        return result

    def _get_eml_attachment_data(self, msg: email.message.Message, names: list[str]) -> list[tuple[str, bytes]]:
        """Return (filename, data) for each requested attachment (case-insensitive match)."""
        lower_names = {n.lower() for n in names}
        result: list[tuple[str, bytes]] = []
        for part in msg.walk():
            disposition = part.get("Content-Disposition", "")
            if "attachment" not in disposition:
                continue
            filename = part.get_filename()
            if not filename or filename.lower() not in lower_names:
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and payload:
                result.append((filename, payload))
        return result

    def _process_msg(self, msg_bytes: bytes, source_name: str, attachment_names: list[str]) -> str:
        try:
            import olefile
        except ImportError:
            logger.error("EmailAnalysisTool: olefile is not installed; cannot parse MSG file")
            return "MSG processing unavailable (olefile not installed)."

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
                tmp.write(msg_bytes)
                tmp.flush()
                temp_path = tmp.name

            ole = olefile.OleFileIO(temp_path)
        except Exception as e:
            logger.error(f"EmailAnalysisTool: failed to open MSG file {source_name}: {e}")
            return f"Failed to open MSG file: {e}"

        try:
            subject = self._read_ole_text(ole, _MSG_SUBJECT_UNICODE) or self._read_ole_text(ole, _MSG_SUBJECT_ASCII)
            sender_name = self._read_ole_text(ole, _MSG_SENDER_NAME)
            sender_smtp = self._read_ole_text(ole, _MSG_SENDER_SMTP) or self._read_ole_text(ole, _MSG_SENDER_EMAIL)
            # Discard Exchange X.500 DN addresses (start with /O=)
            if sender_smtp.upper().startswith("/O="):
                sender_smtp = ""
            sender = f"{sender_name} <{sender_smtp}>" if sender_name and sender_smtp else sender_smtp or sender_name
            recipients = self._read_ole_text(ole, _MSG_TO)
            cc = self._read_ole_text(ole, _MSG_CC)
            date = self._read_msg_date(ole)
            body = (
                self._read_ole_text(ole, _MSG_BODY_UNICODE)
                or self._read_ole_text(ole, _MSG_BODY_ASCII)
                or self._read_msg_html_body(ole)
                or self._read_msg_rtf_body(ole)
            )

            header_lines = [
                line
                for line in [
                    f"**From:** {sender}" if sender else "",
                    f"**To:** {recipients}" if recipients else "",
                    f"**Cc:** {cc}" if cc else "",
                    f"**Subject:** {subject}" if subject else "",
                    f"**Date:** {date}" if date else "",
                ]
                if line
            ]

            parts = header_lines + (["", body] if body else [])

            attach_dirs = [
                entry for entry in ole.listdir(streams=False, storages=True) if entry[0].startswith("__attach_")
            ]

            # Build attachment registry: (filename, data_bytes_or_none)
            attach_registry: list[tuple[str, bytes | None]] = []
            for attach_dir in attach_dirs:
                dir_path = "/".join(attach_dir)
                filename = self._read_ole_text(ole, f"{dir_path}/{_MSG_ATTACH_FILENAME_LONG}") or self._read_ole_text(
                    ole, f"{dir_path}/{_MSG_ATTACH_FILENAME_SHORT}"
                )
                if not filename:
                    continue
                data: bytes | None = None
                data_stream = f"{dir_path}/{_MSG_ATTACH_DATA}"
                if ole.exists(data_stream):
                    try:
                        raw = ole.openstream(data_stream).read()
                        data = raw if raw else None
                    except Exception as e:
                        logger.warning(f"EmailAnalysisTool: failed to read MSG attachment '{filename}': {e}")
                attach_registry.append((filename, data))

            if attach_registry:
                parts.append("\n**Attachments:**")
                for att_name, data in attach_registry:
                    size = len(data) if data else 0
                    parts.append(f"- {att_name} ({size} bytes)")

            if attachment_names:
                lower_names = {n.lower() for n in attachment_names}
                for att_name, data in attach_registry:
                    if att_name.lower() not in lower_names or not data:
                        continue
                    logger.info(f"EmailAnalysisTool: analyzing MSG attachment '{att_name}' from {source_name}")
                    parts.append(f"\n### Attachment: {att_name}\n\n{self._analyze_attachment(data, att_name)}")

            return "\n".join(parts)
        finally:
            ole.close()
            if temp_path:
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)

    @staticmethod
    def _read_ole_text(ole: Any, stream_path: str) -> str:
        try:
            if ole.exists(stream_path):
                raw = ole.openstream(stream_path).read()
                if stream_path.endswith("001F"):
                    return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
                return raw.decode("latin-1", errors="replace").rstrip("\x00")
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not read OLE stream {stream_path}: {e}")
        return ""

    @staticmethod
    def _read_msg_date(ole: Any) -> str:
        """Extract the send date from the MSG file.

        First tries the transport headers (contains full RFC 2822 Date header).
        Falls back to parsing the PR_CLIENT_SUBMIT_TIME FILETIME value.
        """
        # Try transport headers first — contains the original RFC Date line
        try:
            if ole.exists(_MSG_TRANSPORT_HEADERS):
                raw = ole.openstream(_MSG_TRANSPORT_HEADERS).read()
                headers_text = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
                for line in headers_text.splitlines():
                    if line.lower().startswith("date:"):
                        return line[5:].strip()
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not parse transport headers for date: {e}")

        # Fall back to FILETIME binary property (PR_CLIENT_SUBMIT_TIME)
        try:
            if ole.exists(_MSG_DATE):
                raw = ole.openstream(_MSG_DATE).read()
                if len(raw) >= 8:
                    filetime = struct.unpack_from("<Q", raw)[0]
                    # Windows FILETIME: 100-nanosecond intervals since 1601-01-01
                    epoch_diff = 116444736000000000  # 100-ns ticks between 1601 and 1970
                    unix_ts = (filetime - epoch_diff) / 10_000_000
                    dt = datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)
                    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not parse MSG date FILETIME: {e}")

        return ""

    @staticmethod
    def _read_msg_html_body(ole: Any) -> str:
        """Extract and strip the HTML body stored in PR_HTML (binary blob) to plain text."""
        raw_html = ""
        try:
            if not ole.exists(_MSG_BODY_HTML):
                return ""
            raw = ole.openstream(_MSG_BODY_HTML).read()
            if not raw:
                return ""
            # Determine charset from PR_INTERNET_CPID (PT_LONG, 4 bytes little-endian)
            cpid: int = 0
            try:
                if ole.exists(_MSG_INTERNET_CPID):
                    cpid_raw = ole.openstream(_MSG_INTERNET_CPID).read()
                    if len(cpid_raw) >= 4:
                        cpid = struct.unpack_from("<I", cpid_raw)[0]
            except Exception:
                pass
            charset = _CPID_MAP.get(cpid) or (f"cp{cpid}" if cpid else "utf-8")
            try:
                raw_html = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                raw_html = raw.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not read MSG HTML body: {e}")
            return ""

        if not raw_html:
            return ""

        # Strip <style> and <script> blocks entirely
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
        # Replace common block-level tags with newlines for readability
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|tr|li|blockquote|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        # Remove all remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode basic HTML entities
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&nbsp;", " ")
        )
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _read_msg_rtf_body(ole: Any) -> str:
        """Decompress and extract plain text from the RTF-compressed body stream."""
        try:
            if not ole.exists(_MSG_BODY_RTF):
                return ""
            compressed = ole.openstream(_MSG_BODY_RTF).read()
            if not compressed:
                return ""
            try:
                import compressed_rtf

                rtf_bytes = compressed_rtf.decompress(compressed)
            except ImportError:
                logger.debug("EmailAnalysisTool: compressed_rtf not installed; skipping RTF body")
                return ""
            rtf_text = rtf_bytes.decode("latin-1", errors="replace")
            # Strip RTF control words to get readable plain text
            plain = re.sub(r"\\[a-z]+[-\d]*\s?", " ", rtf_text)
            plain = re.sub(r"[{}]", "", plain)
            plain = re.sub(r"\\'\.\.", "", plain)
            plain = re.sub(r" {2,}", " ", plain)
            return plain.strip()
        except Exception as e:
            logger.debug(f"EmailAnalysisTool: could not extract RTF body: {e}")
        return ""

    def _analyze_attachment(self, data: bytes, filename: str) -> str:
        """Delegate attachment analysis to the appropriate specialized tool.

        Images require a temp file because MarkItDown's ImageConverter needs the file extension.
        All other formats are handled by their dedicated tool or FileAnalysisTool.
        """
        ext = os.path.splitext(filename.lower())[1]

        if ext in _IMAGE_EXTENSIONS:
            return self._analyze_image_attachment(data, filename, ext)

        from codemie_tools.file_analysis.docx.models import QueryType as DocxQueryType
        from codemie_tools.file_analysis.docx.tools import DocxTool
        from codemie_tools.file_analysis.file_analysis_tool import FileAnalysisTool
        from codemie_tools.file_analysis.pdf.tools import PDFTool
        from codemie_tools.file_analysis.pdf.tools import QueryType as PDFQueryType
        from codemie_tools.file_analysis.pptx.tools import PPTXTool
        from codemie_tools.file_analysis.pptx.tools import QueryType as PPTXQueryType
        from codemie_tools.file_analysis.xlsx.tools import XlsxTool

        mime = _EXT_TO_MIME.get(ext, "application/octet-stream")
        file_obj = FileObject(name=filename, mime_type=mime, owner="email", content=data)
        cfg = FileAnalysisConfig(input_files=[file_obj], chat_model=self.config.chat_model)

        try:
            if ext == ".pdf":
                return str(PDFTool(config=cfg).execute(pages=[], query=PDFQueryType.TEXT))
            if ext == ".docx":
                return str(DocxTool(config=cfg).execute(query=DocxQueryType.TEXT))
            if ext in (".xlsx", ".xls"):
                return str(XlsxTool(config=cfg).execute())
            if ext == ".pptx":
                return str(PPTXTool(config=cfg).execute(slides=[], query=PPTXQueryType.TEXT))
            # CSV, HTML, TXT, ZIP and other formats: use FileAnalysisTool's core processor directly
            # _is_supported_file() is intentionally bypassed — we are analyzing a known attachment
            result = FileAnalysisTool(config=cfg)._process_single_file(file_obj)
            return result or f"[No extractable text in {filename}]"
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to analyze attachment '{filename}': {e}")
            return f"[Could not extract content from {filename}: {e}]"

    def _analyze_image_attachment(self, data: bytes, filename: str, ext: str) -> str:
        """Analyze image attachments via MarkItDown using a temp file (extension required)."""
        temp_path: str | None = None
        try:
            chat_model = self.config.chat_model
            llm_model = (
                getattr(chat_model, "model_name", None) or getattr(chat_model, "model", None) if chat_model else None
            )
            md = MarkItDown(
                enable_builtins=True,
                llm_client=getattr(chat_model, "root_client", None) if chat_model else None,
                llm_model=llm_model,
            )
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(data)
                tmp.flush()
                temp_path = tmp.name
            result = md.convert(temp_path)
            return result.text_content or f"[No extractable text in {filename}]"
        except Exception as e:
            logger.warning(f"EmailAnalysisTool: failed to analyze image attachment '{filename}': {e}")
            return f"[Could not extract content from {filename}: {e}]"
        finally:
            if temp_path:
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)
