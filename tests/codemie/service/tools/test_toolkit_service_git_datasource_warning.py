# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

import pytest
from unittest.mock import MagicMock, Mock, patch

from codemie_tools.base.models import ToolSet

from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantChatRequest,
    Context,
    ContextType,
    ToolKitDetails,
)
from codemie.rest_api.security.user import User
from codemie.service.tools import ToolkitService


class TestAddContextToolsGitDatasourceWarning:
    """
    Regression tests for EPMCDME-14058: `add_context_tools` must emit a warning
    once when an assistant has Git tools enabled but no CODE datasource attached.
    """

    @pytest.fixture
    def user(self):
        u = Mock(spec=User)
        u.id = "user-1"
        return u

    @pytest.fixture
    def request_obj(self):
        r = Mock(spec=AssistantChatRequest)
        r.conversation_id = None
        r.history_index = None
        return r

    def _assistant(self, contexts, toolkits):
        a = MagicMock(spec=Assistant)
        a.id = "assistant-1"
        a.name = "test-assistant"
        a.project = "test-project"
        a.context = contexts
        a.toolkits = toolkits
        return a

    def _context(self, ctx_type):
        c = MagicMock(spec=Context)
        c.context_type = ctx_type
        c.name = f"ctx-{ctx_type}"
        return c

    def _git_toolkit(self, tools):
        tk = MagicMock(spec=ToolKitDetails)
        tk.toolkit = ToolSet.GIT
        tk.tools = tools
        return tk

    def test_warns_once_when_git_tools_enabled_and_no_code_context(self, user, request_obj):
        assistant = self._assistant(
            contexts=[self._context(ContextType.KNOWLEDGE_BASE)],
            toolkits=[self._git_toolkit(tools=[Mock(name="create_branch")])],
        )

        with (
            patch("codemie.service.tools.toolkit_service.logger") as mock_logger,
            patch.object(ToolkitService, "_add_kb_tools"),
        ):
            ToolkitService.add_context_tools(
                assistant=assistant,
                request=request_obj,
                llm_model="gpt-4",
                user=user,
                request_uuid="req-1",
            )

        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args[0][0]
        assert "assistant-1" in msg
        assert "test-assistant" in msg
        assert "attach one in Context & Data Sources." in msg

    def test_does_not_warn_when_code_context_present(self, user, request_obj):
        assistant = self._assistant(
            contexts=[
                self._context(ContextType.KNOWLEDGE_BASE),
                self._context(ContextType.CODE),
            ],
            toolkits=[self._git_toolkit(tools=[Mock(name="create_branch")])],
        )

        with (
            patch("codemie.service.tools.toolkit_service.logger") as mock_logger,
            patch.object(ToolkitService, "_add_kb_tools"),
            patch.object(ToolkitService, "_add_code_tools"),
            patch.object(ToolkitService, "_add_git_related_tools"),
        ):
            ToolkitService.add_context_tools(
                assistant=assistant,
                request=request_obj,
                llm_model="gpt-4",
                user=user,
                request_uuid="req-1",
            )

        mock_logger.warning.assert_not_called()

    def test_does_not_warn_when_git_toolkit_has_no_enabled_tools(self, user, request_obj):
        assistant = self._assistant(
            contexts=[self._context(ContextType.KNOWLEDGE_BASE)],
            toolkits=[self._git_toolkit(tools=[])],
        )

        with (
            patch("codemie.service.tools.toolkit_service.logger") as mock_logger,
            patch.object(ToolkitService, "_add_kb_tools"),
        ):
            ToolkitService.add_context_tools(
                assistant=assistant,
                request=request_obj,
                llm_model="gpt-4",
                user=user,
                request_uuid="req-1",
            )

        mock_logger.warning.assert_not_called()

    def test_does_not_warn_when_no_git_toolkit(self, user, request_obj):
        assistant = self._assistant(
            contexts=[self._context(ContextType.KNOWLEDGE_BASE)],
            toolkits=[],
        )

        with (
            patch("codemie.service.tools.toolkit_service.logger") as mock_logger,
            patch.object(ToolkitService, "_add_kb_tools"),
        ):
            ToolkitService.add_context_tools(
                assistant=assistant,
                request=request_obj,
                llm_model="gpt-4",
                user=user,
                request_uuid="req-1",
            )

        mock_logger.warning.assert_not_called()

    def test_warns_only_once_when_assistant_has_multiple_non_code_contexts(self, user, request_obj):
        assistant = self._assistant(
            contexts=[
                self._context(ContextType.KNOWLEDGE_BASE),
                self._context(ContextType.KNOWLEDGE_BASE),
                self._context(ContextType.PROVIDER),
            ],
            toolkits=[self._git_toolkit(tools=[Mock(name="create_branch")])],
        )

        with (
            patch("codemie.service.tools.toolkit_service.logger") as mock_logger,
            patch.object(ToolkitService, "_add_kb_tools"),
            patch.object(ToolkitService, "_add_provider_context_tools"),
        ):
            ToolkitService.add_context_tools(
                assistant=assistant,
                request=request_obj,
                llm_model="gpt-4",
                user=user,
                request_uuid="req-1",
            )

        assert mock_logger.warning.call_count == 1
