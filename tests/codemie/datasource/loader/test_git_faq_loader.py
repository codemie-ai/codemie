# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from git import Blob

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.git_faq_loader import GitFaqLoader

TOTAL_DOCUMENTS_KEY = BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "faq")


def make_loader(tmp_path, **kwargs) -> GitFaqLoader:
    return GitFaqLoader(
        repo_path=str(tmp_path),
        clone_url=None,
        branch="main",
        **kwargs,
    )


def make_blob(rel_path: str) -> Mock:
    blob = Mock(spec=Blob)
    blob.path = rel_path
    blob.name = rel_path.rsplit("/", 1)[-1]
    return blob


def make_repo(items):
    repo = MagicMock()
    repo.tree.return_value.traverse.return_value = items
    return repo


def copy_fixtures(tmp_path) -> list[Mock]:
    """Copy the parser fixtures into the fake repo under ``faq/`` and return blob mocks."""
    blobs = []
    for root, _, files in os.walk(FIXTURES_DIR):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, FIXTURES_DIR).replace(os.sep, "/")
            dest = tmp_path / "faq" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
            blobs.append(make_blob(f"faq/{rel}"))
    return blobs


class TestIsFaqFile:
    def test_filter_truth_table(self, tmp_path):
        loader = make_loader(tmp_path)

        # No files_filter configured: every .md file anywhere in the repo matches.
        cases = {
            "faq/a.md": True,
            "faq/sub/b.md": True,
            "faqx/a.md": True,
            "docs/faq/a.md": True,
            "README.md": True,
            "faq/a.txt": False,
            "faq/README": False,
        }
        for rel, expected in cases.items():
            assert loader._is_faq_file(os.path.join(str(tmp_path), *rel.split("/"))) is expected


class TestLazyLoad:
    def test_yields_parsed_articles_and_skips_invalid(self, tmp_path):
        blobs = copy_fixtures(tmp_path)
        loader = make_loader(tmp_path)
        loader.repo = make_repo(blobs)

        documents = list(loader.lazy_load())

        by_reference = {doc.metadata["reference"]: doc for doc in documents}
        # notes.txt is filtered by the file filter, bad frontmatter skipped with a warning
        assert "faq/getting-started/installation" in by_reference
        assert "faq/notes" not in by_reference
        assert "faq/broken/bad-frontmatter" not in by_reference
        doc = by_reference["faq/getting-started/installation"]
        assert doc.metadata["title"] == "How do I install the Codemie backend locally?"
        assert doc.metadata["instructions"].startswith("Walk the user")
        assert doc.metadata["file_type"] == ".md"
        assert doc.page_content.startswith("Clone the repository")


class TestFetchRemoteStats:
    def test_counts_only_faq_markdown_files(self, tmp_path):
        blobs = copy_fixtures(tmp_path)
        loader = make_loader(tmp_path)
        loader.repo = make_repo(blobs)

        stats = loader.fetch_remote_stats()

        md_under_faq = sum(1 for b in blobs if b.path.startswith("faq/") and b.path.endswith(".md"))
        assert stats[TOTAL_DOCUMENTS_KEY] == md_under_faq
        assert stats["total_documents"] > 0


class TestCreateLoader:
    def test_factory_wires_paths_and_auth(self):
        with (
            patch("codemie.datasource.loader.git_faq_loader.config") as mock_config,
            patch(
                "codemie.datasource.loader.git_faq_loader._build_clone_url", return_value="https://x@repo"
            ) as mock_url,
            patch("codemie.datasource.loader.git_faq_loader._build_auth_header", return_value=None) as mock_header,
            patch("codemie.datasource.loader.git_faq_loader.os.makedirs"),
        ):
            mock_config.REPOS_LOCAL_DIR = "/repos"
            creds = MagicMock()
            creds.use_header_auth = False

            loader = GitFaqLoader.create_loader(
                project_name="proj",
                datasource_name="ds",
                repo_link="https://git.example.com/repo",
                branch="main",
                creds=creds,
            )

        assert loader.repo_path == os.path.join("/repos", "proj", "ds")
        assert loader.branch == "main"
        assert loader.clone_url == "https://x@repo"
        mock_url.assert_called_once()
        passed_repo = mock_url.call_args.args[1]
        assert isinstance(passed_repo, SimpleNamespace)
        assert passed_repo.link == "https://git.example.com/repo"
        mock_header.assert_not_called()

    def test_factory_without_creds_uses_plain_link(self):
        with (
            patch("codemie.datasource.loader.git_faq_loader.config") as mock_config,
            patch("codemie.datasource.loader.git_faq_loader.os.makedirs"),
        ):
            mock_config.REPOS_LOCAL_DIR = "/repos"

            loader = GitFaqLoader.create_loader(
                project_name="proj",
                datasource_name="ds",
                repo_link="https://git.example.com/repo",
                branch="main",
                creds=None,
            )

        # _build_clone_url returns repo.link verbatim when creds are falsy
        assert loader.clone_url == "https://git.example.com/repo"


class TestLoadStats:
    def test_precount_reports_zero_skipped(self, tmp_path):
        blobs = copy_fixtures(tmp_path)
        loader = make_loader(tmp_path)
        loader.repo = make_repo(blobs)

        stats = loader.fetch_remote_stats()

        assert stats[BaseDatasourceLoader.SKIPPED_DOCUMENTS_KEY] == 0

    def test_parse_skips_reported_after_load(self, tmp_path):
        blobs = copy_fixtures(tmp_path)
        loader = make_loader(tmp_path)
        loader.repo = make_repo(blobs)

        documents = list(loader.lazy_load())

        broken = [b for b in blobs if "bad-frontmatter" in b.path or "broken" in b.path]
        assert len(documents) == 10 - len(broken) + (len(broken) - 1)  # fixtures: 10 md, 1 broken
        stats = loader.get_load_stats()
        assert stats[BaseDatasourceLoader.SKIPPED_DOCUMENTS_KEY] == len(broken)


class TestFilesFilterMode:
    """FU-2: gitignore-style files_filter, same mechanism as the Git code datasource."""

    def test_files_filter_includes_matching(self, tmp_path):
        loader = GitFaqLoader(
            repo_path=str(tmp_path),
            clone_url=None,
            branch="main",
            files_filter="docs/**/*.md\n!docs/internal/**",
        )
        loader.repo = make_repo([])

        assert loader._is_faq_file(str(tmp_path / "docs" / "guide.md")) is True
        assert loader._is_faq_file(str(tmp_path / "docs" / "internal" / "secret.md")) is False
        assert loader._is_faq_file(str(tmp_path / "docs" / "internal" / "sub" / "deep.md")) is False
        assert loader._is_faq_file(str(tmp_path / "src" / "code.md")) is False

    def test_files_filter_directory_exclusion_with_trailing_slash(self, tmp_path):
        loader = GitFaqLoader(
            repo_path=str(tmp_path),
            clone_url=None,
            branch="main",
            files_filter="docs/**/*.md\n!docs/legacy/",
        )
        loader.repo = make_repo([])

        assert loader._is_faq_file(str(tmp_path / "docs" / "legacy" / "old.md")) is False
        assert loader._is_faq_file(str(tmp_path / "docs" / "current" / "new.md")) is True

    def test_files_filter_only_md_still_enforced(self, tmp_path):
        loader = GitFaqLoader(
            repo_path=str(tmp_path),
            clone_url=None,
            branch="main",
            files_filter="docs/**",
        )
        loader.repo = make_repo([])

        assert loader._is_faq_file(str(tmp_path / "docs" / "readme.md")) is True
        assert loader._is_faq_file(str(tmp_path / "docs" / "image.png")) is False

    def test_no_files_filter_matches_every_md_in_repo(self, tmp_path):
        loader = GitFaqLoader(
            repo_path=str(tmp_path),
            clone_url=None,
            branch="main",
            files_filter=None,
        )
        loader.repo = make_repo([])

        assert loader._is_faq_file(str(tmp_path / "faq" / "a.md")) is True
        assert loader._is_faq_file(str(tmp_path / "docs" / "a.md")) is True
