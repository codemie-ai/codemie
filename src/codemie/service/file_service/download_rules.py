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

"""Ownership rules deciding whether a user may download a stored file."""

from __future__ import annotations

from codemie_tools.base.file_object import FileObject

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.ability import Ability, Action
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.repository.repository_factory import MCP_IMAGES_SUBDIR
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.security.user import User
from codemie.service.file_service.blob_ref_rules import is_safe_blob_ref
from codemie.service.share_conversation_service import collect_message_file_tokens

_WORKFLOW_SCHEMA_PREFIX = "workflows/"
_WORKFLOW_SCHEMA_SUFFIX = ".svg"
_WORKSPACE_OWNER_PREFIX = "workspace-"


def _can_read_workflow_schema(name: str, user: User) -> bool:
    """Rule B helper: a workflow schema SVG is readable only by users who may read the workflow.

    Schema blobs are written as ``workflows/{workflow_id}.svg`` (workflow_service.save_workflow_schema)
    under the shared CODEMIE_STORAGE_BUCKET_NAME owner. That same owner namespace also holds
    memory-profiling snapshots, so the name shape is checked first and everything else is denied.
    Fails closed: any lookup error denies access rather than surfacing a 500 from the auth path.
    """
    if not name.startswith(_WORKFLOW_SCHEMA_PREFIX) or not name.endswith(_WORKFLOW_SCHEMA_SUFFIX):
        return False

    workflow_id = name[len(_WORKFLOW_SCHEMA_PREFIX) : -len(_WORKFLOW_SCHEMA_SUFFIX)]
    if not workflow_id:
        return False

    try:
        workflow_config = WorkflowConfig.find_by_id(workflow_id)
        return workflow_config is not None and Ability(user).can(Action.READ, workflow_config)
    except Exception:
        logger.warning(f"Workflow schema authorization lookup failed for workflow_id={workflow_id!r}")
        return False


def _is_readable_workflow_schema(file_object: FileObject, user: User) -> bool:
    """Rule B: a workflow schema SVG, and only for a workflow this user may read.

    The same owner namespace also holds memory-profiling snapshots, so the name shape is
    checked before anything else and everything outside `workflows/` is denied.
    """
    bucket = config.CODEMIE_STORAGE_BUCKET_NAME
    return bool(bucket) and file_object.owner == bucket and _can_read_workflow_schema(file_object.name, user)


def _owns_workspace_blob(owner: str, user: User, workspace_repo: AgentWorkspaceRepository) -> bool:
    """Rule D: a workspace-prefixed blob whose workspace belongs to the requester."""
    if not owner.startswith(_WORKSPACE_OWNER_PREFIX):
        return False

    workspace_id = owner.removeprefix(_WORKSPACE_OWNER_PREFIX)
    return workspace_repo.get_by_id_for_user(workspace_id, user.id) is not None


def _conversation_refers_to_file(conversation: Conversation, file_name_param: str) -> bool:
    """True when any message of the conversation references this exact encoded token.

    Uses the same collector the share endpoint issues grants from, so the set this authorizes and
    the set the recipient was handed URLs for cannot drift apart. That covers both carriers:
    `file_names` entries and inline `sandbox:/v1/files/<token>` references in message text.
    """
    return any(file_name_param in collect_message_file_tokens(message) for message in (conversation.history or []))


def _has_share_grant(
    file_object: FileObject,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> bool:
    """Rule E: the file belongs to a conversation shared with the holder of this token.

    Two carriers are accepted: the token appears in a message's `file_names`, or the blob is
    registered in the sharer's workspace. The second covers files referenced only by an inline
    `sandbox:/v1/files/...` URL, including blobs registered by reference under their original
    owner rather than under the workspace.
    """
    if not share_token:
        return False

    shared = SharedConversation.get_by_fields({"share_token.keyword": share_token})
    if not shared:
        return False

    conversation = Conversation.find_by_id(shared.conversation_id)
    if not conversation:
        return False

    if _conversation_refers_to_file(conversation, file_name_param):
        return True

    workspace = workspace_repo.get_by_conversation_for_user(shared.conversation_id, shared.shared_by_user_id)
    if not workspace:
        return False

    return (
        workspace_repo.find_by_blob(workspace.id, file_object.owner, file_object.name, file_object.mime_type)
        is not None
    )


def can_download(
    file_object: FileObject,
    user: User,
    share_token: str | None,
    file_name_param: str,
    workspace_repo: AgentWorkspaceRepository,
) -> bool:
    """True when the user may download the file. Rules are evaluated in order."""
    owner = file_object.owner

    # Before any rule: a token whose owner or name escapes the owner's directory is never served,
    # however the rules below would classify it.
    if not is_safe_blob_ref(file_object):
        return False

    # Rule A: requester owns the file directly.
    if owner == user.id:
        return True

    # Rule B: workflow schema, and only for a workflow this user may read.
    if _is_readable_workflow_schema(file_object, user):
        return True

    # Rule C: MCP screenshot stored under the shared namespace. Screenshots are normally written
    # under the invoking user's id (see _save_screenshot_to_storage) and so are covered by Rule A;
    # this rule covers blobs written before that change, plus any written when no invoking user was
    # known. Such blobs are readable by any authenticated user who has the name - accepted residual
    # risk: names are uuid4().hex, and denying them would break images in existing conversations.
    # The unattributed-write path is logged, so whether it still occurs is observable rather than
    # assumed; the rule can be tightened once that log stays silent and history has aged out.
    if owner == MCP_IMAGES_SUBDIR:
        return True

    # Rule D: workspace blob the requester owns. A miss falls through to Rule E, which still
    # grants a share recipient viewing the sharer's workspace files.
    if _owns_workspace_blob(owner, user, workspace_repo):
        return True

    # Rule E: share grant.
    return _has_share_grant(file_object, share_token, file_name_param, workspace_repo)
