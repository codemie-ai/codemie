# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Failing regression for EPMCDME-12227 (reopened, Maksym 2026-08-18 evening).

Reproduces the reported failure: ``generic_jira_tool`` called with
``{"file": "3.jpg"}`` returns ``The root cause is: ''`` when the tool's
``input_files`` does not contain the requested file.

Today's ``GenericJiraIssueTool.execute`` silently falls through to a plain
JSON POST to ``/attachments`` whenever it cannot resolve the requested file —
which Jira rejects with an empty-body 415. These tests assert the correct
behavior (raise a clear ``ToolException`` naming the missing file) so they
FAIL on current code and will only pass after the tool grows an explicit
guard for the attachment endpoint.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.base.file_object import FileObject
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool


@pytest.fixture
def jira_tool():
    config = JiraConfig(url="https://jira.example.com", username="u@e.com", token="t", cloud=False)
    with (
        patch("codemie_tools.core.project_management.jira.tools.Jira"),
        patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"),
    ):
        tool = GenericJiraIssueTool(config=config)
        tool.jira = MagicMock()
    return tool


def test_attach_raises_clear_error_when_input_files_empty(jira_tool):
    """Maksym repro: no file surfaced into input_files, LLM asks to attach 3.jpg.

    Current code (RED): silently falls through to a plain JSON POST to /attachments
    which Jira 415s with an empty body — surfaces as ``root cause is: ''``.
    Expected: raise ToolException naming the requested file so the LLM/user
    sees a clear cause instead of an empty error.
    """
    jira_tool.config.input_files = []
    response = MagicMock(status_code=415, reason="Unsupported Media Type", text="")
    jira_tool.jira.request.return_value = response
    jira_tool.jira.raise_for_status.return_value = None

    with pytest.raises(ToolException, match=r"3\.jpg"):
        jira_tool.execute(
            method="POST",
            relative_url="/rest/api/2/issue/EPMCDME-12227/attachments",
            params='{"file": "3.jpg"}',
        )
    jira_tool.jira.request.assert_not_called()


def test_attach_raises_clear_error_when_requested_file_missing_from_input_files(jira_tool):
    """Same failure mode via a different route: input_files carries a prior-turn file,
    the requested name does not match. After the EPMCDME-12227 mixin fix
    ``_filter_requested_files`` returns {} (no silent fallback), so today's tool code
    still falls through to plain POST — same empty-root-cause symptom.
    """
    stale = MagicMock(spec=FileObject)
    stale.name = "prior.png"
    stale.mime_type = "image/png"
    stale.bytes_content.return_value = b"prior-content"
    jira_tool.config.input_files = [stale]

    response = MagicMock(status_code=415, reason="Unsupported Media Type", text="")
    jira_tool.jira.request.return_value = response
    jira_tool.jira.raise_for_status.return_value = None

    with pytest.raises(ToolException, match=r"3\.jpg"):
        jira_tool.execute(
            method="POST",
            relative_url="/rest/api/2/issue/EPMCDME-12227/attachments",
            params='{"file": "3.jpg"}',
        )
    jira_tool.jira.request.assert_not_called()


def test_attach_uses_attachment_branch_when_current_turn_file_present(jira_tool):
    """Control (GREEN today and after the fix): with the current-turn upload correctly
    surfaced into input_files, the proper attach path runs and no plain POST leaks."""
    file_obj = MagicMock(spec=FileObject)
    file_obj.name = "3.jpg"
    file_obj.mime_type = "image/jpeg"
    file_obj.bytes_content.return_value = b"\xff\xd8\xff\xe0raw-jpeg-bytes"
    jira_tool.config.input_files = [file_obj]

    with patch.object(
        jira_tool, "_handle_file_attachments", return_value="Successfully attached '3.jpg' to issue EPMCDME-12227"
    ) as handler:
        result = jira_tool.execute(
            method="POST",
            relative_url="/rest/api/2/issue/EPMCDME-12227/attachments",
            params='{"file": "3.jpg"}',
        )

    handler.assert_called_once()
    _relative_url, _params, requested = handler.call_args.args
    assert set(requested.keys()) == {"3.jpg"}
    assert "3.jpg" in result
    jira_tool.jira.request.assert_not_called()
