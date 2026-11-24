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

"""Validator startup: loopback by default, TLS when mounted, information-free liveness, private workspace."""

import hashlib
import os
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_validator import preview_capture, server


@pytest.fixture(autouse=True)
def unprivileged(monkeypatch):
    """CI runs the tests as root; the validator itself refuses root, which one test checks on purpose."""
    monkeypatch.setattr(server, "running_as_root", lambda: False)


def inspector():
    async def health(request):
        if request.headers.get("x-validator-key") != "k" * 32:
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validator authentication failed", 503)
        return JSONResponse({"version": "1.5.4"})

    return SimpleNamespace(
        health=health,
        reservation=AsyncMock(),
        validate=AsyncMock(),
        file=AsyncMock(),
        delete=AsyncMock(),
        preview=AsyncMock(),
    )


def test_liveness_is_unauthenticated_and_information_free():
    client = TestClient(server.create_app(inspector()))
    response = client.get("/livez")
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"


def test_detailed_health_still_requires_the_service_key():
    client = TestClient(server.create_app(inspector()))
    assert client.get("/health").status_code == 503
    assert client.get("/health", headers={"x-validator-key": "k" * 32}).json() == {"version": "1.5.4"}


def test_sidecar_binds_to_loopback_by_default():
    options = server.server_options({})
    assert (options["host"], options["port"]) == ("127.0.0.1", 8091)
    assert "ssl_certfile" not in options


def test_tls_is_enabled_when_a_certificate_and_key_are_mounted():
    options = server.server_options(
        {
            "VALIDATOR_BIND_HOST": "0.0.0.0",
            "VALIDATOR_PORT": "8443",
            "VALIDATOR_TLS_CERT_FILE": "/tls/tls.crt",
            "VALIDATOR_TLS_KEY_FILE": "/tls/tls.key",
        }
    )
    assert options["host"] == "0.0.0.0" and options["port"] == 8443
    assert (options["ssl_certfile"], options["ssl_keyfile"]) == ("/tls/tls.crt", "/tls/tls.key")


@pytest.mark.parametrize(
    "environ",
    [
        {"VALIDATOR_TLS_CERT_FILE": "/tls/tls.crt"},
        {"VALIDATOR_TLS_KEY_FILE": "/tls/tls.key"},
        {"VALIDATOR_PORT": "0"},
        {"VALIDATOR_PORT": "http"},
    ],
)
def test_incomplete_listener_configuration_refuses_to_start(environ):
    with pytest.raises(ValueError):
        server.server_options(environ)


@pytest.mark.parametrize(
    ("environ", "url"),
    [
        ({}, "http://127.0.0.1:8091/livez"),
        ({"VALIDATOR_BIND_HOST": "0.0.0.0", "VALIDATOR_PORT": "9000"}, "http://127.0.0.1:9000/livez"),
        ({"VALIDATOR_BIND_HOST": "::"}, "http://[::1]:8091/livez"),
    ],
)
def test_container_probe_checks_the_local_listener(environ, url):
    answered = MagicMock(status=204)
    answered.__enter__.return_value = answered
    with patch.object(server.urllib.request, "urlopen", return_value=answered) as urlopen:
        assert server.probe(environ) == 0
    assert urlopen.call_args.args[0] == url


def certificate(directory, name, *, dns=(), common_name=None):
    """A self-signed leaf certificate and key in ``directory``; returns (certificate path, key path)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    attributes = [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CodeMie tests")]
    if common_name:
        attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, common_name))
    subject = x509.Name(attributes)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if dns:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns]), critical=False)
    cert_path, key_path = directory / f"{name}.crt", directory / f"{name}.key"
    cert_path.write_bytes(builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return str(cert_path), str(key_path)


@contextmanager
def tls_listener(cert_path, key_path):
    """A loopback HTTPS listener answering /livez with 204; yields its port."""

    class Live(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's handler name
            self.send_response(204 if self.path == "/livez" else 404)
            self.end_headers()

        def log_message(self, *args):
            """No request log in test output."""

    httpd = HTTPServer(("127.0.0.1", 0), Live)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def tls_environ(port, cert_path, key_path):
    return {"VALIDATOR_PORT": str(port), "VALIDATOR_TLS_CERT_FILE": cert_path, "VALIDATOR_TLS_KEY_FILE": key_path}


@pytest.mark.parametrize(
    "names",
    [
        {"dns": ("validator.hosted.svc",)},
        {"dns": ("*.hosted.svc", "validator.hosted.svc")},
        {"common_name": "validator.hosted.svc"},
    ],
)
def test_tls_probe_verifies_the_loopback_listener_against_the_certificate_name(tmp_path, names):
    cert_path, key_path = certificate(tmp_path, "validator", **names)
    assert server.certificate_host_name(cert_path) == "validator.hosted.svc"
    with tls_listener(cert_path, key_path) as port:
        assert server.probe(tls_environ(port, cert_path, key_path)) == 0


@pytest.mark.parametrize(
    "names",
    [
        {},
        {"dns": ("*.hosted.svc",)},
        # Alternative names present: the common name is never used in their place.
        {"dns": ("*.hosted.svc",), "common_name": "validator.hosted.svc"},
    ],
)
def test_tls_probe_fails_closed_when_the_certificate_names_no_usable_host(tmp_path, names):
    cert_path, key_path = certificate(tmp_path, "validator", **names)
    assert server.certificate_host_name(cert_path) is None
    with tls_listener(cert_path, key_path) as port, patch.object(server.socket, "create_connection") as dial:
        assert server.probe(tls_environ(port, cert_path, key_path)) == 1
    dial.assert_not_called()


def test_tls_probe_fails_when_the_listener_presents_another_certificate(tmp_path):
    mounted, mounted_key = certificate(tmp_path, "mounted", dns=("validator.hosted.svc",))
    served, served_key = certificate(tmp_path, "served", dns=("validator.hosted.svc",))
    with tls_listener(served, served_key) as port:
        assert server.probe(tls_environ(port, mounted, mounted_key)) == 1


def test_tls_probe_fails_closed_when_the_certificate_is_unreadable():
    environ = {"VALIDATOR_TLS_CERT_FILE": "/missing/tls.crt", "VALIDATOR_TLS_KEY_FILE": "/missing/tls.key"}
    assert server.probe(environ) == 1


def test_container_probe_fails_when_nothing_answers():
    with patch.object(server.urllib.request, "urlopen", side_effect=OSError("refused")):
        assert server.probe({}) == 1


def test_workspace_is_a_fresh_private_child_of_the_mounted_volume(tmp_path):
    leftover = tmp_path / "jobs" / ("a" * 64)
    leftover.mkdir(parents=True)
    (leftover / "source").write_bytes(b"from a previous container run")
    (tmp_path / "jobs").chmod(0o777)

    jobs = server.private_workspace(tmp_path)

    assert jobs == tmp_path / "jobs" and list(jobs.iterdir()) == []
    assert jobs.stat().st_mode & 0o777 == 0o700


def test_workspace_refuses_a_root_process(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "running_as_root", lambda: True)
    with pytest.raises(ValueError, match="unprivileged"):
        server.private_workspace(tmp_path)
    assert not (tmp_path / "jobs").exists()


def test_workspace_refuses_a_symbolic_link(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "jobs").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        server.private_workspace(tmp_path)
    assert (tmp_path / "elsewhere").exists()


def parsed(tmp_path, content="<html>Safe</html>"):
    from codemie.core.hosted_applications.validation import inspect_bundle

    source = tmp_path / "source.html"
    source.write_text(content)
    return inspect_bundle(source, "html", tmp_path / "assets").to_dict(), tmp_path / "assets"


def test_reported_files_accepts_the_parser_output_it_can_verify(tmp_path):
    result, assets = parsed(tmp_path)
    checked, files = server.reported_files(result, assets)
    assert files == [assets.resolve() / "index.html"]
    assert checked["files"] == result["files"] and checked["entries"] == ["index.html"]


@pytest.mark.parametrize("path", ["../outside.html", "/etc/passwd", "a/../index.html", "missing.html"])
def test_reported_files_never_trusts_a_path_outside_the_parser_output(tmp_path, path):
    result, assets = parsed(tmp_path)
    (tmp_path / "outside.html").write_text("<html>secret</html>")
    result["files"][0].update(path=path, key=path)
    result["entries"] = [path]
    with pytest.raises(HostedApplicationError) as error:
        server.reported_files(result, assets)
    assert error.value.code == "VALIDATION_UNAVAILABLE"


def test_reported_files_rejects_links_and_size_mismatches(tmp_path):
    result, assets = parsed(tmp_path)
    (tmp_path / "secret.html").write_text("<html>other</html>")
    (assets / "index.html").unlink()
    (assets / "index.html").symlink_to(tmp_path / "secret.html")
    with pytest.raises(HostedApplicationError):
        server.reported_files(result, assets)

    (tmp_path / "second").mkdir()
    result, assets = parsed(tmp_path / "second")
    result["files"][0]["size"] += 1
    result["total_bytes"] += 1
    with pytest.raises(HostedApplicationError):
        server.reported_files(result, assets)


def test_validate_scans_nothing_the_parser_reports_outside_its_workspace(tmp_path):
    scanner = SimpleNamespace(health=AsyncMock(return_value={"version": "1"}), scan=AsyncMock())
    inspector = server.Inspector(tmp_path, scanner, "k" * 32)
    escaped = {"files": [{"path": "../../outside", "key": "../../outside"}], "entries": [], "roots": []}
    content = b"<html>Safe</html>"
    with patch.object(server.Inspector, "parser", AsyncMock(return_value=escaped)):
        response = TestClient(server.create_app(inspector)).post(
            "/validate",
            content=content,
            headers={
                "x-validator-key": "k" * 32,
                "x-artifact-kind": "html",
                "content-type": "application/octet-stream",
                "x-artifact-size": str(len(content)),
                "x-artifact-sha256": __import__("hashlib").sha256(content).hexdigest(),
            },
        )
    assert response.status_code == 503
    assert response.json()["code"] == "VALIDATION_UNAVAILABLE"
    assert scanner.scan.await_count == 1  # the uploaded source only
    assert list(inspector.root.iterdir()) == []


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (0, '{"network_denied": true, "outside_read_denied": false}'),
        (1, '{"code": "VALIDATION_UNAVAILABLE"}'),
        (0, "not json"),
        (0, "{}"),
    ],
)
def test_startup_refuses_a_node_that_cannot_sandbox_the_parser(tmp_path, returncode, stdout):
    completed = SimpleNamespace(returncode=returncode, stdout=stdout.encode(), stderr=b"")
    with patch.object(server.subprocess, "run", return_value=completed), pytest.raises(RuntimeError):
        server.require_isolation(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_startup_accepts_an_enforced_sandbox(tmp_path):
    checks = '{"network_denied": true, "outside_read_denied": true, "exec_denied": true}'
    completed = SimpleNamespace(returncode=0, stdout=checks.encode())
    with patch.object(server.subprocess, "run", return_value=completed) as run:
        server.require_isolation(tmp_path)
    assert run.call_args.args[0][-1] == "probe"
    assert list(tmp_path.iterdir()) == []


def scanned_inspector(tmp_path, capacity=2):
    scanner = SimpleNamespace(health=AsyncMock(return_value={"version": "1"}), scan=AsyncMock())
    return server.Inspector(tmp_path, scanner, "k" * 32, capacity=capacity)


async def finished(inspector, content=b"<html>Result</html>"):
    """A validation whose result waits for the client, as POST /validate leaves it."""
    import time

    key, path = await inspector.start(await inspector.reserve())
    inspector.validating.discard(key)
    (path / "file").write_bytes(content)
    result = {"files": [{"size": len(content)}]}
    inspector.workspaces[key] = server.Workspace(path, time.monotonic() + 60, result, [path / "file"])
    return key, path


def file_request(key, index=0):
    from starlette.datastructures import Headers

    return SimpleNamespace(headers=Headers({"x-validator-key": "k" * 32}), path_params={"key": key, "index": index})


@pytest.mark.asyncio
async def test_a_result_being_read_occupies_one_slot_not_two(tmp_path):
    inspector = scanned_inspector(tmp_path)
    key, _ = await finished(inspector)
    response = await inspector.file(file_request(key))
    assert await anext(response.body_iterator) == b"<html>Result</html>"
    assert await inspector.reserve(), "One finished validation and one new one fit two slots"
    with pytest.raises(HostedApplicationError) as error:
        await inspector.reserve()
    assert (error.value.code, error.value.status) == ("VALIDATOR_BUSY", 503)


@pytest.mark.asyncio
async def test_expired_reservations_and_results_free_their_slots(tmp_path):
    inspector = scanned_inspector(tmp_path)
    key, path = await finished(inspector)
    reservation = await inspector.reserve()
    with pytest.raises(HostedApplicationError):
        await inspector.reserve()
    inspector.workspaces[key].expires = 0
    inspector.reservations[reservation] = 0
    assert await inspector.reserve()
    assert not path.exists() and key not in inspector.workspaces


@pytest.mark.asyncio
async def test_deleting_a_result_is_never_refused_by_a_reader(tmp_path):
    inspector = scanned_inspector(tmp_path)
    key, path = await finished(inspector, b"x" * (3 * 1024 * 1024))
    response = await inspector.file(file_request(key))
    first = await anext(response.body_iterator)
    assert (await inspector.delete(file_request(key))).status_code == 204
    assert not path.exists() and inspector.occupied() == 0
    rest = b"".join([chunk async for chunk in response.body_iterator])
    assert len(first) + len(rest) == 3 * 1024 * 1024, "An open file stays readable after its workspace is gone"


@pytest.mark.asyncio
async def test_a_reservation_is_needed_once_and_never_reused(tmp_path):
    inspector = scanned_inspector(tmp_path, capacity=1)
    reservation = await inspector.reserve()
    key, _ = await inspector.start(reservation)
    assert key == reservation
    inspector.validating.discard(key)
    for unknown in (reservation, "f" * 64):
        with pytest.raises(HostedApplicationError) as error:
            await inspector.start(unknown)
        assert error.value.code == "VALIDATOR_BUSY"


def test_validation_uses_its_reservation_job_budget_and_a_parser_only_directory(tmp_path):
    import hashlib
    import time

    from codemie.core.hosted_applications.validation import inspect_bundle
    from codemie.hosted_apps_validator.contract import RESULT_GRACE_SECONDS, validation_budget

    inspector = scanned_inspector(tmp_path)
    outputs = []

    async def parser(self, source, output, kind):
        outputs.append(output)
        candidate = inspect_bundle(source, kind, output)
        return {**candidate.to_dict(), "file_count": len(candidate.files)}

    content = b"<html>Reserved</html>"
    headers = {
        "x-validator-key": "k" * 32,
        "x-artifact-kind": "html",
        "content-type": "application/octet-stream",
        "x-artifact-size": str(len(content)),
        "x-artifact-sha256": hashlib.sha256(content).hexdigest(),
    }
    client = TestClient(server.create_app(inspector))
    reserved = client.post("/reservations", headers={"x-validator-key": "k" * 32})
    assert reserved.status_code == 201
    reservation = reserved.json()["reservation"]
    with patch.object(server.Inspector, "parser", parser):
        started = time.monotonic()
        response = client.post(
            "/validate", content=content, headers=headers | {"x-validation-reservation": reservation}
        )
    assert response.status_code == 200 and response.json()["validation_id"] == reservation
    assert outputs == [inspector.root / reservation / "parser" / "assets"], "The parser never writes next to the source"
    remaining = inspector.workspaces[reservation].expires - started
    assert abs(remaining - validation_budget("html", len(content)) - RESULT_GRACE_SECONDS) < 5
    assert client.get(f"/results/{reservation}/files/0", headers=headers).content == content
    again = client.post("/validate", content=content, headers=headers | {"x-validation-reservation": reservation})
    assert (again.status_code, again.json()["code"]) == (503, "VALIDATOR_BUSY")
    assert client.delete(f"/results/{reservation}", headers=headers).status_code == 204
    assert inspector.occupied() == 0


def test_reported_files_rejects_non_text_paths_without_crashing(tmp_path):
    result, assets = parsed(tmp_path)
    result["files"][0].update(path=5, key=5)
    with pytest.raises(HostedApplicationError) as error:
        server.reported_files(result, assets)
    assert error.value.code == "VALIDATION_UNAVAILABLE"


def test_startup_probe_writes_outside_the_sandbox_and_leaves_nothing_behind(tmp_path):
    outside = tmp_path / "isolation-check-outside"

    def child_without_landlock(command, **kwargs):
        # The probe's sibling target is writable for the validator, so only Landlock can deny it.
        outside.write_bytes(b"x")
        stdout = '{"network_denied": true, "outside_read_denied": true, "outside_write_denied": false}'
        return SimpleNamespace(returncode=0, stdout=stdout.encode(), stderr=b"")

    with patch.object(server.subprocess, "run", side_effect=child_without_landlock), pytest.raises(RuntimeError):
        server.require_isolation(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_sandbox_probe_targets_a_sibling_of_its_writable_directory(tmp_path, monkeypatch, capsys):
    import json

    from codemie.hosted_apps_validator import sandbox

    workspace = tmp_path / "isolation-check"
    workspace.mkdir()
    (workspace / "source").write_bytes(b"isolation check")
    monkeypatch.setattr(sandbox, "enforce_parser_sandbox", lambda source, writable: None)
    monkeypatch.setattr(sandbox.sys, "argv", ["sandbox", str(workspace / "source"), str(workspace / "out"), "probe"])
    assert sandbox.main() == 0
    checks = json.loads(capsys.readouterr().out)
    assert checks["outside_write_denied"] is False, "Without Landlock the validator-writable sibling is written"
    assert (tmp_path / "isolation-check-outside").exists()


@pytest.mark.parametrize("failure", ["warnings", "memory"])
def test_parser_limits_are_reported_as_final_limit_rejections(tmp_path, monkeypatch, capsys, failure):
    import json

    from codemie.core.hosted_applications.validation import BundleCandidate, ValidationWarning
    from codemie.hosted_apps_validator import sandbox
    from codemie.hosted_apps_validator.contract import MAX_WARNINGS

    def inspected(source, kind, destination):
        if failure == "memory":
            raise MemoryError
        warning = ValidationWarning("EXTERNAL_DEPENDENCY", "index.html", "External")
        return BundleCandidate(warnings=[warning] * (MAX_WARNINGS + 1))

    source = tmp_path / "source"
    source.write_bytes(b"<html></html>")
    monkeypatch.setattr(sandbox, "enforce_parser_sandbox", lambda source, writable: None)
    monkeypatch.setattr(sandbox, "inspect_bundle", inspected)
    monkeypatch.setattr(sandbox.sys, "argv", ["sandbox", str(source), str(tmp_path / "parser" / "assets"), "html"])
    assert sandbox.main() == 1
    assert json.loads(capsys.readouterr().out)["code"] == "VALIDATION_LIMIT_EXCEEDED"


class FakeChild:
    def __init__(self, stdout, returncode):
        self.pid, self.returncode, self._returncode = 0, None, returncode
        self.stdout = SimpleNamespace(read=AsyncMock(side_effect=[stdout, b""]))
        self.stderr = SimpleNamespace(read=AsyncMock(return_value=b""))

    async def wait(self):
        self.returncode = self._returncode
        return self.returncode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "returncode", "code", "status"),
    [
        (b'{"code": "VALIDATION_LIMIT_EXCEEDED", "message": "x"}', 1, "VALIDATION_LIMIT_EXCEEDED", 422),
        (b"", -24, "VALIDATION_LIMIT_EXCEEDED", 422),
        (b"x" * (server.MAX_RESULT + 1), 0, "VALIDATION_LIMIT_EXCEEDED", 422),
        (b"not json", 0, "VALIDATION_UNAVAILABLE", 503),
        (b'{"code": "INVENTED_BY_A_COMPROMISED_PARSER"}', 1, "VALIDATION_UNAVAILABLE", 503),
    ],
)
async def test_parser_outcomes_map_to_final_or_retryable_codes(tmp_path, monkeypatch, stdout, returncode, code, status):
    import signal

    inspector = scanned_inspector(tmp_path)
    child = FakeChild(stdout, returncode)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    monkeypatch.setattr(server.os, "killpg", lambda pid, sig: None)
    assert signal.SIGXCPU == 24
    with pytest.raises(HostedApplicationError) as error:
        await inspector.parser(tmp_path / "source", tmp_path / "parser" / "assets", "html")
    assert (error.value.code, error.value.status) == (code, status)


@pytest.mark.asyncio
async def test_a_parser_that_floods_stderr_before_its_result_does_not_hang(tmp_path, monkeypatch):
    """CR-008: stderr is drained while stdout is read, so a full stderr pipe never blocks the child."""
    import asyncio
    import sys

    real_exec = asyncio.create_subprocess_exec
    script = (
        "import json, sys; sys.stderr.write('w' * (1024 * 1024)); sys.stderr.flush(); "
        "sys.stdout.write(json.dumps({'files': []})); sys.stdout.flush()"
    )

    async def flooding_parser(*args, **kwargs):
        kwargs.pop("env", None)
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", flooding_parser)
    inspector = scanned_inspector(tmp_path)
    result = await asyncio.wait_for(
        inspector.parser(tmp_path / "source", tmp_path / "parser" / "assets", "html"), timeout=20
    )
    assert result == {"files": []}


# --- Preview capture (EPMCDME-17155) -----------------------------------------------------------------------------

KEY = "a" * 64
# The smallest JPEG that dimensions reads: SOI, one SOF0 header of 1280x800, EOI.
JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x03\x20\x05\x00\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"


def test_kept_preview_refuses_a_picture_without_the_jpeg_signature(tmp_path):
    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    # No FF D8 start, but a valid SOF0 header at offset 2: dimensions alone would read 1280x800.
    data = b"\x00\x00" + JPEG[2:]
    (scratch / "preview.jpg").write_bytes(data)
    reported = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "width": 1280, "height": 800}
    assert preview_capture._kept_preview(reported, scratch, target) is None
    assert not target.exists()
    (scratch / "preview.jpg").write_bytes(JPEG)
    reported = {**reported, "size": len(JPEG), "sha256": hashlib.sha256(JPEG).hexdigest()}
    assert preview_capture._kept_preview(reported, scratch, target) == reported
    assert target.read_bytes() == JPEG


def _reported(data, width=1280, height=800):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height}


def swapped_after_a_path_check(monkeypatch, tmp_path, picture):
    """A renderer process swaps ``picture`` right after a check by path: that check sees a good regular file."""
    from pathlib import Path

    decoy = tmp_path / "decoy.jpg"
    decoy.write_bytes(JPEG)
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: real_lstat(decoy if path == picture else path))


def test_fifo_picture_is_refused_without_blocking(tmp_path, monkeypatch):
    import os
    import threading

    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    # A renderer process can leave a FIFO in place of the picture: opening it for reading would wait for a writer.
    os.mkfifo(scratch / "preview.jpg")
    swapped_after_a_path_check(monkeypatch, tmp_path, scratch / "preview.jpg")
    answers = []
    reader = threading.Thread(
        target=lambda: answers.append(preview_capture._kept_preview(_reported(JPEG), scratch, target)), daemon=True
    )
    reader.start()
    reader.join(1)
    if reader.is_alive():
        # Unblock the stuck reader, so the test process can exit.
        with open(scratch / "preview.jpg", "wb"):
            pass
        reader.join(1)
        raise AssertionError("Reading the picture blocked on a FIFO")
    assert answers == [None]
    assert not target.exists()


def test_symlink_picture_is_refused(tmp_path, monkeypatch):
    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    picture = scratch / "preview.jpg"
    # A link to /dev/zero would give an endless read.
    picture.symlink_to("/dev/zero")
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) is None
    # A link is never followed, also when it points to a good JPEG and appears after a check by path.
    real = tmp_path / "real.jpg"
    real.write_bytes(JPEG)
    picture.unlink()
    picture.symlink_to(real)
    swapped_after_a_path_check(monkeypatch, tmp_path, picture)
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) is None
    assert not target.exists()


def test_a_picture_with_a_second_hard_link_is_refused(tmp_path):
    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    (scratch / "preview.jpg").write_bytes(JPEG)
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) is not None
    target.unlink()
    os.link(scratch / "preview.jpg", tmp_path / "other-name.jpg")
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) is None
    assert not target.exists()


def test_longer_picture_is_refused(tmp_path):
    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    (scratch / "preview.jpg").write_bytes(JPEG + b"\x00")
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) is None
    assert not target.exists()


def test_the_kept_picture_is_the_bytes_that_were_checked(tmp_path, monkeypatch):
    scratch, target = tmp_path / "scratch", tmp_path / "kept.jpg"
    scratch.mkdir()
    (scratch / "preview.jpg").write_bytes(JPEG)
    real_image = preview_capture.image

    def swap_after_the_read(data, suffix):
        # A renderer process replaces the checked file before the validator keeps it.
        (scratch / "preview.jpg").unlink()
        (scratch / "preview.jpg").symlink_to("/dev/zero")
        return real_image(data, suffix)

    monkeypatch.setattr(preview_capture, "image", swap_after_the_read)
    assert preview_capture._kept_preview(_reported(JPEG), scratch, target) == _reported(JPEG)
    assert not target.is_symlink() and target.read_bytes() == JPEG
    assert sorted(item.name for item in tmp_path.iterdir()) == ["kept.jpg", "scratch"]


PROLOGUE = """
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
STARTS = Path({starts!r})
with STARTS.open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
JPEG = {jpeg!r}
def grandchild(name):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(STARTS.parent / name).write_text(str(child.pid))
"""
GOOD = """
if sys.argv[1] == "probe":
    print(json.dumps({"sandbox": True, "chromium": "156.0.8078.79", "landlock_abi": 6}))
    sys.exit(0)
scratch = Path(sys.argv[3])
Path(STARTS.parent / "job.json").write_text(Path(sys.argv[2]).read_text())
(scratch / "preview.jpg").write_bytes(JPEG)
print(json.dumps({"size": len(JPEG), "sha256": hashlib.sha256(JPEG).hexdigest(), "width": 1280, "height": 800}))
"""
HANG = """
grandchild("grandchild.pid")
time.sleep(60)
"""
NULL_AND_LEAVE_A_CHILD = """
grandchild("grandchild.pid")
print("null")
"""
# A capture that takes 1.6 s, then gives the good picture.
SLOW_GOOD = "time.sleep(1.6)\n" + GOOD
# Writes more than a scratch limit of 1 MiB, then hangs.
FILL_SCRATCH = """
grandchild("grandchild.pid")
(Path(sys.argv[3]) / "filler").write_bytes(os.urandom(2 * 1024 * 1024))
time.sleep(60)
"""


def fake_renderer(tmp_path, body):
    """A renderer command running a small script; each start is appended to ``fake/starts``."""
    directory = tmp_path / "fake"
    directory.mkdir(exist_ok=True)
    script = directory / "renderer.py"
    script.write_text(PROLOGUE.format(starts=str(directory / "starts"), jpeg=JPEG) + body)
    return (__import__("sys").executable, str(script))


def starts(tmp_path):
    path = tmp_path / "fake" / "starts"
    return path.read_text().splitlines() if path.exists() else []


def capturing_inspector(tmp_path, body=GOOD, capture=True):
    scanner = SimpleNamespace(health=AsyncMock(return_value={"version": "1"}), scan=AsyncMock())
    inspector = server.Inspector(tmp_path / "volume", scanner, "k" * 32, renderer=fake_renderer(tmp_path, body))
    inspector.capture = capture
    return inspector


def stored(inspector, files=None, seconds=60.0, key=KEY):
    """A finished validation result under ``key``, laid out as POST /validate leaves it."""
    import io
    import time
    import zipfile

    from codemie.core.hosted_applications.validation import inspect_bundle

    path = inspector.root / key
    path.mkdir(mode=0o700)
    assets = path / "parser" / "assets"
    if files is None:
        (path / "source").write_bytes(b"<html><body>Cover</body></html>")
        candidate = inspect_bundle(path / "source", "html", assets)
    else:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        (path / "source").write_bytes(buffer.getvalue())
        candidate = inspect_bundle(path / "source", "zip", assets)
    result, reported = server.reported_files(candidate.to_dict(), assets)
    inspector.workspaces[key] = server.Workspace(path, time.monotonic() + seconds, result, reported)
    return key


def preview_request(key=KEY, body=None):
    import json

    from starlette.datastructures import Headers

    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static"} if body is None else body
    return SimpleNamespace(
        headers=Headers({"x-validator-key": "k" * 32}),
        path_params={"key": key},
        body=AsyncMock(return_value=json.dumps(body).encode()),
    )


async def answered(inspector, key=KEY, body=None):
    """The whole JSON answer of the preview route."""
    import json

    response = await inspector.preview(preview_request(key, body))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    return json.loads(response.body)


async def previewed(inspector, key=KEY, body=None):
    return (await answered(inspector, key, body))["preview"]


def is_dead(pid):
    """True when ``pid`` is gone or is a zombie.

    The renderer's leftover child is re-parented to the test container's PID 1 when the kill makes it an orphan.
    In a CI container that PID 1 may never reap it, so it stays a zombie and ``os.kill(pid, 0)`` still succeeds.
    A zombie runs no code, so it counts as dead. The validator reaps such orphans itself only as PID 1.
    """
    import os
    from pathlib import Path

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    if not Path("/proc/self/stat").exists():
        return False  # No /proc (macOS): the process still exists and is not reaped.
    try:
        state = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()[0]
    except FileNotFoundError:
        return True  # It was reaped between the two checks.
    return state == b"Z"


def assert_dead(tmp_path, name="grandchild.pid", seconds=5):
    import time

    pid = int((tmp_path / "fake" / name).read_text())
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if is_dead(pid):
            return
        time.sleep(0.05)
    raise AssertionError("The renderer's process group outlived the capture")


def test_assert_dead_fails_for_a_running_process(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (tmp_path / "fake").mkdir()
        (tmp_path / "fake" / "grandchild.pid").write_text(str(child.pid))
        with pytest.raises(AssertionError, match="outlived"):
            assert_dead(tmp_path, seconds=0.3)
        child.kill()
        if Path("/proc/self/stat").exists():
            # Linux: a killed child that nobody reaped yet is a zombie, and a zombie counts as dead.
            assert_dead(tmp_path)
    finally:
        child.kill()
        child.wait()
    assert_dead(tmp_path)


def test_preview_route_returns_null_when_capture_off(tmp_path):
    inspector = capturing_inspector(tmp_path, capture=False)
    key = stored(inspector)
    client = TestClient(server.create_app(inspector))
    headers = {"x-validator-key": "k" * 32}
    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static"}
    response = client.post(f"/results/{key}/preview", json=body, headers=headers)
    assert (response.status_code, response.json()) == (200, {"preview": None, "reason": "off"})
    assert starts(tmp_path) == []
    assert client.get("/health", headers=headers).json() == {"version": "1", "capture": "off", "landlock_abi": None}


@pytest.mark.asyncio
async def test_capture_off_answers_reason_off(tmp_path):
    inspector = capturing_inspector(tmp_path, capture=False)
    key = stored(inspector)
    assert await answered(inspector, key) == {"preview": None, "reason": "off"}
    assert starts(tmp_path) == []


@pytest.mark.asyncio
async def test_blank_page_answers_reason_no_picture(tmp_path):
    inspector = capturing_inspector(tmp_path, 'print("null")')
    key = stored(inspector)
    assert await answered(inspector, key) == {"preview": None, "reason": "no_picture"}
    assert len(starts(tmp_path)) == 1


@pytest.mark.asyncio
async def test_busy_renderer_answers_reason_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(preview_capture, "CAPTURE_LOCK_WAIT_SECONDS", 0.3)
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    async with inspector.capture_lock:
        assert await answered(inspector, key) == {"preview": None, "reason": "busy"}
    assert starts(tmp_path) == []


@pytest.mark.asyncio
async def test_lock_wait_is_used(tmp_path):
    import asyncio

    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    await inspector.capture_lock.acquire()
    asyncio.get_running_loop().call_later(4, inspector.capture_lock.release)
    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static", "lock_wait": 10}
    assert (await previewed(inspector, key, body))["size"] == len(JPEG)
    # The default wait of 3 seconds would have given "busy" for the same lock.
    await inspector.capture_lock.acquire()
    asyncio.get_running_loop().call_later(4, inspector.capture_lock.release)
    default = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static"}
    assert await answered(inspector, key, default) == {"preview": None, "reason": "busy"}


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_wait", [2, 61, True, "5", None, [10]])
async def test_lock_wait_out_of_range_is_refused(tmp_path, lock_wait):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static", "lock_wait": lock_wait}
    with pytest.raises(HostedApplicationError) as error:
        await inspector.preview(preview_request(key, body))
    assert (error.value.code, error.value.status) == ("INVALID_CONFIGURATION", 422)
    assert starts(tmp_path) == []


@pytest.mark.parametrize("lock_wait", [3, 3.5, 60])
def test_lock_wait_inside_the_range_is_accepted(lock_wait):
    import json

    body = json.dumps(
        {"site_root": "", "entrypoint": "index.html", "routing_mode": "static", "lock_wait": lock_wait}
    ).encode()
    assert preview_capture.preview_request(body) == (("", "index.html", "static"), lock_wait)
    plain = json.dumps({"site_root": "", "entrypoint": "index.html", "routing_mode": "static"}).encode()
    assert preview_capture.preview_request(plain)[1] == preview_capture.CAPTURE_LOCK_WAIT_SECONDS


@pytest.mark.asyncio
async def test_expired_workspace_after_long_wait_keeps_no_picture(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(preview_capture, "CAPTURE_LOCK_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 2)
    inspector = capturing_inspector(tmp_path)
    # Enough time left at the request, but not after the wait for the renderer.
    key = stored(inspector, seconds=2.6)
    await inspector.capture_lock.acquire()
    asyncio.get_running_loop().call_later(1, inspector.capture_lock.release)
    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static", "lock_wait": 5}
    assert await answered(inspector, key, body) == {"preview": None, "reason": "no_picture"}
    assert starts(tmp_path) == []


def test_preview_route_uses_the_result_authentication_and_key(tmp_path):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    client = TestClient(server.create_app(inspector))
    body = {"site_root": "", "entrypoint": "index.html", "routing_mode": "static"}
    assert client.post(f"/results/{key}/preview", json=body).status_code == 503
    unknown = client.post(f"/results/{'b' * 64}/preview", json=body, headers={"x-validator-key": "k" * 32})
    assert (unknown.status_code, unknown.json()["code"]) == (503, "VALIDATION_UNAVAILABLE")
    assert starts(tmp_path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"site_root": "", "entrypoint": "index.html"},
        {"site_root": "", "entrypoint": "index.html", "routing_mode": "static", "extra": "x"},
        {"site_root": None, "entrypoint": "index.html", "routing_mode": "static"},
        {"site_root": "", "entrypoint": "index.html", "routing_mode": "hash"},
        ["index.html"],
    ],
)
async def test_a_malformed_preview_request_is_refused(tmp_path, body):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    with pytest.raises(HostedApplicationError) as error:
        await inspector.preview(preview_request(key, body))
    assert (error.value.code, error.value.status) == ("INVALID_CONFIGURATION", 422)
    assert starts(tmp_path) == []


@pytest.mark.asyncio
async def test_a_good_capture_is_served_as_the_next_file_index(tmp_path):
    import copy
    import hashlib
    import json

    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    workspace = inspector.workspaces[key]
    before = copy.deepcopy(workspace.result)

    picture = await previewed(inspector, key)

    assert picture == {
        "index": 1,
        "size": len(JPEG),
        "sha256": hashlib.sha256(JPEG).hexdigest(),
        "width": 1280,
        "height": 800,
    }
    response = await inspector.file(file_request(key, 1))
    assert response.headers["content-length"] == str(len(JPEG))
    assert b"".join([chunk async for chunk in response.body_iterator]) == JPEG
    assert workspace.result == before, "The capture never changes the stored validation result"
    job = json.loads((tmp_path / "fake" / "job.json").read_text())
    assets = (workspace.path / "parser" / "assets").resolve()
    assert job["root"] == str(assets)
    assert job["files"] == {"index.html": str(assets / "index.html")}
    assert job["manifest"]["entrypoint"] == "index.html" and job["manifest"]["routing_mode"] == "static"
    assert sorted(item.name for item in workspace.path.iterdir()) == ["parser", "preview.jpg", "source"]
    # Chromium's UNIX sockets live in scratch (108-byte path limit): a short fixed directory, not one per 64-char key.
    scratch = inspector.root / "capture"
    assert starts(tmp_path) == [f"capture {scratch / 'job.json'} {scratch}"]
    assert not scratch.exists(), "The scratch directory is deleted after the capture"
    with pytest.raises(HostedApplicationError):
        await inspector.file(file_request(key, 2))
    assert (await inspector.delete(file_request(key))).status_code == 204
    assert not workspace.path.exists()


@pytest.mark.asyncio
async def test_a_site_root_maps_manifest_paths_to_the_parser_files(tmp_path):
    import json

    inspector = capturing_inspector(tmp_path)
    key = stored(
        inspector, {"dist/index.html": "<html>Cover</html>", "dist/app.css": "body{}", "other.html": "<html>x</html>"}
    )
    body = {"site_root": "dist", "entrypoint": "dist/index.html", "routing_mode": "spa_history"}
    assert (await previewed(inspector, key, body))["index"] == 3
    job = json.loads((tmp_path / "fake" / "job.json").read_text())
    assets = (inspector.workspaces[key].path / "parser" / "assets").resolve()
    assert job["files"] == {"index.html": str(assets / "dist/index.html"), "app.css": str(assets / "dist/app.css")}


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["app.css", "missing.html", "../index.html"])
async def test_a_non_html_or_unknown_entrypoint_gives_no_preview(tmp_path, entrypoint):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector, {"index.html": "<html>Cover</html>", "app.css": "body{}"})
    body = {"site_root": "", "entrypoint": entrypoint, "routing_mode": "static"}
    assert await previewed(inspector, key, body) is None
    assert starts(tmp_path) == []


@pytest.mark.asyncio
async def test_a_result_about_to_expire_gives_no_preview(tmp_path):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector, seconds=preview_capture.CAPTURE_HARD_SECONDS - 1)
    assert await previewed(inspector, key) is None
    assert starts(tmp_path) == []


@pytest.mark.asyncio
async def test_a_hung_renderer_and_its_group_are_killed_at_the_hard_limit(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 1.5)
    inspector = capturing_inspector(tmp_path, HANG)
    key = stored(inspector)
    started = time.monotonic()
    assert await previewed(inspector, key) is None
    assert time.monotonic() - started < 3
    assert_dead(tmp_path)
    assert not (inspector.root / "capture").exists()
    with pytest.raises(HostedApplicationError):
        await inspector.file(file_request(key, 1))


@pytest.mark.asyncio
async def test_a_child_left_behind_after_the_renderer_exits_is_killed(tmp_path):
    import time

    inspector = capturing_inspector(tmp_path, NULL_AND_LEAVE_A_CHILD)
    key = stored(inspector)
    started = time.monotonic()
    assert await previewed(inspector, key) is None
    assert time.monotonic() - started < 5, "A child holding the renderer's pipes never delays the answer"
    assert_dead(tmp_path)


# Leaves a process in a new session, as a renderer process that calls setsid() does, then gives no picture.
ESCAPE_THE_GROUP = """
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
Path(STARTS.parent / "escaped.pid").write_text(str(child.pid))
print("null")
"""


@pytest.mark.skipif(not __import__("os").path.isdir("/proc/self"), reason="The sweep reads /proc (Linux)")
@pytest.mark.asyncio
async def test_a_renderer_process_in_a_new_session_is_stopped_at_the_end_of_the_capture(tmp_path, monkeypatch):
    import os
    import signal

    from codemie.hosted_apps_validator import child_process

    escaped = tmp_path / "fake" / "escaped.pid"
    # Outside a container /proc lists the whole machine: let the sweep see the escaped process only.
    monkeypatch.setattr(child_process, "container_init", lambda: True)
    monkeypatch.setattr(child_process, "_process_ids", lambda: [int(escaped.read_text())] if escaped.exists() else [])
    inspector = capturing_inspector(tmp_path, ESCAPE_THE_GROUP)
    key = stored(inspector)
    try:
        assert await previewed(inspector, key) is None
        assert_dead(tmp_path, "escaped.pid")
    finally:
        if escaped.exists():
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(int(escaped.read_text()), signal.SIGKILL)


@pytest.mark.asyncio
async def test_a_capture_sweeps_strays_while_it_holds_the_renderer(tmp_path, monkeypatch):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    calls = []
    monkeypatch.setattr(
        preview_capture, "sweep_strays", lambda: calls.append(("sweep", inspector.capture_lock.locked())) or 0
    )
    monkeypatch.setattr(
        preview_capture, "reap_orphans", lambda: calls.append(("reap", inspector.capture_lock.locked())) or 0
    )
    assert await previewed(inspector, key)
    # The sweep runs before the next capture can start a renderer; the zombies it leaves are reaped after it.
    assert calls == [("reap", False), ("sweep", True), ("reap", False)]


@pytest.mark.asyncio
async def test_a_capture_cancelled_during_its_cleanup_frees_the_renderer(tmp_path, monkeypatch):
    import asyncio
    import shutil
    import threading
    from pathlib import Path

    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    entered, proceed = threading.Event(), threading.Event()
    real_rmtree = shutil.rmtree

    def slow_rmtree(path, *args, **kwargs):
        if (Path(path) / "job.json").exists():  # The cleanup after the renderer, not the one before it.
            entered.set()
            proceed.wait(10)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(preview_capture.shutil, "rmtree", slow_rmtree)
    running = asyncio.create_task(previewed(inspector, key))
    try:
        async with asyncio.timeout(10):
            while not entered.is_set():
                await asyncio.sleep(0.02)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        proceed.set()
    # The next capture gets the renderer; a lock left taken would make every later capture "busy".
    await asyncio.wait_for(inspector.capture_lock.acquire(), 5)
    inspector.capture_lock.release()
    assert not (inspector.root / "capture").exists()


@pytest.mark.skipif(not __import__("os").path.isdir("/proc/self"), reason="The sweep reads /proc (Linux)")
@pytest.mark.asyncio
async def test_the_sweep_waits_for_a_slow_spawn_in_flight(tmp_path, monkeypatch):
    import asyncio
    import signal
    import subprocess

    from codemie.hosted_apps_validator import child_process

    stray = sleeper(start_new_session=True)
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        sweep_only(monkeypatch, lambda: [stray.pid])
        monkeypatch.setattr(child_process._spawning, "count", 0)
        inspector = capturing_inspector(tmp_path)
        key = stored(inspector)
        real_kept, real_free = preview_capture._kept_preview, preview_capture._free_renderer

        def kept_while_a_parser_starts(*args):
            # A parser spawn is in flight when the capture ends; it finishes a moment later.
            child_process._spawning.count = 1
            return real_kept(*args)

        async def parser_started():
            while not child_process._spawning.count:
                await asyncio.sleep(0.01)
            # Longer than the 0.5 s cap the wait once had: the sweep must still run, not be skipped.
            await asyncio.sleep(1.0)
            child_process._spawning.count = 0

        outcomes = []

        def free(lock):
            # The moment the renderer is freed: the stray must already be gone.
            try:
                outcomes.append(stray.wait(timeout=2))
            except subprocess.TimeoutExpired:
                outcomes.append(None)
            real_free(lock)

        monkeypatch.setattr(preview_capture, "_kept_preview", kept_while_a_parser_starts)
        monkeypatch.setattr(preview_capture, "_free_renderer", free)
        starter = asyncio.create_task(parser_started())
        assert await previewed(inspector, key)
        await starter
        assert outcomes == [-signal.SIGKILL]
    finally:
        stray.kill()
        stray.wait()


@pytest.mark.asyncio
async def test_a_second_capture_waits_for_the_renderer_until_its_lock_wait_ends(tmp_path, monkeypatch):
    import asyncio
    import time

    monkeypatch.setattr(preview_capture, "CAPTURE_LOCK_WAIT_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 1.5)
    inspector = capturing_inspector(tmp_path, HANG)
    first, second = stored(inspector), stored(inspector, key="c" * 64)
    running = asyncio.create_task(previewed(inspector, first))
    while not starts(tmp_path):
        await asyncio.sleep(0.02)
    started = time.monotonic()
    assert await previewed(inspector, second) is None
    assert time.monotonic() - started < 1, "The waiting capture gives up when its lock wait ends"
    assert await running is None
    assert len(starts(tmp_path)) == 1, "Only one renderer ever runs"


@pytest.mark.asyncio
async def test_a_capture_that_waited_for_the_renderer_still_gets_its_whole_hard_limit(tmp_path, monkeypatch):
    import asyncio

    # Spec section 1: both limits count from capture start (renderer start), not from the request.
    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 3)
    monkeypatch.setattr(preview_capture, "CAPTURE_LOCK_WAIT_SECONDS", 2.5, raising=False)
    inspector = capturing_inspector(tmp_path, SLOW_GOOD)
    first, second = stored(inspector), stored(inspector, key="c" * 64)
    pictures = await asyncio.gather(previewed(inspector, first), previewed(inspector, second))
    assert all(pictures), pictures
    assert len(starts(tmp_path)) == 2


@pytest.mark.asyncio
async def test_a_renderer_that_fills_its_scratch_is_killed(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 5)
    monkeypatch.setattr(preview_capture, "RENDERER_SCRATCH_BYTES", 1024 * 1024, raising=False)
    inspector = capturing_inspector(tmp_path, FILL_SCRATCH)
    key = stored(inspector)
    started = time.monotonic()
    assert await previewed(inspector, key) is None
    assert time.monotonic() - started < 3, "The capture stops when its scratch outgrows the limit"
    assert_dead(tmp_path)
    assert not (inspector.root / "capture").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lie",
    [
        '{"size": len(JPEG) + 1}',
        '{"sha256": "0" * 64}',
        '{"width": 1281}',
        '{"height": 0}',
        '{"size": True}',
    ],
)
async def test_a_picture_that_differs_from_its_report_is_refused(tmp_path, lie):
    body = GOOD.replace('"height": 800}', '"height": 800, **' + lie + "}")
    inspector = capturing_inspector(tmp_path, body)
    key = stored(inspector)
    assert await previewed(inspector, key) is None
    assert sorted(item.name for item in inspector.workspaces[key].path.iterdir()) == ["parser", "source"]


@pytest.mark.asyncio
async def test_a_new_capture_replaces_the_previous_picture(tmp_path):
    inspector = capturing_inspector(tmp_path)
    key = stored(inspector)
    assert await previewed(inspector, key)
    assert (
        await previewed(inspector, key, {"site_root": "", "entrypoint": "missing.html", "routing_mode": "static"})
        is None
    )
    with pytest.raises(HostedApplicationError):
        await inspector.file(file_request(key, 1))
    assert not (inspector.workspaces[key].path / "preview.jpg").exists()


def test_renderer_self_test_returns_the_chromium_version(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(preview_capture.logger, "info", lambda message, *args: logged.append(message % args))
    root = tmp_path / "volume"
    root.mkdir()
    assert preview_capture.require_renderer(root, fake_renderer(tmp_path, GOOD)) == ("156.0.8078.79", 6)
    # Operators read the node's Landlock ABI here: ABI 6 or later (Linux 6.12) scopes the renderer's signals.
    assert logged == ["Renderer self-test passed (chromium=156.0.8078.79, landlock_abi=6)"]
    assert starts(tmp_path)[0].startswith("probe ")
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        'print(json.dumps({"sandbox": False, "chromium": "156"})); sys.exit(1)',
        'print(json.dumps({"sandbox": True, "chromium": None}))',
        'print(json.dumps({"sandbox": False, "chromium": "156"}))',
        'print("not json")',
        "time.sleep(60)",
    ],
)
def test_renderer_self_test_failures_turn_capture_off(tmp_path, monkeypatch, body):
    monkeypatch.setattr(preview_capture, "CAPTURE_HARD_SECONDS", 1.5)
    root = tmp_path / "volume"
    root.mkdir()
    assert preview_capture.require_renderer(root, fake_renderer(tmp_path, body)) is None
    assert list(root.iterdir()) == []


@pytest.fixture
def pid_one(monkeypatch):
    """The validator runs as PID 1 in its container, as the image starts it, so the stray sweep can run."""
    from codemie.hosted_apps_validator import child_process

    monkeypatch.setattr(child_process, "container_init", lambda: True)


def test_capture_is_on_when_secrets_are_hidden_and_the_self_test_passes(tmp_path, monkeypatch, pid_one):
    inspector = capturing_inspector(tmp_path, capture=False)
    monkeypatch.setattr(preview_capture, "hide_process_secrets", lambda: None)
    assert preview_capture.hide_secrets() is True
    assert preview_capture.configure_capture(inspector, secrets_hidden=True, environ={}) == "156.0.8078.79"
    assert inspector.capture is True
    headers = {"x-validator-key": "k" * 32}
    health = TestClient(server.create_app(inspector)).get("/health", headers=headers).json()
    assert health == {"version": "1", "capture": "on", "landlock_abi": 6}


def test_capture_is_off_when_the_validator_is_not_pid_one(tmp_path, monkeypatch):
    from codemie.hosted_apps_validator import child_process

    # Not PID 1: /proc lists more than this container, so the stray sweep cannot run after a capture.
    warnings = []
    monkeypatch.setattr(preview_capture.logger, "warning", lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr(child_process, "container_init", lambda: False)
    inspector = capturing_inspector(tmp_path, capture=False)
    assert preview_capture.configure_capture(inspector, secrets_hidden=True, environ={}) is None
    assert inspector.capture is False and starts(tmp_path) == [], "No self-test runs"
    assert warnings == ["Preview capture off: the validator is not PID 1 in its container, so no stray sweep can run"]


UNSCOPED = GOOD.replace('"landlock_abi": 6', '"landlock_abi": 5')
NO_ABI = GOOD.replace(', "landlock_abi": 6', "")


@pytest.mark.parametrize(("body", "abi"), [(UNSCOPED, 5), (NO_ABI, 0)])
def test_capture_is_off_below_landlock_abi_6(tmp_path, monkeypatch, pid_one, body, abi):
    warnings = []
    monkeypatch.setattr(preview_capture.logger, "warning", lambda message, *args: warnings.append(message % args))
    inspector = capturing_inspector(tmp_path, body, capture=False)
    assert preview_capture.configure_capture(inspector, secrets_hidden=True, environ={}) is None
    assert inspector.capture is False
    # The node's ABI stays visible on /health, so operators see why capture is off.
    assert inspector.landlock_abi == abi
    headers = {"x-validator-key": "k" * 32}
    health = TestClient(server.create_app(inspector)).get("/health", headers=headers).json()
    assert health == {"version": "1", "capture": "off", "landlock_abi": abi}
    assert warnings == [
        f"Preview capture off: Landlock ABI {abi} is below 6, so the renderer could signal the validator; "
        "VALIDATOR_PREVIEW_CAPTURE_UNSCOPED=true accepts this"
    ]


def test_the_unscoped_setting_accepts_capture_below_landlock_abi_6(tmp_path, pid_one):
    inspector = capturing_inspector(tmp_path, UNSCOPED, capture=False)
    environ = {"VALIDATOR_PREVIEW_CAPTURE_UNSCOPED": "true"}
    assert preview_capture.configure_capture(inspector, secrets_hidden=True, environ=environ) == "156.0.8078.79"
    assert inspector.capture is True and inspector.landlock_abi == 5


@pytest.mark.parametrize("value", ["", "TRUE", "yes", "1"])
def test_an_invalid_unscoped_setting_refuses_to_start(tmp_path, pid_one, value):
    inspector = capturing_inspector(tmp_path, capture=False)
    environ = {"VALIDATOR_PREVIEW_CAPTURE_UNSCOPED": value}
    with pytest.raises(ValueError, match="VALIDATOR_PREVIEW_CAPTURE_UNSCOPED"):
        preview_capture.configure_capture(inspector, secrets_hidden=True, environ=environ)
    assert starts(tmp_path) == []


def test_capture_is_off_when_secrets_cannot_be_hidden(tmp_path, monkeypatch):
    def refuse():
        raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Process secrets cannot be hidden", 503)

    inspector = capturing_inspector(tmp_path, capture=False)
    monkeypatch.setattr(preview_capture, "hide_process_secrets", refuse)
    assert preview_capture.hide_secrets() is False
    assert preview_capture.configure_capture(inspector, secrets_hidden=False, environ={}) is None
    assert inspector.capture is False and starts(tmp_path) == [], "No self-test runs"


def test_capture_setting_false_runs_no_self_test(tmp_path):
    inspector = capturing_inspector(tmp_path, capture=False)
    environ = {"VALIDATOR_PREVIEW_CAPTURE": "false"}
    assert preview_capture.configure_capture(inspector, secrets_hidden=True, environ=environ) is None
    assert inspector.capture is False and starts(tmp_path) == []


def test_capture_is_off_when_the_self_test_fails(tmp_path, pid_one):
    inspector = capturing_inspector(tmp_path, 'print("null")', capture=False)
    assert (
        preview_capture.configure_capture(inspector, secrets_hidden=True, environ={"VALIDATOR_PREVIEW_CAPTURE": "true"})
        is None
    )
    assert inspector.capture is False and len(starts(tmp_path)) == 1


@pytest.mark.parametrize("value", ["", "TRUE", "yes", "1", "off"])
def test_an_invalid_capture_setting_refuses_to_start(tmp_path, value):
    inspector = capturing_inspector(tmp_path, capture=False)
    with pytest.raises(ValueError, match="VALIDATOR_PREVIEW_CAPTURE"):
        preview_capture.configure_capture(inspector, secrets_hidden=True, environ={"VALIDATOR_PREVIEW_CAPTURE": value})
    assert starts(tmp_path) == []


def test_reap_orphans_reaps_only_children_the_validator_did_not_start(monkeypatch):
    import os
    import shutil
    import time

    from codemie.hosted_apps_validator import child_process

    # A killed renderer's Chromium processes (crashpad runs in its own session) are reparented to the validator,
    # which is PID 1 in its container: it must reap them, and never the parser or renderer it waits for itself.
    true = shutil.which("true")
    stray = os.posix_spawn(true, [true], {})
    owned = os.posix_spawn(true, [true], {})
    time.sleep(0.2)
    monkeypatch.setattr(child_process, "_zombie_children", lambda: [stray, owned])
    monkeypatch.setattr(child_process, "_owned", {owned})
    assert child_process.reap_orphans() == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(stray, os.WNOHANG)
    assert os.waitpid(owned, 0)[0] == owned


@pytest.mark.asyncio
async def test_spawn_settled_has_no_time_cap(monkeypatch):
    import asyncio

    from codemie.hosted_apps_validator import child_process

    monkeypatch.setattr(child_process._spawning, "count", 1)
    waiting = asyncio.create_task(child_process.spawn_settled())
    await asyncio.sleep(0.8)
    assert not waiting.done(), "A spawn still in flight keeps the sweep waiting"
    child_process._spawning.count = 0
    await asyncio.wait_for(waiting, timeout=1)


def test_reap_orphans_waits_while_a_child_is_being_started(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    monkeypatch.setattr(child_process, "_zombie_children", lambda: [123456])
    monkeypatch.setattr(child_process._spawning, "count", 1)
    assert child_process.reap_orphans() == 0


HAS_PROC = __import__("os").path.isdir("/proc/self")
needs_proc = pytest.mark.skipif(not HAS_PROC, reason="The sweep reads session ids from /proc (Linux)")


def sleeper(**options):
    import subprocess
    import sys

    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )


def sweep_only(monkeypatch, pids):
    """Let the sweep run outside a container, over ``pids`` only: the tests never signal other processes."""
    from codemie.hosted_apps_validator import child_process

    monkeypatch.setattr(child_process, "container_init", lambda: True)
    monkeypatch.setattr(child_process, "_process_ids", pids)


@needs_proc
def test_sweep_kills_a_new_session_process(monkeypatch):
    import signal

    from codemie.hosted_apps_validator import child_process

    # A renderer process that called setsid() is outside the renderer's group and outlives the group kill.
    stray = sleeper(start_new_session=True)
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        sweep_only(monkeypatch, lambda: [stray.pid])
        assert child_process.sweep_strays() == 1
        assert stray.wait(timeout=5) == -signal.SIGKILL
    finally:
        stray.kill()
        stray.wait()


@needs_proc
@pytest.mark.asyncio
async def test_sweep_spares_owned_sessions(monkeypatch):
    import asyncio
    import sys

    from codemie.hosted_apps_validator import child_process

    # A parser runs in its own session while a capture ends; the sweep must leave it running.
    monkeypatch.setattr(child_process, "_owned", set())
    process = await child_process.spawn(sys.executable, "-c", "import time; time.sleep(60)", start_new_session=True)
    try:
        sweep_only(monkeypatch, lambda: [process.pid])
        assert child_process.sweep_strays() == 0
        await asyncio.sleep(0.2)
        assert process.returncode is None
    finally:
        child_process.kill_group(process.pid)
        await process.wait()
        child_process.release(process)


@needs_proc
def test_sweep_spares_the_validators_own_session(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    sibling = sleeper()
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        sweep_only(monkeypatch, lambda: [sibling.pid])
        assert child_process.sweep_strays() == 0
        assert sibling.poll() is None
    finally:
        sibling.kill()
        sibling.wait()


@needs_proc
def test_sweep_spares_a_session_entered_from_outside_the_container(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    # kubectl exec starts a process whose parent is outside the container: parent pid 0 in its /proc view.
    entered = sleeper(start_new_session=True)
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        sweep_only(monkeypatch, lambda: [entered.pid])
        real_status = child_process._status
        monkeypatch.setattr(child_process, "_status", lambda pid: (*real_status(pid)[:1], 0, real_status(pid)[2]))
        assert child_process.sweep_strays() == 0
        assert entered.poll() is None
    finally:
        entered.kill()
        entered.wait()


def test_sweep_does_nothing_while_a_child_is_being_started(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    stray = sleeper(start_new_session=True)
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        sweep_only(monkeypatch, lambda: [stray.pid])
        monkeypatch.setattr(child_process._spawning, "count", 1)
        assert child_process.sweep_strays() == 0
        assert stray.poll() is None
    finally:
        stray.kill()
        stray.wait()


def test_sweep_does_nothing_outside_its_own_container(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    # Only as PID 1 does /proc list the validator's container alone; elsewhere it lists the whole machine.
    stray = sleeper(start_new_session=True)
    try:
        monkeypatch.setattr(child_process, "_owned", set())
        monkeypatch.setattr(child_process, "_process_ids", lambda: [stray.pid])
        assert child_process.container_init() is (__import__("os").getpid() == 1)
        monkeypatch.setattr(child_process, "container_init", lambda: False)
        assert child_process.sweep_strays() == 0
        assert stray.poll() is None
    finally:
        stray.kill()
        stray.wait()


@pytest.mark.parametrize(
    ("pids", "rounds"),
    [
        # Nothing is created: three quiet rounds end the sweep.
        ([7, 7, 7, 7], 3),
        # A forking chain the listing of /proc misses still moves the last pid: no round is quiet.
        (list(range(100, 130)), 20),
        # /proc/loadavg cannot be read: no round is quiet.
        ([None] * 30, 20),
    ],
)
def test_sweep_rounds_are_quiet_only_when_no_process_is_created(monkeypatch, pids, rounds):
    from codemie.hosted_apps_validator import child_process

    listings = []
    last = iter(pids)
    monkeypatch.setattr(child_process, "container_init", lambda: True)
    monkeypatch.setattr(child_process, "_unspared", lambda: listings.append(1) or {})
    monkeypatch.setattr(child_process, "_last_pid", lambda: next(last))
    assert child_process.sweep_strays() == 0
    assert len(listings) == rounds


def test_sweep_does_nothing_without_proc(monkeypatch):
    from codemie.hosted_apps_validator import child_process

    def no_proc(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(child_process, "container_init", lambda: True)
    monkeypatch.setattr(child_process.os, "listdir", no_proc)
    assert child_process.sweep_strays() == 0
