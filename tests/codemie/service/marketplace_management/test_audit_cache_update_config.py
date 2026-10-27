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

"""`AuditCache.update_config` invalidates; it must never write the shipped defaults.

`get_marketplace_management_config()` already merges the DB overrides in, so saving its
result into `_KIND_DEFAULTS` would make a pod keep a deleted override as its default
until restart, while other pods scored against the real default.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie.configs import marketplace_management as mm_cfg
from codemie.service.marketplace_management.audit_service import AuditCache
from codemie.service.marketplace_management.models import EntityKind


def test_update_config_leaves_kind_defaults_untouched_when_an_override_is_active():
    cache = AuditCache()
    shipped = mm_cfg._KIND_DEFAULTS["assistants"]
    shipped_hot = shipped.hot_min_users

    with patch.object(mm_cfg, "_read_threshold_overrides", return_value={"hot_min_users": shipped_hot + 100}):
        cache.update_config(EntityKind.ASSISTANT, {})

    assert mm_cfg._KIND_DEFAULTS["assistants"] is shipped
    assert mm_cfg._KIND_DEFAULTS["assistants"].hot_min_users == shipped_hot
    assert mm_cfg.MARKETPLACE_MANAGEMENT_DEFAULTS.hot_min_users == shipped_hot


def test_config_after_a_reset_is_the_shipped_default():
    cache = AuditCache()
    shipped_hot = mm_cfg._KIND_DEFAULTS["assistants"].hot_min_users

    with patch.object(mm_cfg, "_read_threshold_overrides", return_value={"hot_min_users": shipped_hot + 100}):
        cache.update_config(EntityKind.ASSISTANT, {})
        assert cache._config_for(EntityKind.ASSISTANT) is not None

    with patch.object(mm_cfg, "_read_threshold_overrides", return_value={}):
        cache.update_config(EntityKind.ASSISTANT, {})
        assert mm_cfg.get_marketplace_management_config("assistants").hot_min_users == shipped_hot


def test_update_config_drops_the_cached_validator_config():
    cache = AuditCache()
    cache._configs[EntityKind.ASSISTANT] = object()

    cache.update_config(EntityKind.ASSISTANT, {})

    assert EntityKind.ASSISTANT not in cache._configs


def test_update_config_refuses_to_persist_values():
    with pytest.raises(ValueError):
        AuditCache().update_config(EntityKind.ASSISTANT, {"hot_min_users": 1})
