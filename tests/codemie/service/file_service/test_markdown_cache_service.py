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

from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.base.file_object import FileObject


@pytest.fixture
def file_obj():
    return FileObject(
        name="report.pdf",
        content=b"%PDF-fake",
        mime_type="application/pdf",
        owner="user1",
    )


@pytest.fixture
def mock_repo():
    return MagicMock()


class TestMarkdownCacheService:
    def test_get_or_convert_cache_hit(self, file_obj, mock_repo):
        """Returns cached markdown string without calling convert_file_to_markdown."""
        cached_file = MagicMock()
        cached_file.content = "# Cached heading"
        cached_file.string_content.return_value = "# Cached heading"
        mock_repo.read_file.return_value = cached_file

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_or_convert(file_obj)

        assert result == "# Cached heading"
        mock_convert.assert_not_called()

    def test_get_or_convert_cache_miss_calls_markitdown_and_writes(self, file_obj, mock_repo):
        """Cache miss: calls convert_file_to_markdown and writes result to storage."""
        mock_repo.read_file.side_effect = Exception("not found")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.return_value = "# Fresh markdown"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_or_convert(file_obj)

        assert result == "# Fresh markdown"
        mock_convert.assert_called_once_with(file_obj.bytes_content(), file_obj.name)
        mock_repo.write_file.assert_called_once_with(
            name="report.pdf-md-cache.md",
            mime_type="text/markdown",
            owner="user1",
            content=b"# Fresh markdown",
        )

    def test_get_or_convert_empty_cache_treated_as_miss(self, file_obj, mock_repo):
        """Empty cached content (invalidated) triggers re-computation."""
        cached_file = MagicMock()
        cached_file.content = ""
        cached_file.string_content.return_value = ""
        mock_repo.read_file.return_value = cached_file

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.return_value = "# Recomputed"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_or_convert(file_obj)

        assert result == "# Recomputed"
        mock_convert.assert_called_once()

    def test_get_or_convert_cache_write_failure_is_non_fatal(self, file_obj, mock_repo):
        """Cache write failure does not raise; result is still returned."""
        mock_repo.read_file.side_effect = Exception("not found")
        mock_repo.write_file.side_effect = Exception("storage error")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.return_value = "# Markdown"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_or_convert(file_obj)

        assert result == "# Markdown"

    def test_invalidate_writes_empty_bytes(self, mock_repo):
        """invalidate() writes empty bytes to cache key to signal a miss."""
        with patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory:
            mock_factory.return_value.get_current_repository.return_value = mock_repo

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            svc.invalidate(owner="user1", filename="report.pdf")

        mock_repo.write_file.assert_called_once_with(
            name="report.pdf-md-cache.md",
            mime_type="text/markdown",
            owner="user1",
            content=b"",
        )

    def test_get_preconverted_returns_dict_keyed_by_filename(self, mock_repo):
        """get_preconverted returns {filename: markdown} for all given FileObjects."""
        files = [
            FileObject(name="a.html", content=b"<html/>", mime_type="text/html", owner="u"),
            FileObject(name="b.txt", content=b"hello", mime_type="text/plain", owner="u"),
        ]
        mock_repo.read_file.side_effect = Exception("not found")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.side_effect = lambda content, name: f"# {name}"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_preconverted(files)

        assert result == {"a.html": "# a.html", "b.txt": "# b.txt"}

    def test_get_preconverted_skips_csv_by_mime(self, mock_repo):
        """CSV files (text/csv) are excluded from preconversion — CSVTool uses pandas."""
        files = [
            FileObject(name="data.csv", content=b"a,b\n1,2", mime_type="text/csv", owner="u"),
            FileObject(name="doc.pdf", content=b"%PDF", mime_type="application/pdf", owner="u"),
        ]
        mock_repo.read_file.side_effect = Exception("not found")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.side_effect = lambda content, name: f"# {name}"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_preconverted(files)

        assert "data.csv" not in result
        assert result == {"doc.pdf": "# doc.pdf"}

    def test_get_preconverted_skips_email_mime_types(self, mock_repo):
        """EML/MSG files are excluded from preconversion — EmailAnalysisTool has its own parser."""
        files = [
            FileObject(name="msg.eml", content=b"From: x", mime_type="message/rfc822", owner="u"),
            FileObject(name="msg.msg", content=b"\xd0\xcf", mime_type="application/vnd.ms-outlook", owner="u"),
        ]
        mock_repo.read_file.side_effect = Exception("not found")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.return_value = "irrelevant"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_preconverted(files)

        assert result == {}
        mock_convert.assert_not_called()

    def test_get_preconverted_skips_csv_by_extension_fallback(self, mock_repo):
        """CSV skipped by extension when MIME type is generic (e.g. application/octet-stream)."""
        files = [
            FileObject(name="data.csv", content=b"a,b\n1,2", mime_type="application/octet-stream", owner="u"),
        ]
        mock_repo.read_file.side_effect = Exception("not found")

        with (
            patch("codemie.service.file_service.markdown_cache_service.FileRepositoryFactory") as mock_factory,
            patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown") as mock_convert,
        ):
            mock_factory.return_value.get_current_repository.return_value = mock_repo
            mock_convert.return_value = "irrelevant"

            from codemie.service.file_service.markdown_cache_service import MarkdownCacheService

            svc = MarkdownCacheService()
            result = svc.get_preconverted(files)

        assert result == {}
        mock_convert.assert_not_called()
