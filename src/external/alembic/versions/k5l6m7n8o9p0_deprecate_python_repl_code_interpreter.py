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

"""Deprecate python_repl_code_interpreter and replace with code_executor in toolkits and workflows

Revision ID: k5l6m7n8o9p0
Revises: 14c6b7e8f9a0
Create Date: 2026-05-29 00:00:00.000000

Replaces tool name 'python_repl_code_interpreter' with 'code_executor' in:

1. toolkits JSONB column of assistants, assistant_configurations, and skills tables:
   - If toolkit contains python_repl_code_interpreter but not code_executor: replaces in-place.
   - If toolkit contains both: removes python_repl_code_interpreter, keeps code_executor.
   - If toolkit is left empty after transform: removes the toolkit entry.

2. workflows table (assistants JSONB + tools JSONB + yaml_config text):
   - assistants JSONB: each assistant's tools[].name field renamed from old to new name.
   - tools JSONB: each item's "tool" field renamed from old to new name.
   - yaml_config: string replacement of "tool: python_repl_code_interpreter" in YAML text.

Downgrade is a no-op: the old tool was removed from prod and cannot be restored.
"""

import json
import logging
import sys
from typing import Sequence, Union

from alembic.context import get_context
from sqlalchemy import text

from codemie_tools.base.models import Tool
from codemie_tools.data_management.code_executor.tools_vars import CODE_EXECUTOR_TOOL

revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, None] = "14c6b7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_OLD_TOOL_NAME = "python_repl_code_interpreter"
_NEW_TOOL_NAME = "code_executor"


def _transform_toolkits(toolkits: list[dict], code_executor_tool: dict) -> list[dict]:
    """Return a new toolkits list with python_repl_code_interpreter replaced/removed.

    Pure function — no DB access, fully unit-testable.
    """
    result = []
    for toolkit in toolkits:
        tools: list[dict] = toolkit.get("tools") or []

        # Drop toolkits with empty tools lists
        if not tools:
            continue

        has_old = any(t.get("name") == _OLD_TOOL_NAME for t in tools)
        if not has_old:
            result.append(toolkit)
            continue

        has_new = any(t.get("name") == code_executor_tool["name"] for t in tools)
        new_tools = []
        for tool in tools:
            if tool.get("name") == _OLD_TOOL_NAME:
                if not has_new:
                    # replace in-place
                    new_tools.append(code_executor_tool)
                    has_new = True  # prevent duplicate if old tool appeared twice
                # else: drop — code_executor already present
            else:
                new_tools.append(tool)

        if new_tools:
            result.append({**toolkit, "tools": new_tools})
        # empty tools list → drop the toolkit entry

    return result


def _migrate_table(conn, table: str, code_executor_tool: dict) -> int:
    """Fetch affected rows, transform, write back. Returns count of updated rows."""
    rows = conn.execute(
        text(f"SELECT id, toolkits FROM {table} WHERE toolkits::text LIKE '%{_OLD_TOOL_NAME}%'")  # noqa: S608
    ).fetchall()

    updated = 0
    for row in rows:
        row_id = row[0]
        toolkits = row[1] if isinstance(row[1], list) else json.loads(row[1])
        new_toolkits = _transform_toolkits(toolkits, code_executor_tool)
        conn.execute(
            text(f"UPDATE {table} SET toolkits = :toolkits WHERE id = :id"),  # noqa: S608
            {"toolkits": json.dumps(new_toolkits), "id": row_id},
        )
        updated += 1

    return updated


def _transform_workflow_tools(tools: list[dict]) -> list[dict]:
    """Return new workflow tools list with python_repl_code_interpreter renamed to code_executor.

    Pure function — no DB access, fully unit-testable.
    """
    return [{**t, "tool": _NEW_TOOL_NAME} if t.get("tool") == _OLD_TOOL_NAME else t for t in tools]


def _transform_workflow_assistants(assistants: list[dict]) -> list[dict]:
    """Rename python_repl_code_interpreter in assistants[].tools[].name. Pure function."""
    result = []
    for assistant in assistants:
        tools = assistant.get("tools") or []
        new_tools = [{**t, "name": _NEW_TOOL_NAME} if t.get("name") == _OLD_TOOL_NAME else t for t in tools]
        result.append({**assistant, "tools": new_tools})
    return result


def _transform_yaml_config(yaml_config: str) -> str:
    """Replace 'tool: python_repl_code_interpreter' in YAML text. Pure function."""
    return yaml_config.replace(f"tool: {_OLD_TOOL_NAME}", f"tool: {_NEW_TOOL_NAME}")


def _migrate_workflows(conn) -> int:
    """Update assistants JSONB, tools JSONB, and yaml_config in workflows where old tool name appears."""
    rows = conn.execute(
        text(  # noqa: S608
            f"SELECT id, assistants, tools, yaml_config FROM codemie.workflows"
            f" WHERE assistants::text LIKE '%{_OLD_TOOL_NAME}%'"
            f" OR tools::text LIKE '%{_OLD_TOOL_NAME}%'"
            f" OR yaml_config LIKE '%{_OLD_TOOL_NAME}%'"
        )
    ).fetchall()

    updated = 0
    for row in rows:
        row_id, assistants_raw, tools_raw, yaml_config = row[0], row[1], row[2], row[3]

        assistants = assistants_raw if isinstance(assistants_raw, list) else json.loads(assistants_raw or "[]")
        new_assistants = (
            _transform_workflow_assistants(assistants) if _OLD_TOOL_NAME in json.dumps(assistants) else assistants
        )

        tools = tools_raw if isinstance(tools_raw, list) else json.loads(tools_raw or "[]")
        new_tools = _transform_workflow_tools(tools) if _OLD_TOOL_NAME in json.dumps(tools) else tools

        new_yaml = _transform_yaml_config(yaml_config) if yaml_config and _OLD_TOOL_NAME in yaml_config else yaml_config

        conn.execute(
            text(  # noqa: S608
                "UPDATE codemie.workflows"
                " SET assistants = :assistants, tools = :tools, yaml_config = :yaml_config"
                " WHERE id = :id"
            ),
            {
                "assistants": json.dumps(new_assistants),
                "tools": json.dumps(new_tools),
                "yaml_config": new_yaml,
                "id": row_id,
            },
        )
        updated += 1

    return updated


def upgrade() -> None:
    """Replace python_repl_code_interpreter with code_executor in toolkits."""
    code_executor_tool = Tool.from_metadata(CODE_EXECUTOR_TOOL).model_dump(exclude={"config_class", "tool_class"})
    code_executor_tool.setdefault("settings", None)
    conn = get_context().bind

    for table in ("codemie.assistants", "codemie.assistant_configurations", "codemie.skills"):
        count = _migrate_table(conn, table, code_executor_tool)
        logger.info("Table %s: updated %d rows", table, count)

    count = _migrate_workflows(conn)
    logger.info("Table codemie.workflows: updated %d rows", count)


def downgrade() -> None:
    """No-op: python_repl_code_interpreter was removed from prod; data cannot be restored."""
    pass
