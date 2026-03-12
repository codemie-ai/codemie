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

from logging.config import fileConfig

from sqlalchemy import pool, engine_from_config, text

from alembic import context
import alembic_postgresql_enum
from codemie.configs import config as codemie_config
from codemie.rest_api.models.base import PydanticListType, PydanticType
from sqlmodel import SQLModel
from codemie.clients.postgres import PostgresClient


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

import sys
from unittest.mock import patch
import importlib

# Patch alembic_postgresql_enum to work correctly
# with not default schema.
###############################################
# Force reload modules to ensure clean state
if 'alembic_postgresql_enum.get_enum_data.defined_enums' in sys.modules:
    importlib.reload(sys.modules['alembic_postgresql_enum.get_enum_data.defined_enums'])
if 'alembic_postgresql_enum.sql_commands.enum_type' in sys.modules:
    importlib.reload(sys.modules['alembic_postgresql_enum.sql_commands.enum_type'])


def patched_get_all_enums(connection, schema):
    sql = f"""
        SELECT
            pg_catalog.format_type(t.oid, NULL),
            ARRAY(SELECT enumlabel
                  FROM pg_catalog.pg_enum
                  WHERE enumtypid = t.oid
                  ORDER BY enumsortorder)
        FROM pg_catalog.pg_type t
        LEFT JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        WHERE
            t.typtype = 'e'
            AND n.nspname = :schema
    """
    schema = codemie_config.DEFAULT_DB_SCHEMA
    return connection.execute(text(sql), dict(schema=schema))


# Patch both modules
patches = [
    patch('alembic_postgresql_enum.sql_commands.enum_type.get_all_enums', patched_get_all_enums),
    patch('alembic_postgresql_enum.get_enum_data.defined_enums.get_all_enums', patched_get_all_enums),
]

# Start patches
for p in patches:
    p.start()
###############################################

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# if config.config_file_name is not None:
#     fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration
from codemie.rest_api.models.skill import Skill
from codemie.rest_api.models.usage.skill_user_interaction import SkillUserInteraction
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.user import UserData
from codemie.rest_api.models.user_management import UserDB, UserProject, UserKnowledgeBase, EmailVerificationToken
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.models.feedback import FeedbackEntry
from codemie.rest_api.models.settings import Settings
from codemie.rest_api.models.provider import Provider
from codemie.core.models import Application, GitRepo
from codemie.rest_api.models.usage.assistant_user_interaction import AssistantUserInterationSQL
from codemie.rest_api.models.usage.assistant_user_mapping import AssistantUserMappingSQL
from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import AssistantPromptVariableMappingSQL
from codemie.rest_api.models.conversation import ConversationMetrics, Conversation
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.core.workflow_models.workflow_execution import (
    WorkflowExecutionState,
    WorkflowExecution,
    WorkflowExecutionStateThought,
)
from codemie.rest_api.models.background_tasks import BackgroundTasks
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.permission import Permission
from codemie.rest_api.models.guardrail import Guardrail, GuardrailAssignment
from codemie.rest_api.models.ai_kata import AIKata
from codemie.rest_api.models.conversation_analysis import ConversationAnalysisQueue, ConversationAnalytics
from codemie.rest_api.models.user_kata_progress import UserKataProgress
from codemie.rest_api.models.usage.kata_user_interaction import KataUserInteractionSQL
from codemie.rest_api.models.mcp_config import MCPConfig
from codemie.rest_api.models.dynamic_config import DynamicConfig
from codemie.rest_api.a2a.types import Task

target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name == "table_migration_status":
        return False
    return True


def render_item(type_, obj, autogen_context):
    """Apply custom rendering for selected items."""

    if type_ == 'type':
        if isinstance(obj, (PydanticType, PydanticListType)):
            return "postgresql.JSONB(astext_type=sa.Text())"

    # default rendering for other objects
    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = PostgresClient.get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            include_object=include_object,
        )
        # context._ensure_version_table()

        with context.begin_transaction():
            context.execute(f"SET search_path TO {codemie_config.DEFAULT_DB_SCHEMA}, public")
            # Aquire lock to prevent parallel migrations
            context.get_context()._ensure_version_table()
            context.execute("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE")
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
