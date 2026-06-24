# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import copy
import json
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage

from codemie.configs import logger
from codemie.core.constants import ChatRole
from codemie.core.models import ChatMessage
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig
from codemie.service.aws_bedrock.agentcore.utils import set_json_path


class AgentcoreRequestBuilder:
    """Builds the JSON payload sent to an AgentCore runtime endpoint.

    Serialises the user query into the structure expected by the runtime,
    using the dot-notation ``message_path`` from ``AgentcoreRequestConfig``
    to place the query at the correct key in the request body.

    When ``config.history`` is set and a non-empty ``history`` list is supplied,
    each turn is serialised into a turn dict and written at ``config.history.history_path``.
    If ``config.history`` is ``None`` the history argument is always ignored.
    """

    def __init__(self, config: AgentcoreRequestConfig):
        self._config = config

    def build(self, user_query: str, history: Optional[List[Any]] = None) -> bytes:
        """Construct and serialise the runtime request payload.

        Returns UTF-8 encoded JSON with the user query at ``config.message_path``
        and, when configured, the history turns array at ``config.history.history_path``.
        """
        payload: dict = copy.deepcopy(self._config.extra_payload) if self._config.extra_payload else {}
        set_json_path(payload, self._config.message_path, user_query)

        if self._config.history and history:
            self._inject_history(payload, history)

        if self._config.history and history:
            turns = [self._to_history_turn(msg) for msg in history]
            logger.debug("[AgentCore] History injected at %r: %s", self._config.history.history_path, turns)
        return json.dumps(payload).encode("utf-8")

    def _inject_history(self, payload: dict, history: List[Any]) -> None:
        turns = [self._to_history_turn(msg) for msg in history]
        set_json_path(payload, self._config.history.history_path, turns)

    def _to_history_turn(self, msg: Any) -> dict:
        """Convert a ChatMessage or LangChain BaseMessage to a configured turn dict."""
        cfg = self._config.history

        if isinstance(msg, ChatMessage):
            is_user, content = msg.role == ChatRole.USER, msg.message or ""
        else:
            is_user, content = isinstance(msg, HumanMessage), str(msg.content)

        role = cfg.user_role if is_user else cfg.assistant_role
        return {cfg.role_path: role, cfg.message_path: content}
