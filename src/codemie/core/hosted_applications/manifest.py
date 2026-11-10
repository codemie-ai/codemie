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

"""A bounded, digest-bound allowlist is the only authority for serving artifact bytes."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import PurePosixPath

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.paths import canonical_artifact_path
from codemie.core.hosted_applications.validation import MIME_TYPES

MAX_MANIFEST_BYTES = 6 * 1024 * 1024


def _unavailable():
    return HostedApplicationError("CONTENT_UNAVAILABLE", "Application content is unavailable", 503)


def _prefix(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+){1,8}", value)
        or len(value) > 240
    ):
        raise ValueError("Invalid storage prefix")
    return value


def build_manifest(candidate, *, prefix: str, site_root: str, entrypoint: str, routing_mode: str) -> dict:
    _prefix(prefix)
    if site_root:
        canonical_artifact_path(site_root)
    canonical_artifact_path(entrypoint)
    start = site_root + "/" if site_root else ""
    files = {}
    for item in candidate.files:
        if not item.path.startswith(start):
            continue
        path = item.path[len(start) :]
        canonical_artifact_path(path)
        files[path] = {
            "path": path,
            "key": prefix + "/assets/" + item.path,
            "size": item.size,
            "sha256": item.sha256,
            "mime": item.mime,
            "document": item.document,
        }
    relative_entry = entrypoint[len(start) :] if entrypoint.startswith(start) else None
    if (
        relative_entry not in files
        or not files[relative_entry]["mime"].startswith("text/html")
        or routing_mode not in ("static", "spa_history")
    ):
        raise HostedApplicationError("INVALID_CONFIGURATION", "Choose an HTML entrypoint inside the selected site root")
    return {
        "version": 1,
        "site_root": site_root,
        "entrypoint": relative_entry,
        "routing_mode": routing_mode,
        "files": files,
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def _validate_asset(path, item, prefix, site_root):
    canonical_artifact_path(path)
    if not isinstance(item, dict) or set(item) != {"path", "key", "size", "sha256", "mime", "document"}:
        raise ValueError("Invalid asset")
    expected_key = prefix + "/assets/" + (site_root + "/" if site_root else "") + path
    suffix = PurePosixPath(path).suffix.lower()
    if (
        item["path"] != path
        or item["key"] != expected_key
        or type(item["size"]) is not int
        or not 0 <= item["size"] <= 50 * 1024 * 1024
        or item["mime"] != MIME_TYPES.get(suffix)
        or type(item["document"]) is not bool
        or item["document"] != (suffix in (".html", ".htm", ".svg"))
        or not isinstance(item["sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
    ):
        raise ValueError("Invalid asset metadata")


def parse_manifest(data: bytes, *, expected_digest: bytes, prefix: str) -> dict:
    try:
        _prefix(prefix)
        if len(data) > MAX_MANIFEST_BYTES or not hmac.compare_digest(hashlib.sha256(data).digest(), expected_digest):
            raise ValueError("Manifest integrity")
        value = json.loads(data, object_pairs_hook=_unique_pairs)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "site_root", "entrypoint", "routing_mode", "files"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["routing_mode"] not in ("static", "spa_history")
            or not isinstance(value["site_root"], str)
            or not isinstance(value["files"], dict)
            or not 1 <= len(value["files"]) <= 5000
        ):
            raise ValueError("Manifest schema")
        if value["site_root"]:
            canonical_artifact_path(value["site_root"])
        folded = set()
        total = 0
        for path, item in value["files"].items():
            _validate_asset(path, item, prefix, value["site_root"])
            if path.casefold() in folded:
                raise ValueError("Asset collision")
            folded.add(path.casefold())
            total += item["size"]
        if total > 500 * 1024 * 1024:
            raise ValueError("Manifest byte limit")
        entry = value["files"][value["entrypoint"]]
        if not entry["mime"].startswith("text/html"):
            raise ValueError("Manifest entrypoint")
        return value
    except (ValueError, TypeError, KeyError, HostedApplicationError) as exc:
        raise _unavailable() from exc


def resolve_asset(manifest: dict, path: str, *, html_navigation: bool) -> dict:
    try:
        if path:
            canonical_artifact_path(path[:-1] if path.endswith("/") else path)
        if not path:
            path = manifest["entrypoint"]
        elif path.endswith("/"):
            path += "index.html"
        asset = manifest["files"].get(path)
        if asset is not None:
            return asset
        if (
            manifest["routing_mode"] == "spa_history"
            and html_navigation
            and not path.endswith("/")
            and not PurePosixPath(path).suffix
        ):
            return manifest["files"][manifest["entrypoint"]]
    except HostedApplicationError:
        pass
    raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404)
