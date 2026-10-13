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


from fastapi import APIRouter, status, Depends

from codemie.core.ability import Action
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.routers.assistant._shared import (
    _track_assistant_management_metric,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


router = APIRouter()


# ============================================================================
# Assistant Versioning Endpoints
# ============================================================================


@router.get(
    "/assistants/{assistant_id}/versions",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def get_assistant_versions(assistant_id: str, user: User = Depends(authenticate), page: int = 0, per_page: int = 20):
    """
    Get version history for an assistant.

    Returns paginated list of all versions with metadata.

    **Query Parameters:**
    - page: Page number (default: 0)
    - per_page: Number of versions per page (default: 20)

    **Returns:**
    - versions: list of version configurations
    - total_versions: Total number of versions
    - assistant_name: Assistant name
    - assistant_id: Assistant ID
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "view", Action.READ)

    return AssistantVersionService.get_version_history(assistant=assistant, page=page, per_page=per_page)


@router.get(
    "/assistants/{assistant_id}/versions/{version_number}",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def get_assistant_version(assistant_id: str, version_number: int, user: User = Depends(authenticate)):
    """
    Get a specific version of an assistant.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version_number: The version number to retrieve

    **Returns:**
    Complete configuration for the specified version
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "view", Action.READ)

    return AssistantVersionService.get_version(assistant_id=assistant_id, version_number=version_number)


@router.get(
    "/assistants/{assistant_id}/versions/{version1}/compare/{version2}",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def compare_assistant_versions(assistant_id: str, version1: int, version2: int, user: User = Depends(authenticate)):
    """
    Compare two versions of an assistant.

    Returns detailed differences between the two versions.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version1: First version number
    - version2: Second version number

    **Returns:**
    - version1: Complete first version configuration
    - version2: Complete second version configuration
    - differences: Detailed diff object
    - change_summary: Human-readable summary
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService
    from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService

    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "view", Action.READ)

    # Get both versions
    config1 = AssistantVersionService.get_version(assistant_id, version1)
    config2 = AssistantVersionService.get_version(assistant_id, version2)

    # Compare
    return AssistantVersionCompareService.compare_versions(
        assistant_id=assistant_id, version1=config1, version2=config2
    )


@router.post(
    "/assistants/{assistant_id}/versions/{version_number}/rollback",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    tags=["Assistant Versioning"],
)
def rollback_assistant_version(
    assistant_id: str,
    version_number: int,
    user: User = Depends(authenticate),
):
    """
    Rollback assistant to a previous version.

    Creates a new version with the configuration from the target version.
    This maintains version history immutability.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version_number: The version to rollback to

    **Returns:**
    Success message with the new version number created from rollback
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "update", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    try:
        new_config = AssistantVersionService.rollback_to_version(
            assistant=assistant, target_version_number=version_number, user=user, change_notes=None
        )

        _track_assistant_management_metric(
            "rollback_version", assistant, user, True, {"target_version": version_number}
        )

        return BaseResponse(
            message=f"Rolled back to version {version_number}. Created new version {new_config.version_number}"
        )

    except Exception as e:
        _track_assistant_management_metric(
            "rollback_version", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        raise
