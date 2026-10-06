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
from unittest.mock import patch, MagicMock

from codemie.core.exceptions import (
    ModelNotWhitelistedException,
    NoDefaultModelException,
    NotFoundException,
)
from codemie.service.llm.model_availability_service import (
    ModelAvailabilityService,
    ProjectModelConfig,
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

    # ===== get_project_models tests =====

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_get_project_models_success(self, mock_get, mock_app):
        """Should return ProjectModelConfig with allowed_models and default_model."""
        mock_get.return_value = mock_app

        config = ModelAvailabilityService.get_project_models("my-project")

        assert isinstance(config, ProjectModelConfig)
        assert config.allowed_models == ["gpt-4", "claude-opus", "claude-sonnet"]
        assert config.default_model == "gpt-4"

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_get_project_models_no_default(self, mock_get, mock_no_default_app):
        """Missing default model should raise NoDefaultModelException."""
        mock_get.return_value = mock_no_default_app

        with pytest.raises(NoDefaultModelException):
            ModelAvailabilityService.get_project_models("my-project")

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_get_project_models_project_not_found(self, mock_get):
        """Project not found should raise NotFoundException."""
        mock_get.side_effect = KeyError("not found")

        with pytest.raises(NotFoundException):
            ModelAvailabilityService.get_project_models("nonexistent")

    @patch("codemie.service.llm.model_availability_service.Application.get_by_id")
    def test_get_project_models_unconfigured_project_does_not_raise(self, mock_get):
        """Project with no whitelist configured (allowed_models is None) should not
        require a default_model, for backward compatibility with legacy projects."""
        app = MagicMock()
        app.allowed_models = None
        app.default_model = None
        mock_get.return_value = app

        config = ModelAvailabilityService.get_project_models("my-project")

        assert config.allowed_models == []
        assert config.default_model == ""

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
@pytest.mark.parametrize("allowed_models", [None, ["project-default", "allowed"]])
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
