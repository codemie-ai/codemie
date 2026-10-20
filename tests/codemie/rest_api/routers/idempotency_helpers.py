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


"""Shared helpers for idempotent-write (re-PUT of an unchanged document) tests."""

from typing import Any, Iterable

DEFAULT_IGNORED_KEYS = ("update_date", "updated_date", "last_reindex_date")


def _strip_ignored(value: Any, ignore: frozenset[str]) -> Any:
    """Return a copy of ``value`` with every ignored key removed at any depth."""
    if isinstance(value, dict):
        return {k: _strip_ignored(v, ignore) for k, v in value.items() if k not in ignore}
    if isinstance(value, (list, tuple)):
        return [_strip_ignored(item, ignore) for item in value]
    return value


def assert_stored_unchanged(before: dict, after: dict, ignore: Iterable[str] = DEFAULT_IGNORED_KEYS) -> None:
    """Assert two stored-entity snapshots are equal, ignoring volatile timestamp keys (recursively)."""
    ignored = frozenset(ignore)
    stripped_before = _strip_ignored(before, ignored)
    stripped_after = _strip_ignored(after, ignored)
    assert (
        stripped_after == stripped_before
    ), f"Stored entity changed on re-PUT.\nbefore={stripped_before!r}\nafter={stripped_after!r}"
