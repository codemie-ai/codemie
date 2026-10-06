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

from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.routers.assistant import _filter_invalid_datasources

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")

KEYS = "codemie.service.assistant_service.AssistantService._existing_context_keys"


def test_filter_invalid_datasources_uses_service_lookup():
    assistant = MagicMock(spec=Assistant)
    assistant.project = "proj"
    assistant.name = "asst"
    assistant.context = [KB, CODE_GONE]
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}):
        _filter_invalid_datasources(assistant)
    assert assistant.context == [KB]


def test_filter_invalid_datasources_no_context_is_noop():
    assistant = MagicMock(spec=Assistant)
    assistant.context = []
    with patch(KEYS) as keys:
        _filter_invalid_datasources(assistant)
    keys.assert_not_called()
