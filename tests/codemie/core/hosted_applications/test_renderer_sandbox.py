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

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_validator import isolation

KEY_VALUE = "renderer-sandbox-test-key-7f3a"

# The parent stands in for the validator server: the key is in its initial environment block.
# It spawns one renderer stand-in before and one after it hides its secrets.
PARENT = """
import json, os, subprocess, sys
from pathlib import Path
from codemie.hosted_apps_validator.isolation import hide_process_secrets

work = Path("/work")
files = work / "files"
files.mkdir()
(files / "index.html").write_text("<html><body><h1>Preview</h1></body></html>")


def renderer(name):
    scratch = work / ("scratch-" + name)
    scratch.mkdir()
    # Worst case: the renderer inherits the parent's whole environment, the key included.
    done = subprocess.run(
        [sys.executable, "-B", "/probe/renderer.py", str(files), str(scratch)],
        capture_output=True, text=True, timeout=150, check=False,
    )
    if done.returncode:
        return {"returncode": done.returncode, "stderr": done.stderr[-2000:]}
    return json.loads(done.stdout)


result = {"control": renderer("control")}
hide_process_secrets()
result["hidden"] = renderer("hidden")
print(json.dumps(result))
"""

# Everything the probe uses is imported before the sandbox: afterwards Python cannot read its library.
RENDERER = """
import ctypes, errno, json, os, socket, subprocess, sys
from pathlib import Path
from codemie.hosted_apps_validator.isolation import enforce_renderer_sandbox

BROWSER = Path("/usr/lib/chromium/chromium")
KEY = b"VALIDATOR_SERVICE_KEY"
files, scratch = Path(sys.argv[1]), Path(sys.argv[2])
parent = os.getppid()


def outcome(action):
    try:
        action()
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    return "ok"


def key_in(path):
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    return "key" if KEY in data else "no-key"


def make_socket(family, kind=socket.SOCK_STREAM):
    return lambda: socket.socket(family, kind).close()


def make_pair(family):
    return lambda: [end.close() for end in socket.socketpair(family)]


def execute(program):
    return lambda: subprocess.run([program, "-c", "pass"], capture_output=True, timeout=5, check=False)


def parent_probes():
    return {
        "parent_environ": key_in(f"/proc/{parent}/environ"),
        "parent_mem": outcome(lambda: open(f"/proc/{parent}/mem", "rb").close()),
        "parent_fd": outcome(lambda: os.listdir(f"/proc/{parent}/fd")),
        "parent_signal": outcome(lambda: os.kill(parent, 0)),
    }


libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
baseline = {
    "landlock_abi": libc.syscall(444, None, 0, 1),
    "inet": outcome(make_socket(socket.AF_INET)),
    "netlink": outcome(make_socket(socket.AF_NETLINK, socket.SOCK_RAW)),
    "pair_inet": outcome(make_pair(socket.AF_INET)),
    "etc_read": outcome(lambda: Path("/etc/passwd").read_bytes()),
    "files_write": outcome(lambda: (files / "baseline").write_bytes(b"x")),
    "sh_exec": outcome(execute("/bin/sh")),
    "self_environ": key_in("/proc/self/environ"),
    **parent_probes(),
}
(files / "baseline").unlink()

enforce_renderer_sandbox(files, scratch, BROWSER)


def capture():
    shot = scratch / "shot.png"
    done = subprocess.run(
        [
            str(BROWSER), "--headless", "--no-sandbox", "--no-zygote", "--disable-gpu",
            "--disable-dev-shm-usage", "--no-first-run", "--disable-extensions",
            f"--user-data-dir={scratch / 'profile'}", f"--screenshot={shot}", "--window-size=800,600",
            (files / "index.html").as_uri(),
        ],
        cwd=scratch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=90, check=False,
    )
    png = shot.exists() and shot.read_bytes()[:8] == b"\\x89PNG\\r\\n\\x1a\\n"
    return {"returncode": done.returncode, "png": png}


checks = {
    "socket": {
        "inet": outcome(make_socket(socket.AF_INET)),
        "inet6": outcome(make_socket(socket.AF_INET6)),
        "packet": outcome(make_socket(socket.AF_PACKET, socket.SOCK_RAW)),
        "netlink": outcome(make_socket(socket.AF_NETLINK, socket.SOCK_RAW)),
        "pair_inet": outcome(make_pair(socket.AF_INET)),
        "unix": outcome(make_socket(socket.AF_UNIX)),
        "pair_unix": outcome(make_pair(socket.AF_UNIX)),
    },
    "files_read": outcome(lambda: (files / "index.html").read_bytes()),
    "fonts_list": outcome(lambda: os.listdir("/usr/share/fonts")),
    "etc_read": outcome(lambda: Path("/etc/passwd").read_bytes()),
    "app_src_read": outcome(lambda: Path("/app/src/codemie/__init__.py").read_bytes()),
    "scratch_write": outcome(lambda: (scratch / "probe").write_bytes(b"x")),
    "files_write": outcome(lambda: (files / "probe").write_bytes(b"x")),
    "work_write": outcome(lambda: Path("/work/escape").write_bytes(b"x")),
    "sh_exec": outcome(execute("/bin/sh")),
    "python_exec": outcome(execute(sys.executable)),
    "environment": dict(os.environ),
    "self_environ": key_in("/proc/self/environ"),
    **parent_probes(),
    "chromium": capture(),
}
print(json.dumps({"baseline": baseline, "checks": checks}))
"""

DENIED = {"EACCES", "EPERM"}


def _sandbox_run(root: Path, probe: Path, image: str) -> dict:
    command = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # Chromium starts about nine processes per capture; the parser's 32 is too low.
        "--pids-limit=256",
        "--cpus=1",
        "--memory=2g",
        "--user=65532:65532",
        "--mount",
        f"type=bind,source={root / 'src'},target=/app/src,readonly",
        "--mount",
        f"type=bind,source={probe},target=/probe,readonly",
        # mode=1777 instead of the parser's uid=/gid=, which podman rejects; both engines accept it.
        "--tmpfs",
        "/work:rw,nosuid,nodev,noexec,size=600m,mode=1777",
        "--env",
        "PYTHONPATH=/app/src",
        "--env",
        f"VALIDATOR_SERVICE_KEY={KEY_VALUE}",
        image,
        "python",
        "-B",
        "/probe/parent.py",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=400, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


def test_real_linux_renderer_isolation():
    image = os.environ.get("HOSTED_TEST_SANDBOX_IMAGE")
    if not image:
        pytest.skip("Set HOSTED_TEST_SANDBOX_IMAGE for real Docker/Linux isolation tests")
    root = Path(__file__).parents[4]
    with tempfile.TemporaryDirectory(prefix=".hosted-sandbox-test-", dir=root) as shared:
        probe = Path(shared)
        (probe / "parent.py").write_text(PARENT)
        (probe / "renderer.py").write_text(RENDERER)
        probe.chmod(0o755)
        for script in probe.iterdir():
            script.chmod(0o644)
        value = _sandbox_run(root, probe, image)

    control, hidden = value["control"], value["hidden"]
    assert "checks" in control, control
    assert "checks" in hidden, hidden
    baseline, checks = hidden["baseline"], hidden["checks"]

    # Without the sandbox each probe succeeds, so each denial below is the sandbox's work.
    assert baseline["inet"] == baseline["netlink"] == "ok"
    assert baseline["pair_inet"] not in DENIED
    assert baseline["etc_read"] == baseline["files_write"] == baseline["sh_exec"] == "ok"
    assert baseline["self_environ"] == "key" and baseline["parent_signal"] == "ok"

    assert checks["socket"] == {
        "inet": "EPERM",
        "inet6": "EPERM",
        "packet": "EPERM",
        "netlink": "EPERM",
        "pair_inet": "EPERM",
        "unix": "ok",
        "pair_unix": "ok",
    }
    assert checks["files_read"] == checks["fonts_list"] == checks["scratch_write"] == "ok"
    assert checks["etc_read"] in DENIED and checks["app_src_read"] in DENIED
    assert checks["files_write"] in DENIED and checks["work_write"] in DENIED
    assert checks["sh_exec"] in DENIED and checks["python_exec"] in DENIED

    # The inherited key is gone from the renderer and from everything it starts.
    assert checks["environment"] == {"TMPDIR": "/work/scratch-hidden"}
    assert checks["self_environ"] == "no-key"
    assert checks["chromium"] == {"returncode": 0, "png": True}

    # Control: a parent that does not hide its secrets is readable by a same-UID process. Inside the
    # sandbox Landlock already refuses environ and mem, but the descriptor list stays readable.
    assert control["baseline"]["parent_environ"] == "key"
    assert control["baseline"]["parent_fd"] == "ok"
    assert control["checks"]["parent_fd"] == "ok"
    # The hidden parent is unreadable, before and after the renderer applies its sandbox.
    for probes in (baseline, checks):
        assert probes["parent_environ"] in DENIED
        assert probes["parent_mem"] in DENIED
        assert probes["parent_fd"] in DENIED
    # Landlock ABI 6 scopes signals: the renderer cannot kill the validator.
    assert checks["parent_signal"] == ("EPERM" if baseline["landlock_abi"] >= 6 else "ok")


# --- The renderer's seccomp program, run on a small classic-BPF interpreter (no Docker) ---

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_SOCKET_CALLS = {"x86_64": (41, 53), "aarch64": (198, 199)}  # socket, socketpair
_PTRACE = {"x86_64": 101, "aarch64": 117}
_READ = {"x86_64": 0, "aarch64": 63}
_KILL = 0x80000000


def _installed_program(architecture):
    """The instructions _renderer_seccomp hands to prctl(PR_SET_SECCOMP), as (code, jt, jf, k) tuples."""
    installed = []

    class _Libc:
        def prctl(self, option, mode, program, *_):
            assert (option, mode) == (22, 2)  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
            value = program._obj
            installed.extend((item.code, item.jt, item.jf, item.k) for item in value.filter[: value.length])
            return 0

    isolation._renderer_seccomp(_Libc(), architecture)
    return installed


def _verdict(program, *, arch, nr, family=0):
    """Run ``program`` on one seccomp_data: nr at offset 0, arch at 4, the low word of args[0] at 16."""
    data = {0: nr, 4: arch, 16: family}
    pc = accumulator = 0
    while True:
        code, jt, jf, k = program[pc]
        if code == 0x20:  # BPF_LD | BPF_W | BPF_ABS
            accumulator, pc = data[k], pc + 1
        elif code == 0x15:  # BPF_JMP | BPF_JEQ | BPF_K
            pc += 1 + (jt if accumulator == k else jf)
        elif code == 0x35:  # BPF_JMP | BPF_JGE | BPF_K
            pc += 1 + (jt if accumulator >= k else jf)
        elif code == 0x06:  # BPF_RET | BPF_K
            return k
        else:
            raise AssertionError(f"Unexpected BPF instruction {code:#x}")


@pytest.mark.parametrize("architecture", ["x86_64", "aarch64"])
def test_renderer_seccomp_allows_only_unix_sockets(architecture):
    import socket

    program = _installed_program(architecture)
    arch = _AUDIT_ARCH[architecture]
    for call in _SOCKET_CALLS[architecture]:
        for family in (socket.AF_INET, socket.AF_INET6, 17, 16, 0, 0xFFFFFFFF):  # 17 AF_PACKET, 16 AF_NETLINK
            assert _verdict(program, arch=arch, nr=call, family=family) == isolation._SECCOMP_DENY, (call, family)
        assert _verdict(program, arch=arch, nr=call, family=socket.AF_UNIX) == isolation._SECCOMP_ALLOW
    assert _verdict(program, arch=arch, nr=_PTRACE[architecture]) == isolation._SECCOMP_DENY
    assert _verdict(program, arch=arch, nr=_READ[architecture]) == isolation._SECCOMP_ALLOW


@pytest.mark.parametrize("architecture", ["x86_64", "aarch64"])
def test_renderer_seccomp_kills_a_foreign_abi(architecture):
    program = _installed_program(architecture)
    other = _AUDIT_ARCH["aarch64" if architecture == "x86_64" else "x86_64"]
    assert _verdict(program, arch=other, nr=_READ[architecture]) == _KILL
    # x32 system calls on x86_64 (bit 30 set in nr).
    assert _verdict(program, arch=_AUDIT_ARCH[architecture], nr=0x40000000 | 41) == _KILL


def _unavailable(action):
    with pytest.raises(HostedApplicationError) as raised:
        action()
    assert raised.value.code == "VALIDATION_UNAVAILABLE"
    assert raised.value.status == 503


def test_renderer_sandbox_is_unavailable_off_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation.platform, "system", lambda: "Darwin")
    _unavailable(lambda: isolation.enforce_renderer_sandbox(tmp_path, tmp_path, tmp_path / "chromium"))


def test_renderer_sandbox_refuses_root(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(isolation.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(isolation.os, "getuid", lambda: 0)
    _unavailable(lambda: isolation.enforce_renderer_sandbox(tmp_path, tmp_path, tmp_path / "chromium"))


def test_musl_loader_matches_the_running_architecture(tmp_path):
    for name in ("ld-musl-aarch64.so.1", "ld-musl-x86_64.so.1"):
        (tmp_path / name).write_bytes(b"")
    assert isolation._musl_loader("x86_64", tmp_path) == tmp_path / "ld-musl-x86_64.so.1"
    assert isolation._musl_loader("aarch64", tmp_path) == tmp_path / "ld-musl-aarch64.so.1"


def test_musl_loader_fails_closed_without_a_loader_for_this_architecture(tmp_path):
    (tmp_path / "ld-musl-aarch64.so.1").write_bytes(b"")
    with pytest.raises(OSError):
        isolation._musl_loader("x86_64", tmp_path)


def test_hide_process_secrets_is_unavailable_off_linux(monkeypatch):
    monkeypatch.setattr(isolation.platform, "system", lambda: "Darwin")
    _unavailable(isolation.hide_process_secrets)


def test_hide_process_secrets_fails_closed_when_the_kernel_refuses(monkeypatch):
    calls = []

    class _Libc:
        def prctl(self, *args):
            calls.append(args)
            return -1

    monkeypatch.setattr(isolation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(isolation, "_libc", _Libc)
    _unavailable(isolation.hide_process_secrets)
    assert calls == [(4, 0, 0, 0, 0)]  # PR_SET_DUMPABLE, 0
