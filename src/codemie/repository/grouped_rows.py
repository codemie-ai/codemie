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

"""Which rows of a grouped ledger read an attribution predicate selects, evaluated in Python.

A grouped read returns one row per distinct key, and each filter's own predicate tree is evaluated over
those rows rather than written into the statement. Only `=`, `IN`, `AND`, `OR`, `true()` and `false()`
over projected ledger columns are understood; anything else raises `UnmatchablePredicate`, because a
guessed match would assign money to the wrong filter with nothing to show for it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence

from sqlalchemy import BindParameter, ColumnClause, ColumnElement
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, False_, Grouping, True_
from sqlalchemy.sql.visitors import iterate

from codemie.repository.spend_logs_model import PROJECTED_DIMENSIONS
from codemie.repository.spend_projection import is_ledger_column, is_value_list

_AND, _OR, _MEMBER, _EVERY_ROW, _NO_ROW = "and", "or", "in", "true", "false"


class UnmatchablePredicate(ValueError):
    """A predicate `GroupedRows` cannot evaluate exactly; refusing it fails the read instead of mis-assigning money."""


def grouping_columns(predicates: Iterable[ColumnElement[bool] | None]) -> tuple[str, ...]:
    """The projected ledger columns `predicates` name, in `PROJECTED_DIMENSIONS` order: the key a grouped read needs."""
    named: set[str] = set()
    for predicate in predicates:
        for element in () if predicate is None else iterate(predicate):
            if not isinstance(element, ColumnClause):
                continue
            if not is_ledger_column(element) or element.name not in PROJECTED_DIMENSIONS:
                raise UnmatchablePredicate(f"{element} is not a projected ledger column")
            named.add(element.name)
    return tuple(name for name in PROJECTED_DIMENSIONS if name in named)


class GroupedRows:
    """Which grouped rows a predicate tree selects, evaluated set-wise over one index per key column.

    The same tree selects the same rows here as `WHERE` does: a NULL key value is left out of its column's
    index, so it matches no `=` or `IN`, and `NOT` is refused rather than evaluated two-valued. Terms are
    memoised on their structure, so one shared by many filters is looked up once. An AND looks up its most
    selective `=`/`IN` operand and tests the others only on the rows that one selected, so a broad operand
    costs the narrow one's matches. `visits` counts the row indexes touched, which is what a read costs.
    """

    def __init__(self, rows: Sequence[Sequence[object]], key: tuple[str, ...]) -> None:
        self._every_row = frozenset(range(len(rows)))
        self._position = {column: position for position, column in enumerate(key)}
        self._keys = [tuple(row[: len(key)]) for row in rows]
        self._index = _index_by_column(rows, key)
        self._memo: dict[tuple, frozenset[int]] = {}
        self.visits = 0

    def matching(self, predicate: ColumnElement[bool] | None) -> frozenset[int]:
        return self._every_row if predicate is None else self._rows_of(_structural_key(predicate))

    def _rows_of(self, term: tuple) -> frozenset[int]:
        if term not in self._memo:
            self._memo[term] = self._compute(term)
        return self._memo[term]

    def _compute(self, term: tuple) -> frozenset[int]:
        match term[0]:
            case "true":
                return self._every_row
            case "false":
                return frozenset()
            case "and":
                return self._conjunction(term[1])
            case "or":
                return self._union([self._rows_of(child) for child in term[1]])
        return self._lookup(term[1], term[2])

    def _lookup(self, column: str, values: frozenset[object]) -> frozenset[int]:
        found = frozenset().union(*(self._column_index(column).get(value, frozenset()) for value in values))
        self.visits += len(found)
        return found

    def _column_index(self, column: str) -> dict[object, frozenset[int]]:
        if column not in self._index:
            raise UnmatchablePredicate(f"{column} is not in the group key")
        return self._index[column]

    def _conjunction(self, operands: Collection[tuple]) -> frozenset[int]:
        for operand in operands:
            self._require_key(operand)
        seeds = [operand for operand in operands if operand[0] == _MEMBER or operand in self._memo]
        if not seeds:
            return self._intersection([self._rows_of(operand) for operand in operands])
        seed = self._most_selective(seeds)
        candidates = self._rows_of(seed)
        rest = sorted((operand for operand in operands if operand != seed), key=lambda op: op not in self._memo)
        for operand in rest:
            if not candidates:
                break
            if operand in self._memo:
                self.visits += min(len(candidates), len(self._memo[operand]))
                candidates &= self._memo[operand]
            else:
                self.visits += len(candidates)
                candidates = frozenset(row for row in candidates if self._holds(operand, row))
        return candidates

    def _most_selective(self, seeds: list[tuple]) -> tuple:
        """The seed selecting the fewest rows, sized without touching a row - and an `IN` only until it loses."""
        best, best_size = seeds[0], None
        for seed in sorted(seeds, key=self._sizing_order):
            size = self._size_at_most(seed, best_size)
            if best_size is None or size < best_size:
                best, best_size = seed, size
        return best

    def _sizing_order(self, term: tuple) -> tuple[bool, int]:
        return (term not in self._memo, len(term[2]) if term[0] == _MEMBER else 0)

    def _size_at_most(self, term: tuple, bound: int | None) -> int:
        if term in self._memo:
            return len(self._memo[term])
        by_value, size = self._column_index(term[1]), 0
        for value in term[2]:
            size += len(by_value.get(value, ()))
            if bound is not None and size >= bound:
                break
        return size

    def _holds(self, term: tuple, row: int) -> bool:
        match term[0]:
            case "true":
                return True
            case "false":
                return False
            case "and":
                return all(self._holds(child, row) for child in term[1])
            case "or":
                return any(self._holds(child, row) for child in term[1])
        value = self._keys[row][self._position[term[1]]]
        return value is not None and value in term[2]

    def _require_key(self, term: tuple) -> None:
        """Refuses a term naming a column outside the key even where no candidate row would reach it."""
        if term[0] == _MEMBER:
            self._column_index(term[1])
        elif term[0] in (_AND, _OR):
            for child in term[1]:
                self._require_key(child)

    def _intersection(self, operands: list[frozenset[int]]) -> frozenset[int]:
        smallest, *rest = sorted(operands, key=len) or [self._every_row]
        for operand in rest:
            if not smallest:
                break
            self.visits += min(len(smallest), len(operand))
            smallest &= operand
        return smallest

    def _union(self, operands: list[frozenset[int]]) -> frozenset[int]:
        self.visits += sum(map(len, operands))
        return frozenset().union(*operands)


def _index_by_column(rows: Sequence[Sequence[object]], key: tuple[str, ...]) -> dict[str, dict[object, frozenset[int]]]:
    """Per key column, `value -> row indexes`; the key columns lead each row, and NULLs are left out."""
    positions: list[defaultdict[object, list[int]]] = [defaultdict(list) for _ in key]
    for row_index, row in enumerate(rows):
        for by_value, value in zip(positions, row, strict=False):
            if value is not None:
                by_value[value].append(row_index)
    return {
        column: {value: frozenset(indexes) for value, indexes in by_value.items()}
        for column, by_value in zip(key, positions, strict=True)
    }


def _ungrouped(node: ColumnElement[bool]) -> ColumnElement[bool]:
    while isinstance(node, Grouping):
        node = node.element
    return node


def _structural_key(node: ColumnElement[bool]) -> tuple:
    """What a term means, independent of how it was built: `x = 'a'` and `x IN ('a')` are one term."""
    node = _ungrouped(node)
    if isinstance(node, True_):
        return (_EVERY_ROW,)
    if isinstance(node, False_):
        return (_NO_ROW,)
    if isinstance(node, BooleanClauseList):
        return (_AND if node.operator is operators.and_ else _OR, frozenset(map(_structural_key, node.clauses)))
    if is_value_list(node) and node.operator is operators.in_op:
        return (_MEMBER, node.left.name, frozenset(node.right.value))
    if _is_equality(node):
        return (_MEMBER, node.left.name, frozenset((node.right.value,)))
    raise UnmatchablePredicate(f"cannot match {node}")


def _is_equality(element: object) -> bool:
    """`<ledger column> = <bound value>`."""
    return (
        isinstance(element, BinaryExpression)
        and element.operator is operators.eq
        and is_ledger_column(element.left)
        and isinstance(element.right, BindParameter)
        and not element.right.expanding
    )
