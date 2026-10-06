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


def codemie_bootstrap(sdk_source: str, sdk_config: dict) -> None:
    import pathlib
    import sys
    import types

    sdk = types.ModuleType('codemie_runtime_sdk')
    exec(compile(sdk_source, 'codemie_runtime_sdk.py', 'exec'), sdk.__dict__)
    sys.modules['codemie_runtime_sdk'] = sdk
    exchange_dir = sdk_config.get('exchange_dir')
    if exchange_dir is None:
        return
    try:
        pathlib.Path(exchange_dir).mkdir(parents=True, exist_ok=True)
        sdk._configure(sdk_config)
    except OSError as exc:
        sys.stderr.write('codemie tool calling unavailable: ' + repr(exc) + '\n')
