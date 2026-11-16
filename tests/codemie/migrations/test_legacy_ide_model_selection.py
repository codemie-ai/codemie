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

"""Legacy IDE eligibility backfill, including an opt-in isolated PostgreSQL check."""

import importlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB


MIGRATION = "external.alembic.versions.d4f8a2c691be_mark_legacy_ide_model_selections"
MARKER = "legacy_ide_model_selection_pending"


def test_migration_belongs_to_the_single_alembic_chain():
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"))
    migration = importlib.import_module(MIGRATION)
    heads = scripts.get_heads()

    assert len(heads) == 1
    assert scripts.get_revision(migration.revision).down_revision == "ab20f38d912e"
    assert migration.revision in {revision.revision for revision in scripts.walk_revisions()}


@pytest.fixture
def migration_connection():
    """Use only an explicitly supplied disposable database, never the application DB."""
    url = os.environ.get("TEST_LEGACY_IDE_PG_URL")
    if not url:
        pytest.skip("Set TEST_LEGACY_IDE_PG_URL to a disposable PostgreSQL database")

    engine = sa.create_engine(url)
    schema = f"legacy_ide_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(sa.schema.CreateSchema(schema))
            connection.execute(sa.text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            yield connection
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


def test_live_migration_marks_only_existing_ide_work_and_defaults_new_rows_to_false(migration_connection, monkeypatch):
    connection = migration_connection
    conversations = sa.Table(
        "conversations",
        sa.MetaData(),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("initial_assistant_id", sa.String()),
        sa.Column("llm_model", sa.String()),
        sa.Column("history", JSONB()),
        sa.Column("import_source", sa.String()),
    )
    conversations.create(connection)
    history = [{"role": "Assistant", "message": "Previous answer"}]
    base = {
        "initial_assistant_id": "__internal__IDE_plugin-123",
        "llm_model": None,
        "history": history,
        "import_source": None,
    }
    cases = {
        "legacy": {},
        "known-model": {"llm_model": "active"},
        "empty": {"history": []},
        "json-null": {"history": None},
        "sql-null": {"history": sa.null()},
        "non-array": {"history": "client history"},
        "web": {"initial_assistant_id": "web-assistant"},
        "missing-assistant": {"initial_assistant_id": None},
        "lookalike-prefix": {"initial_assistant_id": "XXinternalXXIDEXplugin-123"},
        "imported": {"import_source": "codex"},
    }
    for name, overrides in cases.items():
        connection.execute(conversations.insert().values(id=name, **{**base, **overrides}))

    migration = importlib.import_module(MIGRATION)
    monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
    migration.upgrade()

    migrated = sa.Table("conversations", sa.MetaData(), autoload_with=connection)
    markers = dict(connection.execute(sa.select(migrated.c.id, migrated.c[MARKER])).all())
    assert markers == {name: name == "legacy" for name in cases}
    assert migrated.c[MARKER].nullable is False

    # Supplying an IDE prefix and historical message timestamps after rollout cannot grant eligibility.
    connection.execute(
        conversations.insert().values(
            id="new-import",
            **{**base, "history": [{**history[0], "date": "2020-01-01T00:00:00Z"}]},
        )
    )
    assert connection.scalar(sa.select(migrated.c[MARKER]).where(migrated.c.id == "new-import")) is False

    migration.downgrade()
    assert MARKER not in {column["name"] for column in sa.inspect(connection).get_columns("conversations")}
