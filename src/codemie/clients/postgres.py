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

import os
import json
import atexit
from contextlib import asynccontextmanager, contextmanager

from pydantic.json import pydantic_encoder
from sqlmodel import create_engine, Session
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from urllib.parse import quote_plus

from codemie.configs import config


class PostgresClient:
    _engines = {}
    _async_engines = {}
    initial_pid = os.getpid()

    @classmethod
    def _get_connection_string(cls, async_mode: bool = False):
        if config.PG_URL:
            url = config.PG_URL
            if async_mode:
                if not url.startswith("postgresql+asyncpg://"):
                    # Convert postgresql:// to postgresql+asyncpg://
                    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
                # asyncpg uses 'ssl=' instead of 'sslmode='
                # https://github.com/MagicStack/asyncpg/issues/737
                ssl_require = "ssl=require"
                if "sslmode=" in url:
                    url = url.replace("sslmode=require", ssl_require)
                    url = url.replace("sslmode=verify-full", ssl_require)
                    url = url.replace("sslmode=verify-ca", ssl_require)
                    url = url.replace("sslmode=prefer", "ssl=prefer")
                    url = url.replace("sslmode=allow", "ssl=allow")
                    url = url.replace("sslmode=disable", "ssl=false")
            return url

        user = quote_plus(config.POSTGRES_USER)
        password = quote_plus(config.POSTGRES_PASSWORD)
        host = config.POSTGRES_HOST
        port = config.POSTGRES_PORT
        database = quote_plus(config.POSTGRES_DB)

        driver = "+asyncpg" if async_mode else ""
        return f"postgresql{driver}://{user}:{password}@{host}:{port}/{database}"

    @classmethod
    def get_engine(cls):
        """
        For each new process we create new engine, to not use parent engine to prevent errors
        """
        pid = os.getpid()
        if pid not in cls._engines:
            cls._engines[pid] = create_engine(
                url=cls._get_connection_string(),
                echo=False,  # config.is_local
                pool_pre_ping=True,
                pool_size=config.PG_POOL_SIZE if cls.initial_pid == pid else 2,
                json_serializer=lambda v: json.dumps(v, default=pydantic_encoder),
                connect_args={"options": f"-c search_path={config.DEFAULT_DB_SCHEMA},public"},
            )
            atexit.register(cls.cleanup_engine)
        return cls._engines[pid]

    @classmethod
    def cleanup_engine(cls):
        pid = os.getpid()
        engine = cls._engines.pop(pid, None)
        if engine is not None:
            engine.dispose()

    @classmethod
    def get_async_engine(cls):
        """
        Get or create async engine for the current process.
        Uses asyncpg driver for true async PostgreSQL operations.
        """
        pid = os.getpid()
        if pid not in cls._async_engines:
            cls._async_engines[pid] = create_async_engine(
                url=cls._get_connection_string(async_mode=True),
                echo=False,
                pool_pre_ping=True,
                pool_size=config.PG_POOL_SIZE if cls.initial_pid == pid else 2,
                max_overflow=0,
                json_serializer=lambda v: json.dumps(v, default=pydantic_encoder),
                connect_args={"server_settings": {"search_path": f"{config.DEFAULT_DB_SCHEMA},public"}},
            )
            atexit.register(cls.cleanup_async_engine)
        return cls._async_engines[pid]

    @classmethod
    def cleanup_async_engine(cls):
        pid = os.getpid()
        engine = cls._async_engines.pop(pid, None)
        if engine is not None:
            engine.sync_engine.dispose()


def alembic_upgrade_postgres() -> None:
    """Run Alembic migrations to head. Safe to call from any entrypoint."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(config.ALEMBIC_INI_PATH)
    alembic_cfg.set_main_option("script_location", str(config.ALEMBIC_MIGRATIONS_DIR))
    command.upgrade(alembic_cfg, "head")


@contextmanager
def get_session():
    """
    Context manager for database sessions.

    Yields:
        Session: SQLModel session for database operations.
    """
    engine = PostgresClient.get_engine()
    with Session(engine) as session:
        yield session


@asynccontextmanager
async def get_async_session():
    """
    Async context manager for database sessions.

    Yields:
        AsyncSession: SQLAlchemy async session for database operations.
    """
    async with AsyncSession(PostgresClient.get_async_engine()) as session:
        yield session
