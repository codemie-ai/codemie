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
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from codemie.core.hosted_applications import content_policy
from codemie.core.hosted_applications.binary import dimensions
from codemie.core.hosted_applications.preview import MAX_PREVIEW_BYTES
from codemie.hosted_apps_gateway import content as gateway_content
from codemie.hosted_apps_gateway.navigation import WRAPPER_SCRIPT
from codemie.hosted_apps_validator import contract, renderer

HTML = "text/html; charset=utf-8"


def asset(path, mime):
    return {"path": path, "key": "k/" + path, "size": 1, "sha256": "a" * 64, "mime": mime, "document": False}


def manifest(mode="static"):
    return {
        "version": 1,
        "site_root": "",
        "entrypoint": "index.html",
        "routing_mode": mode,
        "preview": None,
        "files": {
            "index.html": asset("index.html", HTML),
            "assets/app.js": asset("assets/app.js", "text/javascript; charset=utf-8"),
            "docs/index.html": asset("docs/index.html", HTML),
        },
    }


@pytest.fixture
def files(tmp_path):
    result = {}
    for name, body in (("index.html", "<h1>hi</h1>"), ("assets/app.js", "x=1"), ("docs/index.html", "<p>d</p>")):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        result[name] = target
    return result


def test_route_serves_origin_files(files):
    status, headers, path = renderer.route(manifest(), files, renderer.ORIGIN + "/assets/app.js?v=1#frag")
    assert status == 200
    assert path == files["assets/app.js"]
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert headers["Content-Security-Policy"] == content_policy.CONTENT_CSP


@pytest.mark.parametrize("url", [renderer.ORIGIN, renderer.ORIGIN + "/"])
def test_route_root_serves_entrypoint(files, url):
    assert renderer.route(manifest(), files, url)[::2] == (200, files["index.html"])


def test_route_directory_serves_index(files):
    assert renderer.route(manifest(), files, renderer.ORIGIN + "/docs/")[2] == files["docs/index.html"]


def test_route_spa_navigation_returns_entrypoint(files):
    status, _, path = renderer.route(manifest("spa_history"), files, renderer.ORIGIN + "/a/b", navigation=True)
    assert (status, path) == (200, files["index.html"])


def test_route_spa_subresource_is_not_found(files):
    assert renderer.route(manifest("spa_history"), files, renderer.ORIGIN + "/api/data")[0] == 404


def test_route_missing_path_is_not_found(files):
    status, headers, path = renderer.route(manifest(), files, renderer.ORIGIN + "/nope.js")
    assert (status, path) == (404, None)
    assert headers["Content-Security-Policy"] == content_policy.CONTENT_CSP


def test_route_missing_local_file_is_not_found(files):
    del files["assets/app.js"]
    assert renderer.route(manifest(), files, renderer.ORIGIN + "/assets/app.js")[::2] == (404, None)


@pytest.mark.parametrize(
    "url",
    [
        "http://app.preview.invalid/",
        "https://example.com/",
        "https://app.preview.invalid:8443/",
        "https://app.preview.invalid:443/",
        "https://user@app.preview.invalid/",
        "https://app.preview.invalid.evil.com/",
        "data:text/html,hi",
        "file:///etc/passwd",
        "wss://app.preview.invalid/",
        "",
    ],
)
def test_route_refuses_other_origins(files, url):
    assert renderer.route(manifest(), files, url) == (renderer.FAIL, {}, None)


@pytest.mark.parametrize("path", ["/%2e%2e/etc", "/a%2fb", "/_platform/view", "/%00"])
def test_route_unsafe_paths_are_not_found(files, path):
    assert renderer.route(manifest(), files, renderer.ORIGIN + path)[0] == 404


def test_gateway_shares_csp():
    assert gateway_content.CONTENT_CSP is content_policy.CONTENT_CSP


def test_routed_body_has_no_wrapper(files):
    _, _, path = renderer.route(manifest(), files, renderer.ORIGIN + "/")
    assert WRAPPER_SCRIPT not in path.read_text()


# --- Capture process: pure helpers (no Docker) ---


def jpeg(width=1280, height=800, pad=0):
    """The markers a real JPEG starts with: SOI, an APP0 segment and a baseline SOF0 frame header."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
    sof0 += b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xda" + b"\x00" * pad + b"\xff\xd9"


def test_dimensions_reads_the_jpeg_frame_header():
    assert dimensions(jpeg(1280, 800), ".jpg") == (1280, 800)
    assert dimensions(jpeg(2049, 7), ".jpg") == (2049, 7)


@pytest.mark.parametrize("data", [b"", b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"\xff\xd8\xff\xe0\x00", jpeg()[:30]])
def test_dimensions_gives_zero_for_what_is_not_a_jpeg(data):
    assert dimensions(data, ".jpg") == (0, 0)


def test_describe_reports_size_digest_and_dimensions():
    data = jpeg(1280, 800, pad=10)
    assert renderer.describe(data) == {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": 1280,
        "height": 800,
    }


@pytest.mark.parametrize("data", [jpeg(2049, 800), jpeg(1280, 2049), jpeg(0, 800), b"not a jpeg"])
def test_describe_refuses_unusable_pictures(data):
    assert renderer.describe(data) is None


def test_describe_refuses_more_than_one_mebibyte():
    assert renderer.describe(jpeg(pad=MAX_PREVIEW_BYTES)) is None


def test_encode_retries_lower_quality_until_it_fits():
    tried = []

    def shoot(quality):
        tried.append(quality)
        return b"x" * (MAX_PREVIEW_BYTES + (1 if quality > 60 else 0))

    assert renderer.encode(shoot) == b"x" * MAX_PREVIEW_BYTES
    assert tried == [80, 60]


def test_encode_gives_nothing_when_quality_40_is_still_too_big():
    tried = []

    def shoot(quality):
        tried.append(quality)
        return b"x" * (MAX_PREVIEW_BYTES + 1)

    assert renderer.encode(shoot) is None
    assert tried == [80, 60, 40]


def test_flags_cap_the_javascript_heap():
    heap = [flag for flag in renderer.FLAGS if flag.startswith("--js-flags=--max-old-space-size=")]
    assert heap == [f"--js-flags=--max-old-space-size={renderer.RENDERER_HEAP_MIB}"]


class RecordingBrowser:
    def __init__(self):
        self.sent = []

    def send(self, method, params=None, session=None):
        self.sent.append((method, params, session))
        return len(self.sent)


def page_for(files):
    job = renderer.Job(manifest(), next(iter(files.values())).parent, files)
    page = renderer._Page(job)
    page.browser = RecordingBrowser()
    return page


def test_page_dismisses_javascript_dialogs(files):
    page = page_for(files)
    event = {"method": "Page.javascriptDialogOpening", "params": {"type": "alert"}, "sessionId": "tab"}
    page.on_event(event)
    assert page.browser.sent == [("Page.handleJavaScriptDialog", {"accept": False}, "tab")]


def test_page_serves_a_file_within_the_served_limit(files):
    page = page_for(files)
    page.on_event({"method": "Fetch.requestPaused", "params": request("/assets/app.js"), "sessionId": "tab"})
    ((method, params, session),) = page.browser.sent
    assert (method, session, params["responseCode"]) == ("Fetch.fulfillRequest", "tab", 200)


def test_page_refuses_a_file_above_the_served_limit(files, monkeypatch):
    monkeypatch.setattr(renderer, "MAX_SERVED_FILE_BYTES", 2, raising=False)
    page = page_for(files)
    page.on_event({"method": "Fetch.requestPaused", "params": request("/assets/app.js"), "sessionId": "tab"})
    assert page.browser.sent == [("Fetch.failRequest", {"requestId": "r1", "errorReason": "BlockedByClient"}, "tab")]


def request(path):
    return {"requestId": "r1", "request": {"url": renderer.ORIGIN + path}, "resourceType": "Script"}


def test_capture_constants():
    assert (contract.CAPTURE_SOFT_SECONDS, contract.CAPTURE_HARD_SECONDS) == (15, 20)
    assert MAX_PREVIEW_BYTES == 1024 * 1024
    assert contract.PREVIEW_VIEWPORT == (1280, 800)


def write_job(tmp_path, files, root=None):
    job = tmp_path / "job.json"
    body = {"manifest": manifest(), "root": str(root or tmp_path), "files": {k: str(v) for k, v in files.items()}}
    job.write_text(json.dumps(body))
    return job


def test_load_job_reads_manifest_root_and_files(tmp_path, files):
    job = renderer.load_job(write_job(tmp_path, files))
    assert job.manifest == manifest()
    assert job.root == tmp_path.resolve()
    assert job.files == {name: path.resolve() for name, path in files.items()}


def test_load_job_refuses_a_file_outside_the_root(tmp_path, files):
    root = tmp_path / "served"
    root.mkdir()
    with pytest.raises(ValueError):
        renderer.load_job(write_job(tmp_path, files, root=root))


def test_load_job_refuses_a_link_that_leaves_the_root(tmp_path, files):
    root = tmp_path / "served"
    root.mkdir()
    (root / "index.html").symlink_to(files["index.html"])
    with pytest.raises(ValueError):
        renderer.load_job(write_job(tmp_path, {"index.html": root / "index.html"}, root=root))


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        '{"root": "/", "files": {}}',
        '{"manifest": [], "root": "/", "files": {}}',
        '{"manifest": {}, "root": "relative", "files": {}}',
        '{"manifest": {}, "root": "/", "files": {"a": 1}}',
        "not json",
    ],
)
def test_load_job_refuses_malformed_jobs(tmp_path, body):
    job = tmp_path / "job.json"
    job.write_text(body)
    with pytest.raises(ValueError):
        renderer.load_job(job)


def _run_module(*args):
    env = dict(os.environ, PYTHONPATH=str(Path(renderer.__file__).parents[2]))
    return subprocess.run(
        [sys.executable, "-B", "-m", "codemie.hosted_apps_validator.renderer", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def test_usage_error_prints_null(tmp_path):
    done = _run_module("paint", str(tmp_path))
    assert (done.returncode, done.stdout) == (2, "null\n")


def test_capture_failure_prints_null_and_leaves_no_profile(tmp_path, files):
    # Off Linux (or without Chromium) the sandbox fails closed: no picture, nothing left in scratch.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "keep.txt").write_text("before")
    done = _run_module("capture", str(write_job(tmp_path, files)), str(scratch))
    assert (done.returncode, done.stdout) == (0, "null\n")
    assert sorted(p.name for p in scratch.iterdir()) == ["keep.txt"]
    assert "Traceback" not in done.stderr


# --- Capture process: real Chromium in the validator image (Docker) ---

KEY_VALUE = "renderer-capture-test-key-5c1d"

CASES = {
    "static": {
        "index.html": (
            '<!doctype html><html><head><link rel="stylesheet" href="assets/site.css"></head>'
            '<body><h1>Preview</h1><p id="note">static</p><script src="assets/app.js"></script></body></html>'
        ),
        "assets/site.css": "body{background:#fafafa;color:#123;font:32px sans-serif}h1{color:#c33}",
        "assets/app.js": "document.getElementById('note').textContent = 'rendered by script';",
    },
    "single_color": {"index.html": '<!doctype html><html><body style="margin:0;background:#3366cc"></body></html>'},
    "never_quiet": {
        "index.html": (
            "<!doctype html><html><body><h1>Busy</h1><script>"
            "setInterval(() => fetch('/tick?' + Date.now()).catch(() => {}), 100);"
            "</script></body></html>"
        ),
    },
    # The main thread never yields, so the screenshot never arrives: the renderer stops at the hard limit.
    "hung": {"index.html": "<!doctype html><html><body><h1>Hung</h1><script>while (true) {}</script></body></html>"},
    # alert() during load: the renderer dismisses the dialog, so the page loads and gets its picture.
    "dialog": {
        "index.html": "<!doctype html><html><body><h1>Dialog</h1><script>alert('hello');</script></body></html>"
    },
    # The heading appears only when the foreign request fails: success or a hang leaves a blank page.
    "other_origin": {
        "index.html": (
            '<!doctype html><html><body><img src="https://example.com/logo.png"><script>'
            "fetch('https://example.com/data.json').then("
            "() => {}, () => { document.body.insertAdjacentHTML('beforeend', '<h1>Request failed</h1>'); });"
            "</script></body></html>"
        ),
    },
}

DRIVER = """
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
from codemie.hosted_apps_validator.isolation import hide_process_secrets

hide_process_secrets()
work = Path("/work")
cases = json.loads(Path("/probe/cases.json").read_text())
HTML = "text/html; charset=utf-8"
MIMES = {".html": HTML, ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def renderer(*args):
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-B", "-m", "codemie.hosted_apps_validator.renderer", *args],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return {
        "returncode": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr[-2000:],
        "elapsed": time.monotonic() - started,
    }


result = {}
for name, pages in cases.items():
    case = work / name
    root, scratch = case / "assets", case / "scratch"
    scratch.mkdir(parents=True)
    files, assets = {}, {}
    for path, body in pages.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        files[path] = str(target)
        assets[path] = {
            "path": path, "key": "p/assets/" + path, "size": len(body.encode()), "sha256": "a" * 64,
            "mime": MIMES[Path(path).suffix], "document": path.endswith(".html"),
        }
    manifest = {"version": 1, "site_root": "", "entrypoint": "index.html", "routing_mode": "static", "files": assets}
    job = case / "job.json"
    job.write_text(json.dumps({"manifest": manifest, "root": str(root), "files": files}))
    run = renderer("capture", str(job), str(scratch))
    run["scratch"] = sorted(p.name for p in scratch.iterdir())
    picture = scratch / "preview.jpg"
    if picture.exists():
        data = picture.read_bytes()
        run["picture"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "soi": data[:3].hex()}
    result[name] = run

probe = work / "probe"
probe.mkdir()
result["probe"] = renderer("probe", str(probe))
result["probe"]["scratch"] = sorted(p.name for p in probe.iterdir())
print(json.dumps(result))
"""


@pytest.fixture(scope="module")
def chromium_runs():
    image = os.environ.get("HOSTED_TEST_SANDBOX_IMAGE")
    if not image:
        pytest.skip("Set HOSTED_TEST_SANDBOX_IMAGE for real Docker/Linux renderer tests")
    root = Path(__file__).parents[4]
    with tempfile.TemporaryDirectory(prefix=".hosted-renderer-test-", dir=root) as shared:
        probe = Path(shared)
        (probe / "driver.py").write_text(DRIVER)
        (probe / "cases.json").write_text(json.dumps(CASES))
        probe.chmod(0o755)
        for script in probe.iterdir():
            script.chmod(0o644)
        command = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--cpus=1",
            "--memory=3g",
            "--user=65532:65532",
            "--mount",
            f"type=bind,source={root / 'src'},target=/app/src,readonly",
            "--mount",
            f"type=bind,source={probe},target=/probe,readonly",
            "--tmpfs",
            "/work:rw,nosuid,nodev,noexec,size=600m,mode=1777",
            "--env",
            "PYTHONPATH=/app/src",
            "--env",
            f"VALIDATOR_SERVICE_KEY={KEY_VALUE}",
            image,
            "python",
            "-B",
            "/probe/driver.py",
        ]
        done = subprocess.run(command, capture_output=True, text=True, timeout=400, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def _captured(run):
    assert run["returncode"] == 0, run["stderr"]
    return json.loads(run["stdout"])


def test_capture_static_page(chromium_runs):
    run = chromium_runs["static"]
    preview = _captured(run)
    assert preview is not None, run["stderr"]
    assert (preview["width"], preview["height"]) == contract.PREVIEW_VIEWPORT
    assert 0 < preview["size"] <= MAX_PREVIEW_BYTES
    assert run["picture"] == {"size": preview["size"], "sha256": preview["sha256"], "soi": "ffd8ff"}
    assert run["scratch"] == ["preview.jpg"]
    # A quiet page does not wait for the soft deadline.
    assert run["elapsed"] < contract.CAPTURE_SOFT_SECONDS


def test_capture_single_color_page_gives_null(chromium_runs):
    run = chromium_runs["single_color"]
    assert _captured(run) is None
    assert run["scratch"] == []
    assert run["elapsed"] < contract.CAPTURE_SOFT_SECONDS


def test_capture_page_that_never_gets_quiet_is_taken_at_the_soft_deadline(chromium_runs):
    run = chromium_runs["never_quiet"]
    preview = _captured(run)
    assert preview is not None, run["stderr"]
    assert contract.CAPTURE_SOFT_SECONDS <= run["elapsed"] < contract.CAPTURE_HARD_SECONDS
    assert run["scratch"] == ["preview.jpg"]


def test_capture_page_that_fetches_another_origin_renders_and_its_request_fails(chromium_runs):
    run = chromium_runs["other_origin"]
    preview = _captured(run)
    assert preview is not None, run["stderr"]
    assert run["elapsed"] < contract.CAPTURE_SOFT_SECONDS


def test_capture_page_with_a_dialog_on_load_gets_its_picture(chromium_runs):
    run = chromium_runs["dialog"]
    preview = _captured(run)
    assert preview is not None, run["stderr"]
    assert run["elapsed"] < contract.CAPTURE_SOFT_SECONDS


def test_capture_hung_page_gives_null_at_the_hard_limit(chromium_runs):
    run = chromium_runs["hung"]
    assert _captured(run) is None
    assert contract.CAPTURE_HARD_SECONDS <= run["elapsed"] < contract.CAPTURE_HARD_SECONDS + 5
    assert run["scratch"] == []
    # Class names only: no page text and no exception text.
    assert run["stderr"] == "renderer: CdpError\n"


def test_probe_reports_every_check_and_the_chromium_version(chromium_runs):
    run = chromium_runs["probe"]
    assert run["returncode"] == 0, run
    report = json.loads(run["stdout"])
    version = report.pop("chromium")
    assert re.fullmatch(r"\d+(\.\d+){3}", version), version
    assert report.pop("landlock_abi") >= 3
    assert set(report) >= {
        "sandbox",
        "ip_socket_denied",
        "unix_socket_allowed",
        "outside_read_denied",
        "outside_write_denied",
        "environment_clean",
        "shell_exec_denied",
        "parent_environ_denied",
    }
    assert all(value is True for value in report.values()), report
    assert run["scratch"] == []
