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
from typing import Optional, Dict, Any

import pytest
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Field, Column
from sqlalchemy.dialects.postgresql import JSONB

from codemie.rest_api.models.base import BaseModelWithSQLSupport


class _UpdateTestModel(BaseModelWithSQLSupport, table=True):
    __tablename__ = "update_test_model"

    name: Optional[str] = Field(default=None)
    created_by: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))


@patch("codemie.rest_api.models.base.get_session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_update_raises_stale_data_error_when_record_deleted(mock_get_engine, mock_get_session):
    model = _UpdateTestModel(id="deleted-id", name="x")
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = None

    with pytest.raises(StaleDataError):
        model.update()

    mock_session.merge.assert_not_called()


@patch("codemie.rest_api.models.base.get_session")
@patch("codemie.clients.postgres.PostgresClient.get_engine")
def test_update_proceeds_normally_for_existing_record(mock_get_engine, mock_get_session):
    model = _UpdateTestModel(id="live-id", name="x")
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = model

    result = model.update()

    mock_session.merge.assert_called_once_with(model)
    mock_session.commit.assert_called_once()
    assert result.id_ == "live-id"
