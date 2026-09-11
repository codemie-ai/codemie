# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
from typing import List, Optional

from codemie.datasource.base_llm_routing_processor import BaseLLMRoutingDatasourceProcessor
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.security.user import User
from codemie.rest_api.models.index import (
    IndexInfo,
)
from codemie.service.llm_service.llm_service import llm_service
from codemie.configs import logger
from codemie.datasource.loader.google_doc_loader import GoogleDocLoader
from codemie.datasource.exceptions import (
    ConnectionException,
    InvalidQueryException,
    UnauthorizedException,
)
from codemie.service.constants import FullDatasourceTypes
from codemie.service.google_oauth.token_manager import GoogleOAuthTokenManager
from codemie.core.exceptions import ExtendedHTTPException


class GoogleDocDatasourceProcessor(BaseLLMRoutingDatasourceProcessor):
    INDEX_TYPE = FullDatasourceTypes.GOOGLE.value

    def __init__(
        self,
        *,
        datasource_name: str,
        project_name: str,
        google_doc: str,
        description: str = "",
        project_space_visible: bool = False,
        user: Optional[User] = None,
        index_info: Optional[IndexInfo] = None,
        callbacks: Optional[list] = None,
        request_uuid: Optional[str] = None,
        embedding_model: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        cron_expression: Optional[str] = None,
        setting_id: Optional[str] = None,
    ):
        self.project_name = project_name
        self.description = description
        self.google_doc = google_doc
        self.product_id = self._parse_google_doc_id(google_doc)
        self.project_space_visible = project_space_visible
        self.embedding_model = embedding_model
        self.setting_id = setting_id
        self._access_token: Optional[str] = None

        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=cron_expression,
        )

    def _init_loader(self):
        if self.setting_id:
            token_manager = GoogleOAuthTokenManager()
            try:
                access_token = token_manager.get_valid_access_token(self.setting_id)
            except ExtendedHTTPException as e:
                raise ValueError(f"Failed to fetch Google OAuth credentials: {e.message}") from e
            self._access_token = access_token
        else:
            raise ValueError(
                "This Google Docs datasource was created using a deprecated service account authentication method. "
                "OAuth authentication is now required to access Google Docs. "
                "Please delete this datasource and create a new one with OAuth credentials to continue."
            )

        return GoogleDocLoader(product_id=self.product_id, access_token=self._access_token)

    def _init_index(self):
        if not self.index:
            # this also handles index creation if it not exists
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                google_doc_link=self.google_doc,
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )

        self._assign_and_sync_guardrails()

    def _process(self) -> int:
        (
            documents,
            titles,
            document_id,
        ) = self.loader.load_with_extra()

        # to add ability to reindex old indicies
        if not documents:
            raise ValueError("Trying to index empty datasource, use different URL or document")

        self._apply_guardrails_for_documents(documents)
        self._add_documents(documents)
        self._update_kb_info(document_id)
        self._save_table_of_contents(titles)
        return len(documents)

    @classmethod
    def _parse_google_doc_id(cls, url):
        # Accept any /d/{id} spelling: with or without /edit, with query params,
        # /mobilebasic, /view etc. Requiring "/edit" silently produced an empty
        # document id (and a confusing Google HTTP 400) for share-dialog links.
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
        if match:
            return match.group(1)
        else:
            logger.error(f"Invalid Google Doc URL field {url}")
            return ""

    @classmethod
    def check_google_doc(cls, product_id: str, access_token: Optional[str] = None) -> None:
        loader = GoogleDocLoader(product_id=product_id, access_token=access_token)
        try:
            loader.check_accessible()
        except (UnauthorizedException, ConnectionException):
            raise
        except Exception as e:
            raise InvalidQueryException(str(e))
