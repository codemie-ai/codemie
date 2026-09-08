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

import pytest
from pydantic import ValidationError

from codemie.configs.config import Config


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


def test_env_local_overrides_env(tmp_path):
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
    monkeypatch.delenv("ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT", raising=False)
    cfg = Config(_env_file=())
    assert cfg.ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT == "http://otelcol:4318"
    assert cfg.ANALYTICS_INGEST_MAX_BODY_BYTES == 5_242_880
