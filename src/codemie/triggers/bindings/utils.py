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

from codemie.configs import config, logger
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User, stand_in_user
from codemie.service.constants import FullDatasourceTypes
from codemie.service.user.user_by_id_loader import UserLookup, load_user_by_id
from codemie.triggers.trigger_exceptions import DatasourceNotValidated, NotImplementedDatasource


def resolve_trigger_user(user_id: str) -> User | None:
    """Return the trigger owner as an interactive request would see them, or None when the owner is not active.

    Budget attribution needs the owner's real projects and default project (EPMCDME-16625 D7). The owner is loaded
    as LocalIdp loads a signed loopback call (AR-10), so a run and its callbacks see the same owner. A stand-in is
    only used for an owner with no user row on ENV local, or when user management is off: that mode keeps no user
    rows and has no deactivation, so the owner's last login (claims snapshot) stands in when it is fresh.
    """
    lookup = _load_trigger_owner(user_id)
    if lookup.user is not None:
        return lookup.user
    if _may_stand_in(lookup):
        return stand_in_user(user_id, component="trigger_owner")
    logger.warning("resolve_trigger_user: owner user_id=%r is not active, skipping the job", user_id)
    return None


def is_trigger_owner_active(user_id: str) -> bool:
    """Whether the owner may run a job. Checks only: no stand-in is built or logged, so it is counted once."""
    lookup = _load_trigger_owner(user_id)
    if lookup.user is not None or _may_stand_in(lookup):
        return True
    logger.warning("is_trigger_owner_active: owner user_id=%r is not active, skipping the job", user_id)
    return False


def _load_trigger_owner(user_id: str) -> UserLookup:
    from codemie.clients.postgres import get_session

    try:
        with get_session() as session:
            return load_user_by_id(session, user_id, with_snapshot=True)
    except Exception:
        logger.warning("resolve_trigger_user: DB lookup failed for user_id=%r", user_id)
        return UserLookup(None, has_row=False)


def _may_stand_in(lookup: UserLookup) -> bool:
    return (config.ENV == "local" or not config.ENABLE_USER_MANAGEMENT) and not lookup.has_row


def validate_assistant(assistant_id):
    """Validate assistant"""
    assistant = Assistant.find_by_id(id_=assistant_id)
    if not assistant:
        logger.error("Assistant not found: %s", assistant_id)
        return None
    logger.debug("Assistant validated: %s", assistant_id)
    return assistant


DATASOURCE_WITHOUT_SETTING_ID = [
    FullDatasourceTypes.GOOGLE,
    FullDatasourceTypes.GIT_FAQ,  # public FAQ repos reindex anonymously; private ones fail at clone time
    FullDatasourceTypes.PROVIDER,
]


def validate_datasource(datasource_id) -> IndexInfo | None:
    """Validate datasource"""
    ds = IndexInfo.find_by_id(id_=datasource_id)
    if not ds:
        logger.error("Datasource not found: %s", datasource_id)
        return None

    if ds.is_code_index() or ds.index_type in [
        FullDatasourceTypes.CONFLUENCE,
        FullDatasourceTypes.JIRA,
        FullDatasourceTypes.GOOGLE,
        FullDatasourceTypes.GIT_FAQ,
        FullDatasourceTypes.AZURE_DEVOPS_WIKI,
        FullDatasourceTypes.AZURE_DEVOPS_WORK_ITEM,
        FullDatasourceTypes.PROVIDER,
        FullDatasourceTypes.XWIKI,
        "knowledge_base_xray",
        "knowledge_base_sharepoint",
    ]:
        logger.debug("Datasource validated: %s", datasource_id)

        if not ds.setting_id and ds.index_type not in DATASOURCE_WITHOUT_SETTING_ID:
            logger.error("Datasource require setting_id: %s", datasource_id)
            raise DatasourceNotValidated(
                f"Datasource '{datasource_id}' is missing repository or setting ID.",
            )

        return ds

    raise NotImplementedDatasource(f"Datasource type '{ds.index_type}' is not supported via webhook.")
