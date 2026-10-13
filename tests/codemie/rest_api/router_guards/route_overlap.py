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

"""Exact overlap test for Starlette path templates and the all-pairs scan built on it.

Two templates overlap when one concrete URL path matches both. Starlette compiles ``{x}`` to
``[^/]+`` (one or more non-slash characters) and ``{x:path}`` to ``.*`` (anything, empty and ``/``
included). The matcher intersects the two patterns exactly with a reachability search over pairs of
pattern positions. Any other converter (``int``, ``uuid``...) is treated as ``{x}``, which can
over-report an overlap but never misses one.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from itertools import islice
from typing import Any, NamedTuple

_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")
_PATH_CONVERTOR = "path"


class OverlapPair(NamedTuple):
    """Two route templates of one method that can match the same URL, ``earlier`` registered first."""

    method: str
    earlier: str
    later: str


class _CharSet(Enum):
    LITERAL = "literal"
    NON_SLASH = "non-slash"
    ANY = "any"


@dataclass(frozen=True)
class _Atom:
    """One element of a compiled pattern: a character set, optionally repeated zero or more times."""

    chars: _CharSet
    literal: str = ""
    repeats: bool = False


@functools.cache
def _compile(path: str) -> tuple[_Atom, ...]:
    atoms: list[_Atom] = []
    position = 0
    for match in _PARAM.finditer(path):
        atoms.extend(_Atom(_CharSet.LITERAL, char) for char in path[position : match.start()])
        if match.group(2) == _PATH_CONVERTOR:
            atoms.append(_Atom(_CharSet.ANY, repeats=True))
        else:
            atoms.extend((_Atom(_CharSet.NON_SLASH), _Atom(_CharSet.NON_SLASH, repeats=True)))
        position = match.end()
    atoms.extend(_Atom(_CharSet.LITERAL, char) for char in path[position:])
    return tuple(atoms)


def _share_a_character(first: _Atom, second: _Atom) -> bool:
    if _CharSet.ANY in (first.chars, second.chars):
        return True
    if first.chars is _CharSet.LITERAL and second.chars is _CharSet.LITERAL:
        return first.literal == second.literal
    literal = first if first.chars is _CharSet.LITERAL else second
    if literal.chars is _CharSet.LITERAL:
        return literal.literal != "/"
    return True


def _after(atom: _Atom, at: int) -> int:
    return at if atom.repeats else at + 1


def _next_positions(
    left: tuple[_Atom, ...], right: tuple[_Atom, ...], left_at: int, right_at: int
) -> Iterable[tuple[int, int]]:
    left_atom = left[left_at] if left_at < len(left) else None
    right_atom = right[right_at] if right_at < len(right) else None
    if left_atom and left_atom.repeats:
        yield left_at + 1, right_at
    if right_atom and right_atom.repeats:
        yield left_at, right_at + 1
    if left_atom and right_atom and _share_a_character(left_atom, right_atom):
        yield _after(left_atom, left_at), _after(right_atom, right_at)


def paths_overlap(first: str, second: str) -> bool:
    """Return True when some concrete URL path matches both templates."""
    left, right = _compile(first), _compile(second)
    start = (0, 0)
    visited = {start}
    pending = [start]
    while pending:
        left_at, right_at = pending.pop()
        if left_at == len(left) and right_at == len(right):
            return True
        for position in _next_positions(left, right, left_at, right_at):
            if position not in visited:
                visited.add(position)
                pending.append(position)
    return False


@functools.cache
def _literal_prefix(path: str) -> tuple[str, ...]:
    """Leading path segments free of parameters; they are aligned with any other template's segments."""
    prefix: list[str] = []
    for segment in path.split("/"):
        if "{" in segment:
            break
        prefix.append(segment)
    return tuple(prefix)


def _literal_prefixes_differ(first: str, second: str) -> bool:
    return any(a != b for a, b in zip(_literal_prefix(first), _literal_prefix(second)))


def overlapping_pairs(routes: Iterable[Mapping[str, Any]]) -> list[OverlapPair]:
    """List every same-method pair of overlapping templates, in route registration order.

    ``routes`` are ``{"path": ..., "methods": [...]}`` mappings in registration order. A pair keeps the
    order of its routes: a generic route registered first is reported as ``earlier`` and not corrected.
    A repeated (method, path) collapses to its first occurrence.
    """
    entries_by_method: dict[str, list[tuple[int, str]]] = {}
    seen: set[tuple[str, str]] = set()
    for index, route in enumerate(routes):
        for method in sorted(route["methods"]):
            if (method, route["path"]) not in seen:
                seen.add((method, route["path"]))
                entries_by_method.setdefault(method, []).append((index, route["path"]))

    found: list[tuple[int, int, OverlapPair]] = []
    for method, entries in entries_by_method.items():
        for position, (earlier_index, earlier) in enumerate(entries):
            for later_index, later in islice(entries, position + 1, None):
                if not _literal_prefixes_differ(earlier, later) and paths_overlap(earlier, later):
                    found.append((earlier_index, later_index, OverlapPair(method, earlier, later)))
    return [pair for _, _, pair in sorted(found, key=lambda item: (item[0], item[1], item[2].method))]
