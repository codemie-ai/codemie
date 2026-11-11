#!/usr/bin/env python3
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Poetry lock freshness check for a repository's CI (no network, no install of the project needed).

A pyproject.toml change without a matching `poetry lock` leaves poetry.lock stale; `poetry install` (the Docker
build, every developer) then refuses it. This check finds it in the MR pipeline (EPMCDME-16990).

    python scripts/check_poetry_lock.py              # the repository in the current directory
    python scripts/check_poetry_lock.py --root DIR   # another Poetry project

It runs Poetry's own `poetry check --lock` (the content-hash test of poetry.lock against pyproject.toml) and prints
only its errors: the deprecation warnings Poetry 2 prints for the `[tool.poetry]` section are not the lock's problem.
The `poetry` executable comes from POETRY_BIN or PATH.
Exit code 0 = the lock matches, 1 = stale or missing lock (or another error Poetry reports), 2 = Poetry not found.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIX_HINT = "Run `poetry lock` (it keeps the locked versions where it can) and commit poetry.lock."


def out(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def poetry_errors(output: str) -> list[str]:
    """The `Error:` lines of `poetry check` output; every non-warning line when Poetry printed no `Error:` line."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if line.lstrip().startswith("Error:")]
    return errors or [line for line in lines if not line.lstrip().startswith("Warning:")]


def check(root: Path, poetry: str) -> tuple[bool, list[str]]:
    """(lock matches, problems) for the Poetry project in root."""
    if not (root / "poetry.lock").is_file():
        return False, ["poetry.lock does not exist"]
    p = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [poetry, "check", "--lock", "--no-plugins", "--no-interaction", "--no-ansi", "--directory", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if p.returncode == 0:
        return True, []
    return False, poetry_errors(p.stdout + "\n" + p.stderr) or [f"poetry check --lock exited {p.returncode}"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="Poetry project directory (default: the current directory)")
    a = ap.parse_args(argv)
    poetry = os.environ.get("POETRY_BIN") or shutil.which("poetry")
    if not poetry:
        sys.stderr.write("poetry not found: install Poetry (see the README) or set POETRY_BIN\n")
        return 2
    root = Path(a.root)
    ok, problems = check(root, poetry)
    if ok:
        out(f"{(root / 'poetry.lock').as_posix()}: matches pyproject.toml")
        return 0
    out(f"{(root / 'poetry.lock').as_posix()}: does not match pyproject.toml")
    for problem in problems:
        out(f"  - {problem}")
    out(FIX_HINT)
    return 1


if __name__ == "__main__":
    sys.exit(main())
