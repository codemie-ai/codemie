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

"""Python programs the backend runs inside the sandbox pod, one exec at a time.

Each file is a self-contained script, standard library only, and is sent as text (`python3 -c <text> <args>`), so
the sandbox image needs nothing installed and a script running in the pod has no helper file to tamper with.
Leading comment lines (the licence header and the notes above each script) are dropped: they are not sent.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_POD_SCRIPTS_DIR: Path = Path(__file__).resolve().parent


@cache
def load_pod_script(name: str) -> str:
    """Source of ``<name>.py`` without its leading comment block, ready for ``python3 -c``."""
    lines = (_POD_SCRIPTS_DIR / f"{name}.py").read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")), len(lines))
    return "".join(lines[start:])
