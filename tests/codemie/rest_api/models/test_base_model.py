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

from unittest.mock import patch, MagicMock
import pytest
from enum import auto
from typing import Optional, Dict, Any

from codemie.rest_api.models.base import BaseModelWithSQLSupport, CamelCaseStrEnum
from codemie.rest_api.models.standard import PostResponse
from sqlmodel import Field, Column
from sqlalchemy.dialects.postgresql import JSONB


def test_camelcase_enum():
    class TestEnumType(CamelCaseStrEnum):
        TEST_ONE = auto()
        TEST_TWO = auto()

    assert TestEnumType.TEST_ONE.value == "TestOne"
    assert TestEnumType.TEST_TWO.value == "TestTwo"


class TestSQLModel(BaseModelWithSQLSupport, table=True):
    """Test model class for SQL support testing"""

    __tablename__ = "test_sql_model"

    name: Optional[str] = Field(default=None)
    created_by: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_get_by_id(mock_get_engine, mock_session_class):
    # Given
    model_id = "test-id"
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = TestSQLModel(id=model_id, name="test")

    # When
    result = TestSQLModel.get_by_id(model_id)

    # Then
    assert result.id == model_id
    mock_session.get.assert_called_once_with(TestSQLModel, model_id)


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_get_by_id_not_found(mock_get_engine, mock_session_class):
    # Given
    model_id = "test-id"
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = None

    # When/Then
    with pytest.raises(KeyError):
        TestSQLModel.get_by_id(model_id)


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_save_new_model(mock_get_engine, mock_session_class):
    # Given
    model = TestSQLModel(name="test")
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # When
    result = model.save()

    # Then
    assert isinstance(result, PostResponse)
    assert model.date is not None
    assert model.update_date is not None
    mock_session.add.assert_called_once_with(model)
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(model)


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_get_all(mock_get_engine, mock_session_class):
    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [
        TestSQLModel(id="1", name="test1"),
        TestSQLModel(id="2", name="test2"),
    ]

    # When
    results = TestSQLModel.get_all(response_class=TestSQLModel)

    # Then
    assert len(results) == 2
    assert all(isinstance(r, TestSQLModel) for r in results)
    mock_session.exec.assert_called_once()


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_delete(mock_get_engine, mock_session_class):
    # Given
    model = TestSQLModel(id="test-id", name="test")
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # When
    result = model.delete()

    # Then
    assert result == {"status": "deleted"}
    mock_session.delete.assert_called_once_with(model)
    mock_session.commit.assert_called_once()


def test_sql_get_field_expression():
    # Given/When
    simple_expr = TestSQLModel.get_field_expression('name')
    nested_expr = TestSQLModel.get_field_expression('created_by.name')
    deep_nested_expr = TestSQLModel.get_field_expression('created_by.user.name')

    # Then
    # Simple column access
    assert str(simple_expr) == 'TestSQLModel.name'

    # Single level JSONB access should use ->> for text extraction
    nested_expr_str = str(nested_expr)
    assert 'test_sql_model.created_by' in nested_expr_str
    assert '->>' in nested_expr_str  # JSONB text extraction operator

    # Multi-level JSONB access should use -> for intermediate paths and ->> for final value
    deep_nested_expr_str = str(deep_nested_expr)
    assert 'test_sql_model.created_by' in deep_nested_expr_str
    assert '->' in deep_nested_expr_str  # JSONB path operator
    assert '->>' in deep_nested_expr_str  # JSONB text extraction operator


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_get_by_fields(mock_get_engine, mock_session_class):
    # Given
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = TestSQLModel(id="1", name="test")

    # When
    result = TestSQLModel.get_by_fields({"name": "test"})

    # Then
    assert result.name == "test"
    mock_session.exec.assert_called_once()


@patch("codemie.rest_api.models.base.Session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_sql_update(mock_get_engine, mock_session_class):
    # Given
    model = TestSQLModel(id="test-id", name="test")
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    old_update_date = model.update_date

    # When
    result = model.update()

    # Then
    assert isinstance(result, PostResponse)
    assert model.update_date is not None
    assert model.update_date != old_update_date
    mock_session.merge.assert_called_once_with(model)
    mock_session.commit.assert_called_once()
