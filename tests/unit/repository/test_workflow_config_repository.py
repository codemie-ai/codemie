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


from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.workflow_config_repository import WorkflowConfigRepository

_WORKFLOW_CONFIG_PATCH = "codemie.repository.workflow_config_repository.WorkflowConfig"


@pytest.fixture
def conn():
    connection = MagicMock()
    with patch(_WORKFLOW_CONFIG_PATCH) as workflow_config:
        engine = workflow_config.get_engine.return_value
        engine.begin.return_value.__enter__.return_value = connection
        engine.connect.return_value.__enter__.return_value = connection
        yield connection


def _executed_sql(connection: MagicMock) -> str:
    return str(connection.execute.call_args.args[0])


def _executed_params(connection: MagicMock) -> dict:
    return connection.execute.call_args.args[0].compile().params


def test_get_yaml_config_returns_missing_marker_when_row_absent(conn):
    conn.execute.return_value.first.return_value = None

    assert WorkflowConfigRepository().get_yaml_config("wf-1") == (False, None)


@pytest.mark.parametrize("column_value", ["a: 1", "", None])
def test_get_yaml_config_returns_stored_value(conn, column_value):
    conn.execute.return_value.first.return_value = (column_value,)

    assert WorkflowConfigRepository().get_yaml_config("wf-1") == (True, column_value)


def test_get_yaml_config_selects_only_yaml_config_by_id(conn):
    conn.execute.return_value.first.return_value = None

    WorkflowConfigRepository().get_yaml_config("wf-1")

    sql = _executed_sql(conn)
    assert sql.startswith("SELECT yaml_config FROM workflows")
    assert "WHERE id = :wf_id" in sql
    assert _executed_params(conn) == {"wf_id": "wf-1"}


def test_set_schema_url_updates_only_schema_url_when_different(conn):
    WorkflowConfigRepository().set_schema_url("wf-1", "https://example.com/wf.svg")

    conn.execute.assert_called_once()
    sql = _executed_sql(conn)
    assert "UPDATE workflows SET schema_url = :schema_url" in sql
    assert "WHERE id = :wf_id" in sql
    assert "schema_url IS DISTINCT FROM :schema_url" in sql
    assert "update_date" not in sql
    assert _executed_params(conn) == {"wf_id": "wf-1", "schema_url": "https://example.com/wf.svg"}


def test_set_schema_url_does_not_raise_when_no_rows_updated(conn):
    conn.execute.return_value.rowcount = 0

    assert WorkflowConfigRepository().set_schema_url("wf-1", "https://example.com/wf.svg") is None
