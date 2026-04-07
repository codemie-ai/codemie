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
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from codemie.configs import config, logger
from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.ai_kata import (
    AIKata,
    KataLevel,
    KataStatus,
    KataLink,
    get_valid_kata_tag_ids,
    get_valid_kata_role_ids,
)


# Constants for kata file names
KATA_STEPS_FILENAME = "steps.md"
KATA_YAML_FILENAME = "kata.yaml"
KATA_YAML_ALT_FILENAME = "kata.yml"


class KataImportService(BaseModel):
    """
    Service for importing AI Katas from file system into database.
    Handles validation, sanitization, and kata creation from YAML + Markdown files.
    """

    model_config = {"arbitrary_types_allowed": True}

    def _find_yaml_file(self, kata_path: Path) -> Path | None:
        """Find kata.yaml or kata.yml file in directory."""
        yaml_path = kata_path / KATA_YAML_FILENAME
        if yaml_path.exists():
            return yaml_path

        yaml_alt_path = kata_path / KATA_YAML_ALT_FILENAME
        if yaml_alt_path.exists():
            return yaml_alt_path

        return None

    def validate_kata_directory(self, kata_path: Path) -> tuple[bool, str]:
        """
        Validate kata directory structure and files.
        Only validates YAML and Markdown files, ignores images and other files.

        Args:
            kata_path: Path to kata directory

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not kata_path.is_dir():
            return False, f"Path is not a directory: {kata_path}"

        # Check required files exist
        kata_yaml_path = self._find_yaml_file(kata_path)
        if not kata_yaml_path:
            return False, "Missing kata.yaml or kata.yml file"

        steps_path = kata_path / KATA_STEPS_FILENAME
        if not steps_path.exists():
            return False, f"Missing {KATA_STEPS_FILENAME} file"

        # Validate file sizes (only for YAML and Markdown, ignore other files like images)
        try:
            yaml_size = kata_yaml_path.stat().st_size
            steps_size = steps_path.stat().st_size

            if yaml_size > config.KATAS_MAX_YAML_SIZE:
                return False, f"YAML file too large: {yaml_size} bytes (max: {config.KATAS_MAX_YAML_SIZE})"

            if steps_size > config.KATAS_MAX_MARKDOWN_SIZE:
                return False, f"Markdown file too large: {steps_size} bytes (max: {config.KATAS_MAX_MARKDOWN_SIZE})"

        except Exception as e:
            return False, f"Error checking file sizes: {e}"

        return True, ""

    def _validate_required_fields(self, data: dict) -> None:
        """Validate that all required fields are present."""
        required_fields = [
            "id",
            "version",
            "title",
            "description",
            "level",
            "duration_minutes",
            "tags",
            "roles",
            "status",
        ]
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            raise ValidationException(f"Missing required fields: {', '.join(missing_fields)}")

    def _validate_basic_fields(self, data: dict) -> None:
        """Validate basic field types and constraints."""
        # Validate ID
        kata_id = data.get("id")
        if not isinstance(kata_id, str) or not kata_id:
            raise ValidationException("Field 'id' must be a non-empty string")
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", kata_id):
            raise ValidationException(f"Invalid kata ID format: {kata_id}. Must be kebab-case (e.g., 'my-kata')")

        # Validate version
        version = data.get("version")
        if not isinstance(version, str) or not re.match(r"^\d+\.\d+\.\d+", version):
            raise ValidationException(f"Invalid version format: {version}. Must be semver (e.g., '1.0.0')")

        # Validate title
        title = data.get("title")
        if not isinstance(title, str) or not title or len(title) > 200:
            raise ValidationException("Field 'title' must be a non-empty string (max 200 characters)")

        # Validate description
        description = data.get("description")
        if not isinstance(description, str) or not description or len(description) > 1000:
            raise ValidationException("Field 'description' must be a non-empty string (max 1000 characters)")

    def _validate_level_and_duration(self, data: dict) -> None:
        """Validate level and duration fields."""
        # Validate level
        level = data.get("level", "").upper()
        valid_levels = ["BEGINNER", "INTERMEDIATE", "ADVANCED"]
        if level not in valid_levels:
            raise ValidationException(f"Invalid level: {data.get('level')}. Must be one of: {', '.join(valid_levels)}")

        # Validate duration
        duration = data.get("duration_minutes")
        if not isinstance(duration, int) or duration < 5 or duration > 240:
            raise ValidationException(f"Invalid duration_minutes: {duration}. Must be between 5 and 240")

    def _validate_tags_and_roles(self, data: dict) -> None:
        """Validate tags and roles with cross-reference checking."""
        kata_id = data.get("id")

        # Validate tags
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            raise ValidationException("Field 'tags' must be a list")

        if len(tags) > 3:
            logger.warning(f"Kata {kata_id} has {len(tags)} tags, but max is 3. Will truncate.")
            data["tags"] = tags[:3]

        valid_tag_ids = get_valid_kata_tag_ids()
        unknown_tags = [tag for tag in data["tags"] if tag not in valid_tag_ids]
        if unknown_tags:
            logger.warning(f"Kata {kata_id} has unknown tags: {unknown_tags}. Backend may not recognize them.")

        # Validate roles
        roles = data.get("roles", [])
        if not isinstance(roles, list):
            raise ValidationException("Field 'roles' must be a list")

        if len(roles) > 3:
            logger.warning(f"Kata {kata_id} has {len(roles)} roles, but max is 3. Will truncate.")
            data["roles"] = roles[:3]

        valid_role_ids = get_valid_kata_role_ids()
        unknown_roles = [role for role in data["roles"] if role not in valid_role_ids]
        if unknown_roles:
            logger.warning(f"Kata {kata_id} has unknown roles: {unknown_roles}. Backend may not recognize them.")

    def _validate_status_and_optional_fields(self, data: dict) -> None:
        """Validate status and optional fields."""
        # Validate status
        status_value = data.get("status", "").upper()
        valid_statuses = ["DRAFT", "PUBLISHED", "ARCHIVED"]
        if status_value not in valid_statuses:
            raise ValidationException(
                f"Invalid status: {data.get('status')}. Must be one of: {', '.join(valid_statuses)}"
            )

        # Validate optional fields
        if "image_url" in data and data["image_url"] is not None:
            if not isinstance(data["image_url"], str) or len(data["image_url"]) > 500:
                raise ValidationException("Field 'image_url' must be a string (max 500 characters)")

        if "links" in data and not isinstance(data["links"], list):
            raise ValidationException("Field 'links' must be a list")

        if "references" in data and not isinstance(data["references"], list):
            raise ValidationException("Field 'references' must be a list")

    def validate_kata_yaml(self, yaml_path: Path) -> dict:
        """
        Load and validate kata YAML file.

        Args:
            yaml_path: Path to kata.yaml file

        Returns:
            Parsed YAML dictionary

        Raises:
            ValidationException: If validation fails
        """
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                raise ValidationException("YAML content must be a dictionary")

            # Validate in stages using extracted methods
            self._validate_required_fields(data)
            self._validate_basic_fields(data)
            self._validate_level_and_duration(data)
            self._validate_tags_and_roles(data)
            self._validate_status_and_optional_fields(data)

            return data

        except yaml.YAMLError as e:
            raise ValidationException(f"YAML parsing error: {e}")
        except FileNotFoundError:
            raise ValidationException(f"YAML file not found: {yaml_path}")
        except Exception as e:
            raise ValidationException(f"Error validating YAML: {e}")

    def sanitize_markdown_content(self, md_path: Path) -> str:
        """
        Read and sanitize markdown content.

        Args:
            md_path: Path to steps.md file

        Returns:
            Sanitized markdown string

        Raises:
            ValidationException: If file cannot be read or is too large
        """
        try:
            # Check file size
            file_size = md_path.stat().st_size
            if file_size > config.KATAS_MAX_MARKDOWN_SIZE:
                raise ValidationException(
                    f"Markdown file too large: {file_size} bytes (max: {config.KATAS_MAX_MARKDOWN_SIZE})"
                )

            # Read content with size check
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Basic sanitization
            # Remove null bytes
            content = content.replace("\x00", "")

            # Normalize line endings to Unix style
            content = content.replace("\r\n", "\n").replace("\r", "\n")

            # Remove excessive blank lines (more than 3 consecutive)
            content = re.sub(r'\n{4,}', '\n\n\n', content)

            return content.strip()

        except FileNotFoundError:
            raise ValidationException(f"Markdown file not found: {md_path}")
        except Exception as e:
            raise ValidationException(f"Error reading markdown file: {e}")

    def calculate_content_checksum(self, kata_dir: Path) -> str:
        """
        Calculate SHA256 checksum of kata content (kata.yaml + steps.md).
        Used to detect changes in kata content for update detection.

        Args:
            kata_dir: Path to kata directory

        Returns:
            SHA256 hex digest of combined content

        Raises:
            ValidationException: If files cannot be read
        """
        try:
            # Find YAML file
            yaml_path = self._find_yaml_file(kata_dir)
            if not yaml_path:
                raise ValidationException("YAML file not found")

            steps_path = kata_dir / KATA_STEPS_FILENAME
            if not steps_path.exists():
                raise ValidationException(f"{KATA_STEPS_FILENAME} file not found")

            # Read both files
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_content = f.read()

            with open(steps_path, "r", encoding="utf-8") as f:
                steps_content = f.read()

            # Combine content and calculate SHA256
            combined_content = yaml_content + "\n---\n" + steps_content
            checksum = hashlib.sha256(combined_content.encode("utf-8")).hexdigest()

            return checksum

        except FileNotFoundError as e:
            raise ValidationException(f"File not found for checksum calculation: {e}")
        except Exception as e:
            raise ValidationException(f"Error calculating checksum: {e}")

    def create_kata_from_files(self, kata_dir: Path) -> AIKata:
        """
        Create AIKata instance from kata directory files.
        Orchestrates validation and parsing.

        Args:
            kata_dir: Path to kata directory

        Returns:
            AIKata instance (not saved to database yet)

        Raises:
            ValidationException: If validation fails
        """
        # Step 1: Validate directory structure
        is_valid, error_message = self.validate_kata_directory(kata_dir)
        if not is_valid:
            raise ValidationException(f"Invalid kata directory '{kata_dir.name}': {error_message}")

        # Step 2: Load and validate YAML
        yaml_path = kata_dir / KATA_YAML_FILENAME
        if not yaml_path.exists():
            yaml_path = kata_dir / KATA_YAML_ALT_FILENAME

        kata_data = self.validate_kata_yaml(yaml_path)

        # Step 3: Load and sanitize markdown
        steps_path = kata_dir / KATA_STEPS_FILENAME
        steps_content = self.sanitize_markdown_content(steps_path)

        # Step 4: Calculate content checksum for update detection
        content_checksum = self.calculate_content_checksum(kata_dir)

        # Step 5: Log warning if ID doesn't match directory name, but use YAML ID as authoritative
        if kata_data["id"] != kata_dir.name:
            logger.warning(
                f"Kata ID mismatch: directory name is '{kata_dir.name}' but ID in YAML is '{kata_data['id']}'. "
                f"Using YAML ID '{kata_data['id']}' as authoritative."
            )

        # Step 6: Map YAML data to AIKata model
        try:
            # Parse links if present
            links = []
            if "links" in kata_data and kata_data["links"]:
                for link_data in kata_data["links"]:
                    if isinstance(link_data, dict):
                        links.append(
                            KataLink(
                                title=link_data.get("title", ""),
                                url=link_data.get("url", ""),
                                type=link_data.get("type", "documentation"),
                            )
                        )

            # Extract creator name from author field
            creator_name = None
            if "author" in kata_data and isinstance(kata_data["author"], dict):
                creator_name = kata_data["author"].get("name")

            # Create AIKata instance
            kata = AIKata(
                id=kata_data["id"],
                title=kata_data["title"],
                description=kata_data["description"],
                steps=steps_content,
                level=KataLevel[kata_data["level"].upper()],
                duration_minutes=kata_data["duration_minutes"],
                tags=kata_data["tags"][:3],  # Ensure max 3 tags
                roles=kata_data["roles"][:3],  # Ensure max 3 roles
                status=KataStatus[kata_data["status"].upper()],
                image_url=kata_data.get("image_url"),
                links=links,
                references=kata_data.get("references", []),
                creator_id="system",  # System kata - special system user ID
                creator_name=creator_name,
                creator_username=None,  # Not in YAML
                version=kata_data["version"],  # Semver from YAML
                content_checksum=content_checksum,  # SHA256 checksum for update detection
                # Counters default to 0
                enrollment_count=0,
                completed_count=0,
                unique_likes_count=0,
                unique_dislikes_count=0,
            )

            logger.info(f"Successfully created kata object for: {kata.id}")
            return kata

        except Exception as e:
            raise ValidationException(f"Error creating kata model: {e}")


# Singleton instance
kata_import_service = KataImportService()
