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

from __future__ import annotations

import textwrap

from codemie_tools.data_management.code_executor.filesystem_policy import (
    extract_denial_events,
    render_runtime_prelude,
)


def build_guarded_python_script(customer_code: str, *, workspace_root: str) -> str:
    prelude = render_runtime_prelude(workspace_root)
    return "\n".join(
        [
            prelude,
            "install_guard()",
            "try:",
            f"    exec(compile({customer_code!r}, '<customer_code>', 'exec'), {{'__name__': '__main__'}})",
            "except FilesystemAccessDenied as exc:",
            "    _emit_denial(exc)",
            "    raise SystemExit(1)",
        ]
    )


def build_guarded_workspace_script(script_path: str, *, workspace_root: str) -> str:
    launcher = textwrap.dedent(
        f"""
        import runpy
        runpy.run_path({script_path!r}, run_name='__main__')
        """
    ).strip()
    return build_guarded_python_script(launcher, workspace_root=workspace_root)
