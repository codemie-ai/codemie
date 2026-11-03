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

"""Framework sessions counted in Postgres, matched to the Elasticsearch session rows by person and repository.

Pure helpers, no I/O. Elasticsearch ``attributes.repository`` is written by the CLI (codemie-code); Postgres keeps
``repo_remote``, the repository id the CLI already parsed from the git remote (the same value as in Elasticsearch;
older or other writers may store a raw URL, and both are handled), and ``repository``, the earliest raw working
directory of the session. The match key keeps a parsed id as it is and derives the working-directory fallback by
the rules the CLI applies, so both sides meet on one string; the merge compares the repository through
``identity.canonical_value`` on both sides, so ``Acme//Api/`` and ``acme/api`` are one repository.

Mirrored CLI code (codemie-code):

- ``src/utils/processes.ts`` ``parseGitRemoteUrl``: suffix and query strip, scheme URL and scp-style
  matchers, credential bail-out, host and port, per-segment URI decoding, Azure DevOps and legacy Visual Studio
  hosts, Bitbucket ``scm/`` and ``~user``, bare-path last two segments, lower-casing.
- ``src/utils/processes.ts`` ``detectGitRemoteRepo`` (``git remote get-url origin`` output is trimmed) and
  ``resolveRepositoryName``: the remote first, the working directory second.
- ``src/utils/paths.ts`` ``normalizePathSeparators``, ``splitPath``, the Cowork sandbox
  pattern and ``extractRepository``.
- ``src/providers/plugins/sso/session/processors/metrics/metrics-post-processor.ts`` ``truncateProjectPath``,
  applied by the CLI before upload: for a value that is not an absolute path only the drive-letter result matters.

The CLI keeps the case of the working-directory fallback (``Cowork``, ``Projects/App``); the key here is always
lower-cased and the merge lower-cases the Elasticsearch value too, so the case never matters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

from codemie.service.usage_attribution.identity import canonical_value

_ASCII = re.ASCII
_URL_RE = re.compile(r"(\w+)://(?:[^@/]+@)?([^/]+)/(.+)\Z", _ASCII)
_SCP_USER_RE = re.compile(r"[^/@]+@([^:]+):(.+)\Z")
_SCP_ALIAS_RE = re.compile(r"([^/@\\:]{2,}):(?!//)([^@]+)\Z")
_SCHEME_RE = re.compile(r"(?!file:)\w+://", _ASCII | re.IGNORECASE)
_SCHEME_ANY_RE = re.compile(r"\w+://", _ASCII)
_PORT_RE = re.compile(r":\d+\Z")
_PERCENT_RUN_RE = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_BAD_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_COWORK_RE = re.compile(
    r"/local-agent-mode-sessions/|/local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:/|\Z)",
    re.IGNORECASE,
)
_DRIVE_RE = re.compile(r"[A-Za-z]:\Z")
_UNKNOWN = "unknown"


def _decode_uri_component(segment: str) -> str:
    """JavaScript ``decodeURIComponent``: raises ``ValueError`` on a malformed escape or invalid UTF-8."""
    if _BAD_PERCENT_RE.search(segment):
        raise ValueError(segment)
    return _PERCENT_RUN_RE.sub(lambda m: bytes.fromhex(m.group().replace("%", "")).decode("utf-8"), segment)


def _decode_segment(segment: str) -> str:
    try:
        decoded = _decode_uri_component(segment)
    except ValueError:
        return segment  # a malformed escape keeps the raw segment
    unsafe = "/" in decoded or any(ord(c) <= 0x1F or 0x7F <= ord(c) <= 0x9F or ord(c) > 0xFF for c in decoded)
    return segment if unsafe else decoded


class _RemoteParts(NamedTuple):
    """The scheme, host and path of a raw remote; ``structured`` is True for a scheme URL or an scp form."""

    scheme: str | None
    host: str | None
    path: str
    structured: bool


def _split_remote(without_git_suffix: str) -> _RemoteParts | None:
    """Scheme URL and scp-style matchers of ``parseGitRemoteUrl``; None for a credential or unparsable remote."""
    url_match = _URL_RE.match(without_git_suffix)
    if url_match:
        return _RemoteParts(url_match.group(1), url_match.group(2), url_match.group(3), True)
    scp_match = _SCP_USER_RE.match(without_git_suffix) or _SCP_ALIAS_RE.match(without_git_suffix)
    if scp_match:
        return _RemoteParts(None, scp_match.group(1), scp_match.group(2), True)
    if _SCHEME_RE.match(without_git_suffix) or "@" in without_git_suffix:
        return None
    return _RemoteParts(None, None, without_git_suffix, False)


def _path_segments(parts: _RemoteParts) -> list[str]:
    """The non-empty path segments, URI-decoded: split on ``/`` for a URL or scp form, on ``/`` and ``\\`` otherwise."""
    raw_segments = parts.path.split("/") if parts.structured else re.split(r"[/\\]", parts.path)
    return [_decode_segment(s) for s in raw_segments if s]


def _visualstudio_https_segments(host: str, segments: list[str]) -> list[str] | None:
    """Legacy ``<org>.visualstudio.com`` host: the organization from the host name, then the path without ``_git``
    and ``DefaultCollection``; None when no path is left."""
    org = _PORT_RE.sub("", host).split(".")[0]
    rest = [s for i, s in enumerate(segments) if s != "_git" and not (i == 0 and s.lower() == "defaultcollection")]
    return [org, *rest] if rest else None


def _bitbucket_scm_segments(parts: _RemoteParts, segments: list[str]) -> list[str]:
    """Bitbucket Server HTTP(S) ``.../scm/<project>/<repo>``: the segments after the ``scm`` marker."""
    if parts.scheme is None or parts.scheme.lower() not in ("http", "https"):
        return segments
    scm_index = next((i for i, s in enumerate(segments) if s == "scm" and len(segments) - i - 1 >= 2), -1)
    return segments if scm_index == -1 else segments[scm_index + 1 :]


def _host_segments(parts: _RemoteParts, hostname: str | None, segments: list[str]) -> list[str] | None:
    """Segments after the host rules: Azure DevOps and ``vs-ssh`` hosts, legacy Visual Studio HTTPS hosts, Bitbucket
    ``scm/``; None when the Visual Studio path is empty."""
    if hostname in ("dev.azure.com", "ssh.dev.azure.com", "vs-ssh.visualstudio.com"):
        return [s for i, s in enumerate(segments) if s != "_git" and not (i == 0 and s == "v3")]
    if parts.host is not None and hostname and hostname.endswith(".visualstudio.com"):
        return _visualstudio_https_segments(parts.host, segments)
    return _bitbucket_scm_segments(parts, segments)


def _trim_user_segment(result: list[str]) -> list[str] | None:
    """Bitbucket ``~user`` loses the tilde; a lone ``~`` or no segments gives None."""
    if result and len(result[0]) > 1 and result[0].startswith("~"):
        result = [result[0][1:], *result[1:]]
    return None if result == ["~"] or not result else result


def _parse_git_remote_url(remote_url: str) -> str | None:
    """``parseGitRemoteUrl`` of the CLI: a lower-cased repository identifier or None."""
    without_git_suffix = re.sub(r"\.git\Z", "", re.sub(r"[?#].*\Z", "", remote_url), flags=re.IGNORECASE)
    parts = _split_remote(without_git_suffix)
    if parts is None:
        return None
    hostname = _PORT_RE.sub("", parts.host).lower() if parts.host is not None else None
    result = _host_segments(parts, hostname, _path_segments(parts))
    trimmed = _trim_user_segment(result) if result is not None else None
    if trimmed is None:
        return None
    joined = "/".join(trimmed) if parts.structured else "/".join(trimmed[-2:])
    return joined.lower() if joined else None


def _extract_repository(working_directory: str) -> str:
    """``extractRepository`` of the CLI followed by the drive-letter rule of ``truncateProjectPath``."""
    normalized = working_directory.replace("\\", "/")
    if _COWORK_RE.search(normalized):
        return "Cowork"
    filtered = [p for p in normalized.split("/") if p]
    if len(filtered) >= 2:
        return f"{filtered[-2]}/{filtered[-1]}"
    last = filtered[-1] if filtered else _UNKNOWN
    return _UNKNOWN if _DRIVE_RE.match(last) else last


def _is_raw_remote(remote: str) -> bool:
    """A raw git remote, not an id the CLI already parsed: a ``scheme://`` URL, an scp form (``user@host:path`` or
    ``alias:path``), or anything with an ``@`` (credentials are never kept)."""
    return bool(_SCHEME_ANY_RE.match(remote) or _SCP_ALIAS_RE.match(remote) or "@" in remote)


def normalize_repository(repo_remote: str, repository: str) -> str:
    """The repository string the CLI reports, lower-cased: the git remote first, else the working directory.

    The CLI parses the git remote itself and writes the parsed id (``owner/repo``, or a longer subgroup or Azure
    DevOps path) to Elasticsearch and as ``repo_remote`` to Postgres, so a ``repo_remote`` that is not a URL or an scp
    form passes through with all its segments, only stripped and lower-cased. URL and scp forms are still parsed with
    the CLI rules (``parseGitRemoteUrl``), for older or other writers; a remote that cannot be parsed, like an empty
    one, falls back to the working directory. A working directory that is empty, blank or a root gives ``unknown``,
    the bucket the CLI itself reports.
    """
    remote = (repo_remote or "").strip()
    if remote:
        parsed = _parse_git_remote_url(remote) if _is_raw_remote(remote) else remote.lower()
        if parsed:
            return parsed
    return _extract_repository((repository or "").strip()).lower()


def aggregate_pg_rows(rows: Iterable[Sequence[Any]]) -> list[list[str | int]]:
    """Reader tuples ``(user_email, repo_remote, repository, framework_sessions)`` summed per person and normalized
    repository (also ``canonical_value``, the form the merge compares) into ``[email_lower, repository, framework]``,
    one row per key; rows without an email dropped."""
    totals: dict[tuple[str, str], list[str | int]] = {}
    for email, repo_remote, repository, framework in rows:
        key_email = (email or "").strip().lower()
        key_repo = canonical_value(normalize_repository(repo_remote, repository))
        if not key_email:
            continue
        entry = totals.setdefault((key_email, key_repo), [key_email, key_repo, 0])
        entry[2] += framework
    return list(totals.values())


def merge_framework(
    sess_rows: Sequence[Sequence[Any]], pg_rows: Iterable[Sequence[Any]], email_by_uid: dict[str, str]
) -> list[list[Any]]:
    """ES session rows ``[uid, repo, sessions, mcp_any, mcp_work, framework]`` with the framework count raised to the
    Postgres one for the same person and repository (the repository by ``canonical_value``, the email trimmed and
    lower-cased), never above the sessions; rows that do not change are returned as they are. ``pg_rows`` are the rows
    of ``aggregate_pg_rows``: one per person and repository.

    The Postgres count belongs to the person and repository, not to one row: rows that share the key (two users with
    one email, repositories that differ only in case) split it. Each row keeps its own Elasticsearch count, and the
    rest of the key's total, ``min(sessions, max(Elasticsearch, Postgres))`` summed over the rows, is given to the rows
    by their remaining room (sessions minus framework), in row order."""
    pg_framework = {(email, canonical_value(repo)): framework for email, repo, framework in pg_rows}

    merged: list[list[Any]] = list(sess_rows)
    rows_by_key: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(merged):
        email = email_by_uid.get(row[0])
        key = (email.strip().lower(), canonical_value(str(row[1]))) if email else None
        if key is not None and key in pg_framework:
            rows_by_key.setdefault(key, []).append(index)

    for key, indexes in rows_by_key.items():
        rows = [list(merged[i]) for i in indexes]
        total = min(sum(r[2] for r in rows), max(sum(r[5] for r in rows), pg_framework[key]))
        rest = total - sum(r[5] for r in rows)
        if rest <= 0:
            continue
        for index, row in zip(indexes, rows, strict=True):
            add = min(rest, row[2] - row[5])
            if add > 0:
                row[5] = type(row[5])(row[5] + add)
                rest -= add
                merged[index] = row
    return merged
