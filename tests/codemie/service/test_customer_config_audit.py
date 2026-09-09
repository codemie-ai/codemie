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

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service import customer_config_service
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    CustomerConfigEvent,
)
from codemie.service.customer_config_service import reset_setting, save_setting


@pytest.fixture(autouse=True)
def reset_cache():
    customer_config_service.override_cache.invalidate()
    yield
    customer_config_service.override_cache.invalidate()


@pytest.fixture
def actor():
    user = MagicMock()
    user.id = "actor-1"
    return user


@pytest.fixture
def audit():
    with patch.object(customer_config_service, "activity_event_repository") as repository:
        repository.async_insert = AsyncMock()
        with patch.object(customer_config_service, "get_async_session") as session:
            db_session = MagicMock()
            db_session.commit = AsyncMock()
            session.return_value.__aenter__.return_value = db_session
            yield repository


@pytest.fixture
def stored_row():
    row = MagicMock()
    row.key = "CUSTOMER_CONFIG__CHAT_DISCLAIMER"
    row.value = json.dumps({"enabled": False, "text": "old"})
    return row


def _emitted(audit):
    return audit.async_insert.await_args.args[0]


@pytest.mark.asyncio
async def test_save_stores_the_declared_fields_as_serialised_json(audit, actor):
    aset = AsyncMock()
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)):
        with patch.object(customer_config_service.DynamicConfigService, "aset", aset):
            await save_setting("chatDisclaimer", {"enabled": True, "text": "Mind the gap"}, actor)

    key, value = aset.await_args.kwargs["key"], aset.await_args.kwargs["value"]
    assert key == "CUSTOMER_CONFIG__CHAT_DISCLAIMER"
    assert json.loads(value) == {"enabled": True, "text": "Mind the gap"}


@pytest.mark.asyncio
async def test_save_emits_an_audit_event_with_old_and_new_value(audit, actor, stored_row):
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=stored_row)):
        with patch.object(customer_config_service.DynamicConfigService, "aset", AsyncMock()):
            await save_setting("chatDisclaimer", {"enabled": True, "text": "new"}, actor)

    event = _emitted(audit)
    assert event.domain == ActivityDomain.CUSTOMER_CONFIG
    assert event.event_type == CustomerConfigEvent.SETTING_UPDATED
    assert event.entity_type == ActivityEntityType.CUSTOMER_CONFIG_SETTING
    assert event.entity_id == "chatDisclaimer"
    assert event.actor_id == "actor-1"
    assert event.attributes["old_value"] == {"enabled": False, "text": "old"}
    assert event.attributes["new_value"] == {"enabled": True, "text": "new"}


@pytest.mark.asyncio
async def test_save_reports_no_old_value_when_there_was_no_override(audit, actor):
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)):
        with patch.object(customer_config_service.DynamicConfigService, "aset", AsyncMock()):
            await save_setting("chatDisclaimer", {"enabled": True, "text": "new"}, actor)

    assert _emitted(audit).attributes["old_value"] is None


@pytest.mark.asyncio
async def test_save_refreshes_the_local_snapshot(audit, actor):
    """The writing pod reloads rather than discarding: synchronous readers have no reload path."""
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)):
        with patch.object(customer_config_service.DynamicConfigService, "aset", AsyncMock()):
            with patch.object(customer_config_service, "refresh_overrides", AsyncMock()) as refresh:
                await save_setting("chatDisclaimer", {"enabled": True, "text": "new"}, actor)

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_rejects_an_undeclared_component(audit, actor):
    with pytest.raises(customer_config_service.ExtendedHTTPException) as error:
        await save_setting("features:dynamicCodeInterpreter", {"enabled": True}, actor)

    assert error.value.code == 404


@pytest.mark.asyncio
async def test_save_stores_nothing_when_validation_fails(audit, actor):
    aset = AsyncMock()
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)):
        with patch.object(customer_config_service.DynamicConfigService, "aset", aset):
            with pytest.raises(customer_config_service.ExtendedHTTPException):
                await save_setting("chatDisclaimer", {"enabled": True, "text": 42}, actor)

    aset.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_deletes_the_row_and_audits_it(audit, actor, stored_row):
    adelete = AsyncMock(return_value=True)
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=stored_row)):
        with patch.object(customer_config_service.DynamicConfigService, "adelete", adelete):
            await reset_setting("chatDisclaimer", actor)

    adelete.assert_awaited_once_with("CUSTOMER_CONFIG__CHAT_DISCLAIMER")
    event = _emitted(audit)
    assert event.event_type == CustomerConfigEvent.SETTING_RESET
    assert event.attributes["old_value"] == {"enabled": False, "text": "old"}
    assert event.attributes["new_value"] is None


@pytest.mark.asyncio
async def test_reset_of_a_setting_without_an_override_is_a_no_op(audit, actor):
    with patch.object(customer_config_service.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None)):
        with patch.object(customer_config_service.DynamicConfigService, "adelete", AsyncMock(return_value=False)):
            await reset_setting("chatDisclaimer", actor)

    audit.async_insert.assert_not_awaited()
