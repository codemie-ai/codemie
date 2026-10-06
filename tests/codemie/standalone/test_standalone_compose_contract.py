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

"""The standalone stack is CodeMie plus PostgreSQL; CLI Analytics stores into that PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy.engine import make_url

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

STANDALONE_DIR = Path(__file__).resolve().parents[3] / "standalone"
COMPOSE_FILE = STANDALONE_DIR / "docker-compose.standalone.yml"
OPERATOR_FILES = (COMPOSE_FILE, STANDALONE_DIR / ".env.standalone.example", STANDALONE_DIR / "README.md")
REMOVED_ARCHITECTURE = ("clickhouse", "otelcollector", "standalone-analytics", "config/otel", "--profile")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_default_stack_is_codemie_and_postgres_without_profiles(compose):
    assert set(compose["services"]) == {"codemie", "postgres"}
    assert not any("profiles" in service for service in compose["services"].values())
    assert set(compose["volumes"]) == {"postgres_data", "codemie_storage", "codemie_repos"}


@pytest.mark.parametrize("path", OPERATOR_FILES, ids=lambda p: p.name)
def test_operator_files_do_not_mention_the_removed_analytics_architecture(path):
    text = path.read_text(encoding="utf-8").lower()

    assert [token for token in REMOVED_ARCHITECTURE if token in text] == []


def test_cli_analytics_uses_the_compose_postgres_through_pg_url(compose):
    env = dict(entry.split("=", 1) for entry in compose["services"]["codemie"]["environment"])
    postgres_env = dict(entry.split("=", 1) for entry in compose["services"]["postgres"]["environment"])

    settings = AnalyticsPgSettings.from_config(Config(PG_URL=env["PG_URL"], CLI_ANALYTICS_PG_URL=""))

    target = make_url(settings.dsn)
    assert (target.host, target.database, target.username) == (
        "postgres",
        postgres_env["POSTGRES_DB"],
        postgres_env["POSTGRES_USER"],
    )
    assert "CLI_ANALYTICS_PG_URL" not in env
