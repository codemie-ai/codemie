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

"""Search relevance: match a term across fields and rank earlier-field matches first."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from sqlalchemy import ColumnElement, case, or_

from codemie.core.db_utils import escape_like_wildcards


class Searchable(Protocol):
    @property
    def name(self) -> str | None: ...

    @property
    def description(self) -> str | None: ...


def search_ranking(text: str, *columns: ColumnElement[str]) -> tuple[ColumnElement[bool], ColumnElement[int]]:
    """Build a case-insensitive substring search over ``columns`` and its relevance rank.

    Returns ``(condition, rank)``: ``condition`` matches rows where any column contains
    ``text``; ``rank`` is the index of the first matching column, so ordering by it puts
    matches in earlier columns first. The caller decides where ``rank`` goes in ORDER BY.
    """
    pattern = f"%{escape_like_wildcards(text)}%"
    matches = [column.ilike(pattern, escape="\\") for column in columns]
    rank = case(*((match, index) for index, match in enumerate(matches)), else_=len(matches))
    return or_(*matches), rank


def rank_by_search[T: Searchable](items: Iterable[T], text: str) -> list[T]:
    """Keep items whose name or description contains ``text``, name matches first."""
    term = text.lower()

    def rank(item: T) -> int | None:
        if term in (item.name or "").lower():
            return 0
        if term in (item.description or "").lower():
            return 1
        return None

    ranked = [(item_rank, item) for item in items if (item_rank := rank(item)) is not None]
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0])]
