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

import secrets
import string
from datetime import datetime
from typing import Optional

from codemie.configs import logger
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from sqlmodel import Field as SQLField, Session, delete


class SharedConversation(BaseModelWithSQLSupport, table=True):
    """
    Model representing a shared conversation that can be accessed via a public link.
    """

    __tablename__ = "shared_conversations"

    share_id: str  # Unique identifier for the share
    conversation_id: str = SQLField(index=True)  # Reference to the original conversation
    shared_by_user_id: str = SQLField(index=True)  # ID of the user who shared the conversation
    shared_by_user_name: Optional[str] = None  # Name of the user who shared the conversation
    created_at: datetime = SQLField(default_factory=datetime.now)
    access_count: int = 0  # Number of times this share has been accessed
    share_token: str = SQLField(index=True)  # Random token used in the share URL for security

    @classmethod
    def generate_share_token(cls, length: int = 12) -> str:
        """
        Generate a secure random token for sharing.
        """
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    def increment_access_count(self) -> None:
        """
        Increment the access count when the share is viewed.
        """
        self.access_count += 1
        self.update()

    @classmethod
    def delete_by_conversation(cls, conversation_id: str):
        try:
            with Session(cls.get_engine()) as session:
                statement = delete(cls).where(cls.conversation_id == conversation_id)
                result = session.exec(statement)
                session.commit()
        except Exception as e:
            logger.error(f"Error deleting shared conversation by conversation_id {conversation_id}: {e}")
            result = None

        return result

    @classmethod
    def delete_by_user_who_shared(cls, user_id: str):
        try:
            with Session(cls.get_engine()) as session:
                statement = delete(cls).where(cls.shared_by_user_id == user_id)
                result = session.exec(statement)
                session.commit()
        except Exception as e:
            logger.error(f"Error deleting shared conversation by user {user_id}: {e}")
            result = None
        return result
