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

from types import SimpleNamespace
from unittest.mock import MagicMock

from codemie.service.chat_naming_service import ChatNamingService
from codemie.service import chat_naming_service as chat_naming_module


def test_generate_name_returns_capped_llm_output(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content="A" * 80)
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="How do I set up OAuth?",
        assistant_response="Here's how to configure OAuth2 PKCE flow...",
        request_id="req-1",
    )

    assert name == "A" * 60


def test_generate_name_strips_surrounding_quotes(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content='"OAuth setup walkthrough"')
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="How do I set up OAuth?",
        assistant_response="...",
        request_id="req-1",
    )

    assert name == "OAuth setup walkthrough"


def test_generate_name_returns_none_on_llm_exception(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", raise_error)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_generate_name_returns_none_on_empty_response(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content="   ")
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_generate_name_returns_none_when_llm_returns_none(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = None
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_generate_name_returns_none_when_content_is_none(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content=None)
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_generate_name_extracts_text_from_list_content(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content=["OAuth setup ", "walkthrough"])
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="How do I set up OAuth?",
        assistant_response="...",
        request_id="req-1",
    )

    assert name == "OAuth setup walkthrough"


def test_rename_conversation_no_op_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService,
        "generate_name",
        classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not be called"))),
    )
    fake_conversation_cls = MagicMock()
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    fake_conversation_cls.find_by_id.assert_not_called()


def test_rename_conversation_updates_conversation_on_success(monkeypatch):
    monkeypatch.setattr(chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: "OAuth setup")
    )
    fake_conversation = MagicMock()
    fake_conversation_cls = MagicMock()
    fake_conversation_cls.find_by_id.return_value = fake_conversation
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    assert fake_conversation.conversation_name == "OAuth setup"
    fake_conversation.update.assert_called_once()


def test_rename_conversation_leaves_conversation_untouched_on_generation_failure(monkeypatch):
    monkeypatch.setattr(chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: None))
    fake_conversation_cls = MagicMock()
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    fake_conversation_cls.find_by_id.assert_not_called()


def test_rename_conversation_swallows_exception_from_find_by_id(monkeypatch):
    monkeypatch.setattr(chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: "OAuth setup")
    )
    fake_conversation_cls = MagicMock()
    fake_conversation_cls.find_by_id.side_effect = RuntimeError("db unavailable")
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )


def test_rename_conversation_swallows_exception_from_update(monkeypatch):
    monkeypatch.setattr(chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: "OAuth setup")
    )
    fake_conversation = MagicMock()
    fake_conversation.update.side_effect = RuntimeError("db unavailable")
    fake_conversation_cls = MagicMock()
    fake_conversation_cls.find_by_id.return_value = fake_conversation
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )
