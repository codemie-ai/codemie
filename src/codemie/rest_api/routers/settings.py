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

"""Settings endpoints that span both the user and project scopes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from codemie.configs.logger import logger
from codemie.rest_api.models.settings import SettingType
from codemie.rest_api.models.settings_transfer import TransferSettingsRequest, TransferSettingsResponse
from codemie.rest_api.security.authentication import User, admin_or_maintainer_access_only, authenticate
from codemie.service.settings.settings_transfer_service import SettingsTransferService

router = APIRouter(
    tags=["Settings"],
    prefix="/v1",
    dependencies=[],
)


@router.post(
    "/settings/transfer",
    status_code=status.HTTP_200_OK,
    response_model=TransferSettingsResponse,
    dependencies=[Depends(authenticate), Depends(admin_or_maintainer_access_only)],
)
def transfer_settings(request: TransferSettingsRequest, user: User = Depends(authenticate)):
    """
    Transfer integrations from one project to another.

    Requires administrator or maintainer privileges.

    ### Modes

    **`move`** — reassigns each integration to the target project. Integration IDs are unchanged,
    so datasources, assistants, and other entities that reference integrations by ID continue to
    work without reconfiguration.

    **`copy`** — duplicates each integration into the target project. The source is unchanged.
    Webhook and scheduler integrations are excluded from copy and returned in `skipped`.
    They can be transferred with `move`.

    ### Filters

    **`type_of_integration`** — controls which integration types are included. Omitting this field
    transfers both types. When the field is present, any omitted property defaults to `false`.

    | `user_integrations` | `project_integrations` | Effect |
    |---|---|---|
    | `true` (default) | `true` (default) | All integrations |
    | `true` | `false` | User-owned integrations only |
    | `false` | `true` | Project-scoped integrations only |
    | `false` | `false` | 422 — at least one type must be selected |

    **`list`** — optional list of aliases to transfer. When provided, only the listed integrations
    are transferred. An alias that does not exist in the source project, is excluded by the type
    filter, is managed by an internal subsystem, or cannot be transferred in the chosen mode
    returns a 422 with a specific error message.

    Note: user-type integrations are scoped per user, so multiple users in the same project
    can each own an integration with the same alias. When `list` contains such an alias and
    `user_integrations` is `true`, all of them are transferred.
    """
    type_of_integration = request.type_of_integration
    setting_types: set[SettingType] = set()
    if type_of_integration.user_integrations:
        setting_types.add(SettingType.USER)
    if type_of_integration.project_integrations:
        setting_types.add(SettingType.PROJECT)

    logger.info(
        "settings_transfer_requested: actor_user_id=%s mode=%s source=%r target=%r types=%s list=%s",
        user.id,
        request.mode.value,
        request.source_project_name,
        request.target_project_name,
        {t.value for t in setting_types},
        request.integrations_list,
    )

    return SettingsTransferService.transfer(
        source_project_name=request.source_project_name,
        target_project_name=request.target_project_name,
        mode=request.mode,
        setting_types=setting_types,
        integrations_list=request.integrations_list,
    )
