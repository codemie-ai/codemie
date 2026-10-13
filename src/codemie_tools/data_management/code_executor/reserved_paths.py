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

"""The single definition of the workspace paths owned by the script runtime."""

from __future__ import annotations

import posixpath
import unicodedata

from codemie_tools.data_management.code_executor.runtime_sdk import codemie_runtime_sdk
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME

# The SDK module is written to the workspace root as "<module name>.py". The name comes from the module name, not its
# file path, so an image that ships only compiled files (.pyc) still protects the right name. The module's public
# surface is pinned by a test, so it does not export this constant itself.
RUNTIME_SDK_FILE_NAME = f"{codemie_runtime_sdk.__name__.rsplit('.', 1)[-1]}.py"


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


_BRIDGE_DIR = _fold(BRIDGE_DIR_NAME)
_SDK_FILE = _fold(RUNTIME_SDK_FILE_NAME)


def is_reserved_path(path: str) -> bool:
    """True for the bridge directory (and anything under it) or the root runtime SDK file, however spelled.

    Letter case and Unicode compatibility forms are folded before separators are normalized, so look-alike
    spellings cannot reach the runtime's own files. Only the workspace root is reserved: a folder with the same name
    deeper in the tree (for example inside a skill folder) is an ordinary folder.
    """
    # Fold first: a compatibility character that becomes a separator or a dot ("／", "＼", "．") must be seen as one
    # by the normalization below, exactly as the executor's file system would never see it as a plain name.
    folded = posixpath.normpath(_fold(path).replace("\\", "/")).lstrip("/")
    return folded.split("/", 1)[0] == _BRIDGE_DIR or folded == _SDK_FILE
