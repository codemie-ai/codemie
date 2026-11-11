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

"""How one windowed ledger read is rendered: over raw rows, or over the hourly projection.

**A read is described once, as a shape, and rendered from one of two sources.** A shape is the read's
narrowing beyond its time range plus its ordered aggregates, built by `spend_logs_repository`'s own
predicate builders - this module never writes an attribution predicate. `raw_statement` renders it over
`litellm_spend_logs`, exactly as the repository always has. `projected_statement` renders the same shape
over `litellm_spend_hourly` (EPMCDME-15537): a 30-day read then aggregates a few hundred thousand hourly
rows instead of tens of millions of ledger rows, and returns the identical figures.

**Why the projected answer is exact.** Every routed read is a sum of `spend` or a row count, and both are
additive, so a period is answered as the disjoint, half-open union of:

* **its whole hours from the projection** - `hour = date_bin('1 hour', start_time)`, so an hour's rows
  are exactly the ledger rows that started in it;
* **its partial edge hours from raw rows** - at most two, each under an hour.

The metric store's correlation ids are the one input the projection cannot carry. A shape is therefore
built twice: `S1` with the ids, and `S0` with every id set emptied, where `request_resolved` and
`request_unresolved` reduce to plain `false`/`true`. For a row whose id is outside the read's id union `U`
(or NULL), `S0` and `S1` agree, because they differ only in those membership atoms. So every row is first
counted under `S0`; the rows in `U` are then read raw, once with their `S0` image subtracted and once with
their `S1` image added, all in **one statement and so one snapshot**. A resolved row nets to exactly its
`S1` image; every other row is counted once, as `S0`. This is the residue rule of the decision record -
eligible total minus deduplicated matched spend - for every id-bearing selector at once.

**A projected statement binds every value list as one array.** The metric store's id sets reach tens of
thousands of ids, and an expanding `IN` binds one parameter per id - past asyncpg's 32,767 the statement is
refused. So every `IN` / `NOT IN` over a value list becomes `= ANY(:values)` / `<> ALL(:values)` here, one
`text[]` parameter each; the semantics are identical for lists without NULLs, which these are. Raw
statements keep their `IN` lists: they also run on SQLite in the suites, which has no arrays.

**The agreement of `S0` and `S1` outside `U` holds because every id atom sits in a positive position** -
under `AND`/`OR`, never under a `NOT` - where *unknown* and *false* select the same rows. A selector that
negated an id atom would break it for rows whose correlation id is NULL.

**Equal, not byte-identical.** A projected sum equals the raw one exactly, but a cancelled pair can leave a
wider `numeric` scale behind (`1.000000000000` for `1`), and a projected row count arrives as `numeric`
rather than an integer; every caller converts both. The cancellation also assumes finite money: a
`NaN` or infinite `spend` - which the uploader never writes - would not cancel.

**Anything the projection cannot express renders raw, whole.** A predicate naming a column the projection
does not carry - the correlation id in a named execution, a run id - makes
`projected_statement` return `None`, and the caller renders the read raw. A partially translated predicate
is never produced.

The exactness argument is pinned by the deterministic database oracle, not by these unit-level shapes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import cache

from sqlalchemy import (
    BigInteger,
    BindParameter,
    ColumnClause,
    ColumnElement,
    Select,
    Subquery,
    Text,
    all_,
    and_,
    any_,
    bindparam,
    cast,
    false,
    func,
    literal,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.sql.expression import FromClause, SelectBase
from sqlalchemy.sql.visitors import replacement_traverse

from codemie.repository.spend_logs_model import PROJECTED_DIMENSIONS
from codemie.repository.spend_logs_model import LiteLLMSpendHourly as Hourly
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import bucket_origin, bucket_step
from codemie.service.spend.traffic_class import request_resolved

_LEDGER = Row.__table__
_HOUR = bucket_step("hour")
_HOUR_ORIGIN = bucket_origin("hour")
_REQUEST_ID = "codemie_request_id"

#: What a predicate may name on the projection. `spend` and `start_time` are absent on purpose: on the
#: projection they hold a sum and an hour, so a predicate reading either would compare the wrong value.
_HOURLY_COLUMNS: Mapping[str, ColumnElement] = {name: Hourly.c[name] for name in PROJECTED_DIMENSIONS}


class Measure(Enum):
    """What one aggregate counts: money, or ledger rows."""

    SPEND = "spend"
    ROWS = "rows"


@dataclass(frozen=True, eq=False)
class Aggregate:
    """One output column of a read, in the read's column order.

    `selector` is the column's `FILTER`; `None` is **every admitted row** - a bare aggregate, never an
    always-true filter, so a later edit cannot narrow a total without looking like a narrowing.
    """

    measure: Measure = Measure.SPEND
    selector: ColumnElement[bool] | None = None
    label: str | None = None


@dataclass(frozen=True, eq=False)
class ReadShape:
    """One read: its narrowing beyond the time range, its aggregates, and the columns it groups by.

    Each `group_by` entry names a `PROJECTED_DIMENSIONS` column, so either source can group on it. A grouped
    read selects its key columns, labelled by name, ahead of its aggregates.
    """

    where: ColumnElement[bool] | None
    aggregates: tuple[Aggregate, ...]
    group_by: tuple[str, ...] = ()


#: A read's shape with the metric store's ids applied (`True`) or with every id set emptied (`False`).
#: Both calls must yield the same aggregates in the same order: only their predicates may differ. It must
#: also be pure - it may run on a worker thread (`read_statement`). Returning the very same object for both
#: is how a read applying no ids says so, and it saves the projected build a whole traversal.
ShapeOf = Callable[[bool], ReadShape]


class _Untranslatable(Exception):
    """A predicate names a column the target source does not carry."""


def within(period: TimeRange) -> ColumnElement[bool]:
    """The half-open range predicate every read carries.

    Written once so no query can reach for `BETWEEN`, and so adjacent periods tile exactly: no
    row counted twice, none falling between.
    """
    return and_(Row.start_time >= period.start, Row.start_time < period.end)


def time_bucket(width: str, start_time: ColumnElement[datetime] = Row.start_time) -> ColumnElement[datetime]:
    """The expression one bucket width groups on - `date_bin` for every width, `date_trunc` for none.

    **`date_trunc` over a `timestamptz` truncates in the session's own `TimeZone`.** A session set to
    anything but UTC would therefore file rows under keys the walk - which is pure UTC arithmetic -
    never emits, and every bucket on the chart would read as a measured zero while real money is
    dropped, with nothing raising. `date_bin(stride, ts, origin)` is instant arithmetic from an
    absolute origin, so no session setting can move a boundary. It needs **PostgreSQL 14+**, taken as
    safe because PostgreSQL 13 reached end of life in 2025.

    The stride and the origin are the port's own `bucket_step` and `bucket_origin`, which is the whole
    point of reading them from there: the walk that decides which buckets a chart *has* aligns on those
    same two values by the same formula, and a grouping keyed a partial step off it matches none of the
    walk's keys. A week binds the epoch's first Monday rather than the epoch itself, so it lands on the
    ISO Monday every chart drawn so far already shows.

    `start_time` is the column binned: a raw row's own start, or a projected row's hour. Every width is
    a whole number of hours from an hour-aligned origin, so an hour and every row that started in it
    fall in the same bucket.
    """
    return func.date_bin(bucket_step(width), start_time, bucket_origin(width)).label("bucket")


def raw_statement(period: TimeRange, shape: ReadShape, width: str | None = None) -> Select:
    """The read over `litellm_spend_logs` itself - the statement the repository has always issued."""
    keys = [getattr(Row, name) for name in shape.group_by]
    columns = [_labelled(key, name) for name, key in zip(shape.group_by, keys, strict=True)]
    columns += [_labelled(_raw_column(aggregate), aggregate.label) for aggregate in shape.aggregates]
    bounded = within(period) if shape.where is None else and_(within(period), shape.where)
    if width is None:
        return select(*columns).where(bounded).group_by(*keys)
    bucket = time_bucket(width)
    return select(bucket, *columns).where(bounded).group_by(bucket, *keys).order_by(bucket)


def projected_statement(
    period: TimeRange,
    shape_of: ShapeOf,
    resolved_ids: tuple[str, ...] = (),
    width: str | None = None,
) -> Select | None:
    """The same read over the hourly projection, or `None` when it must be answered from raw rows.

    `None` when the period holds no whole hour - raw rows are then the whole answer anyway - when a
    bucket width is not a whole number of hours from an hour-aligned origin, or when any predicate names
    a column the projection cannot carry. `resolved_ids` must cover every id set the shape applies - a
    resolved row outside it would be counted under its unresolved image - and a shape that applies an id
    outside it is refused with `ValueError` rather than answered. A `shape_of` returning one object for both
    images declares that it applies none: it skips that check, and an id atom it carries anyway renders the
    read raw.
    """
    hours = whole_hours(period)
    if hours is None or (width is not None and not _hour_aligned(width)):
        return None
    try:
        return _projected(period, hours, shape_of, resolved_ids, width)
    except _Untranslatable:
        return None


def read_statement(
    period: TimeRange, shape_of: ShapeOf, resolved_ids: tuple[str, ...] = (), width: str | None = None
) -> Select:
    """The read over the hourly projection where that is exact, else over raw rows.

    Session-free and pure, so a caller may build it off the event loop: a projected statement translates
    every predicate onto three sources, several times the CPU of the raw one.
    """
    shape_of = cache(shape_of)  # each image is built once, whichever source renders it
    statement = projected_statement(period, shape_of, resolved_ids, width)
    return raw_statement(period, shape_of(True), width) if statement is None else statement


def identities_statement(period: TimeRange, projection: bool) -> Select:
    """The distinct `(attr_user_id, attr_principal)` pairs the rows of `period` carry.

    A person-keyed holder whose user id and aliases appear in none of these pairs holds no row in the period,
    so its figure is a measured zero that needs no pricing. With `projection`, whole hours are read from the
    projection's group keys - exactly the pairs of the rows each hour holds, since the triggers drop a group
    its last row left - and the partial edge hours from raw rows. A projection missing a pair would have
    mis-summed the holder anyway, because every person predicate translates onto it; this read relies on that
    and adds no failure mode of its own.
    """
    identities = (Row.attr_user_id, Row.attr_principal)
    hours = whole_hours(period) if projection else None
    if hours is None:
        return select(*identities).where(within(period)).distinct()
    branches = [
        select(Hourly.c.attr_user_id, Hourly.c.attr_principal).where(
            and_(Hourly.c.hour >= hours.start, Hourly.c.hour < hours.end)
        )
    ]
    branches += [select(*identities).where(within(edge)) for edge in raw_edges(period, hours)]
    window = union_all(*branches).subquery("window_identities")
    return select(window.c.attr_user_id, window.c.attr_principal).distinct()


def whole_hours(period: TimeRange) -> TimeRange | None:
    """The whole UTC hours inside `period`, or `None` when it contains none."""
    start, end = _ceil_hour(period.start), _floor_hour(period.end)
    return TimeRange(start, end) if start < end else None


def raw_edges(period: TimeRange, hours: TimeRange) -> list[TimeRange]:
    """The parts of `period` outside its whole hours - at most two, each shorter than an hour."""
    edges = []
    if period.start < hours.start:
        edges.append(TimeRange(period.start, hours.start))
    if hours.end < period.end:
        edges.append(TimeRange(hours.end, period.end))
    return edges


def _projected(
    period: TimeRange, hours: TimeRange, shape_of: ShapeOf, resolved_ids: tuple[str, ...], width: str | None
) -> Select:
    unresolved = shape_of(False)
    resolved = _resolved_image(unresolved, shape_of(True), resolved_ids)
    window = _window(period, hours, unresolved, resolved, resolved_ids)
    return _grouped(window, _window_columns(window, unresolved, resolved), width, unresolved.group_by)


def _resolved_image(unresolved: ReadShape, resolved: ReadShape, resolved_ids: tuple[str, ...]) -> ReadShape | None:
    """The resolved image the window adds back, or None when the read has none."""
    if resolved is unresolved:
        # One shape serves both images, so there is no resolved image to add back, and the id pass below - a
        # whole extra traversal of every predicate, the largest part of a holder batch's build - has nothing to
        # check: an id atom in this shape names a column the projection does not carry, so translating it
        # fails below and the read renders raw, whole.
        return None
    if _layout(resolved) != _layout(unresolved):
        raise ValueError("a read shape's aggregates must not depend on the metric store's ids")
    applied: set[str] = set()
    for clause in (resolved.where, *(aggregate.selector for aggregate in resolved.aggregates)):
        if clause is not None:
            _onto(clause, request_ids=applied)
    if not applied <= set(resolved_ids):
        raise ValueError("resolved_ids must cover every correlation id the read applies")
    return resolved if resolved_ids else None


def _window(
    period: TimeRange,
    hours: TimeRange,
    unresolved: ReadShape,
    resolved: ReadShape | None,
    resolved_ids: tuple[str, ...],
) -> Subquery:
    hour_where = None if unresolved.where is None else _onto(unresolved.where, _HOURLY_COLUMNS)
    ledger_where = None if unresolved.where is None else _onto(unresolved.where)
    branches = [_hour_rows(hours, hour_where)]
    branches += [_ledger_rows(within(edge), ledger_where, _Image.EDGE) for edge in raw_edges(period, hours)]
    if resolved is not None:
        # One clause object in both branches, so the id set is one array parameter however large.
        matched = _onto(and_(within(period), request_resolved(resolved_ids)))
        resolved_where = None if resolved.where is None else _onto(resolved.where)
        branches.append(_ledger_rows(matched, ledger_where, _Image.CANCELLED))
        branches.append(_ledger_rows(matched, resolved_where, _Image.RESOLVED))
    return union_all(*branches).subquery("window_rows")


def _window_columns(window: Subquery, unresolved: ReadShape, resolved: ReadShape | None) -> list[ColumnElement]:
    unresolved_columns = {name: window.c[name] for name in PROJECTED_DIMENSIONS}
    resolved_columns = {**unresolved_columns, _REQUEST_ID: window.c[_REQUEST_ID]}
    columns = []
    for index, aggregate in enumerate(unresolved.aggregates):
        selector = None if aggregate.selector is None else _onto(aggregate.selector, unresolved_columns)
        if resolved is not None:
            resolved_selector = resolved.aggregates[index].selector
            selector = _either_image(
                window,
                selector,
                None if resolved_selector is None else _onto(resolved_selector, resolved_columns),
            )
        measured = window.c.row_count if aggregate.measure is Measure.ROWS else window.c.spend
        columns.append(_labelled(func.coalesce(_filtered(func.sum(measured), selector), 0), aggregate.label))
    return columns


def _grouped(window: Subquery, columns: list[ColumnElement], width: str | None, group_by: tuple[str, ...]) -> Select:
    keys = [window.c[name] for name in group_by]
    bucket = None if width is None else time_bucket(width, window.c.start_time)
    grouping = keys if bucket is None else [bucket, *keys]
    leading = [_labelled(key, name) for name, key in zip(group_by, keys, strict=True)]
    statement = select(*([] if bucket is None else [bucket]), *leading, *columns).select_from(window)
    if not grouping:
        return statement
    # Raw grouping returns a group only where some row satisfies the read; the window's unfiltered net row
    # count is exactly that number, so a group left only by a cancelled pair - or none at all - is dropped.
    statement = statement.group_by(*grouping).having(func.sum(window.c.row_count) > 0)
    return statement if bucket is None else statement.order_by(bucket)


def _hour_rows(hours: TimeRange, where: ColumnElement[bool] | None) -> Select:
    """Branch B1: the projection's whole hours, under the shape's unresolved narrowing."""
    bounded = and_(Hourly.c.hour >= hours.start, Hourly.c.hour < hours.end)
    return select(
        Hourly.c.spend,
        Hourly.c.row_count,
        Hourly.c.hour.label("start_time"),
        *(Hourly.c[name] for name in PROJECTED_DIMENSIONS),
        cast(null(), Text).label(_REQUEST_ID),
        false().label("resolved"),
    ).where(bounded if where is None else and_(bounded, where))


class _Image(Enum):
    """Which image of raw ledger rows a branch carries: `(sign, resolved)`."""

    EDGE = (1, False)  # B2: a partial edge hour, judged by the unresolved shape
    CANCELLED = (-1, False)  # B3: a resolved row's unresolved image, subtracted
    RESOLVED = (1, True)  # B4: the same row's resolved image, added back


def _ledger_rows(bounded: ColumnElement[bool], where: ColumnElement[bool] | None, image: _Image) -> Select:
    """Raw ledger rows in the window's shape, as one `_Image`.

    Only a resolved image carries its correlation id; every other row is judged by the unresolved shape,
    which names none.
    """
    sign, resolved = image.value
    return select(
        (Row.spend if sign > 0 else -Row.spend).label("spend"),
        literal(sign, BigInteger).label("row_count"),
        Row.start_time.label("start_time"),
        *(getattr(Row, name) for name in PROJECTED_DIMENSIONS),
        (Row.codemie_request_id if resolved else cast(null(), Text)).label(_REQUEST_ID),
        (true() if resolved else false()).label("resolved"),
    ).where(bounded if where is None else and_(bounded, where))


def _either_image(
    window: Subquery, unresolved: ColumnElement[bool] | None, resolved: ColumnElement[bool] | None
) -> ColumnElement[bool] | None:
    """One `FILTER` for a window holding both images: the unresolved selector on unresolved rows, and so on."""
    if unresolved is None and resolved is None:
        return None
    flag = window.c.resolved
    return or_(
        and_(~flag, true() if unresolved is None else unresolved),
        and_(flag, true() if resolved is None else resolved),
    )


def _onto(
    clause: ColumnElement[bool],
    columns: Mapping[str, ColumnElement] | None = None,
    *,
    request_ids: set[str] | None = None,
) -> ColumnElement[bool]:
    """`clause` for a projected statement: value lists bound as arrays, ledger columns optionally renamed.

    With `columns`, every ledger column is replaced by the same-named column of that source; without, the
    clause stays on the ledger and only its value lists change. A ledger column the target does not carry,
    a column of any other table or of none, and a subquery all raise - a partially translated predicate
    would silently compare the wrong rows, or pull the ledger back in as an implicit cross join.
    `request_ids`, when given, collects every correlation id the clause tests membership of - by value
    list, the only way the predicate builders test it (`request_resolved` / `request_unresolved`).

    `replacement_traverse` is documented as internal SQLAlchemy API; this is its one use, pinned by tests.
    """
    translation = _Translation(columns, request_ids)
    translated = replacement_traverse(clause, {}, translation)
    if translation.missing:
        raise _Untranslatable(", ".join(sorted(translation.missing)))
    return translated


class _Translation:
    """The `replacement_traverse` callback of `_onto`, collecting every element it cannot translate."""

    def __init__(self, columns: Mapping[str, ColumnElement] | None, request_ids: set[str] | None) -> None:
        self.columns = columns
        self.request_ids = request_ids
        self.missing: list[str] = []

    def __call__(self, element: object) -> ColumnElement | None:
        if isinstance(element, (SelectBase, FromClause)):
            self.missing.append(f"subquery {element}")
            return None
        if is_value_list(element):
            return self._value_list(element)
        if not isinstance(element, ColumnClause):
            return None
        if element.table is None:
            self.missing.append(f"column {element.name} of no table")
            return None
        if element.table is not _LEDGER:
            self.missing.append(f"{element.table}.{element.name}")
            return None
        return None if self.columns is None else self._column_for(element)

    def _value_list(self, element: BinaryExpression) -> ColumnElement:
        if self.request_ids is not None and element.left.name == _REQUEST_ID:
            self.request_ids.update(element.right.value)
        left = element.left if self.columns is None else self._column_for(element.left)
        values = bindparam(None, list(element.right.value), type_=ARRAY(Text))
        return left == any_(values) if element.operator is operators.in_op else left != all_(values)

    def _column_for(self, element: ColumnClause) -> ColumnElement | None:
        target = self.columns.get(element.name) if element.table is _LEDGER else None
        if target is None:
            self.missing.append(f"{element.table}.{element.name}")
        return target


def is_value_list(element: object) -> bool:
    """`<ledger column> IN | NOT IN <list>` - the shape a value list is bound in, one parameter per value."""
    return (
        isinstance(element, BinaryExpression)
        and element.operator in (operators.in_op, operators.not_in_op)
        and is_ledger_column(element.left)
        and isinstance(element.right, BindParameter)
        and element.right.expanding
    )


def is_ledger_column(element: object) -> bool:
    return isinstance(element, ColumnClause) and element.table is _LEDGER


def _layout(shape: ReadShape) -> list[tuple[Measure, str | None]]:
    return [(aggregate.measure, aggregate.label) for aggregate in shape.aggregates]


def _hour_aligned(width: str) -> bool:
    """Whether every bucket of `width` is a union of whole projection hours."""
    return bucket_step(width) % _HOUR == timedelta(0) and (bucket_origin(width) - _HOUR_ORIGIN) % _HOUR == timedelta(0)


def _raw_column(aggregate: Aggregate) -> ColumnElement:
    if aggregate.measure is Measure.ROWS:
        return _filtered(func.count(), aggregate.selector)
    return func.coalesce(_filtered(func.sum(Row.spend), aggregate.selector), 0)


def _filtered(aggregate: ColumnElement, selector: ColumnElement[bool] | None) -> ColumnElement:
    return aggregate if selector is None else aggregate.filter(selector)


def _labelled(column: ColumnElement, label: str | None) -> ColumnElement:
    return column if label is None else column.label(label)


def _floor_hour(moment: datetime) -> datetime:
    return _HOUR_ORIGIN + ((moment - _HOUR_ORIGIN) // _HOUR) * _HOUR


def _ceil_hour(moment: datetime) -> datetime:
    floor = _floor_hour(moment)
    return floor if floor == moment else floor + _HOUR
