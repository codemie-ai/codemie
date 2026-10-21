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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from codemie.rest_api.security.authentication import authenticate
from codemie.chains.base import Thought
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.security.user import User

HIDDEN = "assistant-hidden"
OPEN = "assistant-open"
VIS = "codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids_no_permission_check"
R = "codemie.rest_api.routers.conversation"
H = {"Authorization": "Bearer t"}


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def client():
    return TestClient(app)


def _msg(assistant_id, role="Assistant"):
    return GeneratedMessage(
        message="answer",
        role=role,
        assistant_id=assistant_id,
        thoughts=[Thought(id="t1", author_name="Tool", message="secret output")],
    )


@pytest.fixture
def conversation():
    return Conversation(
        id="c1",
        conversation_id="c1",
        name="Conv",
        initial_assistant_id=HIDDEN,
        assistant_ids=[HIDDEN, OPEN],
        history=[_msg(HIDDEN), _msg(OPEN)],
    )


def _lookup(flagged):
    """Fake unfiltered assistant lookup: ids in `flagged` have hide_tool_outputs=True."""
    return lambda ids: [SimpleNamespace(id=i, hide_tool_outputs=i in flagged) for i in ids]


def _assistant(aid, hide):
    a = MagicMock()
    a.id, a.name, a.icon_url, a.context, a.conversation_starters = aid, aid, None, [], []
    a.toolkits, a.type, a.hide_tool_outputs = [], None, hide
    return a


def test_detail_strips_thoughts_for_flagged_assistant_and_exposes_flag(client, conversation):
    with (
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", return_value=conversation),
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.models.assistant.Assistant.get_by_ids",
            return_value=[_assistant(HIDDEN, True), _assistant(OPEN, False)],
        ),
        patch(VIS, side_effect=_lookup({HIDDEN})),
    ):
        body = client.get("/v1/conversations/c1", headers=H).json()

    assert body["history"][0]["thoughts"] == []
    assert body["history"][1]["thoughts"][0]["message"] == "secret output"
    flags = {a["assistant_id"]: a["hide_tool_outputs"] for a in body["assistant_data"]}
    assert flags == {HIDDEN: True, OPEN: False}


def test_detail_does_not_persist_stripped_history(client, conversation):
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]),
        patch(VIS, side_effect=_lookup({HIDDEN})),
        patch.object(Conversation, "update") as update,
    ):
        client.get("/v1/conversations/c1", headers=H)

    update.assert_not_called()
    assert conversation.history[0].thoughts  # stored object untouched


def test_detail_paginated_branch_also_stripped(client, conversation):
    with (
        patch(
            "codemie.service.conversation_service.ConversationService.get_conversation_history_slice",
            return_value=(conversation, 2, None, None),
        ),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]),
        patch(VIS, side_effect=_lookup({HIDDEN})),
    ):
        body = client.get("/v1/conversations/c1?page=0&per_page=10", headers=H).json()

    assert body["history"][0]["thoughts"] == []
    assert body["history"][1]["thoughts"]


def test_flag_off_regression_history_unchanged(client, conversation):
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[_assistant(HIDDEN, False)]),
        patch(VIS, side_effect=_lookup(set())),
    ):
        body = client.get("/v1/conversations/c1", headers=H).json()

    assert all(m["thoughts"] for m in body["history"])
    assert body["assistant_data"][0]["hide_tool_outputs"] is False


def test_turning_flag_off_restores_thoughts(client, conversation):
    flagged = {HIDDEN}
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]),
        patch(VIS, side_effect=lambda ids: _lookup(flagged)(ids)),
    ):
        first = client.get("/v1/conversations/c1", headers=H).json()
        flagged.clear()
        second = client.get("/v1/conversations/c1", headers=H).json()

    assert first["history"][0]["thoughts"] == []
    assert second["history"][0]["thoughts"][0]["message"] == "secret output"


def test_stream_replay_stripped_when_initial_assistant_hidden(client, conversation):
    chunks = [
        json.dumps({"thought": {"id": "1", "message": "secret"}, "last": False}) + "\n",
        json.dumps({"generated_chunk": "hi", "thought": {"id": "2"}, "last": False}) + "\n",
    ]
    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.routers.conversation.streaming.GenerationManager") as gm,
        patch("codemie.rest_api.routers.conversation.streaming.stream_generator", return_value=iter(chunks)),
        patch(VIS, side_effect=_lookup({HIDDEN})),
    ):
        gm.return_value.get_generator.return_value.subscribe.return_value = ([], MagicMock(), True)
        text = client.get("/v1/conversations/c1/stream", headers=H).text

    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["generated_chunk"] == "hi" and lines[0]["thought"] is None


def _export_patches(conversation):
    return (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]),
        patch(VIS, side_effect=_lookup({HIDDEN})),
    )


def test_json_export_strips(client, conversation):
    p = _export_patches(conversation)
    with p[0], p[1], p[2], p[3], p[4]:
        resp = client.get("/v1/conversations/c1/export?export_format=json", headers=H)

    history = json.loads(resp.content)["history"]
    assert not history[0].get("thoughts")
    assert history[1]["thoughts"]
    assert conversation.history[0].thoughts


def test_docx_export_and_single_message_export_receive_stripped_conversation(client, conversation):
    p = _export_patches(conversation)
    seen = []

    def fake_exporter(**kwargs):
        seen.append(kwargs["conversation"])
        svc = MagicMock(content_type="application/octet-stream", filename="f.docx")
        svc.run.return_value = iter([b"ok"])
        svc.export_single_message.return_value = iter([b"ok"])
        return svc

    with (
        p[0],
        p[1],
        p[2],
        p[3],
        p[4],
        patch("codemie.rest_api.routers.conversation.history.MessageExporter", side_effect=fake_exporter),
        patch("codemie.rest_api.routers.conversation.messages.MessageExporter", side_effect=fake_exporter),
    ):
        client.get("/v1/conversations/c1/export?export_format=docx", headers=H)
        client.get("/v1/conversations/c1/history/0/0/export?export_format=docx", headers=H)

    assert len(seen) == 2
    for conv in seen:
        assert not conv.history[0].thoughts
        assert conv.history[1].thoughts
    assert conversation.history[0].thoughts


MUTATIONS = [
    ("delete", "/v1/conversations/c1/history/0", "remove_conversation_history_index", None),
    (
        "put",
        "/v1/conversations/c1/history/1",
        "update_conversation_ai_message",
        {"message": "edited", "history_index": 1},
    ),
    ("put", "/v1/conversations/c1", "update_conversation", {"name": "renamed"}),
]


def _call_mutation(client, method, url, service_method, payload, conversation):
    with (
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", return_value=conversation),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(f"codemie.service.conversation_service.ConversationService.{service_method}", return_value=conversation),
        patch.object(Conversation, "update") as update,
    ):
        resp = client.request(method, url, headers=H, json=payload)
    return resp, update


@pytest.mark.parametrize("method,url,service_method,payload", MUTATIONS)
def test_mutation_response_strips_thoughts_but_stored_conversation_keeps_them(
    client, conversation, method, url, service_method, payload
):
    with patch(VIS, side_effect=_lookup({HIDDEN})):
        resp, update = _call_mutation(client, method, url, service_method, payload, conversation)

    assert resp.status_code == 200, resp.text
    history = resp.json()["history"]
    assert history[0]["thoughts"] == []
    assert history[1]["thoughts"][0]["message"] == "secret output"
    update.assert_not_called()
    assert conversation.history[0].thoughts[0].message == "secret output"


@pytest.mark.parametrize("method,url,service_method,payload", MUTATIONS)
def test_mutation_response_unchanged_when_flag_off(client, conversation, method, url, service_method, payload):
    with patch(VIS, side_effect=_lookup(set())):
        resp, _ = _call_mutation(client, method, url, service_method, payload, conversation)

    assert resp.status_code == 200, resp.text
    assert all(m["thoughts"] for m in resp.json()["history"])
