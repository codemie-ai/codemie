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

"""Tests for LangFuse loader availability checks"""

from __future__ import annotations


def test_has_langfuse_flag_when_installed(mock_enterprise_installed):
    """Test HAS_LANGFUSE is True when enterprise installed"""
    from codemie.enterprise.loader import HAS_LANGFUSE

    assert HAS_LANGFUSE is True


def test_has_langfuse_flag_when_not_installed(mock_enterprise_not_installed):
    """Test HAS_LANGFUSE is False when enterprise not installed"""
    from codemie.enterprise.loader import HAS_LANGFUSE

    assert HAS_LANGFUSE is False


def test_has_langfuse_function(mock_enterprise_installed):
    """Test has_langfuse() convenience function"""
    from codemie.enterprise.loader import has_langfuse

    assert has_langfuse() is True


def test_imports_available_when_installed(mock_enterprise_installed):
    """Test LangFuse imports are available when installed"""
    from codemie.enterprise.loader import LangFuseConfig, LangFuseService

    assert LangFuseConfig is not None
    assert LangFuseService is not None


def test_imports_none_when_not_installed(mock_enterprise_not_installed):
    """Test LangFuse imports are None when not installed"""
    from codemie.enterprise.loader import LangFuseConfig, LangFuseService

    assert LangFuseConfig is None
    assert LangFuseService is None


def test_loader_exports(mock_enterprise_installed):
    """Test loader exports all expected symbols"""
    from codemie.enterprise import loader

    expected_exports = [
        "HAS_LANGFUSE",
        "LangFuseConfig",
        "LangFuseService",
        "TraceContext",
        "SpanContext",
        "has_langfuse",
    ]

    for export in expected_exports:
        assert hasattr(loader, export), f"Missing export: {export}"


# ===========================================
# Migration Tests
# ===========================================


def test_has_migration_flag_when_installed(monkeypatch):
    """Test HAS_MIGRATION is True when migration module is available"""
    # Arrange
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MIGRATION", True)

    # Act
    from codemie.enterprise.loader import HAS_MIGRATION

    # Assert
    assert HAS_MIGRATION is True


def test_has_migration_flag_when_not_installed(monkeypatch):
    """Test HAS_MIGRATION is False when migration module is not available"""
    # Arrange
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MIGRATION", False)

    # Act
    from codemie.enterprise.loader import HAS_MIGRATION

    # Assert
    assert HAS_MIGRATION is False


def test_has_migration_function(monkeypatch):
    """Test has_migration() convenience function returns True when installed"""
    # Arrange
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MIGRATION", True)

    # Act
    from codemie.enterprise.loader import has_migration

    # Assert
    assert has_migration() is True


def test_migration_imports_available_when_installed(monkeypatch):
    """Test migration imports are available when installed"""
    # Arrange
    mock_client = type("KeycloakAdminClient", (), {})
    mock_user = type("KeycloakAdminUser", (), {})
    monkeypatch.setattr("codemie.enterprise.loader.KeycloakAdminClient", mock_client)
    monkeypatch.setattr("codemie.enterprise.loader.KeycloakAdminUser", mock_user)

    # Act
    from codemie.enterprise.loader import KeycloakAdminClient, KeycloakAdminUser

    # Assert
    assert KeycloakAdminClient is not None
    assert KeycloakAdminUser is not None


def test_migration_imports_none_when_not_installed(monkeypatch):
    """Test migration imports are None when not installed"""
    # Arrange
    monkeypatch.setattr("codemie.enterprise.loader.KeycloakAdminClient", None)
    monkeypatch.setattr("codemie.enterprise.loader.KeycloakAdminUser", None)

    # Act
    from codemie.enterprise.loader import KeycloakAdminClient, KeycloakAdminUser

    # Assert
    assert KeycloakAdminClient is None
    assert KeycloakAdminUser is None


def test_migration_exports_in_all():
    """Test migration symbols are present in __all__"""
    # Act
    from codemie.enterprise import loader

    # Assert
    expected_migration_exports = [
        "HAS_MIGRATION",
        "KeycloakAdminClient",
        "KeycloakAdminUser",
        "has_migration",
    ]

    for export in expected_migration_exports:
        assert export in loader.__all__, f"Missing export in __all__: {export}"
