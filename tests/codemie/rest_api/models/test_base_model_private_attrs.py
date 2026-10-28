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

"""Private attributes on rows loaded from the database (SQLAlchemy skips __init__ on load)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest
from pydantic import PrivateAttr
from sqlalchemy import Engine, create_engine
from sqlmodel import Field, SQLModel

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class _PrivateAttrTestModel(BaseModelWithSQLSupport, table=True):
    __tablename__ = "private_attr_test_model"

    name: str | None = Field(default=None)
    _runtime_note: Optional[str] = PrivateAttr(default=None)
    _runtime_count: int = PrivateAttr(default=7)
    _runtime_items: list[str] = PrivateAttr(default_factory=list)
    _runtime_required: int = PrivateAttr()


@pytest.fixture()
def sqlite_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine, tables=[_PrivateAttrTestModel.__table__])
    return engine


@pytest.fixture()
def loaded(sqlite_engine) -> _PrivateAttrTestModel:
    with patch.object(_PrivateAttrTestModel, "get_engine", return_value=sqlite_engine):
        _PrivateAttrTestModel(id="row-1", name="stored").save()
        return _PrivateAttrTestModel.get_by_id("row-1")


def test_loaded_row_reads_private_attr_defaults(loaded):
    assert loaded.name == "stored"
    assert getattr(loaded, "_runtime_note", None) is None
    assert loaded._runtime_count == 7


def test_loaded_row_calls_private_attr_default_factory(loaded):
    assert loaded._runtime_items == []


def test_loaded_rows_do_not_share_default_factory_values(sqlite_engine):
    with patch.object(_PrivateAttrTestModel, "get_engine", return_value=sqlite_engine):
        _PrivateAttrTestModel(id="row-a").save()
        _PrivateAttrTestModel(id="row-b").save()
        first = _PrivateAttrTestModel.get_by_id("row-a")
        second = _PrivateAttrTestModel.get_by_id("row-b")

    first._runtime_items.append("only-first")

    assert second._runtime_items == []


def test_loaded_row_private_attr_without_default_is_unset(loaded):
    with pytest.raises(AttributeError):
        _ = loaded._runtime_required


def test_loaded_row_accepts_private_attr_assignment(loaded):
    loaded._runtime_note = "set after load"

    assert loaded._runtime_note == "set after load"


def test_constructed_model_private_attrs_unchanged():
    model = _PrivateAttrTestModel(id="row-2", name="built")

    assert model._runtime_note is None
    assert model._runtime_count == 7
    assert model._runtime_items == []
