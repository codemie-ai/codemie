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

"""Kernel-enforced parser and renderer isolation; unsupported kernels fail closed."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import resource
import stat
from pathlib import Path

from codemie.core.hosted_applications.errors import HostedApplicationError

# Landlock filesystem rights (ABI 3 handles bits 0-14).
_EXECUTE, _WRITE_FILE, _READ_FILE, _READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_HANDLED = (1 << 15) - 1  # ABI 3: execute, read/write, creation/removal, refer, truncate.
_FILE_RIGHTS = _EXECUTE | _WRITE_FILE | _READ_FILE | (1 << 14)  # The only rights a non-directory rule accepts.
_READ = _READ_FILE | _READ_DIR
# ABI 6 scopes: no signals to, and no abstract UNIX sockets of, processes outside the sandbox.
_SCOPE_ABSTRACT_UNIX_SOCKET, _SCOPE_SIGNAL = 1 << 0, 1 << 1
# The first Landlock ABI with the scopes above.
SCOPED_ABI = 6

_SECCOMP_ALLOW = 0x7FFF0000
_SECCOMP_DENY = 0x00050000 | errno.EPERM
_AF_UNIX = 1

# Measured with Alpine Chromium 156: the least it needs to start and capture. A /proc/self rule is not enough:
# Landlock binds it to the renderer's own /proc/<pid>, and every Chromium process reads its own, other, PID
# directory (spec section 3 accepts this read set; the validator hides its /proc files with PR_SET_DUMPABLE).
_RENDERER_READ = (Path("/usr/lib"), Path("/usr/share/fonts"), Path("/proc"))
# Each process stays below about 132 descriptors; Chromium fails at 128.
RENDERER_NOFILE = 512
# Per process; well above the 20 s capture limit, so only a runaway process reaches it.
RENDERER_CPU_SECONDS = 60
# --disable-dev-shm-usage puts Chromium's shared memory files in TMPDIR, so one file can be large.
RENDERER_FSIZE = 256 * 1024 * 1024


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _ScopedRuleset(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class _Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(_Filter))]


def _checked(value):
    if value < 0:
        raise OSError(ctypes.get_errno(), "Sandbox enforcement unavailable")
    return value


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The kernel's Landlock ABI version; OSError when the kernel has no Landlock."""
    return _checked(_libc().syscall(444, None, 0, 1))


def _restrict_paths(libc, rules, scoped=0):
    """Allow only ``rules`` ((path, rights) pairs); ``scoped`` applies where the kernel has Landlock ABI 6."""
    # Linux asm-generic syscall IDs, shared by supported x86_64 and aarch64.
    abi = _checked(libc.syscall(444, None, 0, 1))
    if abi < 3:
        raise OSError("Landlock ABI 3 or later is required")
    ruleset = _ScopedRuleset(_HANDLED, 0, scoped) if scoped and abi >= SCOPED_ABI else _Ruleset(_HANDLED)
    descriptor = _checked(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0))
    try:
        for path, allowed in rules:
            file = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                access = allowed if stat.S_ISDIR(os.fstat(file).st_mode) else allowed & _FILE_RIGHTS
                rule = _PathRule(access, file)
                _checked(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0))
            finally:
                os.close(file)
        _checked(libc.syscall(446, descriptor, 0))
    finally:
        os.close(descriptor)


def _landlock(libc, source, writable):
    write = _READ | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 7) | (1 << 8) | (1 << 14)
    rules = [(source, _READ_FILE), (writable, write)]
    # Trusted libraries/code are readable; execution, /proc, /etc, and arbitrary
    # sibling workspaces remain denied. No credentials are copied into these trees.
    rules += [
        (path, _READ)
        for path in (Path("/usr/local/lib"), Path("/usr/lib"), Path("/lib"), Path(__file__).parents[2])
        if path.exists()
    ]
    _restrict_paths(libc, rules)


def _audit_arch(architecture):
    return 0xC000003E if architecture == "x86_64" else 0xC00000B7


def _ptrace_calls(architecture):
    """ptrace, process_vm_readv and process_vm_writev."""
    return {101, 310, 311} if architecture == "x86_64" else {117, 270, 271}


def _filter_program(libc, architecture, denied, tail):
    """Kill foreign ABIs, answer ``denied`` syscalls with EPERM, then run ``tail``."""
    instructions = [
        _Filter(0x20, 0, 0, 4),
        _Filter(0x15, 1, 0, _audit_arch(architecture)),
        _Filter(0x06, 0, 0, 0x80000000),
        _Filter(0x20, 0, 0, 0),
        _Filter(0x35, 0, 1, 0x40000000),
        _Filter(0x06, 0, 0, 0x80000000),
    ]
    for number in sorted(denied):
        instructions.extend([_Filter(0x15, 0, 1, number), _Filter(0x06, 0, 0, _SECCOMP_DENY)])
    instructions.extend(tail)
    filters = (_Filter * len(instructions))(*instructions)
    program = _Program(len(filters), filters)
    _checked(libc.prctl(22, 2, ctypes.byref(program), 0, 0))


def _seccomp(libc, architecture):
    if architecture == "x86_64":
        denied = set(range(41, 60)) | {62, 101, 165, 166, 200, 234, 298, 304, 310, 311, 321, 322}
    else:
        denied = set(range(198, 213)) | {39, 40, 117, 129, 130, 131, 220, 221, 241, 242, 265, 270, 271, 280, 281}
    # Prevent asynchronous syscall indirection, new processes, and stealing another process's FD.
    denied |= {425, 426, 427, 435, 438}
    _filter_program(libc, architecture, denied, [_Filter(0x06, 0, 0, _SECCOMP_ALLOW)])


def _renderer_seccomp(libc, architecture):
    # Chromium creates processes, so only io_uring, pidfd_getfd and the ptrace family are denied.
    denied = {425, 426, 427, 438} | _ptrace_calls(architecture)
    socket, socketpair = (41, 53) if architecture == "x86_64" else (198, 199)
    tail = [
        _Filter(0x15, 2, 0, socket),
        _Filter(0x15, 1, 0, socketpair),
        _Filter(0x06, 0, 0, _SECCOMP_ALLOW),
        # socket and socketpair: allow only when args[0] (the family, offset 16) is AF_UNIX.
        _Filter(0x20, 0, 0, 16),
        _Filter(0x15, 0, 1, _AF_UNIX),
        _Filter(0x06, 0, 0, _SECCOMP_ALLOW),
        _Filter(0x06, 0, 0, _SECCOMP_DENY),
    ]
    _filter_program(libc, architecture, denied, tail)


def _supported():
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in ("x86_64", "aarch64") or os.getuid() == 0:
        return None
    return architecture


def enforce_parser_sandbox(source: Path, writable: Path):
    architecture = _supported()
    if architecture is None:
        raise HostedApplicationError("VALIDATION_UNAVAILABLE", "The parser isolation environment is unavailable", 503)
    try:
        libc = _libc()
        _checked(libc.prctl(38, 1, 0, 0, 0))
        os.environ.clear()
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (500 * 1024 * 1024, 500 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _landlock(libc, source.resolve(strict=True), writable.resolve(strict=True))
        _seccomp(libc, architecture)
    except OSError as exc:
        raise HostedApplicationError(
            "VALIDATION_UNAVAILABLE", "The parser isolation environment is unavailable", 503
        ) from exc


def hide_process_secrets() -> None:
    """Make this process's /proc files (environ, mem, fd) unreadable to other processes of the same UID.

    The validator calls this at startup: the renderer runs as the same UID and may read all of /proc.
    The setting is not inherited across execve, so each process that holds a secret calls it itself.
    """
    unavailable = HostedApplicationError("VALIDATION_UNAVAILABLE", "Process secrets cannot be hidden", 503)
    if platform.system() != "Linux":
        raise unavailable
    try:
        _checked(_libc().prctl(4, 0, 0, 0, 0))  # PR_SET_DUMPABLE, 0
    except OSError as exc:
        raise unavailable from exc


def _musl_loader(architecture: str, lib: Path = Path("/lib")) -> Path:
    """The dynamic loader of this architecture; no loader means no sandbox."""
    loader = lib / f"ld-musl-{architecture}.so.1"
    if not loader.is_file():
        raise OSError(errno.ENOENT, "No dynamic loader for this architecture")
    return loader


def _scrub_environment(libc, scratch: Path):
    """Keep only TMPDIR, and erase the inherited environment block that /proc/self/environ shows."""
    fields = Path("/proc/self/stat").read_bytes().rsplit(b")", 1)[1].split()
    # Fields 50 and 51 of /proc/<pid>/stat (env_start, env_end); the split starts at field 3.
    start, end = int(fields[47]), int(fields[48])
    os.environ.clear()
    _checked(libc.clearenv())
    if end > start:
        ctypes.memset(start, 0, end - start)
    os.environ["TMPDIR"] = str(scratch)


def enforce_renderer_sandbox(files: Path, scratch: Path, browser: Path) -> None:
    """Confine the calling process, and every process it starts, to one preview capture.

    It allows reading ``files``, the browser install and the libraries, fonts, /proc and /dev/null that
    Chromium needs; reading and writing ``scratch``; executing only ``browser``, its crashpad handler and
    the dynamic loader (Chromium 156 starts the handler even with ``--disable-crash-reporter`` and stops
    when it cannot); writing /dev/null; and only AF_UNIX sockets. The environment becomes ``TMPDIR=<scratch>``.

    Python cannot read its standard library or application code afterwards: import every module the
    caller needs (``json``, ``base64``, ``select``, ``subprocess`` and so on) before calling this.
    """
    architecture = _supported()
    unavailable = HostedApplicationError(
        "VALIDATION_UNAVAILABLE", "The renderer isolation environment is unavailable", 503
    )
    if architecture is None:
        raise unavailable
    try:
        libc = _libc()
        files, scratch, browser = files.resolve(strict=True), scratch.resolve(strict=True), browser.resolve(strict=True)
        executables = [browser, _musl_loader(architecture)]
        crashpad = browser.parent / "chrome_crashpad_handler"
        if crashpad.is_file():
            executables.append(crashpad)
        _checked(libc.prctl(38, 1, 0, 0, 0))
        _scrub_environment(libc, scratch)
        # No RLIMIT_AS: Chromium reserves far more address space than it uses.
        resource.setrlimit(resource.RLIMIT_CPU, (RENDERER_CPU_SECONDS, RENDERER_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (RENDERER_FSIZE, RENDERER_FSIZE))
        resource.setrlimit(resource.RLIMIT_NOFILE, (RENDERER_NOFILE, RENDERER_NOFILE))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        rules = [(files, _READ), (browser.parent, _READ), (scratch, _HANDLED & ~_EXECUTE)]
        rules += [(path, _READ) for path in _RENDERER_READ if path.exists()]
        # subprocess.DEVNULL opens it for reading and writing.
        rules.append((Path("/dev/null"), _READ_FILE | _WRITE_FILE))
        rules += [(path, _EXECUTE | _READ_FILE) for path in executables]
        _restrict_paths(libc, rules, _SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL)
        _renderer_seccomp(libc, architecture)
    except OSError as exc:
        raise unavailable from exc
