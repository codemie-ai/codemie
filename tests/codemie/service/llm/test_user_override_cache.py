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

"""A request reads a user's model override once; outside a request nothing is remembered."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.service.llm.user_override_cache import (
    _request_cache,
    clear_request_override_cache,
    load_user_override,
    request_override_cache,
)

LOOKUP = "codemie.rest_api.models.user_model_override.UserModelOverride.get_by_fields"
MODEL_SERVICE = "codemie.service.llm.model_availability_service"


def _override(models=("gpt-4",), default=None):
    return MagicMock(allowed_models=list(models), default_model=default)


def test_outside_a_request_every_load_reads_the_database():
    with patch(LOOKUP, return_value=_override()) as lookup:
        load_user_override("u1", "proj")
        load_user_override("u1", "proj")

    assert lookup.call_count == 2


def test_inside_a_request_the_second_load_is_remembered():
    override = _override()
    with patch(LOOKUP, return_value=override) as lookup:
        with request_override_cache():
            first = load_user_override("u1", "proj")
            second = load_user_override("u1", "proj")

    assert first is second is override
    lookup.assert_called_once_with({"user_id": "u1", "project_name": "proj"})


def test_having_no_override_is_remembered_too():
    """The common case: most users have none, and each resolution used to look again."""
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            assert load_user_override("u1", "proj") is None
            assert load_user_override("u1", "proj") is None

    lookup.assert_called_once()


def test_each_user_and_project_is_remembered_on_its_own():
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            load_user_override("u1", "proj")
            load_user_override("u2", "proj")
            load_user_override("u1", "other")
            load_user_override("u1", "proj")

    assert lookup.call_count == 3


def test_a_uuid_and_its_string_are_the_same_user():
    user_id = uuid4()
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            load_user_override(user_id, "proj")
            load_user_override(str(user_id), "proj")

    lookup.assert_called_once()


def test_nothing_survives_the_request():
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            load_user_override("u1", "proj")
        assert _request_cache.get() is None
        with request_override_cache():
            load_user_override("u1", "proj")

    assert lookup.call_count == 2


def test_clearing_makes_the_request_read_again():
    """A request that changes an override must not keep reading the old one."""
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            load_user_override("u1", "proj")
            clear_request_override_cache()
            load_user_override("u1", "proj")

    assert lookup.call_count == 2


def test_clearing_outside_a_request_does_nothing():
    clear_request_override_cache()

    assert _request_cache.get() is None


def test_a_thread_started_with_asyncio_shares_the_requests_cache():
    """asyncio.to_thread copies the context, which is how the chat runs."""

    async def request():
        with request_override_cache():
            await asyncio.to_thread(load_user_override, "u1", "proj")
            await asyncio.to_thread(load_user_override, "u1", "proj")

    with patch(LOOKUP, return_value=None) as lookup:
        asyncio.run(request())

    lookup.assert_called_once()


def test_a_plain_thread_does_not_inherit_the_cache_and_reads_the_database():
    """Not cached, but correct: the thread never sees a stale or foreign entry."""
    with patch(LOOKUP, return_value=None) as lookup:
        with request_override_cache():
            load_user_override("u1", "proj")
            thread = threading.Thread(target=load_user_override, args=("u1", "proj"))
            thread.start()
            thread.join()

    assert lookup.call_count == 2


def test_concurrent_requests_do_not_share_a_cache():
    results = {}

    async def request(name, override):
        with request_override_cache():
            with patch(LOOKUP, return_value=override):
                results[name] = load_user_override("u1", "proj")
            await asyncio.sleep(0)
            results[name + "-again"] = load_user_override("u1", "proj")

    async def both():
        await asyncio.gather(request("a", "override-a"), request("b", "override-b"))

    asyncio.run(both())

    assert results["a-again"] == "override-a"
    assert results["b-again"] == "override-b"


class TestReadersShareTheRequestCache:
    """The resolver, the reject-style check and the model list all read through the same loader."""

    @pytest.fixture(autouse=True)
    def _every_model_is_live(self):
        """These tests count reads; the resolver's liveness check is not what they look at."""
        with patch(f"{MODEL_SERVICE}.is_known_model", return_value=True):
            yield

    def test_resolving_twice_reads_once(self):
        override = _override(models=("gpt-4",))
        with (
            patch(f"{MODEL_SERVICE}.customer_config", is_feature_enabled=MagicMock(return_value=True)),
            patch(LOOKUP, return_value=override) as lookup,
            request_override_cache(),
        ):
            ModelAvailabilityService.resolve_user_override_model("gpt-4", "u1", "proj")
            ModelAvailabilityService.resolve_user_override_model("gpt-4", "u1", "proj")

        lookup.assert_called_once()

    def test_the_resolver_and_the_reject_check_share_one_read(self):
        with (
            patch(f"{MODEL_SERVICE}.customer_config", is_feature_enabled=MagicMock(return_value=True)),
            patch(LOOKUP, return_value=_override()) as lookup,
            request_override_cache(),
        ):
            ModelAvailabilityService.resolve_user_override_model("gpt-4", "u1", "proj")
            assert ModelAvailabilityService.user_override_allows_model("gpt-4", "u1", "proj") is True

        lookup.assert_called_once()

    def test_the_model_list_and_the_resolver_share_one_read(self):
        from codemie.service.llm_service.llm_service import LLMService

        user = MagicMock(id="u1")
        project = MagicMock(id="proj")
        with (
            patch(f"{MODEL_SERVICE}.customer_config", is_feature_enabled=MagicMock(return_value=True)),
            patch(
                "codemie.service.llm_service.llm_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch(LOOKUP, return_value=_override()) as lookup,
            request_override_cache(),
        ):
            LLMService.get_user_model_override(user, project)
            ModelAvailabilityService.resolve_user_override_model("gpt-4", "u1", "proj")

        lookup.assert_called_once()


class TestWritesClearTheRequestCache:
    """A request that changes an override reads the new value afterwards."""

    @staticmethod
    def _service():
        from codemie.service.user import user_model_override_service as module

        return module.UserModelOverrideService, module

    def test_setting_an_override_clears_it(self):
        service, module = self._service()
        with patch.object(module, "clear_request_override_cache") as clear:
            with (
                patch.object(service, "_require_project"),
                patch.object(service, "_validate_override_models"),
                patch.object(service, "_validate_default_model"),
                patch.object(service, "_require_project_members"),
                patch.object(service, "_upsert", return_value=(MagicMock(), None)),
            ):
                session = MagicMock()
                session.execute.return_value.scalars.return_value.first.return_value = MagicMock()
                service.set_override(
                    session, "u1", "proj", ["gpt-4"], actor_id="admin", actor_email="admin@example.com"
                )

        clear.assert_called_once()

    def test_deleting_an_override_clears_it(self):
        service, module = self._service()
        with (
            patch.object(module, "clear_request_override_cache") as clear,
            patch.object(module.UserModelOverrideRepository, "delete", return_value=None),
        ):
            service.delete_override(MagicMock(), "u1", "proj", actor_id="admin", actor_email="admin@example.com")

        clear.assert_called_once()

    @pytest.mark.parametrize("operation", ["grant", "revoke"])
    def test_a_batch_clears_it(self, operation):
        service, module = self._service()
        with (
            patch.object(module, "clear_request_override_cache") as clear,
            patch.object(service, "_require_project"),
            patch.object(service, "_batch_grant", return_value=[]),
            patch.object(service, "_batch_revoke", return_value=[]),
        ):
            service.batch_set_overrides(
                MagicMock(), "proj", ["u1"], operation, ["gpt-4"], actor_id="admin", actor_email="admin@example.com"
            )

        clear.assert_called_once()


@pytest.mark.asyncio
async def test_the_middleware_opens_a_request_scope_around_the_call():
    from codemie.rest_api.main import remember_user_model_overrides_per_request

    seen = {}

    async def call_next(request):
        seen["inside"] = _request_cache.get()
        return "response"

    response = await remember_user_model_overrides_per_request(MagicMock(), call_next)

    assert response == "response"
    assert seen["inside"] == {}
    assert _request_cache.get() is None


@pytest.mark.asyncio
async def test_the_middleware_closes_the_scope_when_the_call_fails():
    from codemie.rest_api.main import remember_user_model_overrides_per_request

    call_next = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await remember_user_model_overrides_per_request(MagicMock(), call_next)

    assert _request_cache.get() is None
