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

"""Guard against half-registered datasource types.

Adding a DatasourceTypes member without wiring its loader config, its FullDatasourceTypes
counterpart, or its health-check arm fails at application boot or silently at runtime rather
than in tests. This turns those failures red.

The exemption sets record the pre-existing partial state of these enums as of EPMCDME-13142.
Do not add to them casually: an entry means "this type deliberately has no such registration".

Verified by removing the xwiki_loader block from datasources-config.yaml: the suite goes red as
a pydantic ValidationError during collection, because LoadersConfig has no defaults. That is a
harder failure than an assertion, and it is the same one that stops the app from booting.
"""

import inspect

import pytest

from codemie.core.constants import DatasourceTypes
from codemie.datasource.datasources_config import datasources_config
from codemie.service.constants import FullDatasourceTypes
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService

NO_LOADER_CONFIG = {
    DatasourceTypes.GIT,  # indexed through code_loader
    DatasourceTypes.GOOGLE,  # Google Docs loader is not configured per-type
}

NO_FULL_TYPE = {
    DatasourceTypes.SVN,
    DatasourceTypes.JSON,
    DatasourceTypes.XRAY,
}

NO_HEALTH_CHECK = {
    DatasourceTypes.FILE,
    DatasourceTypes.JSON,
    DatasourceTypes.GOOGLE,
}


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_loader_config(datasource_type):
    if datasource_type in NO_LOADER_CONFIG:
        pytest.skip(f"{datasource_type.value} deliberately has no per-type loader config")
    assert hasattr(datasources_config.loaders, f"{datasource_type.value}_loader"), (
        f"{datasource_type.value} has no '{datasource_type.value}_loader' entry. "
        "A missing datasources-config.yaml block fails at import time, not in tests."
    )


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_full_type_counterpart(datasource_type):
    if datasource_type in NO_FULL_TYPE:
        pytest.skip(f"{datasource_type.value} deliberately has no FullDatasourceTypes member")
    assert (
        datasource_type.name in FullDatasourceTypes.__members__
    ), f"DatasourceTypes.{datasource_type.name} has no FullDatasourceTypes counterpart."


@pytest.mark.parametrize("datasource_type", list(DatasourceTypes))
def test_every_type_has_a_health_check_arm(datasource_type):
    if datasource_type in NO_HEALTH_CHECK:
        pytest.skip(f"{datasource_type.value} deliberately has no health check")
    source = inspect.getsource(IndexHealthCheckService.health_check_datasource)
    assert f"DatasourceTypes.{datasource_type.name}" in source, (
        f"health_check_datasource has no 'case DatasourceTypes.{datasource_type.name}' arm. "
        "_check_docs_health() is duck-typed - without this arm it is never called."
    )


def test_xwiki_is_fully_registered():
    assert hasattr(datasources_config.loaders, "xwiki_loader")
    assert FullDatasourceTypes.XWIKI.value == "knowledge_base_xwiki"
    source = inspect.getsource(IndexHealthCheckService.health_check_datasource)
    assert "DatasourceTypes.XWIKI" in source
