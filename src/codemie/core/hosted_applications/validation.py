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

"""Bounded inspection/extraction for use inside the unprivileged validator worker."""

from __future__ import annotations

import hashlib
import re
import stat
import struct
import zipfile
import posixpath
from urllib.parse import urlsplit, unquote
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.paths import canonical_artifact_path

_HTML_SUFFIX = ".html"
_HTML_SUFFIXES = (_HTML_SUFFIX, ".htm")
_DOCUMENT_SUFFIXES = (*_HTML_SUFFIXES, ".svg")
_INDEX_HTML = "index.html"
_MACOSX_PREFIX = "__MACOSX/"

MIME_TYPES = {
    _HTML_SUFFIX: "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".webm": "video/webm",
}
_TEXT_SUFFIXES = {_HTML_SUFFIX, ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".csv", ".svg"}
_FORBIDDEN_NAMES = {"package.json", "package-lock.json", "server.js", "server.mjs", "web.config", "nginx.conf"}
_FORBIDDEN_PARTS = {"node_modules", ".git", "server", "ssr"}
_SECRETS = re.compile(
    rb"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,255}\b"
)
# Informational only: the runtime CSP has no 'unsafe-eval', sets worker-src 'none' and rejects service-worker
# requests, so these calls cannot run. Most bundlers emit them (webpack 5's global shim is new Function(...)).
_RISKY_SCRIPT = re.compile(
    r"\beval\s*\(|\bnew\s+Function\s*\(|\b(?:SharedWorker|Worker)\s*\(|serviceWorker\s*\.\s*register"
)


# Fixed wording for every advisory that crosses a process boundary; parser-authored text never does.
WARNING_MESSAGES = {
    "EXTERNAL_DEPENDENCY": "External resources are blocked; bundle them locally",
    "RESTRICTED_CAPABILITY": "Dynamic evaluation and workers are blocked by the runtime policy",
    "PACKAGING_METADATA": "Bounded packaging metadata was omitted",
    "MISSING_LOCAL_RESOURCE": "A referenced local resource is missing; include it in the bundle",
}


@dataclass(frozen=True)
class ValidationLimits:
    max_html_bytes: int = 20 * 1024 * 1024
    max_zip_bytes: int = 100 * 1024 * 1024
    max_expanded_bytes: int = 500 * 1024 * 1024
    max_file_bytes: int = 50 * 1024 * 1024
    max_files: int = 5000
    max_ratio: int = 100


@dataclass(frozen=True)
class CandidateFile:
    path: str
    key: str
    size: int
    sha256: str
    mime: str
    document: bool


@dataclass(frozen=True)
class ValidationWarning:
    code: str
    path: str
    message: str


@dataclass
class BundleCandidate:
    files: list[CandidateFile] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def candidate_from_result(value) -> BundleCandidate:
    """Rebuild a parser result as a bounded candidate, trusting none of its fields.

    The validator parent checks the sandboxed parser's output with this before it opens any
    reported file, and the API checks the validator's response with it again. Raises
    ``ValueError``, ``KeyError``, ``TypeError`` or ``HostedApplicationError`` on any mismatch.
    """
    limits = ValidationLimits()
    if not isinstance(value, dict) or not isinstance(value["files"], list):
        raise ValueError("Invalid candidate")
    if not 1 <= len(value["files"]) <= limits.max_files:
        raise ValueError("Invalid file count")
    candidate = BundleCandidate()
    paths = set()
    for item in value["files"]:
        path = _checked_candidate_path(item, paths, limits)
        paths.add(path.casefold())
        candidate.files.append(
            CandidateFile(**{key: item[key] for key in ("path", "key", "size", "sha256", "mime", "document")})
        )
        candidate.total_bytes += item["size"]
    if candidate.total_bytes > limits.max_expanded_bytes or candidate.total_bytes != value["total_bytes"]:
        raise ValueError("Invalid total size")
    candidate.entries, candidate.roots = value["entries"], value["roots"]
    _check_candidate_entrypoints(candidate, limits)
    _rebuild_candidate_warnings(value["warnings"], candidate)
    return candidate


def _checked_candidate_path(item, seen: set[str], limits: ValidationLimits) -> str:
    path = canonical_artifact_path(item["path"])
    suffix = PurePosixPath(path).suffix.lower()
    if (
        path.casefold() in seen
        or item["key"] != path
        or type(item["size"]) is not int
        or not 0 <= item["size"] <= limits.max_file_bytes
        or not isinstance(item["sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
        or item["mime"] != MIME_TYPES.get(suffix)
        or type(item["document"]) is not bool
        or item["document"] != (suffix in _DOCUMENT_SUFFIXES)
    ):
        raise ValueError("Invalid candidate file")
    return path


def _check_candidate_entrypoints(candidate: BundleCandidate, limits: ValidationLimits) -> None:
    if (
        not isinstance(candidate.entries, list)
        or not 1 <= len(candidate.entries) <= limits.max_files
        or not isinstance(candidate.roots, list)
        or not 1 <= len(candidate.roots) <= limits.max_files
    ):
        raise ValueError("Invalid entrypoints")
    available = {item.path: item for item in candidate.files}
    for entry in candidate.entries:
        canonical_artifact_path(entry)
        if entry not in available or not available[entry].mime.startswith("text/html"):
            raise ValueError("Invalid entrypoint")
    for root in candidate.roots:
        if root:
            canonical_artifact_path(root)


def _rebuild_candidate_warnings(warnings, candidate: BundleCandidate) -> None:
    if not isinstance(warnings, list) or len(warnings) > 10000:
        raise ValueError("Invalid warnings")
    for warning in warnings:
        if warning["code"] not in WARNING_MESSAGES:
            raise ValueError("Invalid warning")
        canonical_artifact_path(warning["path"])
        candidate.warnings.append(
            ValidationWarning(warning["code"], warning["path"], WARNING_MESSAGES[warning["code"]])
        )


class _References(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "link", "img", "video", "audio", "source", "iframe"):
            self.references.extend(value for name, value in attrs if name in ("src", "href") and value)


def _file_type(path: str) -> tuple[str, bool]:
    parts = PurePosixPath(path).parts
    suffix = PurePosixPath(path).suffix.lower()
    if (
        any(p.casefold() in _FORBIDDEN_PARTS or p.startswith(".") for p in parts)
        or parts[-1].casefold() in _FORBIDDEN_NAMES
        or suffix not in MIME_TYPES
        or path.casefold().endswith(".map")
    ):
        raise HostedApplicationError(
            "UNSUPPORTED_ARTIFACT", "Upload browser build files only; an unsupported file was found"
        )
    return MIME_TYPES[suffix], suffix in _DOCUMENT_SUFFIXES


def _inspect_text(data: bytes, path: str, candidate: BundleCandidate) -> None:
    if _SECRETS.search(data):
        raise HostedApplicationError(
            "SECRET_DETECTED", "Credential material detected; remove it and upload a new artifact"
        )
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in _TEXT_SUFFIXES:
        from codemie.core.hosted_applications.binary import inspect_binary

        inspect_binary(data, suffix)
        return
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise HostedApplicationError("INVALID_ENCODING", "Text files must use valid UTF-8") from exc
    if "\x00" in text:
        raise HostedApplicationError("UNSUPPORTED_ARTIFACT", "Text files cannot contain binary data")
    if suffix == ".svg":
        _inspect_svg(text)
    if suffix in _HTML_SUFFIXES:
        _inspect_html(text, path, candidate)
    if suffix in (*_HTML_SUFFIXES, ".js", ".mjs") and _RISKY_SCRIPT.search(text):
        candidate.warnings.append(
            ValidationWarning(
                "RESTRICTED_CAPABILITY", path, "Dynamic evaluation and workers are blocked by the runtime policy"
            )
        )


def _inspect_svg(text: str) -> None:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise HostedApplicationError("UNSAFE_SVG", "SVG document declarations and entities are unsupported")
    try:
        root = ElementTree.fromstring(text)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            raise ValueError("Not SVG")
    except (ElementTree.ParseError, ValueError) as exc:
        raise HostedApplicationError("UNSAFE_SVG", "Invalid SVG document") from exc


def _inspect_html(text: str, path: str, candidate: BundleCandidate) -> None:
    if not re.search(r"<(?:html|head|body|div|main|section|!doctype)\b", text, re.IGNORECASE):
        raise HostedApplicationError("INVALID_HTML", "The upload is not an HTML document")
    parser = _References()
    parser.feed(text)
    if any(re.match(r"(?:https?:)?//", ref, re.IGNORECASE) for ref in parser.references):
        candidate.warnings.append(
            ValidationWarning("EXTERNAL_DEPENDENCY", path, "External resources are blocked; bundle them locally")
        )


def _copy_file(stream, path: str, destination: Path, candidate: BundleCandidate, limits: ValidationLimits) -> None:
    mime, document = _file_type(path)
    output = destination / path
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256()
    count = 0
    # Destination is a fresh private worker directory; ZIP entries cannot create links.
    with output.open("xb") as target:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            candidate.total_bytes += len(chunk)
            if count > limits.max_file_bytes or candidate.total_bytes > limits.max_expanded_bytes:
                raise HostedApplicationError(
                    "ARCHIVE_EXPANSION_LIMIT", "Expanded artifact exceeds the configured limit", 413
                )
            digest.update(chunk)
            target.write(chunk)
    _inspect_text(output.read_bytes(), path, candidate)
    candidate.files.append(CandidateFile(path, path, count, digest.hexdigest(), mime, document))


def _inspect_metadata(bundle, entry, path, candidate):
    if entry.file_size > 1024 * 1024:
        raise HostedApplicationError("UNSUPPORTED_ARTIFACT", "Packaging metadata exceeds its limit")
    data = bundle.read(entry)
    if _SECRETS.search(data):
        raise HostedApplicationError(
            "SECRET_DETECTED", "Credential material detected; remove it and upload a new artifact"
        )
    if PurePosixPath(path).name == ".DS_Store":
        valid = data.startswith(bytes.fromhex("0000000142756431"))
    else:
        valid = _apple_double_valid(data, path)
    if not valid:
        raise HostedApplicationError("UNSUPPORTED_ARTIFACT", "Unrecognized packaging metadata was found")
    candidate.warnings.append(
        ValidationWarning("PACKAGING_METADATA", path, "Recognized packaging metadata excluded from hosted files")
    )


def _apple_double_valid(data: bytes, path: str) -> bool:
    """An AppleDouble ``__MACOSX/**/._name`` sidecar of a supported file, with in-bounds entries."""
    name = PurePosixPath(path).name
    if not path.startswith(_MACOSX_PREFIX) or not name.startswith("._"):
        raise HostedApplicationError("UNSUPPORTED_ARTIFACT", "An unsupported packaging file was found")
    target = str(PurePosixPath(path.removeprefix(_MACOSX_PREFIX)).with_name(name[2:]))
    _file_type(canonical_artifact_path(target))
    if len(data) < 26 or data[:8] != struct.pack(">II", 0x00051607, 0x00020000):
        return False
    count = struct.unpack_from(">H", data, 24)[0]
    if count > 32 or len(data) < 26 + count * 12:
        return False
    for index in range(count):
        _, offset, length = struct.unpack_from(">III", data, 26 + index * 12)
        if offset < 26 + count * 12 or offset + length > len(data):
            return False
    return True


def _archive_entries(bundle, candidate, limits):
    entries = bundle.infolist()
    if len(entries) > limits.max_files + 100:
        raise HostedApplicationError("ARCHIVE_FILE_LIMIT", "Archive contains too many entries", 413)
    seen, files, metadata = set(), [], []
    expanded = compressed = 0
    for entry in entries:
        path = _archive_entry_path(entry)
        if entry.is_dir():
            continue
        folded = path.casefold()
        if folded in seen:
            raise HostedApplicationError("ARCHIVE_PATH_COLLISION", "Archive paths collide after normalization")
        seen.add(folded)
        expanded += entry.file_size
        compressed += entry.compress_size
        if entry.file_size > limits.max_file_bytes or expanded > limits.max_expanded_bytes:
            raise HostedApplicationError(
                "ARCHIVE_EXPANSION_LIMIT", "Expanded artifact exceeds the configured limit", 413
            )
        _classify_archive_entry(entry, path, files, metadata)
        if len(files) > limits.max_files or len(metadata) > 100:
            raise HostedApplicationError("ARCHIVE_FILE_LIMIT", "Archive contains too many files", 413)
    _check_expansion_ratio(files, expanded, compressed, limits)
    for entry, path in metadata:
        _inspect_metadata(bundle, entry, path, candidate)
    return files


def _archive_entry_path(entry) -> str:
    path = canonical_artifact_path(entry.filename[:-1] if entry.is_dir() else entry.filename)
    file_kind = stat.S_IFMT(entry.external_attr >> 16)
    if file_kind not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
        raise HostedApplicationError("UNSUPPORTED_ARCHIVE", "Links, devices and encrypted entries are unsupported")
    return path


def _classify_archive_entry(entry, path: str, files: list, metadata: list) -> None:
    if path.startswith(_MACOSX_PREFIX) or PurePosixPath(path).name == ".DS_Store":
        metadata.append((entry, path))
    else:
        _file_type(path)
        files.append((entry, path))


def _check_expansion_ratio(files: list, expanded: int, compressed: int, limits: ValidationLimits) -> None:
    if not files or (expanded and (not compressed or expanded > compressed * limits.max_ratio)):
        raise HostedApplicationError(
            "ARCHIVE_EXPANSION_LIMIT", "Archive is empty or exceeds the expansion ratio limit", 413
        )


def _missing_dependencies(candidate, destination):
    paths = {item.path for item in candidate.files}
    remaining = 200
    for item in candidate.files:
        if remaining <= 0:
            break
        references = _local_references(item.path, destination)
        if references is None:
            continue
        for value in _missing_references(references[:100], item.path, candidate.roots, paths):
            candidate.warnings.append(
                ValidationWarning("MISSING_LOCAL_RESOURCE", item.path, "Missing bundled resource: " + value[:160])
            )
            remaining -= 1
            if remaining <= 0:
                break


def _local_references(path: str, destination: Path) -> list[str] | None:
    """References in an HTML or CSS file, or ``None`` when the file is not checked."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in (*_HTML_SUFFIXES, ".css"):
        return None
    text = (destination / path).read_text(encoding="utf-8")
    if suffix == ".css":
        return re.findall(r"url\(\s*['\"]?([^'\"\s)]+)", text)
    # Explicit <base> can change the bundle's root; avoid misleading guesses.
    if re.search(r"<base\b", text, re.IGNORECASE):
        return None
    parser = _References()
    parser.feed(text)
    return parser.references


def _missing_references(references: list[str], source: str, roots: list[str], paths: set[str]):
    """Yield the decoded path of each local reference that resolves to no bundled file."""
    for reference in references:
        try:
            resolved = _resolve_reference(reference, source, roots)
        except ValueError:
            continue
        if resolved is not None and not resolved[1].intersection(paths):
            yield resolved[0]


def _resolve_reference(reference: str, source: str, roots: list[str]) -> tuple[str, set[str]] | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    value = unquote(parsed.path, errors="strict")
    if value.startswith("/"):
        targets = {root + value if root else value.lstrip("/") for root in roots}
    else:
        targets = {posixpath.normpath(posixpath.join(posixpath.dirname(source), value))}
    return value, targets


def inspect_bundle(
    source: Path, kind: str, destination: Path, limits: ValidationLimits | None = None
) -> BundleCandidate:
    limits = limits or ValidationLimits()
    candidate = BundleCandidate()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    if kind == "html":
        _extract_html(source, destination, candidate, limits)
    elif kind == "zip":
        _extract_zip(source, destination, candidate, limits)
    else:
        raise HostedApplicationError("UNSUPPORTED_ARTIFACT", "Upload HTML or a ZIP containing a static browser build")
    candidate.files.sort(key=lambda item: item.path)
    candidate.entries = [
        item.path for item in candidate.files if PurePosixPath(item.path).suffix.lower() in _HTML_SUFFIXES
    ]
    if not candidate.entries:
        raise HostedApplicationError("ENTRYPOINT_REQUIRED", "Artifact must contain an HTML entrypoint")
    candidate.roots = _bundle_roots(candidate.entries)
    _missing_dependencies(candidate, destination)
    return candidate


def _extract_html(source: Path, destination: Path, candidate: BundleCandidate, limits: ValidationLimits) -> None:
    if source.stat().st_size > limits.max_html_bytes:
        raise HostedApplicationError("UPLOAD_TOO_LARGE", "HTML upload exceeds the configured limit", 413)
    with source.open("rb") as stream:
        _copy_file(stream, _INDEX_HTML, destination, candidate, limits)


def _extract_zip(source: Path, destination: Path, candidate: BundleCandidate, limits: ValidationLimits) -> None:
    if source.stat().st_size > limits.max_zip_bytes:
        raise HostedApplicationError("UPLOAD_TOO_LARGE", "ZIP upload exceeds the configured limit", 413)
    try:
        with zipfile.ZipFile(source) as bundle:
            files = _archive_entries(bundle, candidate, limits)
            for entry, path in files:
                with bundle.open(entry, "r") as stream:
                    _copy_file(stream, path, destination, candidate, limits)
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise HostedApplicationError("INVALID_ARCHIVE", "Archive could not be safely read") from exc


def _bundle_roots(entries: list[str]) -> list[str]:
    index_entries = [entry for entry in entries if PurePosixPath(entry).name.lower() == _INDEX_HTML]
    if _INDEX_HTML in index_entries:
        return [""]
    root_entries = index_entries or entries
    return sorted(
        {str(PurePosixPath(entry).parent) if str(PurePosixPath(entry).parent) != "." else "" for entry in root_entries}
    )
