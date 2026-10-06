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


import json
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.models.index import IndexInfo
from codemie.service.assistant import datasource_cleanup
from codemie.service.assistant.datasource_cleanup import detach_datasource_from_assistants

MODULE = "codemie.service.assistant.datasource_cleanup"
DELETED = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-x")
OTHER_KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-other")
SAME_NAME_CODE = Context(context_type=ContextType.CODE, name="kb-x")


def _datasource():
    return IndexInfo(project_name="proj", repo_name="kb-x", index_type="knowledge_base_file", description="d")


def _assistant(context, project="proj"):
    assistant = MagicMock(spec=Assistant)
    assistant.project = project
    assistant.context = list(context)
    return assistant


def _run(assistants, still_exists=False):
    session = MagicMock()
    session.exec.return_value.all.return_value = assistants
    with (
        patch(f"{MODULE}._same_key_still_exists", return_value=still_exists),
        patch(f"{MODULE}.Session") as session_cls,
    ):
        session_cls.return_value.__enter__.return_value = session
        updated = detach_datasource_from_assistants(_datasource())
    return updated, session


def test_removes_datasource_keeps_others():
    assistant = _assistant([OTHER_KB, DELETED])
    updated, session = _run([assistant])
    assert updated == 1
    assert assistant.context == [OTHER_KB]
    session.add.assert_called_once_with(assistant)
    session.commit.assert_called_once()


def test_keeps_same_name_other_type():
    assistant = _assistant([SAME_NAME_CODE, DELETED])
    _run([assistant])
    assert assistant.context == [SAME_NAME_CODE]


def test_last_datasource_leaves_empty_list():
    assistant = _assistant([DELETED])
    _run([assistant])
    assert assistant.context == []


def test_skips_assistant_from_other_project():
    assistant = _assistant([DELETED], project="another")
    updated, session = _run([assistant])
    assert updated == 0
    assert assistant.context == [DELETED]
    session.add.assert_not_called()


def test_guard_skips_when_same_key_still_exists():
    assistant = _assistant([DELETED])
    with patch(f"{MODULE}._same_key_still_exists", return_value=True), patch(f"{MODULE}.Session") as session_cls:
        assert detach_datasource_from_assistants(_datasource()) == 0
    session_cls.assert_not_called()
    assert assistant.context == [DELETED]


def test_query_scoped_to_project():
    _, session = _run([])
    statement = session.exec.call_args.args[0]
    where = str(statement.whereclause.compile())
    assert "assistants.project =" in where
    assert "assistants.context" in where


def test_same_key_still_exists_matches_type():
    session = MagicMock()
    session.exec.return_value.all.return_value = ["code"]
    with patch(f"{MODULE}.Session") as session_cls:
        session_cls.return_value.__enter__.return_value = session
        assert datasource_cleanup._same_key_still_exists("proj", "kb-x", ContextType.KNOWLEDGE_BASE) is False
        assert datasource_cleanup._same_key_still_exists("proj", "kb-x", ContextType.CODE) is True


def test_query_payload_serialises_to_stored_shape():
    _, session = _run([])
    statement = session.exec.call_args.args[0]
    dialect = postgresql.dialect()
    compiled = statement.compile(dialect=dialect)
    payloads = [
        bind.type.bind_processor(dialect)(bind.value)
        for bind in compiled.binds.values()
        if isinstance(bind.value, list)
    ]
    assert payloads
    assert all(json.loads(payload) == [{"name": "kb-x", "context_type": "knowledge_base"}] for payload in payloads)
