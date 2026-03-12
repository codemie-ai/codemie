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

from abc import ABC, abstractmethod
from typing import List, Optional

from codemie.core.workflow_models.workflow_config import WorkflowConfigBase
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.guardrail import Guardrail
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.vendor import ImportEntityBase
from codemie.rest_api.security.user import User

ALL_SETTINGS_OVERVIEW_ENTITY_COUNT = 4


class BaseBedrockService(ABC):
    @staticmethod
    @abstractmethod
    def get_all_settings_overview(user: User, page: int, per_page: int):
        pass

    @staticmethod
    @abstractmethod
    def list_main_entities(
        user: User,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return [], None

    @staticmethod
    @abstractmethod
    def get_main_entity_detail(
        user: User,
        main_entity_id: str,
        setting_id: str,
    ) -> dict:
        pass

    @staticmethod
    @abstractmethod
    def list_importable_entities_for_main_entity(
        user: User,
        main_entity_id: str,
        setting_id: str,
        page: int,
        per_page: int,
        next_token: Optional[str] = None,
    ) -> tuple[List[dict], Optional[str]]:
        return [], None

    @staticmethod
    @abstractmethod
    def get_importable_entity_detail(
        user: User,
        main_entity_id: str,
        importable_entity_detail: str,
        setting_id: str,
    ):
        pass

    @staticmethod
    @abstractmethod
    def import_entities(user: User, import_payload: dict[str, List[ImportEntityBase]]):
        pass

    @staticmethod
    @abstractmethod
    def delete_entities(setting_id: str):
        pass

    @staticmethod
    @abstractmethod
    def validate_remote_entity_exists_and_cleanup(entity: Assistant | WorkflowConfigBase | Guardrail | IndexInfo):
        pass
