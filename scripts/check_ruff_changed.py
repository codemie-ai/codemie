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

"""Strict ruff on the Python files a branch changes, new issues only (EPMCDME-16990).

`make ruff` (the repository config) covers the whole tree. This check adds the stricter rule families below, but only
for what a merge request changes, so old code does not have to be cleaned up first:

    python scripts/check_ruff_changed.py                       # the working tree vs its merge base with origin/main
    python scripts/check_ruff_changed.py --base main
    python scripts/check_ruff_changed.py --merge-base <sha>    # a known merge base (CI: the MR diff base)
    python scripts/check_ruff_changed.py --pinned-ruff-version # the ruff version poetry.lock pins

- Files: Python files added, modified or renamed between the merge base and the working tree (git diff), except
  `tests/` and `test/` (S101, PLR2004 and the like are noise in tests).
- Rules: `ruff check --select STRICT_SELECT` with the rest of the repository ruff config (per-file-ignores, exclude,
  mccabe and pylint limits). The merge base is linted with its own config, exported from git into a temporary
  directory.
- New issue: an issue at the head beyond the number of issues with the same rule and message the same file (or the
  file it was renamed from) had at the merge base. Line numbers are not compared, they shift with every edit.
The ruff executable comes from RUFF_BIN or PATH (`poetry run` puts the locked one on PATH); a version other than the
one poetry.lock pins is reported, results may then differ.
Exit code 0 = no new issue, 1 = new issues (all printed as path:line:col: rule message), 2 = usage, git or ruff error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

STRICT_SELECT = "C901,B,SIM,PL,RET,ARG,PTH,UP,S"
STRICT_SKIP = ("tests/", "test/")
CONFIG_FILES = ("pyproject.toml", "ruff.toml", ".ruff.toml")
_LOCK_RUFF = re.compile(r'^name = "ruff"\s*\nversion = "([0-9][0-9A-Za-z.+-]*)"', re.MULTILINE)
_VERSION = re.compile(r"(\d+\.\d+\.\d+)")

Issue = dict[str, Any]


def out(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def _exe(name: str, env_var: str) -> str | None:
    return os.environ.get(env_var) or shutil.which(name)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def git(root: Path, *args: str) -> str:
    exe = _exe("git", "GIT_BIN")
    if not exe:
        raise RuntimeError("git not found")
    r = _run([exe, *args], root)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def changed_python_files(root: Path, merge_base: str) -> list[tuple[str, str]]:
    """(path at the merge base, path now) of Python files added, modified or renamed since merge_base, without
    tests. A new file has no base path: ("", path)."""
    fields = git(root, "diff", "--name-status", "-z", "-M", "--diff-filter=ACMR", merge_base, "--").split("\0")
    pairs = []
    i = 0
    while i < len(fields) - 1 and fields[i]:
        status = fields[i]
        if status[:1] in "RC":
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old, new = ("" if status == "A" else fields[i + 1]), fields[i + 1]
            i += 2
        if new.endswith(".py") and not new.startswith(STRICT_SKIP):
            pairs.append((old if status[:1] != "C" else "", new))
    return pairs


def pinned_ruff_version(lock_text: str) -> str | None:
    m = _LOCK_RUFF.search(lock_text)
    return m.group(1) if m else None


def ruff_version(ruff: str, root: Path) -> str | None:
    m = _VERSION.search(_run([ruff, "--version"], root).stdout or "")
    return m.group(1) if m else None


def run_ruff(ruff: str, cwd: Path, files: list[str]) -> list[Issue]:
    """Strict-rule issues of the files (relative to cwd) that exist, each with `_path` relative to cwd."""
    present = [f for f in files if (cwd / f).is_file()]
    if not present:
        return []
    argv = [ruff, "check", "--no-cache", "--force-exclude", "--exit-zero", "--output-format", "json"]
    r = _run([*argv, "--select", STRICT_SELECT, *present], cwd)
    if r.returncode != 0:
        raise RuntimeError(f"ruff exited {r.returncode}: {(r.stderr or r.stdout).strip()[:500]}")
    issues = json.loads(r.stdout or "[]")
    for issue in issues:
        name = Path(issue.get("filename") or "")
        try:
            issue["_path"] = name.resolve().relative_to(cwd.resolve()).as_posix()
        except ValueError:
            issue["_path"] = name.as_posix()
    return issues


def export(root: Path, rev: str, paths: list[str], dest: Path) -> None:
    """Write the version of each path at rev under dest; paths that do not exist at rev are skipped."""
    existing = set(git(root, "ls-tree", "-r", "--name-only", rev, "--", *paths).splitlines())
    for path in paths:
        if path in existing:
            target = dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(git(root, "show", f"{rev}:{path}"), encoding="utf-8", newline="")


def _key(issue: Issue, path: str) -> tuple[str, str, str]:
    return path, issue.get("code") or "?", issue.get("message") or ""


def new_issues(base: list[Issue], head: list[Issue], renamed: dict[str, str]) -> list[Issue]:
    """Head issues beyond the count of the same (file, rule, message) at the merge base, in head order.

    `renamed` maps a base path to its head path, so a renamed file keeps its old issues."""
    budget = Counter(_key(i, renamed.get(i["_path"], i["_path"])) for i in base)
    seen: Counter = Counter()
    found = []
    for issue in sorted(head, key=lambda i: (i["_path"], (i.get("location") or {}).get("row") or 0)):
        key = _key(issue, issue["_path"])
        seen[key] += 1
        if seen[key] > budget[key]:
            found.append(issue)
    return found


def describe(issue: Issue) -> str:
    loc = issue.get("location") or {}
    return f"{issue['_path']}:{loc.get('row')}:{loc.get('column')}: {issue.get('code')} {issue.get('message')}"


def strict_new_issues(root: Path, ruff: str, merge_base: str) -> tuple[list[Issue], int, int, int]:
    """(new issues, changed files, head issue count, base issue count)."""
    pairs = changed_python_files(root, merge_base)
    if not pairs:
        return [], 0, 0, 0
    head = run_ruff(ruff, root, [new for _, new in pairs])
    base_paths = [old for old, _ in pairs if old]
    with tempfile.TemporaryDirectory(prefix="ruff-changed-") as tmp:
        export(root, merge_base, [*CONFIG_FILES, *base_paths], Path(tmp))
        base = run_ruff(ruff, Path(tmp), base_paths)
    renamed = {old: new for old, new in pairs if old and old != new}
    return new_issues(base, head, renamed), len(pairs), len(head), len(base)


def _version_warning(root: Path, ruff: str) -> None:
    lock = root / "poetry.lock"
    pinned = pinned_ruff_version(lock.read_text(encoding="utf-8")) if lock.is_file() else None
    used = ruff_version(ruff, root)
    if pinned and used != pinned:
        sys.stderr.write(f"warning: ruff {used} is used, poetry.lock pins {pinned}; results may differ in CI\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: the current directory)")
    ap.add_argument("--base", default="origin/main", help="target branch ref (default: origin/main)")
    ap.add_argument("--merge-base", help="merge base commit to compare with, instead of computing it from --base")
    ap.add_argument("--pinned-ruff-version", action="store_true", help="print the ruff version poetry.lock pins")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.pinned_ruff_version:
        lock = root / "poetry.lock"
        version = pinned_ruff_version(lock.read_text(encoding="utf-8")) if lock.is_file() else None
        if version:
            out(version)
        return 0 if version else 2
    ruff = _exe("ruff", "RUFF_BIN")
    if not ruff:
        sys.stderr.write("ruff not found: run it with `poetry run` or set RUFF_BIN\n")
        return 2
    try:
        merge_base = a.merge_base or git(root, "merge-base", a.base, "HEAD").strip()
        _version_warning(root, ruff)
        found, files, head_count, base_count = strict_new_issues(root, ruff, merge_base)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"cannot run the check: {exc}\n")
        return 2
    out(
        f"strict ruff ({STRICT_SELECT}) on {files} changed Python file(s) vs {merge_base[:12]}: "
        f"{head_count} issue(s) now, {base_count} at the merge base, {len(found)} new"
    )
    for issue in found:
        out(f"  {describe(issue)}")
    if found:
        out(
            "Fix the new issues above, or add `# noqa: <rule>` with a reason where a rule does not fit. "
            "Issues the files already had at the merge base are not counted."
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
