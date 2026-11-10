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

import hashlib

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.manifest import build_manifest, parse_manifest, resolve_asset
from codemie.core.hosted_applications.protocol import canonical_json
from codemie.core.hosted_applications.validation import BundleCandidate, CandidateFile


def bundle():
    return BundleCandidate(
        files=[
            CandidateFile("dist/index.html", "dist/index.html", 20, "a" * 64, "text/html; charset=utf-8", True),
            CandidateFile(
                "dist/assets/app.js", "dist/assets/app.js", 30, "b" * 64, "text/javascript; charset=utf-8", False
            ),
            CandidateFile("private/notes.html", "private/notes.html", 10, "c" * 64, "text/html; charset=utf-8", True),
        ],
        entries=["dist/index.html", "private/notes.html"],
        roots=["dist", "private"],
        total_bytes=60,
    )


def manifest():
    return build_manifest(
        bundle(),
        prefix="attempt/" + "a" * 32,
        site_root="dist",
        entrypoint="dist/index.html",
        routing_mode="spa_history",
    )


def test_site_root_has_origin_relative_exact_allowlist():
    value = manifest()
    assert set(value["files"]) == {"index.html", "assets/app.js"}
    data = canonical_json(value)
    parsed = parse_manifest(data, expected_digest=hashlib.sha256(data).digest(), prefix="attempt/" + "a" * 32)
    assert resolve_asset(parsed, "", html_navigation=True)["path"] == "index.html"
    assert resolve_asset(parsed, "settings/history", html_navigation=True)["path"] == "index.html"
    assert resolve_asset(parsed, "assets/app.js", html_navigation=False)["mime"].startswith("text/javascript")


@pytest.mark.parametrize(
    "path", ["missing.js", "missing.css", "private/notes.html", "../index.html", "_platform/report", "missing.json"]
)
def test_missing_assets_and_reports_never_fall_back_to_html(path):
    with pytest.raises(HostedApplicationError) as error:
        resolve_asset(manifest(), path, html_navigation=True)
    assert error.value.status == 404


def test_spa_only_extensionless_document_navigation():
    for path, navigation in (("settings", False), ("api/result", False), ("something.txt", True)):
        with pytest.raises(HostedApplicationError):
            resolve_asset(manifest(), path, html_navigation=navigation)


@pytest.mark.parametrize("change", ["digest", "key", "mime", "size", "unknown"])
def test_manifest_integrity_and_trusted_storage_boundaries(change):
    value = manifest()
    if change == "key":
        value["files"]["index.html"]["key"] = "other-app/secrets"
    elif change == "mime":
        value["files"]["index.html"]["mime"] = "text/javascript; charset=utf-8"
    elif change == "size":
        value["files"]["index.html"]["size"] = True
    elif change == "unknown":
        value["evil"] = True
    data = canonical_json(value)
    expected = b"z" * 32 if change == "digest" else hashlib.sha256(data).digest()
    with pytest.raises(HostedApplicationError):
        parse_manifest(data, expected_digest=expected, prefix="attempt/" + "a" * 32)


def test_duplicate_manifest_json_keys_are_rejected():
    data = b'{"version":1,"version":1}'
    with pytest.raises(HostedApplicationError):
        parse_manifest(data, expected_digest=hashlib.sha256(data).digest(), prefix="attempt/" + "a" * 32)
