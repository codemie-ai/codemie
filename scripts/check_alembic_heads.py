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

"""Alembic migration chain check for a repository's CI (no database, no alembic install needed).

Two branches that each add a migration after the same parent leave the history with two heads; the backend then
fails to start with "Multiple head revisions are present". This check finds it in seconds, in the MR pipeline and
in the pre-push hook (EPMCDME-16155).

    python scripts/check_alembic_heads.py                      # the working tree
    python scripts/check_alembic_heads.py --merge-with origin/main   # the working tree merged with the target branch

Every directory named `versions` that has an `env.py` next to it (or in its parent) is one migration tree. Per tree
the script reads `revision` / `down_revision` from each version file with `ast` and fails on:
  - more than one head (two migrations nobody revises): `upgrade head` is ambiguous;
  - a `down_revision` that does not exist in the tree;
  - the same revision id in two files.
`--merge-with <ref>` adds the version files that exist on <ref> and not in the working tree. That is what a merge
would bring in, so two open MRs that both revise the same parent are caught on the second one to merge, not after.
A file that cannot be read as a migration fails the check and is named.
Exit code 0 = healthy, 1 = a problem (all printed), 2 = usage or git error.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path


_NOT_LITERAL = object()


def revisions_of(source: str) -> tuple[str | None, list[str]]:
    """(revision, [down_revision...]) from a version file's module-level assignments.

    A file with a `revision` must also declare its parent: `down_revision = None` (a first migration), a string, or a
    tuple/list of strings (a merge migration). A missing, non-literal or wrongly typed `down_revision` raises
    ValueError, so the file is reported by name instead of passing as a first migration."""
    found: dict[str, object] = {}
    for node in ast.parse(source).body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target in ("revision", "down_revision"):
            try:
                found[target] = ast.literal_eval(value)
            except ValueError:
                found[target] = _NOT_LITERAL
    rev = found.get("revision")
    if not isinstance(rev, str):
        return None, []  # reported by check() as "no literal `revision`" (or skipped for __init__.py)
    if "down_revision" not in found:
        raise ValueError("no `down_revision` (use `down_revision = None` for a first migration)")
    down = found["down_revision"]
    if down is None:
        return rev, []
    if isinstance(down, str):
        return rev, [down]
    if isinstance(down, (tuple, list)) and down and all(isinstance(d, str) for d in down):
        return rev, list(down)
    if down is _NOT_LITERAL:
        raise ValueError("`down_revision` is not a literal")
    raise ValueError(f"`down_revision` must be None, a string or a tuple of strings, not {type(down).__name__}")


def trees(root: Path) -> list[Path]:
    out = []
    for v in sorted(root.rglob("versions")):
        if (
            v.is_dir()
            and any((p / "env.py").is_file() for p in (v.parent, v))
            and ".venv" not in v.parts
            and "node_modules" not in v.parts
        ):
            out.append(v)
    return out


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def _read(name: str, source: str, unreadable: dict[str, str]) -> tuple[str | None, list[str]]:
    """revisions_of that records a file Python cannot parse instead of aborting the whole check."""
    try:
        return revisions_of(source)
    except (SyntaxError, ValueError) as exc:
        unreadable[name] = f"cannot be read as a migration ({type(exc).__name__}: {exc})"
        return None, []


Revisions = dict[str, tuple[str | None, list[str]]]


def collect(tree: Path, root: Path, merge_with: str | None) -> tuple[Revisions, dict[str, str]]:
    """(file name -> (revision, downs), unreadable files) for the working tree plus, with merge_with, files only the
    other side has."""
    unreadable: dict[str, str] = {}
    files = {p.name: _read(p.name, p.read_text(encoding="utf-8"), unreadable) for p in sorted(tree.glob("*.py"))}
    if merge_with:
        rel = tree.resolve().relative_to(root.resolve()).as_posix()
        for path in git(root, "ls-tree", "-r", "--name-only", merge_with, "--", rel).splitlines():
            name = Path(path).name
            if name.endswith(".py") and name not in files and Path(path).parent.as_posix() == rel:
                files[name] = _read(name, git(root, "show", f"{merge_with}:{path}"), unreadable)
    return files, unreadable


def check(files: Revisions, unreadable: dict[str, str] | None = None) -> list[str]:
    problems = [f"{name}: {why}" for name, why in sorted((unreadable or {}).items())]
    by_rev: dict[str, list[str]] = {}
    for name, (rev, _) in files.items():
        if name in (unreadable or {}):
            continue
        if rev is None:
            if name != "__init__.py":
                problems.append(f"{name}: no literal `revision`")
            continue
        by_rev.setdefault(rev, []).append(name)
    for rev, names in by_rev.items():
        if len(names) > 1:
            problems.append(f"revision {rev} is defined in {len(names)} files: {', '.join(sorted(names))}")
    revised = set()
    for name, (_rev, downs) in files.items():
        for d in downs:
            revised.add(d)
            if d not in by_rev:
                problems.append(f"{name}: down_revision {d} does not exist in this tree")
    heads = sorted(r for r in by_rev if r not in revised)
    if len(heads) != 1:
        where = "; ".join(f"{r} ({', '.join(sorted(by_rev[r]))})" for r in heads) or "none"
        problems.append(
            f"expected exactly one head, found {len(heads)}: {where}. "
            "Set the down_revision of the newer migration(s) to the head of main and keep one head"
        )
    return problems


def out(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: the current directory)")
    ap.add_argument("--merge-with", help="git ref of the target branch, e.g. origin/main")
    a = ap.parse_args(argv)
    root = Path(a.root)
    found = trees(root)
    if not found:
        out("no Alembic tree found; nothing to check")
        return 0
    bad = 0
    for tree in found:
        try:
            files, unreadable = collect(tree, root, a.merge_with)
        except (RuntimeError, ValueError, OSError) as exc:
            sys.stderr.write(f"{tree}: cannot read ({exc})\n")
            return 2
        problems = check(files, unreadable)
        state = "OK" if not problems else f"{len(problems)} problem(s)"
        out(f"{tree.as_posix()}: {len(files)} version file(s), {state}")
        for p in problems:
            out(f"  - {p}")
        bad += bool(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
