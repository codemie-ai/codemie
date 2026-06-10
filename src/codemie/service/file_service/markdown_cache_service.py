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

from codemie_tools.base.file_object import FileObject
from codemie_tools.file_analysis.workers.markdown_workers import convert_file_to_markdown

from codemie.configs.logger import logger
from codemie.repository.repository_factory import FileRepositoryFactory

_CACHE_SUFFIX = "-md-cache.md"
_CACHE_MIME_TYPE = "text/markdown"

# These tools never read preconverted_content — CSVTool uses pandas, EmailAnalysisTool its own parser.
_SKIP_PRECONVERT_MIME_TYPES: frozenset[str] = frozenset(
    {
        "text/csv",
        "message/rfc822",
        "application/vnd.ms-outlook",
    }
)
_SKIP_PRECONVERT_EXTENSIONS: tuple[str, ...] = (".csv", ".eml", ".msg")


class MarkdownCacheService:
    def get_preconverted(self, file_objects: list[FileObject]) -> dict[str, str]:
        """Return {filename: markdown_text} for all files, reading from cache where available.

        Callers are expected to provide files that share a single owner (i.e. all fo.owner values
        are equal). The dict is keyed by filename only, so two entries with the same name but
        different owners would silently collide — the second would overwrite the first.
        """
        repo = FileRepositoryFactory().get_current_repository()
        return {fo.name: self.get_or_convert(fo, repo=repo) for fo in file_objects if not self._skip_preconvert(fo)}

    @staticmethod
    def _skip_preconvert(fo: FileObject) -> bool:
        if fo.mime_type in _SKIP_PRECONVERT_MIME_TYPES:
            return True
        name_lower = fo.name.lower()
        return any(name_lower.endswith(ext) for ext in _SKIP_PRECONVERT_EXTENSIONS)

    def get_or_convert(self, file_obj: FileObject, repo=None) -> str:
        """Check storage for cached markdown; compute and store on miss."""
        cache_name = f"{file_obj.name}{_CACHE_SUFFIX}"
        if repo is None:
            repo = FileRepositoryFactory().get_current_repository()

        logger.debug("MarkdownCache: reading cache %s/%s", file_obj.owner, cache_name)
        try:
            cached = repo.read_file(file_name=cache_name, owner=file_obj.owner, mime_type=_CACHE_MIME_TYPE)
            if cached and cached.content:
                logger.debug("MarkdownCache: hit for %s/%s", file_obj.owner, file_obj.name)
                return cached.string_content()
        except Exception:
            logger.debug(
                "MarkdownCache: read error for %s/%s, treating as miss", file_obj.owner, cache_name, exc_info=True
            )

        logger.debug("MarkdownCache: miss for %s/%s, converting", file_obj.owner, file_obj.name)
        markdown = convert_file_to_markdown(file_obj.bytes_content(), file_obj.name)

        try:
            repo.write_file(
                name=cache_name,
                mime_type=_CACHE_MIME_TYPE,
                owner=file_obj.owner,
                content=markdown.encode("utf-8"),
            )
            logger.debug("MarkdownCache: wrote cache %s/%s", file_obj.owner, cache_name)
        except Exception as e:
            logger.warning("Failed to write markdown cache for %s/%s: %s", file_obj.owner, file_obj.name, e)

        return markdown

    def invalidate(self, owner: str, filename: str, repo=None) -> None:
        """Write empty bytes to cache file to signal a miss on next read."""
        cache_name = f"{filename}{_CACHE_SUFFIX}"
        if repo is None:
            repo = FileRepositoryFactory().get_current_repository()
        logger.debug("MarkdownCache: invalidating %s/%s", owner, cache_name)
        try:
            repo.write_file(name=cache_name, mime_type=_CACHE_MIME_TYPE, owner=owner, content=b"")
            logger.debug("MarkdownCache: invalidated %s/%s", owner, cache_name)
        except Exception as e:
            logger.warning("Failed to invalidate markdown cache for %s/%s: %s", owner, filename, e)
