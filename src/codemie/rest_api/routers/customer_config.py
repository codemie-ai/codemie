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

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder

from codemie.clients.postgres import get_async_session
from codemie.configs.customer_config import Component
from codemie.rest_api.models.application import Application
from codemie.rest_api.models.customer_config import (
    SettingDeclarationResponse,
    SettingUpdateRequest,
    SettingUpdateResponse,
)
from codemie.rest_api.security.authentication import (
    authenticate,
    require_customer_config_write,
)
from codemie.rest_api.security.user import User
from codemie.service.customer_config_service import (
    list_settings,
    reset_setting,
    resolve_components,
    save_setting,
)
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    MarketplaceModerationEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository

logger = logging.getLogger(__name__)

_SETTING_PATH = "/config/declarations/{component_id}"

_MARKETPLACE_COMPONENT_IDS: frozenset[str] = frozenset(
    {
        "marketplaceThresholds",
        "marketplaceCoreGates",
        "marketplacePolishGates",
        "marketplaceSimilarity",
    }
)

router = APIRouter(
    tags=["customer_config"],
    prefix="/v1",
    dependencies=[],
)


@router.get("/config", response_model=List[Component], response_model_exclude_none=True)
async def get_config():
    return jsonable_encoder(await resolve_components())


@router.get(
    "/config/declarations",
    response_model=List[SettingDeclarationResponse],
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def get_declarations():
    """Settings declared as dynamic, with their current value and override marker."""
    return jsonable_encoder(await list_settings())


async def _write_threshold_event(
    event_type: str,
    actor_id: str,
    payload: dict | None,
    entity_id: str = "marketplaceThresholds",
) -> None:
    """Persist one `activity_events` row for a threshold config change.

    Review comment #21: this used to write `mm_marketplace_event`, the table
    `a9c1t2i3v4e5` replaces with `activity_events` -- so threshold changes
    were invisible in the real audit trail and blocked dropping the old
    table. One row is enough: unlike a moderation decision, a threshold
    change is not scoped to a single EntityKind, so there is nothing to
    duplicate per kind here. Wrapped in try/except so a DB failure never
    breaks the caller's response.
    """
    try:
        async with get_async_session() as session:
            await activity_event_repository.async_insert(
                ActivityEventCreate(
                    domain=ActivityDomain.MARKETPLACE,
                    event_type=event_type,
                    entity_type=ActivityEntityType.CUSTOMER_CONFIG_SETTING,
                    entity_id=entity_id,
                    actor_id=actor_id,
                    attributes={"settings": payload} if payload is not None else None,
                ),
                session,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write threshold audit event. %s=%r, %s=%r", "event_type", event_type, "exc", exc)


async def _handle_threshold_hook(
    component_id: str, event_type: str, actor_id: str, settings: dict | None = None
) -> None:
    """Invalidate the audit-cache config and write an audit row for any marketplace threshold component.

    Review comment #21: this must do nothing when the feature is off --
    both so a disabled deployment never pays for the extra cache-invalidation
    loop and activity-event write, and so `audit_service` (and its module-level
    `AUDIT_CACHE` singleton) is only imported when the feature is actually on,
    not unconditionally on every deployment via this router's top-level imports.
    """
    if component_id not in _MARKETPLACE_COMPONENT_IDS:
        return
    from codemie.configs.marketplace_management import is_marketplace_management_enabled

    if not is_marketplace_management_enabled():
        return
    from codemie.service.marketplace_management.audit_service import AUDIT_CACHE
    from codemie.service.marketplace_management.models import EntityKind

    for kind in EntityKind:
        AUDIT_CACHE.update_config(kind, {})
    await _write_threshold_event(event_type, actor_id, settings, entity_id=component_id)


@router.put(
    _SETTING_PATH,
    response_model=SettingUpdateResponse,
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def update_setting(
    component_id: str,
    request: SettingUpdateRequest,
    current_user: User = Depends(authenticate),
):
    """Override one declared setting. A payload failing its declaration stores nothing."""
    settings = await save_setting(component_id, request.settings, current_user)
    await _handle_threshold_hook(component_id, MarketplaceModerationEvent.THRESHOLD_UPDATED, current_user.id, settings)
    return SettingUpdateResponse(component_id=component_id, settings=settings)


@router.delete(
    _SETTING_PATH,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def delete_setting(component_id: str, current_user: User = Depends(authenticate)):
    """Drop the override so the value comes from the deployment default again."""
    # CR-004: capture the current override before reset so the audit event carries
    # the values that were removed rather than recording null payload.
    previous_settings: dict | None = None
    if component_id in _MARKETPLACE_COMPONENT_IDS:
        from codemie.service.customer_config_declarations import by_component_id
        from codemie.service.dynamic_config_service import DynamicConfigService

        decl = by_component_id(component_id)
        if decl is not None:
            row = await DynamicConfigService.aget_by_key(decl.key)
            if row is not None:
                try:
                    previous_settings = json.loads(row.value)
                except (ValueError, TypeError):
                    previous_settings = None

    await reset_setting(component_id, current_user)
    await _handle_threshold_hook(
        component_id, MarketplaceModerationEvent.THRESHOLD_RESET, current_user.id, previous_settings
    )


@router.get("/applications", response_model=List[Application])
async def get_applications():
    components = await resolve_components()

    # Filter for components with IDs starting with 'applications:'
    application_components = [component for component in components if component.id.startswith('applications:')]

    applications = []
    for app in application_components:
        slug = app.id.replace('applications:', '', 1)

        applications.append(
            Application(
                name=app.settings.name,
                description=app.settings.description or '',
                slug=slug,
                entry=app.settings.url,
                type=app.settings.type,
                created_by=app.settings.created_by,
                icon_url=app.settings.icon_url,
                arguments=getattr(app.settings, "arguments", None),
            )
        )

    return jsonable_encoder(applications)
