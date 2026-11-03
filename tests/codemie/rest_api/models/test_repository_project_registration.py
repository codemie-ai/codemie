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

import importlib.util
from pathlib import Path

from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration

_VERSIONS = Path(__file__).parents[4] / "src" / "external" / "alembic" / "versions"


def test_registration_table_keeps_one_project_per_normalized_remote():
    table = RepositoryProjectRegistration.__table__

    assert (table.schema, table.name) == ("codemie", "repository_project_registrations")
    assert table.c.normalized_remote.unique
    assert not table.c.normalized_remote.nullable and not table.c.project_name.nullable


def test_registration_migration_extends_the_current_head():
    (path,) = _VERSIONS.glob("*_add_repository_project_registrations.py")
    spec = importlib.util.spec_from_file_location("registration_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "79ab1c5725cc"
    assert callable(module.upgrade) and callable(module.downgrade)
