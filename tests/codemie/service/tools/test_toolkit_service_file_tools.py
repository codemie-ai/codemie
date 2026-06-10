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

from unittest.mock import MagicMock

from codemie_tools.base.file_object import FileObject


class TestToolkitServiceAddFileTools:
    def test_add_file_tools_uses_markdown_cache_service(self, mocker):
        """add_file_tools calls MarkdownCacheService.get_preconverted for non-image files."""
        file_obj = FileObject(name="doc.html", content=b"<html/>", mime_type="text/html", owner="user1")

        mock_cache_svc = MagicMock()
        mock_cache_svc.get_preconverted.return_value = {"doc.html": "# Cached"}

        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = []

        mocker.patch(
            "codemie.service.tools.toolkit_service.MarkdownCacheService",
            return_value=mock_cache_svc,
        )
        mock_get_toolkit = mocker.patch(
            "codemie.service.tools.toolkit_service.FileAnalysisToolkit.get_toolkit",
            return_value=mock_toolkit,
        )
        mocker.patch(
            "codemie.service.tools.toolkit_service.ToolkitService._initialize_llm_for_files",
            return_value=(None, True),
        )
        mocker.patch(
            "codemie.service.tools.toolkit_service.ToolkitService._process_image_files",
            return_value=[],
        )

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        ToolkitService.add_file_tools(assistant, [file_obj], "req-uuid")

        mock_cache_svc.get_preconverted.assert_called_once()
        args = mock_cache_svc.get_preconverted.call_args[0][0]
        assert file_obj in args

        mock_get_toolkit.assert_called_once()
        call_kwargs = mock_get_toolkit.call_args.kwargs
        assert call_kwargs["preconverted_content"] == {"doc.html": "# Cached"}
