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

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie.service.headroom import factory
from codemie.service.headroom.compressors.base import HeadroomCompressionError
from codemie.service.headroom.factory import get_compressor, get_compressor_mode
from codemie.service.headroom.compressors.sdk_compressor import SDKCompressor
from codemie.service.headroom.compressors.sidecar_compressor import SidecarCompressor

CONFIG = "codemie.service.headroom.factory.config"
LITELLM = "codemie.enterprise.litellm.is_litellm_enabled"


@pytest.fixture(autouse=True)
def _clear_compressor_cache():
    get_compressor.cache_clear()
    factory._report_missing_sdk_once.cache_clear()
    yield
    get_compressor.cache_clear()
    factory._report_missing_sdk_once.cache_clear()


@pytest.fixture
def cfg():
    with patch(CONFIG) as mock_config:
        mock_config.HEADROOM_MODE = "off"
        yield mock_config


def test_sidecar_mode_returns_sidecar_even_when_litellm_disabled(cfg):
    cfg.HEADROOM_MODE = "sidecar"
    with patch(LITELLM, return_value=False):
        assert isinstance(get_compressor(), SidecarCompressor)


def test_sdk_mode_returns_sdk_even_when_litellm_enabled(cfg):
    cfg.HEADROOM_MODE = "sdk"
    with patch(LITELLM, return_value=True):
        assert isinstance(get_compressor(), SDKCompressor)


def test_off_mode_has_no_compressor(cfg):
    cfg.HEADROOM_MODE = "off"

    assert get_compressor_mode() == "off"
    with pytest.raises(HeadroomCompressionError):
        get_compressor()


def test_default_config_means_off(cfg):
    assert get_compressor_mode() == "off"


def test_get_compressor_is_cached(cfg):
    cfg.HEADROOM_MODE = "sdk"

    assert get_compressor() is get_compressor()


@pytest.mark.parametrize("mode", ["sdk", "sidecar", "off"])
def test_get_compressor_mode_does_not_build_a_compressor(cfg, mode):
    cfg.HEADROOM_MODE = mode

    assert get_compressor_mode() == mode
    assert get_compressor.cache_info().currsize == 0


def test_sdk_mode_logs_one_error_when_the_package_is_missing(cfg):
    cfg.HEADROOM_MODE = "sdk"
    with (
        patch("codemie.service.headroom.factory.importlib.util.find_spec", return_value=None),
        patch("codemie.service.headroom.factory.logger") as log,
    ):
        assert get_compressor_mode() == "sdk"
        assert get_compressor_mode() == "sdk"

    log.error.assert_called_once()
    assert "headroom-sdk" in log.error.call_args.args[0]


def test_sdk_mode_with_package_installed_logs_no_error(cfg):
    cfg.HEADROOM_MODE = "sdk"
    with (
        patch("codemie.service.headroom.factory.importlib.util.find_spec", return_value=object()),
        patch("codemie.service.headroom.factory.logger") as log,
    ):
        assert get_compressor_mode() == "sdk"

    log.error.assert_not_called()


def test_non_sdk_modes_never_probe_for_the_package(cfg):
    cfg.HEADROOM_MODE = "sidecar"
    with patch("codemie.service.headroom.factory.importlib.util.find_spec") as find_spec:
        get_compressor_mode()

    find_spec.assert_not_called()


def test_missing_sdk_error_fires_once_per_cache_reset(cfg):
    cfg.HEADROOM_MODE = "sdk"
    with (
        patch("codemie.service.headroom.factory.importlib.util.find_spec", return_value=None),
        patch("codemie.service.headroom.factory.logger") as log,
    ):
        for _ in range(2):
            factory._report_missing_sdk_once.cache_clear()
            get_compressor_mode()
            get_compressor_mode()

    assert log.error.call_count == 2
