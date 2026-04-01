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

import hashlib
from unittest.mock import Mock

import pytest

from codemie.rest_api.models.ai_kata import KataLevel, KataStatus
from codemie.service import kata_import_service as kata_import_module
from codemie.service.kata_import_service import KataImportService, ValidationException


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(kata_import_module.config, "KATAS_MAX_YAML_SIZE", 10_000)
    monkeypatch.setattr(kata_import_module.config, "KATAS_MAX_MARKDOWN_SIZE", 10_000)
    monkeypatch.setattr(kata_import_module, "get_valid_kata_tag_ids", lambda: ["tag-1", "tag-2", "tag-3"])
    monkeypatch.setattr(kata_import_module, "get_valid_kata_role_ids", lambda: ["role-1", "role-2", "role-3"])
    return KataImportService()


@pytest.fixture
def valid_kata_yaml_text():
    return """
id: test-kata
version: 1.2.3
title: Test Kata
description: A compact kata for tests.
level: beginner
duration_minutes: 25
tags:
  - tag-1
roles:
  - role-1
status: published
author:
  name: Kata Author
links:
  - title: Docs
    url: https://example.com/docs
    type: documentation
references:
  - https://example.com/reference
image_url: https://example.com/image.png
""".strip()


def test_validate_kata_directory_accepts_yaml_or_yml_and_steps(service, tmp_path):
    kata_dir = tmp_path / "sample-kata"
    kata_dir.mkdir()
    (kata_dir / "kata.yml").write_text("id: sample-kata", encoding="utf-8")
    (kata_dir / "steps.md").write_text("hello", encoding="utf-8")

    is_valid, error_message = service.validate_kata_directory(kata_dir)

    assert is_valid is True
    assert error_message == ""


def test_validate_kata_directory_rejects_missing_steps(service, tmp_path):
    kata_dir = tmp_path / "sample-kata"
    kata_dir.mkdir()
    (kata_dir / "kata.yaml").write_text("id: sample-kata", encoding="utf-8")

    is_valid, error_message = service.validate_kata_directory(kata_dir)

    assert is_valid is False
    assert error_message == "Missing steps.md file"


def test_validate_kata_yaml_truncates_extra_tags_and_roles_with_warning(service, tmp_path, monkeypatch):
    yaml_path = tmp_path / "kata.yaml"
    yaml_path.write_text(
        """
id: test-kata
version: 1.0.0
title: Test Kata
description: A compact kata for tests.
level: beginner
duration_minutes: 25
tags: [tag-1, tag-2, tag-3, tag-4]
roles: [role-1, role-2, role-3, role-4]
status: draft
""".strip(),
        encoding="utf-8",
    )
    mock_logger = Mock()
    monkeypatch.setattr(kata_import_module, "logger", mock_logger)

    data = service.validate_kata_yaml(yaml_path)

    assert data["tags"] == ["tag-1", "tag-2", "tag-3"]
    assert data["roles"] == ["role-1", "role-2", "role-3"]
    assert mock_logger.warning.call_count == 2


def test_validate_kata_yaml_raises_for_missing_required_fields(service, tmp_path):
    yaml_path = tmp_path / "kata.yaml"
    yaml_path.write_text("id: test-kata\nversion: 1.0.0\n", encoding="utf-8")

    with pytest.raises(ValidationException, match="Missing required fields"):
        service.validate_kata_yaml(yaml_path)


def test_sanitize_markdown_content_normalizes_line_endings_and_blank_lines(service, tmp_path):
    md_path = tmp_path / "steps.md"
    md_path.write_text("Intro\r\n\r\n\r\n\r\nstep one\x00\rstep two\n", encoding="utf-8")

    content = service.sanitize_markdown_content(md_path)

    assert content == "Intro\n\n\nstep one\nstep two"


def test_calculate_content_checksum_uses_yaml_and_steps(service, tmp_path, valid_kata_yaml_text):
    kata_dir = tmp_path / "test-kata"
    kata_dir.mkdir()
    (kata_dir / "kata.yaml").write_text(valid_kata_yaml_text, encoding="utf-8")
    (kata_dir / "steps.md").write_text("Step 1\nStep 2\n", encoding="utf-8")
    expected = hashlib.sha256(f"{valid_kata_yaml_text}\n---\nStep 1\nStep 2\n".encode("utf-8")).hexdigest()

    checksum = service.calculate_content_checksum(kata_dir)

    assert checksum == expected


def test_create_kata_from_files_builds_model_and_logs_mismatch(service, tmp_path, valid_kata_yaml_text, monkeypatch):
    kata_dir = tmp_path / "directory-name"
    kata_dir.mkdir()
    (kata_dir / "kata.yaml").write_text(valid_kata_yaml_text, encoding="utf-8")
    (kata_dir / "steps.md").write_text("## Step 1\nDo the thing.\n", encoding="utf-8")
    mock_logger = Mock()
    monkeypatch.setattr(kata_import_module, "logger", mock_logger)

    kata = service.create_kata_from_files(kata_dir)

    assert kata.id == "test-kata"
    assert kata.title == "Test Kata"
    assert kata.level == KataLevel.BEGINNER
    assert kata.status == KataStatus.PUBLISHED
    assert kata.creator_id == "system"
    assert kata.creator_name == "Kata Author"
    assert kata.tags == ["tag-1"]
    assert kata.roles == ["role-1"]
    assert kata.links[0].url == "https://example.com/docs"
    assert kata.references == ["https://example.com/reference"]
    assert kata.content_checksum == service.calculate_content_checksum(kata_dir)
    mock_logger.warning.assert_called_once()
    mock_logger.info.assert_called_once()


def test_create_kata_from_files_rejects_invalid_directory(service, tmp_path):
    kata_dir = tmp_path / "broken-kata"
    kata_dir.mkdir()

    with pytest.raises(ValidationException, match="Invalid kata directory"):
        service.create_kata_from_files(kata_dir)
