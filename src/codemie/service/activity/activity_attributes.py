# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Flat change-diff attributes for activity events."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID


def _normalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value


def build_change_attributes(
    previous: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    value_fields: Iterable[str],
    name_only_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Diff `new` against `previous` into `updated_fields` plus `previous_<f>`/`new_<f>` pairs.

    Only keys of `new` listed in `value_fields` or `name_only_fields` are compared.
    Name-only fields (free text, emails) are reported by name, never by value.
    """
    value_set = set(value_fields)
    tracked = value_set | set(name_only_fields)
    changed: list[str] = []
    values: dict[str, Any] = {}
    for field, raw_new in new.items():
        if field not in tracked:
            continue
        old_value = _normalize(previous.get(field))
        new_value = _normalize(raw_new)
        if old_value == new_value:
            continue
        changed.append(field)
        if field in value_set:
            values[f"previous_{field}"] = old_value
            values[f"new_{field}"] = new_value
    return {"updated_fields": sorted(changed), **values}
