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


import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock, PropertyMock

from codemie.core.dependecies import enforce_allowed_model
from codemie.core.exceptions import (
    ModelNotWhitelistedException,
    NoDefaultModelException,
    NotFoundException,
)
from codemie.service.llm.model_availability_service import (
    ModelAvailabilityService,
    PolicyMode,
    ProjectModelPolicy,
    is_known_model,
    model_in_override,
)


class TestModelAvailabilityService:
    """Unit tests for ModelAvailabilityService."""

    @pytest.fixture
    def mock_app(self):
        """Create a mock Application object."""
        app = MagicMock()
        app.allowed_models = ["gpt-4", "claude-opus", "claude-sonnet"]
        app.default_model = "gpt-4"
        return app

    @pytest.fixture
    def mock_empty_whitelist_app(self):
        """Create a mock Application with empty whitelist."""
        app = MagicMock()
        app.allowed_models = []
        app.default_model = "gpt-4"
        return app

    @pytest.fixture
    def mock_no_default_app(self):
        """Create a mock Application with no default model."""
        app = MagicMock()
        app.allowed_models = ["gpt-4", "claude-opus"]
        app.default_model = None
        return app

    # ===== validate_model_for_asset_creation tests =====

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_success(self, mock_get, mock_app):
        """Model in whitelist should pass validation."""
        mock_get.return_value = mock_app

        # Should not raise
        ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project")

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_not_in_whitelist(self, mock_get, _mock_feature, mock_app):
        """Model not in whitelist should raise ModelNotWhitelistedException."""
        mock_get.return_value = mock_app

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-3.5", "my-project")

        assert "gpt-3.5" in str(exc_info.value.message)
        assert "my-project" in str(exc_info.value.message)
        assert exc_info.value.code == 400

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_empty_whitelist(self, mock_get, _mock_feature, mock_empty_whitelist_app):
        """Empty whitelist should block all models."""
        mock_get.return_value = mock_empty_whitelist_app

        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project")

        assert "no allowed models" in str(exc_info.value.details).lower()

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_no_default(self, mock_get, _mock_feature, mock_no_default_app):
        """Whitelist without default model should raise NoDefaultModelException."""
        mock_get.return_value = mock_no_default_app

        # Should raise — whitelist requires a default model
        with pytest.raises(NoDefaultModelException) as exc_info:
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project")

        assert "my-project" in str(exc_info.value.message)

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_project_not_found(self, mock_get, _mock_feature):
        """Project not found should raise NotFoundException."""
        mock_get.side_effect = KeyError("not found")

        with pytest.raises(NotFoundException):
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "nonexistent")

    # ===== validate_model_for_asset_creation with user override tests =====

    @staticmethod
    def _feature_flags(**flags):
        """is_feature_enabled side_effect: looks up the flag name in `flags`, default False."""
        return MagicMock(side_effect=lambda name: flags.get(name, False))

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_allowed_by_user_override_despite_project_block(
        self, mock_get, mock_feature_config, mock_get_override, mock_app
    ):
        """A model outside the project whitelist should still pass when the requesting
        user's override allows it - mirroring execution-time resolution precedence."""
        mock_get.return_value = mock_app
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(
            projectModelOverride=True, userModelOverride=True
        ).side_effect
        mock_get_override.return_value = MagicMock(allowed_models=["o4-mini-2025-04-16"], default_model=None)

        # Should not raise even though "o4-mini-2025-04-16" is not in mock_app.allowed_models
        ModelAvailabilityService.validate_model_for_asset_creation("o4-mini-2025-04-16", "my-project", user_id="user-1")

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_user_override_feature_disabled_falls_back_to_project_whitelist(
        self, mock_get, mock_feature_config, mock_get_override, mock_app
    ):
        """With the userModelOverride feature off, an override row must not be consulted and
        the project whitelist alone governs - unchanged behavior."""
        mock_get.return_value = mock_app
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(
            projectModelOverride=True, userModelOverride=False
        ).side_effect
        mock_get_override.return_value = MagicMock(allowed_models=["o4-mini-2025-04-16"], default_model=None)

        with pytest.raises(ModelNotWhitelistedException):
            ModelAvailabilityService.validate_model_for_asset_creation(
                "o4-mini-2025-04-16", "my-project", user_id="user-1"
            )
        mock_get_override.assert_not_called()

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_no_override_row_falls_back_to_project_whitelist(
        self, mock_get, mock_feature_config, mock_get_override, mock_app
    ):
        """A user with no override row for the project is validated against the project
        whitelist alone."""
        mock_get.return_value = mock_app
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(
            projectModelOverride=True, userModelOverride=True
        ).side_effect
        mock_get_override.return_value = None

        with pytest.raises(ModelNotWhitelistedException):
            ModelAvailabilityService.validate_model_for_asset_creation(
                "o4-mini-2025-04-16", "my-project", user_id="user-1"
            )

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_override_excluding_the_model_rejects_it_even_if_the_project_allows_it(
        self, mock_get, mock_feature_config, mock_get_override, mock_app
    ):
        """The override is the user's whole model set: a model it leaves out is rejected at creation,
        not saved and then substituted at run time, even when the project whitelist allows it."""
        mock_get.return_value = mock_app
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(
            projectModelOverride=True, userModelOverride=True
        ).side_effect
        mock_get_override.return_value = MagicMock(allowed_models=["claude-opus"], default_model=None)

        # "gpt-4" is in the project's whitelist (mock_app) but not in the user's override.
        with pytest.raises(ModelNotWhitelistedException) as exc_info:
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project", user_id="user-1")

        assert exc_info.value.code == 400
        assert "override" in exc_info.value.details

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    def test_validate_model_for_asset_creation_override_decides_even_when_the_project_override_is_off(
        self, mock_feature_config, mock_get_override
    ):
        """The two features are independent: with the project one off, a user override still decides."""
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(userModelOverride=True).side_effect
        mock_get_override.return_value = MagicMock(allowed_models=["claude-opus"], default_model=None)

        ModelAvailabilityService.validate_model_for_asset_creation("claude-opus", "my-project", user_id="user-1")
        with pytest.raises(ModelNotWhitelistedException):
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project", user_id="user-1")

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    @patch("codemie.service.llm.model_availability_service.customer_config")
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_model_for_asset_creation_override_rejection_is_counted_as_blocked(
        self, mock_get, mock_feature_config, mock_get_override, mock_app
    ):
        mock_get.return_value = mock_app
        mock_feature_config.is_feature_enabled.side_effect = self._feature_flags(
            projectModelOverride=True, userModelOverride=True
        ).side_effect
        mock_get_override.return_value = MagicMock(allowed_models=["claude-opus"], default_model=None)

        with patch.object(ModelAvailabilityService, "_send_validation_metric") as metric:
            with pytest.raises(ModelNotWhitelistedException):
                ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project", user_id="user-1")

        metric.assert_called_once_with("my-project", "gpt-4", "blocked")

    @patch("codemie.service.llm.model_availability_service.UserModelOverride.get_by_fields")
    def test_validate_model_for_asset_creation_no_user_id_skips_override_lookup(self, mock_get_override, mock_app):
        """Omitting user_id (existing callers) must not touch UserModelOverride at all."""
        with patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=mock_app):
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project")
        mock_get_override.assert_not_called()

    # ===== resolve_model_for_execution tests =====

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_no_fallback_needed(self, mock_get, mock_app):
        """Requested model in whitelist should return without fallback flag."""
        mock_get.return_value = mock_app

        actual_model, was_fallback = ModelAvailabilityService.resolve_model_for_execution("claude-opus", "my-project")

        assert actual_model == "claude-opus"
        assert was_fallback is False

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_with_fallback(self, mock_get, _mock_feature, mock_app):
        """Requested model not in whitelist should fall back to default."""
        mock_get.return_value = mock_app

        actual_model, was_fallback = ModelAvailabilityService.resolve_model_for_execution("gpt-3.5", "my-project")

        assert actual_model == "gpt-4"  # default_model
        assert was_fallback is True

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_default_is_requested(self, mock_get, mock_app):
        """Requesting default model should not trigger fallback."""
        mock_get.return_value = mock_app

        actual_model, was_fallback = ModelAvailabilityService.resolve_model_for_execution("gpt-4", "my-project")

        assert actual_model == "gpt-4"
        assert was_fallback is False

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_empty_whitelist(self, mock_get, _mock_feature, mock_empty_whitelist_app):
        """Empty whitelist should raise ModelNotWhitelistedException."""
        mock_get.return_value = mock_empty_whitelist_app

        with pytest.raises(ModelNotWhitelistedException):
            ModelAvailabilityService.resolve_model_for_execution("gpt-4", "my-project")

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_no_default(self, mock_get, _mock_feature, mock_no_default_app):
        """Whitelist without default model should raise NoDefaultModelException."""
        mock_get.return_value = mock_no_default_app

        # Should raise — whitelist requires a default model
        with pytest.raises(NoDefaultModelException) as exc_info:
            ModelAvailabilityService.resolve_model_for_execution("gpt-3.5", "my-project")

        assert "my-project" in str(exc_info.value.message)

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_model_for_execution_project_not_found(self, mock_get, _mock_feature):
        """Project not found should raise NotFoundException."""
        mock_get.side_effect = KeyError("not found")

        with pytest.raises(NotFoundException):
            ModelAvailabilityService.resolve_model_for_execution("gpt-4", "nonexistent")

    # ===== Edge cases =====

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_validate_with_special_characters_in_model_name(self, mock_get, _mock_feature, mock_app):
        """Model names with special characters should be handled."""
        mock_get.return_value = mock_app

        # Should raise because model not in list (no special handling)
        with pytest.raises(ModelNotWhitelistedException):
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4.5-turbo", "my-project")

    @patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )
    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_resolve_multiple_fallback_scenarios(self, mock_get, _mock_feature):
        """Test resolution in various project configurations."""
        app = MagicMock()
        app.allowed_models = ["claude-opus", "claude-sonnet"]
        app.default_model = "claude-opus"
        mock_get.return_value = app

        # Request non-existent model should fall back
        actual, was_fallback = ModelAvailabilityService.resolve_model_for_execution("gpt-4", "my-project")
        assert actual == "claude-opus"
        assert was_fallback is True

        # Request available model should not fall back
        actual, was_fallback = ModelAvailabilityService.resolve_model_for_execution("claude-sonnet", "my-project")
        assert actual == "claude-sonnet"
        assert was_fallback is False


@pytest.mark.parametrize("requested_model", [None, ""])
@pytest.mark.parametrize(
    "allowed_models", [None, ["project-default", "allowed"], ["project-default", "global-default"]]
)
def test_unset_model_selects_project_default(requested_model, allowed_models):
    app = MagicMock(allowed_models=allowed_models, default_model="project-default")
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=app),
    ):
        assert ModelAvailabilityService.resolve_model_for_execution(
            requested_model, "project", fallback_model="global-default"
        ) == ("project-default", False)


def test_blocked_model_selects_project_default_over_whitelisted_fallback():
    app = MagicMock(allowed_models=["project-default", "global-default"], default_model="project-default")
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=app),
    ):
        assert ModelAvailabilityService.resolve_model_for_execution(
            "blocked", "project", fallback_model="global-default"
        ) == ("project-default", True)


@pytest.mark.parametrize(
    "requested_model, expected", [(None, "global-default"), ("", "global-default"), ("chosen", "chosen")]
)
@pytest.mark.parametrize("is_global, feature_enabled", [(True, True), (False, False)])
def test_execution_bypasses_project_policy(requested_model, expected, is_global, feature_enabled):
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=feature_enabled),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id") as get_project,
    ):
        assert ModelAvailabilityService.resolve_model_for_execution(
            requested_model, "project", is_global=is_global, fallback_model="global-default"
        ) == (expected, False)
        get_project.assert_not_called()


@pytest.mark.parametrize(
    "requested_model, default_model, expected",
    [(None, None, "global-default"), ("chosen", "project-default", "chosen")],
)
def test_unrestricted_project_preserves_explicit_model_or_uses_fallback(requested_model, default_model, expected):
    app = MagicMock(allowed_models=None, default_model=default_model)
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=app),
    ):
        assert ModelAvailabilityService.resolve_model_for_execution(
            requested_model, "project", fallback_model="global-default"
        ) == (expected, False)


@pytest.mark.parametrize(
    "allowed_models, default_model, error",
    [([], "project-default", ModelNotWhitelistedException), (["allowed"], None, NoDefaultModelException)],
)
def test_unset_model_does_not_bypass_invalid_project_configuration(allowed_models, default_model, error):
    app = MagicMock(allowed_models=allowed_models, default_model=default_model)
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=app),
        pytest.raises(error),
    ):
        ModelAvailabilityService.resolve_model_for_execution(None, "project", fallback_model="global-default")


@pytest.mark.parametrize(
    "requested_model, expected",
    [("picked-model", ("picked-model", False)), (None, ("global-default", False))],
)
def test_workflow_without_project_is_unrestricted(requested_model, expected):
    """A workflow with no project name is unrestricted: it keeps its pick or the fallback and never looks a project up."""
    with (
        patch(
            "codemie.service.llm.model_availability_service.customer_config",
            is_feature_enabled=MagicMock(return_value=True),
        ),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id") as get_by_id,
    ):
        resolved = ModelAvailabilityService.resolve_model_for_execution(
            requested_model, None, fallback_model="global-default"
        )

    assert resolved == expected
    get_by_id.assert_not_called()


class TestModelAvailabilityMetrics:
    """Regression for CR-016: the spec-required model_availability.validations.total and
    .fallbacks.total counters were never implemented."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.allowed_models = ["gpt-4", "claude-opus", "claude-sonnet"]
        app.default_model = "gpt-4"
        return app

    @patch("codemie.service.llm.model_availability_service.BaseMonitoringService.send_count_metric")
    def test_validation_allowed_emits_validations_metric(self, mock_send_metric, mock_app):
        with (
            patch(
                "codemie.service.llm.model_availability_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=mock_app),
        ):
            ModelAvailabilityService.validate_model_for_asset_creation("gpt-4", "my-project")

        mock_send_metric.assert_called_once()
        kwargs = mock_send_metric.call_args.kwargs
        assert kwargs["name"] == "codemie_model_availability_validations_total"
        assert kwargs["attributes"]["status"] == "allowed"
        assert kwargs["attributes"]["project"] == "my-project"
        assert kwargs["attributes"]["llm_model"] == "gpt-4"

    @patch("codemie.service.llm.model_availability_service.BaseMonitoringService.send_count_metric")
    def test_validation_blocked_emits_validations_metric(self, mock_send_metric, mock_app):
        with (
            patch(
                "codemie.service.llm.model_availability_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=mock_app),
        ):
            with pytest.raises(ModelNotWhitelistedException):
                ModelAvailabilityService.validate_model_for_asset_creation("not-allowed-model", "my-project")

        mock_send_metric.assert_called_once()
        kwargs = mock_send_metric.call_args.kwargs
        assert kwargs["name"] == "codemie_model_availability_validations_total"
        assert kwargs["attributes"]["status"] == "blocked"

    @patch("codemie.service.llm.model_availability_service.BaseMonitoringService.send_count_metric")
    def test_fallback_applied_emits_fallbacks_metric(self, mock_send_metric, mock_app):
        with (
            patch(
                "codemie.service.llm.model_availability_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=mock_app),
        ):
            ModelAvailabilityService.resolve_model_for_execution("disallowed-model", "my-project")

        mock_send_metric.assert_called_once()
        kwargs = mock_send_metric.call_args.kwargs
        assert kwargs["name"] == "codemie_model_availability_fallbacks_total"
        assert kwargs["attributes"]["project"] == "my-project"
        assert kwargs["attributes"]["llm_model"] == "disallowed-model"

    @patch("codemie.service.llm.model_availability_service.BaseMonitoringService.send_count_metric")
    def test_no_fallback_does_not_emit_fallbacks_metric(self, mock_send_metric, mock_app):
        with (
            patch(
                "codemie.service.llm.model_availability_service.customer_config",
                is_feature_enabled=MagicMock(return_value=True),
            ),
            patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=mock_app),
        ):
            ModelAvailabilityService.resolve_model_for_execution("gpt-4", "my-project")

        mock_send_metric.assert_not_called()


def _flag_on():
    return patch(
        "codemie.service.llm.model_availability_service.customer_config",
        is_feature_enabled=MagicMock(return_value=True),
    )


def test_validate_asset_creation_passes_when_whitelist_unset_but_default_set():
    app = MagicMock(allowed_models=None, default_model="project-default")
    with (
        _flag_on(),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=app),
    ):
        ModelAvailabilityService.validate_model_for_asset_creation("any-model", "project")


def _catalog(*base_names):
    """An enabled-model catalog holding these models, so the resolver's liveness check passes for them."""
    return [MagicMock(base_name=name, deployment_name=f"{name}-dep") for name in base_names]


@pytest.fixture(autouse=True)
def _user_model_override_feature_enabled():
    with patch("codemie.service.llm.model_availability_service.customer_config") as feature_config:
        feature_config.is_feature_enabled.return_value = True
        yield


class TestResolveUserOverrideModel:
    """Sync user-override resolution used by assistant calls."""

    _PATH = "codemie.service.llm.model_availability_service"

    def _resolve(self, override, requested="gpt-4", enabled=True, is_global=False, user_id="u1"):
        with (
            patch(f"{self._PATH}.customer_config.is_feature_enabled", return_value=enabled),
            patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override),
        ):
            return ModelAvailabilityService.resolve_user_override_model(requested, user_id, "proj", is_global)

    def test_requested_model_allowed_is_kept(self):
        override = MagicMock(allowed_models=["gpt-4.1"], default_model="claude-opus")
        with self._with_catalog():
            assert self._resolve(override, requested="gpt-4.1") == "gpt-4.1"

    def test_requested_model_not_allowed_uses_override_default(self):
        with self._with_catalog():
            override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")
            assert self._resolve(override) == "claude-opus"

    _CATALOG = [
        MagicMock(base_name="gpt-4.1", deployment_name="codemie-gpt-4.1-2025-04-14"),
        MagicMock(base_name="claude-opus", deployment_name="claude-opus-dep"),
        MagicMock(base_name="claude-sonnet", deployment_name="claude-sonnet-dep"),
    ]

    def _with_catalog(self, routers=()):
        stack = ExitStack()
        stack.enter_context(
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=self._CATALOG,
            )
        )
        stack.enter_context(
            patch("codemie.service.llm_service.llm_service.llm_service.get_llm_routers", return_value=list(routers))
        )
        return stack

    def test_fallback_default_is_returned_as_base_name(self):
        override = MagicMock(allowed_models=["codemie-gpt-4.1-2025-04-14"], default_model="codemie-gpt-4.1-2025-04-14")
        with self._with_catalog():
            assert self._resolve(override, requested="other") == "gpt-4.1"

    def test_requested_model_keeps_the_name_it_was_given(self):
        override = MagicMock(allowed_models=["codemie-gpt-4.1-2025-04-14"], default_model=None)
        with self._with_catalog():
            assert self._resolve(override, requested="gpt-4.1") == "gpt-4.1"

    def test_no_override_returns_none(self):
        assert self._resolve(None) is None

    def test_flag_off_returns_none(self):
        override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")
        assert self._resolve(override, enabled=False) is None

    def test_global_assistant_returns_none(self):
        override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")
        assert self._resolve(override, is_global=True) is None

    def test_override_miss_does_not_fall_through_to_project_whitelist(self):
        """The project whitelist is never consulted once an override row exists - a model the
        override excludes stays unreachable even when the project itself would allow it."""
        with self._with_catalog():
            override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")
            assert self._resolve(override) == "claude-opus"

    def test_override_miss_without_override_default_uses_project_default(self):
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = ProjectModelPolicy(
            mode=PolicyMode.WHITELIST,
            allowed_models=["claude-opus", "gpt-4.1"],
            default_model="claude-opus",
            project_name="proj",
        )
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            assert self._resolve(override, requested="gpt-4.1") == "claude-opus"

    def test_override_miss_without_override_default_in_deny_all_project_is_rejected(self):
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = ProjectModelPolicy(mode=PolicyMode.DENY_ALL, project_name="proj")
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            with pytest.raises(ModelNotWhitelistedException):
                self._resolve(override, requested="gpt-4.1")

    def test_override_miss_without_any_default_uses_the_platform_default(self):
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = ProjectModelPolicy(mode=PolicyMode.UNRESTRICTED, project_name="proj")
        with (
            self._with_catalog(),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            patch(
                "codemie.service.llm_service.llm_service.LLMService.default_llm_model",
                new_callable=PropertyMock,
                return_value="gpt-4.1",
            ),
        ):
            assert self._resolve(override, requested="claude-opus") == "gpt-4.1"

    def test_override_miss_in_unrestricted_project_with_default_uses_it(self):
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = ProjectModelPolicy(mode=PolicyMode.UNRESTRICTED, default_model="claude-opus", project_name="proj")
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            assert self._resolve(override, requested="gpt-4.1") == "claude-opus"

    def _project_with_default(self, default_model):
        return ProjectModelPolicy(mode=PolicyMode.UNRESTRICTED, default_model=default_model, project_name="proj")

    def _platform_default(self, model="gpt-4.1"):
        return patch(
            "codemie.service.llm_service.llm_service.LLMService.default_llm_model",
            new_callable=PropertyMock,
            return_value=model,
        )

    def test_a_disabled_requested_model_skips_to_the_override_default(self):
        """A model the override lists but the platform has disabled is not returned (EPMCDME-15082 Context)."""
        override = MagicMock(allowed_models=["disabled-model", "claude-opus"], default_model="claude-opus")
        with self._with_catalog():
            assert self._resolve(override, requested="disabled-model") == "claude-opus"

    def test_a_disabled_override_default_skips_to_the_project_default(self):
        override = MagicMock(allowed_models=["claude-sonnet", "disabled-model"], default_model="disabled-model")
        policy = self._project_with_default("claude-opus")
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            assert self._resolve(override, requested="gpt-4.1") == "claude-opus"

    def test_a_disabled_requested_model_and_override_default_skip_to_the_project_default(self):
        override = MagicMock(allowed_models=["disabled-model", "disabled-default"], default_model="disabled-default")
        policy = self._project_with_default("claude-opus")
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            assert self._resolve(override, requested="disabled-model") == "claude-opus"

    def test_a_disabled_project_default_skips_to_the_platform_default(self):
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = self._project_with_default("disabled-default")
        with (
            self._with_catalog(),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            self._platform_default("gpt-4.1"),
        ):
            assert self._resolve(override, requested="claude-opus") == "gpt-4.1"

    def test_every_tier_disabled_ends_at_the_platform_default(self):
        override = MagicMock(allowed_models=["disabled-model", "disabled-default"], default_model="disabled-default")
        policy = self._project_with_default("disabled-project-default")
        with (
            self._with_catalog(),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            self._platform_default("gpt-4.1"),
        ):
            assert self._resolve(override, requested="disabled-model") == "gpt-4.1"

    def _with_status_disabled(self, *names):
        """The catalog with the named models kept in it but disabled in platform model management."""
        from codemie.configs.llm_config import ModelStatus

        catalog = [
            MagicMock(base_name=m.base_name, deployment_name=m.deployment_name, status=ModelStatus.ENABLED)
            for m in self._CATALOG
        ]
        for entry in catalog:
            if entry.base_name in names:
                entry.status = ModelStatus.DISABLED
        stack = ExitStack()
        stack.enter_context(
            patch("codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info", return_value=catalog)
        )
        stack.enter_context(
            patch("codemie.service.llm_service.llm_service.llm_service.get_llm_routers", return_value=[])
        )
        return stack

    def test_a_status_disabled_model_is_not_known(self):
        """Platform model management keeps a disabled model in the catalog; it must still not be live."""
        with self._with_status_disabled("gpt-4.1"):
            assert not is_known_model("gpt-4.1")
            assert not is_known_model("codemie-gpt-4.1-2025-04-14")
            assert is_known_model("claude-opus")

    def test_a_status_disabled_requested_model_skips_to_the_override_default(self):
        override = MagicMock(allowed_models=["gpt-4.1", "claude-opus"], default_model="claude-opus")
        with self._with_status_disabled("gpt-4.1"):
            assert self._resolve(override, requested="gpt-4.1") == "claude-opus"

    def test_a_status_disabled_model_is_skipped_by_its_deployment_name(self):
        override = MagicMock(allowed_models=["codemie-gpt-4.1-2025-04-14", "claude-opus"], default_model="claude-opus")
        with self._with_status_disabled("gpt-4.1"):
            assert self._resolve(override, requested="codemie-gpt-4.1-2025-04-14") == "claude-opus"

    def test_status_disabled_override_and_project_defaults_end_at_the_platform_default(self):
        override = MagicMock(allowed_models=["gpt-4.1", "claude-opus"], default_model="claude-opus")
        policy = self._project_with_default("claude-sonnet")
        with (
            self._with_status_disabled("gpt-4.1", "claude-opus", "claude-sonnet"),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            self._platform_default("platform-default"),
        ):
            assert self._resolve(override, requested="gpt-4.1") == "platform-default"

    def test_a_status_disabled_override_default_skips_to_the_project_default(self):
        override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")
        policy = self._project_with_default("claude-sonnet")
        with (
            self._with_status_disabled("claude-opus"),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
        ):
            assert self._resolve(override, requested="gpt-4.1") == "claude-sonnet"

    def test_a_disabled_model_is_skipped_not_raised(self):
        """Disabled means "next tier", never an error, even with a whitelist project behind the override."""
        override = MagicMock(allowed_models=["disabled-model"], default_model=None)
        policy = ProjectModelPolicy(
            mode=PolicyMode.WHITELIST, allowed_models=["claude-opus"], default_model="claude-opus", project_name="proj"
        )
        with self._with_catalog(), patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy):
            assert self._resolve(override, requested="disabled-model") == "claude-opus"

    def test_a_deployment_name_of_a_live_model_counts_as_live(self):
        override = MagicMock(allowed_models=["claude-opus-dep"], default_model=None)
        with self._with_catalog():
            assert self._resolve(override, requested="claude-opus-dep") == "claude-opus-dep"

    def test_an_enabled_router_is_live(self):
        router = MagicMock(base_name="smart-router", enabled=True)
        override = MagicMock(allowed_models=["smart-router"], default_model=None)
        with self._with_catalog(routers=[router]):
            assert self._resolve(override, requested="smart-router") == "smart-router"

    def test_a_disabled_router_is_skipped(self):
        router = MagicMock(base_name="smart-router", enabled=False)
        override = MagicMock(allowed_models=["smart-router", "claude-opus"], default_model="claude-opus")
        with self._with_catalog(routers=[router]):
            assert self._resolve(override, requested="smart-router") == "claude-opus"

    def test_each_skip_is_logged_with_its_tier(self):
        override = MagicMock(allowed_models=["disabled-model", "disabled-default"], default_model="disabled-default")
        policy = self._project_with_default("claude-opus")
        with (
            self._with_catalog(),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            patch(f"{self._PATH}.logger") as log,
        ):
            self._resolve(override, requested="disabled-model")

        messages = [call.args[0] for call in log.info.call_args_list]
        assert any("model_not_live tier=requested model='disabled-model'" in m for m in messages)
        assert any("model_not_live tier=user_override_default model='disabled-default'" in m for m in messages)

    def test_asset_creation_still_accepts_a_disabled_model_the_override_lists(self):
        """Creation keeps its membership-only check on purpose; only resolution skips disabled models."""
        override = MagicMock(allowed_models=["disabled-model"], default_model=None)
        with self._with_catalog(), patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override):
            ModelAvailabilityService.validate_model_for_asset_creation("disabled-model", "proj", user_id="u1")


class TestUserOverrideAllowsModel:
    """Reject-don't-substitute check used by callers that cannot fall back (the OCR model)."""

    _PATH = "codemie.service.llm.model_availability_service"

    def _allows(self, override, model="gpt-4", enabled=True, user_id="u1", project="proj"):
        with (
            patch(f"{self._PATH}.customer_config.is_feature_enabled", return_value=enabled),
            patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override) as lookup,
        ):
            result = ModelAvailabilityService.user_override_allows_model(model, user_id, project)
        return result, lookup

    def test_model_in_the_override_is_allowed(self):
        result, lookup = self._allows(MagicMock(allowed_models=["gpt-4"]))

        assert result is True
        lookup.assert_called_once_with({"user_id": "u1", "project_name": "proj"})

    def test_model_outside_the_override_is_not_allowed(self):
        result, _ = self._allows(MagicMock(allowed_models=["claude-opus"]))

        assert result is False

    def test_deployment_name_in_the_override_allows_the_base_name(self):
        override = MagicMock(allowed_models=["codemie-gpt-4.1-2025-04-14"])
        with patch(f"{self._PATH}.model_aliases", return_value={"gpt-4.1", "codemie-gpt-4.1-2025-04-14"}):
            result, _ = self._allows(override, model="gpt-4.1")

        assert result is True

    def test_no_override_row_means_no_opinion(self):
        result, _ = self._allows(None)

        assert result is None

    @pytest.mark.parametrize("user_id, project", [(None, "proj"), ("", "proj"), ("u1", None), ("u1", "")])
    def test_missing_ids_mean_no_opinion_and_no_lookup(self, user_id, project):
        result, lookup = self._allows(MagicMock(allowed_models=["gpt-4"]), user_id=user_id, project=project)

        assert result is None
        lookup.assert_not_called()

    def test_feature_off_means_no_opinion_and_no_lookup(self):
        result, lookup = self._allows(MagicMock(allowed_models=["gpt-4"]), enabled=False)

        assert result is None
        lookup.assert_not_called()


class TestChatResolutionAfterOverrideRemoval:
    """Reproduces the reported bug: a chat keeps re-sending the model it already used
    (AssistantService._apply_conversation_runtime_overrides re-applies the conversation's
    sticky model on every message). Once the user's override no longer covers that model,
    AssistantService.build_agent composes `resolve_user_override_model(...) or
    enforce_allowed_model(...)` to decide what to actually run. These tests exercise that
    exact composition - never the real customer-config.yaml or a real project row - and
    cover both the userModelOverride-enabled and -disabled cases.
    """

    _PATH = "codemie.service.llm.model_availability_service"
    _DEPS_PATH = "codemie.core.dependecies"

    @staticmethod
    def _unrestricted_project():
        """A project with no project-level whitelist configured (the common case when a
        deployment only uses the user-level override feature)."""
        return MagicMock(allowed_models=None, default_model=None)

    def test_enabled_override_without_default_falls_back_to_project_default(self):
        """userModelOverride ON: override exists but does not allow the model previously used in
        this chat, and has no default_model configured. The excluded model must not be used: the
        project default is the fallback."""
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        policy = ProjectModelPolicy(
            mode=PolicyMode.WHITELIST,
            allowed_models=["claude-sonnet", "gpt-4"],
            default_model="claude-sonnet",
            project_name="proj",
        )

        with (
            patch(f"{self._PATH}.customer_config", is_feature_enabled=MagicMock(return_value=True)),
            patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=_catalog("claude-sonnet", "gpt-4"),
            ),
        ):
            llm_model = ModelAvailabilityService.resolve_user_override_model("gpt-4", "user-1", "proj", is_global=False)

        assert llm_model != "gpt-4"
        assert llm_model == "claude-sonnet"

    def test_disabled_feature_falls_through_to_project_logic_unchanged(self):
        """userModelOverride OFF: the override tier never applies - resolution defers
        entirely to project-level logic, same as before this fix (no override enforcement
        is expected at all while the feature flag itself is off)."""
        override = MagicMock(allowed_models=["claude-sonnet"], default_model=None)
        project = self._unrestricted_project()

        with (
            patch(f"{self._PATH}.customer_config", is_feature_enabled=MagicMock(return_value=False)),
            patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override),
        ):
            user_override_model = ModelAvailabilityService.resolve_user_override_model(
                "gpt-4", "user-1", "proj", is_global=False
            )

        assert user_override_model is None

        with patch(f"{self._PATH}.customer_config", is_feature_enabled=MagicMock(return_value=True)):
            llm_model = user_override_model or enforce_allowed_model("gpt-4", project)

        # No restriction in play at all (feature off, project unconfigured) - requested
        # model passes through unchanged. This is pre-existing, intentional behavior.
        assert llm_model == "gpt-4"


class TestModelInOverride:
    """The one rule behind every 'does the user's override allow this model' check."""

    def test_a_listed_name_is_allowed(self):
        assert model_in_override(["gpt-4", "claude"], "gpt-4") is True

    def test_any_of_several_names_is_enough(self):
        """A catalog entry is offered under its base name and its deployment name; either one listed counts."""
        assert model_in_override(["gpt-4-deployment"], "gpt-4", "gpt-4-deployment") is True

    def test_an_unlisted_name_is_not_allowed(self):
        assert model_in_override(["gpt-4"], "claude") is False

    def test_blank_names_never_match(self):
        assert model_in_override(["gpt-4", ""], None, "") is False

    def test_an_empty_list_allows_nothing(self):
        assert model_in_override([], "gpt-4") is False

    def test_no_names_allow_nothing(self):
        assert model_in_override(["gpt-4"]) is False

    def test_the_list_may_be_any_iterable(self):
        assert model_in_override(iter(["gpt-4"]), "gpt-4") is True


class TestOverrideFallbackReporting:
    """A user override that swaps the requested model is logged and counted, like a project-level substitution."""

    _PATH = "codemie.service.llm.model_availability_service"

    def _resolve(self, override, requested):
        policy = ProjectModelPolicy(
            mode=PolicyMode.WHITELIST,
            allowed_models=["claude-sonnet"],
            default_model="claude-sonnet",
            project_name="proj",
        )
        with (
            patch(f"{self._PATH}.UserModelOverride.get_by_fields", return_value=override),
            patch(f"{self._PATH}.ProjectModelPolicy.for_project", return_value=policy),
            patch(f"{self._PATH}.BaseMonitoringService") as monitoring,
            patch(f"{self._PATH}.logger") as log,
            patch(
                "codemie.service.llm_service.llm_service.llm_service.get_all_llm_model_info",
                return_value=_catalog("gpt-4", "claude-opus", "claude-sonnet"),
            ),
        ):
            resolved = ModelAvailabilityService.resolve_user_override_model(requested, "u1", "proj")
        return resolved, monitoring, log

    def test_a_substitution_to_the_project_default_is_counted(self):
        resolved, monitoring, _ = self._resolve(MagicMock(allowed_models=["claude-opus"], default_model=None), "gpt-4")

        assert resolved == "claude-sonnet"
        monitoring.send_count_metric.assert_called_once_with(
            name="codemie_model_availability_fallbacks_total",
            attributes={"project": "proj", "llm_model": "gpt-4", "fallback_source": "user_override"},
        )

    def test_a_substitution_to_the_overrides_own_default_is_counted(self):
        override = MagicMock(allowed_models=["claude-opus"], default_model="claude-opus")

        resolved, monitoring, _ = self._resolve(override, "gpt-4")

        assert resolved == "claude-opus"
        monitoring.send_count_metric.assert_called_once()

    def test_a_substitution_is_logged_with_its_source(self):
        _, _, log = self._resolve(MagicMock(allowed_models=["claude-opus"], default_model=None), "gpt-4")

        messages = [call.args[0] for call in log.info.call_args_list]
        assert any(
            "model_availability_event=fallback_applied" in m
            and "requested_model='gpt-4'" in m
            and "fallback_model='claude-sonnet'" in m
            and "context='user_override'" in m
            for m in messages
        )

    def test_an_allowed_model_is_not_a_substitution(self):
        resolved, monitoring, _ = self._resolve(MagicMock(allowed_models=["gpt-4"], default_model=None), "gpt-4")

        assert resolved == "gpt-4"
        monitoring.send_count_metric.assert_not_called()

    def test_an_unset_model_is_not_a_substitution(self):
        _, monitoring, _ = self._resolve(MagicMock(allowed_models=["claude-opus"], default_model="claude-opus"), None)

        monitoring.send_count_metric.assert_not_called()

    def test_a_user_without_an_override_is_not_counted(self):
        resolved, monitoring, _ = self._resolve(None, "gpt-4")

        assert resolved is None
        monitoring.send_count_metric.assert_not_called()
