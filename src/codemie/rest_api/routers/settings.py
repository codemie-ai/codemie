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

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from codemie.configs.logger import logger
from codemie.rest_api.models.settings import SettingsScope, SettingType
from codemie.rest_api.models.settings_transfer import TransferSettingsRequest, TransferSettingsResponse
from codemie.rest_api.security.authentication import User, admin_or_maintainer_access_only, authenticate
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie.service.settings.settings_transfer_service import SettingsTransferService

router = APIRouter(
    tags=["Settings"],
    prefix="/v1",
    dependencies=[],
)


MAX_PER_PAGE = 100
# The row offset is page * per_page; this cap keeps it within PostgreSQL's bigint OFFSET at any allowed per_page.
MAX_PAGE = (2**63 - 1) // MAX_PER_PAGE


# response_model=None: without it FastAPI would derive a response model from the return annotation, changing the
# serialization and OpenAPI schema the per-scope lists share.
@router.get(
    "/settings",
    status_code=status.HTTP_200_OK,
    response_model=None,
)
def index_settings(
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    scope: SettingsScope = Query(SettingsScope.ALL),  # noqa: B008 - FastAPI dependency injection
    filters: str | None = None,
    page: int = Query(0, ge=0, le=MAX_PAGE),  # noqa: B008 - FastAPI dependency injection
    per_page: int = Query(10, ge=1, le=MAX_PER_PAGE),  # noqa: B008 - FastAPI dependency injection
) -> dict[str, Any]:
    """
    List the requester's user and project integrations as one paged list, newest first.

    ### Scope

    **`scope`** — which integrations to list. Defaults to `all`.

    | Value | Lists |
    |---|---|
    | `all` (default) | User and project integrations together |
    | `user` | User integrations only |
    | `project` | Project integrations only |

    Each integration is listed under the visibility rule of its own scope, the same rule as
    `GET /v1/settings/user` and `GET /v1/settings/project`.

    ### Filters

    **`filters`** — optional URL-encoded JSON object with the keys `search`, `project`, `type`,
    `created_by` and `is_global`. A `scope` key inside `filters` is ignored: use the `scope` parameter.
    Malformed JSON, or JSON that is not an object, returns 422. With `search`, project-name matches are
    listed first, then newest first.

    ### Paging

    **`page`** is zero-based, from 0 up to 92233720368547758 (the cap keeps the row offset within the
    database's 64-bit range); **`per_page`** is from 1 to 100. A value out of range returns 422.
    """
    return SettingsIndexService.run(
        user=user, scopes=scope.setting_types, page=page, per_page=per_page, filters=filters
    )


@router.post(
    "/settings/transfer",
    status_code=status.HTTP_200_OK,
    response_model=TransferSettingsResponse,
    dependencies=[Depends(authenticate), Depends(admin_or_maintainer_access_only)],
)
def transfer_settings(
    request: TransferSettingsRequest,
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
):
    """
    Transfer integrations from one project to another.

    Requires administrator or maintainer privileges.

    ### Modes

    **`move`** — reassigns each integration to the target project. Integration IDs are unchanged,
    so assistants and other entities that reference integrations by ID continue to work without
    reconfiguration. Datasources are the exception: any datasource left behind in the source
    project has its `setting_id` cleared, so it fails visibly instead of silently pulling
    credentials from another project's integration.

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
