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

import logging
import os
import sys
from pathlib import Path

from langchain_community.tools.file_management.utils import FileValidationError

logger = logging.getLogger(__name__)


def get_relative_path(root_dir: str, file_path: str) -> Path:
    """Get the relative path, returning an error if unsupported."""
    if root_dir is None:
        return Path(file_path)
    return get_validated_relative_path(Path(root_dir), file_path)


def get_validated_relative_path(root: Path, user_path: str) -> Path:
    """Resolve a relative path, raising an error if not within the root directory."""
    # Note, this still permits symlinks from outside that point within the root.
    # Further validation would be needed if those are to be disallowed.
    root = root.resolve()
    full_path = (root / user_path).resolve()

    if not is_relative_to(full_path, root):
        raise FileValidationError(f"Path {user_path} is outside of the allowed directory {root}")
    return full_path


def is_relative_to(path: Path, root: Path) -> bool:
    """Check if path is relative to root."""
    if sys.version_info >= (3, 9):
        # No need for a try/except block in Python 3.8+.
        return path.is_relative_to(root)
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def create_folders(file_path):
    # Extract the directory path from the file path
    dir_path = os.path.dirname(file_path)

    # Create the directory if it doesn't exist
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
        logger.debug(f"Created directories: {dir_path}")
    else:
        logger.debug(f"Directories already exist: {dir_path}")
