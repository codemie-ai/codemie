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

import json
from pathlib import Path

import pytest

from codemie.service.iac_export.credential_ref import env_ref
from codemie.service.iac_export.document import ExportError

_VECTORS = json.loads((Path(__file__).parent / "env_ref_golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", _VECTORS, ids=lambda v: f"{v['identifier']!r}-{v['key']!r}")
def test_env_ref_matches_golden_vector(vector):
    # Same normalisation as the envelope's CredentialRef.from_entity; where it raises, so do we.
    if vector.get("error"):
        with pytest.raises(ExportError, match="alphanumeric"):
            env_ref(vector["identifier"], vector["key"])
    else:
        assert env_ref(vector["identifier"], vector["key"]) == vector["expected"]


def test_env_ref_without_identifier_is_an_error():
    with pytest.raises(ExportError, match="alphanumeric"):
        env_ref(None, "token")
