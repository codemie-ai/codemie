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

import pytest

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.service.assistant_service import AssistantService

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")
PROVIDER_GONE = Context(context_type=ContextType.PROVIDER, name="prov-deleted")

KEYS = "codemie.service.assistant_service.AssistantService._existing_context_keys"


def _assistant(context):
    assistant = MagicMock(spec=Assistant)
    assistant.project = "proj"
    assistant.name = "asst"
    assistant.id = "asst-1"
    assistant.context = context
    return assistant


@pytest.mark.parametrize("context", [None, []])
def test_drop_missing_context_no_context_skips_query(context):
    assistant = _assistant(context)
    with patch(KEYS) as keys:
        assert AssistantService.drop_missing_context(assistant) == []
    keys.assert_not_called()
    assert assistant.context == context


def test_drop_missing_context_all_present_keeps_everything():
    assistant = _assistant([KB])
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}):
        assert AssistantService.drop_missing_context(assistant) == []
    assert assistant.context == [KB]


def test_drop_missing_context_drops_deleted_code_and_provider():
    assistant = _assistant([KB, CODE_GONE, PROVIDER_GONE])
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}) as keys:
        dropped = AssistantService.drop_missing_context(assistant)
    keys.assert_called_once_with("proj", {"kb-alive", "repo-deleted", "prov-deleted"})
    assert dropped == [CODE_GONE, PROVIDER_GONE]
    assert assistant.context == [KB]


def test_drop_missing_context_drops_deleted_kb_keeps_code():
    code_alive = Context(context_type=ContextType.CODE, name="repo-alive")
    kb_gone = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-deleted")
    assistant = _assistant([kb_gone, code_alive])
    with patch(KEYS, return_value={("repo-alive", ContextType.CODE)}):
        dropped = AssistantService.drop_missing_context(assistant)
    assert dropped == [kb_gone]
    assert assistant.context == [code_alive]


def test_existing_context_keys_maps_index_types_to_context_types():
    rows = [
        ("kb", "knowledge_base_confluence"),
        ("repo", "code"),
        ("svn-repo", "svn"),
        ("prov", "provider"),
    ]
    with patch("codemie.service.assistant_service.Session") as session_cls:
        session = session_cls.return_value.__enter__.return_value
        session.exec.return_value.all.return_value = rows
        keys = AssistantService._existing_context_keys("proj", {"kb", "repo", "svn-repo", "prov"})
    assert keys == {
        ("kb", ContextType.KNOWLEDGE_BASE),
        ("repo", ContextType.CODE),
        ("svn-repo", ContextType.CODE),
        ("prov", ContextType.PROVIDER),
    }
    session.exec.assert_called_once()


def test_drop_missing_context_same_name_other_type_is_missing():
    assistant = _assistant([CODE_GONE])
    with patch(KEYS, return_value={("repo-deleted", ContextType.KNOWLEDGE_BASE)}):
        dropped = AssistantService.drop_missing_context(assistant)
    assert dropped == [CODE_GONE]
    assert assistant.context == []


def test_drop_missing_context_assigns_new_list_and_never_persists():
    original = [KB, CODE_GONE]
    assistant = _assistant(original)
    with patch(KEYS, return_value={("kb-alive", ContextType.KNOWLEDGE_BASE)}):
        AssistantService.drop_missing_context(assistant)
    assert original == [KB, CODE_GONE]
    assert assistant.context is not original
    assistant.update.assert_not_called()
    assistant.save.assert_not_called()
