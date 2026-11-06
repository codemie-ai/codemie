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

"""Regenerates ``envelope_schema.json`` from the IaC document envelope (EPMCDME-15309).

NOT part of the test run and never imported by it: the tests only read the committed JSON, so this repo carries no
runtime dependency on the other one. Run it with the python environment of ``codemie-iac-python`` installed from
a checkout of ``codemie-cdk`` (the current repo; NOT the legacy ``codemie-sdk-cdk`` GitLab clone, which can be
stale or ahead of what is actually published):

    <iac-venv>/bin/python -I tests/codemie/service/iac_export/envelope/regenerate_envelope_schema.py

then review the diff of ``envelope_schema.json``: a change is the envelope moving, and the exporters follow it.
``_generated_from`` records the git commit of the installed package, best-effort, so a stale or wrong-repo source
is visible in the diff rather than silent.
"""

import json
import subprocess
from pathlib import Path

from pydantic import TypeAdapter

import codemie_iac
from codemie_iac.envelope import _SPEC_BY_KIND, DocumentMeta

OUT = Path(__file__).with_name("envelope_schema.json")


def _strip_prose(node):
    """Drop docstring/title prose (a string-valued ``description``/``title``); a property *named* description stays."""
    if isinstance(node, dict):
        return {
            k: _strip_prose(v) for k, v in node.items() if not (k in ("description", "title") and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_strip_prose(v) for v in node]
    return node


def _source_commit() -> str:
    """The git commit of the installed ``codemie_iac`` package's checkout, or a reason it could not be read."""
    package_dir = Path(codemie_iac.__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "-C", str(package_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({e.__class__.__name__}: not a git checkout at {package_dir}?)"


def build() -> dict:
    return _strip_prose(
        {
            "meta": DocumentMeta.model_json_schema(),
            "specs": {kind.value: TypeAdapter(spec).json_schema() for kind, spec in _SPEC_BY_KIND.items()},
        }
    ) | {"_generated_from": _source_commit()}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
