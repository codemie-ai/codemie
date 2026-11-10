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

import stat
import struct
import zipfile

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.paths import canonical_artifact_path, decode_content_path
from codemie.core.hosted_applications.validation import ValidationLimits, inspect_bundle


@pytest.mark.parametrize(
    "name, content",
    [
        ("__MACOSX/server.exe", b"MZ executable"),
        ("__MACOSX/.env", b"secret=value"),
        ("__MACOSX/._index.html", b"not AppleDouble"),
        (".DS_Store", b"-----BEGIN " + b"PRIVATE KEY-----"),
    ],
)
def test_packaging_metadata_cannot_conceal_unsupported_or_secret_files(tmp_path, name, content):
    source = archive(tmp_path, [("index.html", "<html>Safe</html>"), (name, content)])
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")


def test_recognized_packaging_metadata_is_bounded_and_explicitly_reported(tmp_path):
    metadata = struct.pack(">II16sH", 0x00051607, 0x00020000, b"\\x00" * 16, 0)
    source = archive(tmp_path, [("index.html", "<html>Safe</html>"), ("__MACOSX/._index.html", metadata)])
    candidate = inspect_bundle(source, "zip", tmp_path / "output")
    assert len(candidate.files) == 1
    assert candidate.warnings[0].code == "PACKAGING_METADATA"


def archive(tmp_path, entries):
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in entries:
            bundle.writestr(name, content)
    return source


@pytest.mark.parametrize(
    "path",
    [
        "../index.html",
        "/index.html",
        "a/../../b.js",
        "a\\b.js",
        "C:/a.js",
        "a//b.js",
        "a/./b.js",
        "a/%2e%2e/b.js",
        "_platform/launch",
        "a\x00.js",
        "a?.js",
        "a#.js",
        "a/CON.txt",
        "a/b. ",
    ],
)
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(HostedApplicationError):
        canonical_artifact_path(path)


@pytest.mark.parametrize(
    "path", [b"/%2fsecret", b"/%5Csecret", b"/%252e%252e/secret", b"/../secret", b"/%ff.html", b"//other/app"]
)
def test_content_path_does_not_repair_or_double_decode(path):
    with pytest.raises(HostedApplicationError):
        decode_content_path(path)


def test_unicode_and_spaces_have_one_roundtrip():
    assert decode_content_path(b"/assets/hello%20world.css") == "assets/hello world.css"
    assert canonical_artifact_path("assets/café.css") == "assets/café.css"


def test_accepts_static_react_bundle_at_origin_root(tmp_path):
    source = archive(
        tmp_path,
        [
            ("dist/index.html", '<!doctype html><script type="module" src="/assets/app.js"></script>'),
            ("dist/assets/app.js", 'document.body.textContent = "Ready"'),
            ("dist/assets/app.css", "body {color:red}"),
        ],
    )
    candidate = inspect_bundle(source, "zip", tmp_path / "output")
    assert candidate.roots == ["dist"]
    assert candidate.entries == ["dist/index.html"]
    assert {file.path for file in candidate.files} == {"dist/index.html", "dist/assets/app.js", "dist/assets/app.css"}
    assert candidate.total_bytes == sum(file.size for file in candidate.files)
    assert all(len(file.sha256) == 64 for file in candidate.files)


def test_single_html_is_utf8_and_does_not_execute(tmp_path):
    source = tmp_path / "source"
    source.write_text('<html><script>throw new Error("must not execute")</script></html>')
    candidate = inspect_bundle(source, "html", tmp_path / "output")
    assert candidate.entries == ["index.html"]
    assert candidate.roots == [""]


def test_ambiguous_entries_are_preserved_for_explicit_selection(tmp_path):
    source = archive(tmp_path, [("one/index.html", "<html>One</html>"), ("two/index.html", "<html>Two</html>")])
    candidate = inspect_bundle(source, "zip", tmp_path / "output")
    assert candidate.roots == ["one", "two"]
    assert len(candidate.entries) == 2


@pytest.mark.parametrize(
    "name",
    [
        "../escape.html",
        ".env",
        "server.js",
        "package.json",
        "node_modules/x.js",
        "app.js.map",
        "nested.zip",
        "component.tsx",
        "worker.wasm",
    ],
)
def test_rejects_unsafe_or_server_artifacts(tmp_path, name):
    source = archive(tmp_path, [("index.html", "<html>Safe</html>"), (name, "untrusted")])
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")
    assert not (tmp_path / "escape.html").exists()


def test_rejects_case_collision(tmp_path):
    source = archive(tmp_path, [("index.html", "<html>Safe</html>"), ("A.js", "a"), ("a.js", "b")])
    with pytest.raises(HostedApplicationError, match="collid"):
        inspect_bundle(source, "zip", tmp_path / "output")


def test_rejects_symlink_before_any_extraction(tmp_path):
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        entry = zipfile.ZipInfo("index.html")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(entry, "/etc/passwd")
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")
    assert not (tmp_path / "output" / "index.html").exists()


def test_enforces_real_expansion_bound(tmp_path):
    source = archive(tmp_path, [("index.html", "<html>" + "x" * 1024 + "</html>")])
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output", ValidationLimits(max_expanded_bytes=100))


def test_corrupt_zip_and_bad_utf8_fail_closed(tmp_path):
    source = tmp_path / "bad"
    source.write_bytes(b"PK\x03\x04invalid")
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")
    source.write_bytes(b"<html>\xff</html>")
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "html", tmp_path / "output2")


def test_dtd_svg_and_secret_material_are_rejected(tmp_path):
    source = archive(
        tmp_path,
        [
            ("index.html", "<html>Safe</html>"),
            ("image.svg", '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>'),
        ],
    )
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")
    source = archive(tmp_path, [("index.html", "<html>-----BEGIN " + "PRIVATE KEY-----</html>")])
    with pytest.raises(HostedApplicationError) as exc:
        inspect_bundle(source, "zip", tmp_path / "output2")
    assert exc.value.code == "SECRET_DETECTED"
    assert "BEGIN" not in exc.value.message


def test_external_dependencies_reported_without_network_io(tmp_path):
    source = tmp_path / "source"
    source.write_text('<html><script src="https://external.invalid/app.js"></script></html>')
    candidate = inspect_bundle(source, "html", tmp_path / "output")
    assert any(warning.code == "EXTERNAL_DEPENDENCY" for warning in candidate.warnings)


def test_finder_binary_metadata_is_recognized_without_serving_it(tmp_path):
    source = archive(
        tmp_path, [("index.html", "<html>Safe</html>"), (".DS_Store", bytes.fromhex("0000000142756431") + b"metadata")]
    )
    candidate = inspect_bundle(source, "zip", tmp_path / "output")
    assert [item.path for item in candidate.files] == ["index.html"]
    assert any(item.code == "PACKAGING_METADATA" for item in candidate.warnings)


@pytest.mark.parametrize("signature", [b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"])
def test_executable_renamed_as_image_cannot_become_static_content(tmp_path, signature):
    source = archive(tmp_path, [("index.html", "<html>Safe</html>"), ("payload.png", signature + b"x" * 128)])
    with pytest.raises(HostedApplicationError):
        inspect_bundle(source, "zip", tmp_path / "output")


def test_obvious_missing_local_dependencies_have_safe_path_warnings(tmp_path):
    source = archive(
        tmp_path,
        [
            (
                'index.html',
                '<html><script src="missing.js"></script><link rel="stylesheet" href="assets/site.css"></html>',
            ),
            ('assets/site.css', 'body {background:url(../missing.png)}'),
        ],
    )
    result = inspect_bundle(source, 'zip', tmp_path / 'output')
    warnings = [warning for warning in result.warnings if warning.code == 'MISSING_LOCAL_RESOURCE']
    assert {warning.path for warning in warnings} == {'index.html', 'assets/site.css'}
    assert all('missing' in warning.message for warning in warnings)


@pytest.mark.parametrize("value", [5, None, b"index.html", ["index.html"]])
def test_non_text_paths_are_unsafe_paths_not_crashes(value):
    from codemie.core.hosted_applications.paths import canonical_artifact_path

    with pytest.raises(HostedApplicationError) as error:
        canonical_artifact_path(value)
    assert error.value.code == "INVALID_ARCHIVE_PATH"
