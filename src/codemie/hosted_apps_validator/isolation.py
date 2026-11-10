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

"""Kernel-enforced parser isolation; unsupported kernels fail closed."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import resource
from pathlib import Path

from codemie.core.hosted_applications.errors import HostedApplicationError


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


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


def _landlock(libc, source, writable):
    # Linux asm-generic syscall IDs, shared by supported x86_64 and aarch64.
    abi = _checked(libc.syscall(444, None, 0, 1))
    if abi < 3:
        raise OSError("Landlock ABI 3 or later is required")
    handled = (1 << 15) - 1  # ABI 3: execute, read/write, creation/removal, refer, truncate.
    ruleset = _Ruleset(handled)
    descriptor = _checked(libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0))
    read = (1 << 2) | (1 << 3)
    write = read | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 7) | (1 << 8) | (1 << 14)
    rules = [(source, 1 << 2), (writable, write)]
    # Trusted libraries/code are readable; execution, /proc, /etc, and arbitrary
    # sibling workspaces remain denied. No credentials are copied into these trees.
    rules += [
        (path, read)
        for path in (Path("/usr/local/lib"), Path("/usr/lib"), Path("/lib"), Path(__file__).parents[2])
        if path.exists()
    ]
    try:
        for path, allowed in rules:
            file = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathRule(allowed, file)
                _checked(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0))
            finally:
                os.close(file)
        _checked(libc.syscall(446, descriptor, 0))
    finally:
        os.close(descriptor)


def _seccomp(libc, architecture):
    if architecture == "x86_64":
        audit_arch = 0xC000003E
        denied = set(range(41, 60)) | {62, 101, 165, 166, 200, 234, 298, 304, 310, 311, 321, 322}
    else:
        audit_arch = 0xC00000B7
        denied = set(range(198, 213)) | {39, 40, 117, 129, 130, 131, 220, 221, 241, 242, 265, 270, 271, 280, 281}
    # Prevent asynchronous syscall indirection, new processes, and stealing another process's FD.
    denied |= {425, 426, 427, 435, 438}
    instructions = [
        _Filter(0x20, 0, 0, 4),
        _Filter(0x15, 1, 0, audit_arch),
        _Filter(0x06, 0, 0, 0x80000000),
        _Filter(0x20, 0, 0, 0),
        _Filter(0x35, 0, 1, 0x40000000),
        _Filter(0x06, 0, 0, 0x80000000),
    ]
    for number in sorted(denied):
        instructions.extend([_Filter(0x15, 0, 1, number), _Filter(0x06, 0, 0, 0x00050000 | errno.EPERM)])
    instructions.append(_Filter(0x06, 0, 0, 0x7FFF0000))
    filters = (_Filter * len(instructions))(*instructions)
    program = _Program(len(filters), filters)
    _checked(libc.prctl(22, 2, ctypes.byref(program), 0, 0))


def enforce_parser_sandbox(source: Path, writable: Path):
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in ("x86_64", "aarch64") or os.getuid() == 0:
        raise HostedApplicationError("VALIDATION_UNAVAILABLE", "The parser isolation environment is unavailable", 503)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
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
