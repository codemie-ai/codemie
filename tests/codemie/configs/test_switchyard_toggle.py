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

import pytest

from codemie.enterprise.switchyard.engine import get_proxy_switchyard_router


def test_disabled_returns_no_router(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.configs import config

    monkeypatch.setattr(config, "SWITCHYARD_ENABLED", False)
    assert get_proxy_switchyard_router(router_name="any-router") is None
