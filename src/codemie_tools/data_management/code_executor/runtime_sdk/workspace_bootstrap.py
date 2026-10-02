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

"""Bootstrap run inside the guarded exec before a workspace script.

The function source is injected as text into the generated wrapper script (see ``sandbox_guard``), so it must stay
self-contained: local imports only, no module-level state, nothing written to stdout.
"""


def codemie_bootstrap(sdk_source: str, exchange_dir: str | None) -> None:
    import os
    import pathlib
    import sys
    import types

    sdk = types.ModuleType('codemie_runtime_sdk')
    exec(compile(sdk_source, 'codemie_runtime_sdk.py', 'exec'), sdk.__dict__)
    sys.modules['codemie_runtime_sdk'] = sdk
    if exchange_dir is None:
        return
    try:
        run_dir = pathlib.Path(exchange_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / 'pid').write_text(str(os.getpid()))
        try:
            # /proc is read through pathlib: the guard admits read-only absolute paths for stdlib callers only
            stat_text = pathlib.Path('/proc/self/stat').read_text()
            start_time = stat_text.rsplit(')', 1)[1].split()[19]
        except (OSError, IndexError):
            start_time = None
        if start_time is not None:
            (run_dir / 'start_time').write_text(start_time)
        sdk._configure(exchange_dir)
    except OSError as exc:
        sys.stderr.write('codemie tool calling unavailable: ' + repr(exc) + '\n')
