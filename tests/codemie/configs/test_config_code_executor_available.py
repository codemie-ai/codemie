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


import itertools
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.configs.code_executor_availability import code_executor_available
from codemie.configs.customer_config import CustomerConfig


@pytest.mark.parametrize("standalone,flag,operator,cluster", list(itertools.product([True, False], repeat=4)))
def test_matrix(monkeypatch, standalone, flag, operator, cluster):
    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true" if operator else "false")
    monkeypatch.delenv("CODE_EXECUTOR_KUBECONFIG_PATH", raising=False)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1" if cluster else "")
    with patch.object(CustomerConfig, "configured_feature_enabled", return_value=flag):
        result = code_executor_available(SimpleNamespace(STANDALONE=standalone))
    assert result is (flag and operator and cluster if standalone else flag)


def test_operator_switch_parsing_and_kubeconfig(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "")
    monkeypatch.setenv("CODE_EXECUTOR_KUBECONFIG_PATH", "/k")
    cfg = SimpleNamespace(STANDALONE=True)
    with patch.object(CustomerConfig, "configured_feature_enabled", return_value=True):
        monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "TRUE")
        assert code_executor_available(cfg) is True
        monkeypatch.delenv("CODE_EXECUTOR_ENABLED")
        assert code_executor_available(cfg) is False
