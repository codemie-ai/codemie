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

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from codemie.configs.config import Config


@pytest.fixture
def build_config(monkeypatch):
    for name in list(os.environ):
        if name.startswith("SPEND_BACKFILL_"):
            monkeypatch.delenv(name)
    return lambda **values: Config(_env_file=None, **values)


def test_defaults_keep_the_backfill_off_and_write_mode_on(build_config):
    config = build_config()

    assert config.SPEND_BACKFILL_ENABLED is False
    assert config.SPEND_BACKFILL_DRY_RUN is False
    assert config.SPEND_BACKFILL_MAX_AGE_HOURS == 48
    assert config.SPEND_BACKFILL_BATCH == 1000
    assert config.SPEND_BACKFILL_TIMEOUT_SECONDS == 60
    assert config.SPEND_BACKFILL_HISTORY_START is None
    assert config.SPEND_BACKFILL_PROJECTS == ""


def test_history_start_parses_iso_8601_with_a_timezone(build_config):
    history_start = build_config(SPEND_BACKFILL_HISTORY_START="2026-11-19T02:00:00+02:00").SPEND_BACKFILL_HISTORY_START

    assert history_start.utcoffset() == timedelta(hours=2)
    assert history_start.astimezone(UTC) == datetime(2026, 11, 19, 0, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["", "  "])
def test_empty_history_start_turns_the_history_pass_off(build_config, raw):
    assert build_config(SPEND_BACKFILL_HISTORY_START=raw).SPEND_BACKFILL_HISTORY_START is None


@pytest.mark.parametrize("raw", ["not-a-date", "2026-11-19T00:00:00"])
def test_history_start_without_a_valid_timezone_is_rejected(build_config, raw):
    with pytest.raises(ValidationError, match="SPEND_BACKFILL_HISTORY_START"):
        build_config(SPEND_BACKFILL_HISTORY_START=raw)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SPEND_BACKFILL_BATCH", 0),
        ("SPEND_BACKFILL_BATCH", 10_001),
        ("SPEND_BACKFILL_MAX_AGE_HOURS", -1),
        ("SPEND_BACKFILL_TIMEOUT_SECONDS", 0),
    ],
)
def test_out_of_range_settings_are_rejected(build_config, name, value):
    with pytest.raises(ValidationError, match=name):
        build_config(**{name: value})
