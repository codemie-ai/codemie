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

"""One untrusted parser invocation. No application code is evaluated."""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.validation import inspect_bundle
from codemie.hosted_apps_validator.contract import MAX_WARNINGS
from codemie.hosted_apps_validator.isolation import enforce_parser_sandbox

MAX_OUTPUT = 6 * 1024 * 1024


def limit_exceeded(message):
    """The same upload exceeds this bound on every attempt: a final, non-security rejection."""
    return HostedApplicationError("VALIDATION_LIMIT_EXCEEDED", message)


def _denied(action):
    try:
        action()
    except OSError as exc:
        # A missing executable or a read-only mount is not proof of parser confinement.
        return exc.errno in (errno.EACCES, errno.EPERM)
    return False


def _isolation_checks(outside: Path) -> dict:
    """What the confined parser is denied; every value is True only when the sandbox holds."""
    return {
        "network_denied": _denied(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        "outside_read_denied": _denied(lambda: Path("/etc/passwd").read_bytes()),
        "outside_write_denied": _denied(lambda: outside.write_bytes(b"x")),
        "environment_empty": not os.environ,
        "exec_denied": _denied(
            lambda: subprocess.run([sys.executable, "-c", "pass"], check=True, capture_output=True, timeout=1)
        ),
    }


def _inspection(source: Path, kind: str, destination: Path) -> dict:
    """The parsed upload as the validator's result, refused when it has more findings than can be reported."""
    candidate = inspect_bundle(source, kind, destination)
    if len(candidate.warnings) > MAX_WARNINGS:
        raise limit_exceeded("The upload produced more findings than validation can report")
    value = candidate.to_dict()
    value["file_count"] = len(candidate.files)
    return value


def main():
    try:
        if len(sys.argv) != 4 or sys.argv[3] not in ("html", "zip", "probe"):
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Invalid parser invocation", 503)
        source, destination, kind = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
        if source.is_symlink() or not source.is_file() or destination.exists():
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Invalid parser workspace", 503)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Writable for the validator itself (unlike a read-only /tmp), but outside the parser's sandbox.
        outside = destination.parent.with_name(destination.parent.name + "-outside")
        if kind == "probe":
            # Prove the write is possible before Landlock, and execute a binary present in every image.
            outside.write_bytes(b"probe")
            outside.unlink()
            if not Path(sys.executable).is_file() or not os.access(sys.executable, os.X_OK):
                raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Parser probe unavailable", 503)
        enforce_parser_sandbox(source, destination.parent)
        value = _isolation_checks(outside) if kind == "probe" else _inspection(source, kind, destination)
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > MAX_OUTPUT:
            raise limit_exceeded("The validation result exceeds its bound")
        sys.stdout.write(encoded + "\n")
        return 0
    except HostedApplicationError as exc:
        sys.stdout.write(json.dumps({"code": exc.code, "message": exc.message}) + "\n")
        return 1
    except MemoryError:
        # The parser's address-space limit: the same upload exceeds it on every attempt.
        sys.stdout.write(json.dumps({"code": "VALIDATION_LIMIT_EXCEEDED", "message": "Memory limit"}) + "\n")
        return 1
    except Exception:
        sys.stdout.write(
            json.dumps({"code": "VALIDATION_UNAVAILABLE", "message": "Artifact inspection failed safely"}) + "\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
