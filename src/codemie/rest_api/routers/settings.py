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
    Move or copy every transferable integration from one project to another.

    Requires administrator or maintainer privileges. Both project-scoped and user-scoped
    integrations are transferred; the original owner and creator are preserved in every case,
    and only which project the integration is attached to changes.

    **`move`** updates each integration in place, so entities that reference an integration by
    id (datasources, assistants, Bedrock entities, A2A) keep working. References made *by alias*
    resolve against the referencing entity's own project and will fail after a move: workflow
    tool nodes raise an error, per-user MCP integration overrides are ignored, and re-saving an
    assistant-user mapping to a moved integration is rejected. This matches existing behaviour
    for integrations that become unavailable.

    **`copy`** duplicates each integration into the target project and leaves the source
    untouched. Webhook and scheduler integrations are **not** copied — copying a trigger would
    duplicate its firing — and are listed in `skipped`. They are transferred normally by `move`.

    The operation is all-or-nothing: if any alias already exists in the target project, or either
    project is missing, nothing is changed.
    """
    logger.info(
        "settings_transfer_requested: actor_user_id=%s mode=%s source=%r target=%r",
        user.id,
        request.mode.value,
        request.source_project_name,
        request.target_project_name,
    )

    return SettingsTransferService.transfer(
        source_project_name=request.source_project_name,
        target_project_name=request.target_project_name,
        mode=request.mode,
    )
