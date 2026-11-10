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

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from codemie.configs.config import Config, load_env_files, mcp_connect_available, retrieval_available


def test_is_local_local():
    config = Config()
    config.ENV = "local"
    assert config.is_local is True


def test_is_local_prod():
    config = Config()
    config.ENV = "prod"
    assert config.is_local is False


def test_budget_reset_reconciliation_schedule_validation_is_skipped_when_job_disabled():
    config = Config(
        LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED=False,
        LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE="not a valid cron",
    )

    assert config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE == "not a valid cron"


def test_budget_reset_reconciliation_schedule_validation_runs_when_job_enabled():
    with pytest.raises(ValidationError):
        Config(
            LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED=True,
            LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE="not a valid cron",
        )


def test_google_oauth_config_present():
    from codemie.configs.config import config

    assert hasattr(config, 'GOOGLE_OAUTH_CLIENT_ID')
    assert hasattr(config, 'GOOGLE_OAUTH_CLIENT_SECRET')
    assert hasattr(config, 'google_oauth_redirect_uri')  # computed field is lowercase


def test_authorized_apps_allowed_key_domains_default():
    config = Config()
    assert config.AUTHORIZED_APPS_ALLOWED_KEY_DOMAINS == []


def test_authorized_apps_allowed_key_domains_env_override(monkeypatch):
    monkeypatch.setenv("AUTHORIZED_APPS_ALLOWED_KEY_DOMAINS", '["trusted.example", "keys.trusted.example"]')
    config = Config()
    assert config.AUTHORIZED_APPS_ALLOWED_KEY_DOMAINS == ["trusted.example", "keys.trusted.example"]


def test_deletion_enabled_without_stale_detection_fails_validation():
    with pytest.raises(ValidationError):
        Config(
            STALE_DATASOURCE_ENABLED=False,
            STALE_DATASOURCE_DELETION_ENABLED=True,
        )


def test_deletion_enabled_with_detection_enabled_passes_validation():
    cfg = Config(
        STALE_DATASOURCE_ENABLED=True,
        STALE_DATASOURCE_DELETION_ENABLED=True,
    )
    assert cfg.STALE_DATASOURCE_DELETION_ENABLED is True


def test_config_model_config_includes_env_local():
    env_files = Config.model_config.get("env_file", ())
    assert any(
        str(f).endswith(".env.local") for f in env_files
    ), "Config.model_config must include '.env.local' so personal overrides take precedence over .env"


def test_env_local_overrides_env(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env_file = tmp_path / ".env"
    env_local_file = tmp_path / ".env.local"
    env_file.write_text("LOG_LEVEL=WARNING\n")
    env_local_file.write_text("LOG_LEVEL=DEBUG\n")
    config = Config(_env_file=(str(env_file), str(env_local_file)))
    assert config.LOG_LEVEL == "DEBUG", ".env.local values must override .env values"


def test_file_datasource_max_upload_count_defaults_to_ten():
    config = Config()
    assert config.FILE_DATASOURCE_MAX_UPLOAD_COUNT == 10


def test_analytics_ingest_config_defaults(monkeypatch):
    monkeypatch.delenv("ANALYTICS_INGEST_MAX_BODY_BYTES", raising=False)
    cfg = Config(_env_file=())
    assert cfg.ANALYTICS_INGEST_MAX_BODY_BYTES == 5_242_880


class TestLoadEnvFiles:
    def _write_env_files(self, root: Path) -> None:
        (root / ".env").write_text("FROM_ENV_ONLY=env\nFROM_BOTH=env\nFROM_ALL=env\n")
        (root / ".env.local").write_text("FROM_BOTH=local\nFROM_ALL=local\nFROM_LOCAL_ONLY=local\n")

    def test_process_environment_beats_both_files(self, tmp_path: Path) -> None:
        self._write_env_files(tmp_path)
        with patch.dict(os.environ, {"FROM_ALL": "process"}):
            load_env_files(tmp_path)
            assert os.environ["FROM_ALL"] == "process"

    def test_env_local_beats_env(self, tmp_path: Path) -> None:
        self._write_env_files(tmp_path)
        with patch.dict(os.environ):
            os.environ.pop("FROM_BOTH", None)
            load_env_files(tmp_path)
            assert os.environ["FROM_BOTH"] == "local"

    def test_variables_from_a_single_file_are_loaded(self, tmp_path: Path) -> None:
        self._write_env_files(tmp_path)
        with patch.dict(os.environ):
            os.environ.pop("FROM_ENV_ONLY", None)
            os.environ.pop("FROM_LOCAL_ONLY", None)
            load_env_files(tmp_path)
            assert os.environ["FROM_ENV_ONLY"] == "env"
            assert os.environ["FROM_LOCAL_ONLY"] == "local"

    def test_missing_files_are_ignored(self, tmp_path: Path) -> None:
        load_env_files(tmp_path)


def test_retrieval_available_true_for_elasticsearch():
    cfg = SimpleNamespace(STANDALONE=False, RETRIEVAL_BACKEND="elasticsearch")
    assert retrieval_available(cfg) is True


def test_retrieval_available_false_for_none():
    cfg = SimpleNamespace(STANDALONE=False, RETRIEVAL_BACKEND="none")
    assert retrieval_available(cfg) is False


def test_mcp_connect_available_non_standalone_ignores_url():
    cfg = SimpleNamespace(STANDALONE=False, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="")
    assert mcp_connect_available(cfg) is True


def test_mcp_connect_available_non_standalone_follows_flag():
    cfg = SimpleNamespace(STANDALONE=False, MCP_CONNECT_ENABLED=False, MCP_CONNECT_URL="")
    assert mcp_connect_available(cfg) is False


def test_mcp_connect_available_standalone_requires_enabled_and_url():
    cfg = SimpleNamespace(STANDALONE=True, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="http://x")
    assert mcp_connect_available(cfg) is True


def test_mcp_connect_available_standalone_deliberately_disabled():
    cfg = SimpleNamespace(STANDALONE=True, MCP_CONNECT_ENABLED=False, MCP_CONNECT_URL="http://x")
    assert mcp_connect_available(cfg) is False


def test_mcp_connect_available_standalone_empty_url():
    cfg = SimpleNamespace(STANDALONE=True, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL="")
    assert mcp_connect_available(cfg) is False


def test_mcp_connect_available_standalone_none_url():
    cfg = SimpleNamespace(STANDALONE=True, MCP_CONNECT_ENABLED=True, MCP_CONNECT_URL=None)
    assert mcp_connect_available(cfg) is False


def test_standalone_defaults_false(monkeypatch):
    monkeypatch.delenv("STANDALONE", raising=False)
    assert Config(_env_file=None).STANDALONE is False


def test_standalone_from_env(monkeypatch):
    monkeypatch.setenv("STANDALONE", "true")
    assert Config(_env_file=None).STANDALONE is True


def test_standalone_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("STANDALONE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("STANDALONE=true\n")
    assert Config(_env_file=env_file).STANDALONE is True


def test_standalone_invalid_value_rejected(monkeypatch):
    monkeypatch.setenv("STANDALONE", "maybe")
    with pytest.raises(ValidationError):
        Config(_env_file=None)


def test_standalone_does_not_change_external_service_defaults(monkeypatch):
    for key in ("ELASTIC_URL", "MERMAID_SERVER_URL", "MCP_CONNECT_URL", "REDIS_HOST"):
        monkeypatch.delenv(key, raising=False)
    cfg = Config(_env_file=None)
    assert cfg.ELASTIC_URL == "http://localhost:9200"
    assert cfg.MERMAID_SERVER_URL == "http://localhost:8082"
    assert cfg.MCP_CONNECT_URL == "http://localhost:3000"
    assert cfg.REDIS_HOST == "localhost"


@pytest.mark.parametrize("flag", ["true", "false"])
def test_standalone_not_exposed_in_customer_config(monkeypatch, flag):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setenv("STANDALONE", flag)
    assert not [c for c in customer_config.resolve_all() if "standalone" in str(c.id).lower()]
