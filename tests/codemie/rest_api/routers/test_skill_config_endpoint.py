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

"""Tests for the non-admin skills config endpoint and bundle warning helper (EPMCDME-13541)."""

from unittest.mock import MagicMock

from codemie.rest_api.models.skill import MIN_CONTENT_LENGTH
from codemie.service.skill_bundle_seed_policy import (
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT,
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES,
    DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES,
    SeedLimits,
)
from codemie.rest_api.security.user import User
import codemie.rest_api.routers.skill as skill_router
from codemie.rest_api.routers.skill import get_skill_config, router


def _mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    return user


class TestSkillConfigEndpoint:
    def test_returns_default_limit(self):
        resp = get_skill_config(user=_mock_user())
        assert resp.max_content_length == 30000
        assert resp.min_content_length == MIN_CONTENT_LENGTH
        assert resp.bundle_seed_max_file_count == DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_COUNT
        assert resp.bundle_seed_max_file_size_bytes == DEFAULT_SKILL_WORKSPACE_SEED_MAX_FILE_SIZE_BYTES
        assert resp.bundle_seed_max_total_size_bytes == DEFAULT_SKILL_WORKSPACE_SEED_MAX_TOTAL_SIZE_BYTES
        assert "bundle_seed_max_size_bytes" not in resp.model_dump()

    def test_reflects_configured_value(self, monkeypatch):
        monkeypatch.setattr(skill_router, "get_skill_max_content_length", lambda: 50000)
        resp = get_skill_config(user=_mock_user())
        assert resp.max_content_length == 50000

    def test_reflects_configured_bundle_ceilings(self, monkeypatch):
        monkeypatch.setattr(
            SeedLimits, "current", classmethod(lambda cls: SeedLimits(50, 5 * 1024 * 1024, 20 * 1024 * 1024))
        )
        resp = get_skill_config(user=_mock_user())
        assert resp.bundle_seed_max_file_count == 50
        assert resp.bundle_seed_max_file_size_bytes == 5 * 1024 * 1024
        assert resp.bundle_seed_max_total_size_bytes == 20 * 1024 * 1024

    def test_route_registered_as_non_admin_get(self):  # noqa: PLR6301
        # Assert by endpoint identity so the guard survives router-prefix refactors.
        matches = [r for r in router.routes if getattr(r, "endpoint", None) is get_skill_config]
        assert matches, "GET /skills/config route not registered against get_skill_config"
        assert "GET" in matches[0].methods
