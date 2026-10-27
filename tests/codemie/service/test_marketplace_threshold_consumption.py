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

"""Tests verifying that get_marketplace_management_config() reads threshold
overrides from the customer_config DB row, falling back to hardcoded defaults.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs.marketplace_management import get_marketplace_management_config


def test_no_db_override_returns_hardcoded_defaults():
    """When no DB row exists, thresholds are the hardcoded defaults."""
    with patch("codemie.configs.marketplace_management.DynamicConfigService") as mock_svc:
        mock_svc.get_by_key.return_value = None
        config = get_marketplace_management_config("assistants")
    assert config.hot_min_users == 50
    assert config.warm_min_users == 7
    assert config.grace_days == 60
    assert config.archive_dead_after_days == 30


def test_db_override_for_hot_min_users():
    """When DB has an override for hotMinUsers, it's reflected in the config."""
    mock_row = MagicMock()
    mock_row.value = '{"hotMinUsers": "100"}'
    with patch("codemie.configs.marketplace_management.DynamicConfigService") as mock_svc:
        mock_svc.get_by_key.return_value = mock_row
        config = get_marketplace_management_config("assistants")
    assert config.hot_min_users == 100
    assert config.warm_min_users == 7  # unchanged default


def test_db_override_all_four_fields():
    """All four fields can be overridden from the DB."""
    mock_row = MagicMock()
    mock_row.value = '{"hotMinUsers": "75", "warmMinUsers": "10", "graceDays": "45", "archiveDeadAfterDays": "120"}'
    with patch("codemie.configs.marketplace_management.DynamicConfigService") as mock_svc:
        mock_svc.get_by_key.return_value = mock_row
        config = get_marketplace_management_config("assistants")
    assert config.hot_min_users == 75
    assert config.warm_min_users == 10
    assert config.grace_days == 45
    assert config.archive_dead_after_days == 120


def test_override_survives_repeated_calls():
    """Config reflects DB on every call (no stale cache for threshold overrides)."""
    mock_row = MagicMock()
    mock_row.value = '{"hotMinUsers": "200"}'
    with patch("codemie.configs.marketplace_management.DynamicConfigService") as mock_svc:
        mock_svc.get_by_key.return_value = mock_row
        config1 = get_marketplace_management_config("assistants")
        config2 = get_marketplace_management_config("assistants")
    assert config1.hot_min_users == 200
    assert config2.hot_min_users == 200


def _row(json_str):
    r = MagicMock()
    r.value = json_str
    return r


def test_read_core_gates_overrides(mocker):
    from codemie.configs.marketplace_management import _read_core_gates_overrides

    mocker.patch(
        "codemie.configs.marketplace_management.DynamicConfigService.get_by_key",
        return_value=_row('{"descriptionMin":"150","systemPromptMin":"300","substancePromptAltMin":"600"}'),
    )
    result = _read_core_gates_overrides()
    assert result == {"description_min": 150, "system_prompt_min": 300, "substance_prompt_alt_min": 600}


def test_read_polish_gates_overrides(mocker):
    from codemie.configs.marketplace_management import _read_polish_gates_overrides

    mocker.patch(
        "codemie.configs.marketplace_management.DynamicConfigService.get_by_key",
        return_value=_row(
            '{"descriptionMin":"300","categoriesMin":"3","requireIcon":false,"conversationStartersMin":"4"}'
        ),
    )
    result = _read_polish_gates_overrides()
    assert result == {
        "description_min": 300,
        "categories_min": 3,
        "require_icon": False,
        "conversation_starters_min": 4,
    }


def test_read_similarity_overrides_coerces_floats(mocker):
    from codemie.configs.marketplace_management import _read_similarity_overrides

    mocker.patch(
        "codemie.configs.marketplace_management.DynamicConfigService.get_by_key",
        return_value=_row(
            '{"criticalThreshold":"0.9","optionalThreshold":"0.75","reportThreshold":"0.6","shortlistSize":"8"}'
        ),
    )
    result = _read_similarity_overrides()
    assert result["critical_threshold"] == pytest.approx(0.9)
    assert result["optional_threshold"] == pytest.approx(0.75)
    assert result["report_threshold"] == pytest.approx(0.6)
    assert result["shortlist_size"] == 8


def test_get_marketplace_management_config_nested_merge(mocker):
    def fake_get_by_key(key):
        if "CORE_GATES" in key:
            return _row('{"descriptionMin":"150"}')
        return None

    mocker.patch(
        "codemie.configs.marketplace_management.DynamicConfigService.get_by_key",
        side_effect=fake_get_by_key,
    )
    cfg = get_marketplace_management_config()
    assert cfg.core_gates.description_min == 150
    assert cfg.core_gates.system_prompt_min == 200  # default preserved
