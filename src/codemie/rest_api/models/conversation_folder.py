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

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.core.ability import Owned, Action
from codemie.rest_api.security.user import User
from sqlmodel import Field as SQLField, Column
from sqlalchemy.dialects.postgresql import JSONB

FOLDER_NAME_KEYWORD = "folder_name.keyword"
USER_ID_KEYWORD = "user_id.keyword"


class ConversationFolder(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "conversation_folders"

    folder_name: str
    user_id: str = SQLField(index=True)

    user_abilities: Optional[List[Action]] = SQLField(default_factory=list, sa_column=Column(JSONB))

    @classmethod
    def get_by_folder(cls, folder_name: str, user_id: str):
        return cls.get_by_fields({FOLDER_NAME_KEYWORD: folder_name, USER_ID_KEYWORD: user_id})

    def is_owned_by(self, user: User):
        return self.user_id == user.id

    def is_managed_by(self, user: User):
        return False

    def is_shared_with(self, user: User):
        return False

    def validate_fields(self) -> Optional[str]:
        folder = self.get_by_fields({FOLDER_NAME_KEYWORD: self.folder_name, USER_ID_KEYWORD: self.user_id})
        if folder and folder.id != self.id:
            return 'Folder name should be unique'
        if self.folder_name == "Default":
            return 'This folder name is forbidden'
        return ""

    @classmethod
    def delete_by_folder(cls, folder_name: str, user_id: str):
        folder = cls.get_by_fields({FOLDER_NAME_KEYWORD: folder_name, USER_ID_KEYWORD: user_id})
        if folder:
            return folder.delete()
        return {"status": "not found"}

    @classmethod
    def delete_by_user(cls, user_id: str):
        folders = cls.get_all_by_fields({USER_ID_KEYWORD: user_id})
        if folders:
            for folder in folders:
                folder.delete()
        return {"status": "removed"}

    @classmethod
    def create_folder(cls, folder_name: str, user_id: str) -> ConversationFolder:
        """
        Create a new conversation folder.

        Args:
            folder_name: Name of the folder to create
            user_id: ID of the user who owns the folder

        Returns:
            The created ConversationFolder instance
        """
        folder_record = cls(
            folder_name=folder_name,
            user_id=user_id,
        )
        folder_record.save(refresh=True)
        return folder_record

    @classmethod
    def touch_folder(cls, folder_name: str, user_id: str) -> None:
        """
        Update the update_date timestamp for a folder.
        This should be called whenever a conversation in the folder is created or updated.

        Args:
            folder_name: Name of the folder to update
            user_id: ID of the user who owns the folder
        """
        if not folder_name:
            # Skip for conversations without a folder
            return

        folder = cls.get_by_folder(folder_name, user_id)
        if folder:
            folder.update_date = datetime.now(timezone.utc)
            folder.update(refresh=False)
