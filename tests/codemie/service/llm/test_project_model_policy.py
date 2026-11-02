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

"""Table test for ProjectModelPolicy: mode x pick, flag off and global."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ModelNotWhitelistedException, NoDefaultModelException
from codemie.service.llm.model_availability_service import (
    ModelAvailabilityService,
    PolicyMode,
    ProjectModelPolicy,
)
from codemie.service.monitoring.metrics_constants import (
    MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
    MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC,
)

MODULE = "codemie.service.llm.model_availability_service"


def _app(allowed: list[str] | None, default: str | None, name: str = "proj") -> MagicMock:
    app = MagicMock()
    app.id = name
    app.name = name
    app.allowed_models = allowed
    app.default_model = default
    return app


@pytest.fixture
def flag() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.customer_config") as cfg:
        cfg.is_feature_enabled.return_value = True
        yield cfg


@pytest.fixture
def metric() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.BaseMonitoringService.send_count_metric") as m:
        yield m


@pytest.fixture
def log() -> Iterator[MagicMock]:
    with patch(f"{MODULE}.logger") as lg:
        yield lg


def _metric_names(metric: MagicMock) -> list[str]:
    return [c.kwargs["name"] for c in metric.call_args_list]


class TestModes:
    def test_modes(self, flag):
        assert ProjectModelPolicy.for_project(_app(None, None)).mode == PolicyMode.UNRESTRICTED
        assert ProjectModelPolicy.for_project(_app([], "D")).mode == PolicyMode.DENY_ALL
        policy = ProjectModelPolicy.for_project(_app(["D", "M"], "D"))
        assert policy.mode == PolicyMode.WHITELIST
        assert policy.allowed_models == ["D", "M"]
        assert policy.default_model == "D"
        assert policy.project_name == "proj"

    def test_flag_off_is_unrestricted(self, flag):
        flag.is_feature_enabled.return_value = False
        assert ProjectModelPolicy.for_project(_app(["D"], "D")).mode == PolicyMode.UNRESTRICTED
        assert ProjectModelPolicy.for_project(_app([], "D")).mode == PolicyMode.UNRESTRICTED

    def test_global_is_unrestricted(self, flag):
        policy = ProjectModelPolicy.for_project(_app(["D"], "D"), is_global=True)
        assert policy.mode == PolicyMode.UNRESTRICTED

    def test_none_project_is_unrestricted(self, flag):
        assert ProjectModelPolicy.for_project(None).mode == PolicyMode.UNRESTRICTED

    def test_missing_project_by_name_is_unrestricted(self, flag):
        with patch(f"{MODULE}.Application.get_by_id", side_effect=KeyError("x")):
            assert ProjectModelPolicy.for_project("ghost").mode == PolicyMode.UNRESTRICTED

    def test_project_by_name_is_loaded(self, flag):
        with patch(f"{MODULE}.Application.get_by_id", return_value=_app(["D"], "D")):
            assert ProjectModelPolicy.for_project("proj").mode == PolicyMode.WHITELIST

    def test_whitelist_without_default_raises(self, flag):
        with pytest.raises(NoDefaultModelException):
            ProjectModelPolicy.for_project(_app(["D"], None))

    def test_whitelist_without_default_is_allowed_when_default_not_required(self, flag):
        policy = ProjectModelPolicy.for_project(_app(["D"], None), require_default=False)
        assert policy.mode == PolicyMode.WHITELIST
        assert policy.allowed_models == ["D"]


class TestChoose:
    # (candidates, expected, substituted)
    @pytest.mark.parametrize(
        ("candidates", "expected", "substituted"),
        [
            (["M", "D"], "M", False),  # allowed pick
            (["X", "M"], "M", True),  # disallowed pick, own allowed
            (["X", "Y"], "D", True),  # both disallowed -> default
            ([None, None], "D", False),  # empty -> default, nothing rejected
            ([None, "M"], "M", False),  # empty pick, own allowed
            ([], "D", False),
        ],
    )
    def test_whitelist(self, flag, metric, log, candidates, expected, substituted):
        policy = ProjectModelPolicy.for_project(_app(["D", "M"], "D"))

        assert policy.choose(candidates) == expected

        if substituted:
            assert MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC in _metric_names(metric)
            assert any("model_availability_event=" in str(c.args[0]) for c in log.info.call_args_list)
        else:
            metric.assert_not_called()
            log.info.assert_not_called()

    def test_silent_choose_substitutes_without_reporting(self, flag, metric, log):
        policy = ProjectModelPolicy.for_project(_app(["D", "M"], "D"))

        assert policy.choose(["X"], report=False) == "D"

        metric.assert_not_called()
        log.info.assert_not_called()

    def test_whitelist_default_not_in_list_falls_to_first_allowed(self, flag):
        policy = ProjectModelPolicy.for_project(_app(["A", "B"], "Z"))
        assert policy.choose(["X", None]) == "A"

    @pytest.mark.parametrize(
        ("candidates", "expected"),
        [(["X", "M"], "X"), ([None, "M"], "M"), ([None, None], "D"), ([], "D")],
    )
    def test_unrestricted(self, flag, metric, log, candidates, expected):
        policy = ProjectModelPolicy.for_project(_app(None, "D"))
        assert policy.choose(candidates) == expected
        metric.assert_not_called()
        log.info.assert_not_called()

    def test_unrestricted_flag_off(self, flag):
        flag.is_feature_enabled.return_value = False
        policy = ProjectModelPolicy.for_project(_app(["D"], "D"))
        assert policy.choose(["X"]) == "X"

    def test_unrestricted_global(self, flag):
        policy = ProjectModelPolicy.for_project(_app(["D"], "D"), is_global=True)
        assert policy.choose(["X"]) == "X"

    def test_unrestricted_no_default_no_candidates(self, flag):
        policy = ProjectModelPolicy.for_project(_app(None, None))
        assert policy.choose([None]) is None

    def test_deny_all_raises(self, flag):
        policy = ProjectModelPolicy.for_project(_app([], "D"))
        with pytest.raises(ModelNotWhitelistedException):
            policy.choose(["M"])
        with pytest.raises(ModelNotWhitelistedException):
            policy.choose([None])


class TestCheck:
    def test_allowed_emits_metric(self, flag, metric):
        policy = ProjectModelPolicy.for_project(_app(["D", "M"], "D"))
        assert policy.check("M") is None
        assert _metric_names(metric) == [MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC]

    def test_blocked_emits_metric_and_never_substitutes(self, flag, metric):
        policy = ProjectModelPolicy.for_project(_app(["D", "M"], "D"))
        with pytest.raises(ModelNotWhitelistedException):
            policy.check("X")
        assert _metric_names(metric) == [MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC]

    def test_deny_all_blocks(self, flag, metric):
        policy = ProjectModelPolicy.for_project(_app([], "D"))
        with pytest.raises(ModelNotWhitelistedException):
            policy.check("M")
        assert _metric_names(metric) == [MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC]

    def test_unrestricted_accepts_anything(self, flag, metric):
        policy = ProjectModelPolicy.for_project(_app(None, None))
        assert policy.check("X") is None
        metric.assert_not_called()


class TestGetProjectModels:
    def test_unconfigured_project_keeps_none(self):
        with patch(f"{MODULE}.Application.get_by_id", return_value=_app(None, None)):
            config = ModelAvailabilityService.get_project_models("proj")
        assert config.allowed_models is None


class TestDeploymentNameAliases:
    """The whitelist stores deployment names for chat models; requests and assistants carry base names."""

    CATALOG = [MagicMock(base_name="gpt-4.1", deployment_name="codemie-gpt-4.1-2025-04-14")]

    @pytest.fixture
    def catalog(self) -> Iterator[None]:
        with patch(
            "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info", return_value=self.CATALOG
        ):
            yield

    def _whitelist(self) -> ProjectModelPolicy:
        return ProjectModelPolicy.for_project(_app(["codemie-gpt-4.1-2025-04-14", "D"], "D"))

    def test_choose_keeps_base_name_whose_deployment_is_whitelisted(self, flag, catalog, metric, log):
        assert self._whitelist().choose(["gpt-4.1"]) == "gpt-4.1"
        assert MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC not in _metric_names(metric)

    def test_check_accepts_base_name_whose_deployment_is_whitelisted(self, flag, catalog, metric):
        self._whitelist().check("gpt-4.1")

    def test_allows_matches_either_name(self, flag, catalog):
        policy = self._whitelist()
        assert policy.allows("gpt-4.1")
        assert policy.allows("codemie-gpt-4.1-2025-04-14")
        assert not policy.allows("gpt-4.1-mini")

    def test_unknown_model_still_falls_back(self, flag, catalog, metric, log):
        assert self._whitelist().choose(["gpt-4.1-mini"]) == "D"
        assert MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC in _metric_names(metric)

    def test_model_list_filter_and_execution_agree(self, catalog):
        """The UI list (llm_service filter) offers what execution accepts, flag checks aside."""
        from codemie.service.llm_service.llm_service import llm_service

        project = _app(["codemie-gpt-4.1-2025-04-14", "D"], "D")
        listed = llm_service._filter_models_by_project(self.CATALOG, project)
        policy = ProjectModelPolicy.for_application(project)
        assert [m.base_name for m in listed] == ["gpt-4.1"]
        assert policy.choose([listed[0].base_name], report=False) == "gpt-4.1"
