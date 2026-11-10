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
from codemie.hosted_apps_validator import server


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
        health=health, reservation=AsyncMock(), validate=AsyncMock(), file=AsyncMock(), delete=AsyncMock()
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
