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

import yaml

from codemie.configs import config, logger
from codemie.repository.ai_kata_repository import SQLAIKataRepository
from codemie.service.kata_import_service import (
    kata_import_service,
    ValidationException,
    KATA_YAML_FILENAME,
    KATA_YAML_ALT_FILENAME,
)


def import_preconfigured_katas():
    """
    Import AI Katas from config/katas/ directory into database.
    Idempotent: creates new katas or updates existing if content changed (checksum differs).
    Preserves user statistics (enrollment counts, reaction counts) during updates.

    This function:
    1. Scans the config/katas/ directory for kata subdirectories
    2. Validates each kata directory structure
    3. Checks if kata already exists in database
    4. For existing katas: compares content checksums
       - If unchanged: skips kata
       - If changed: updates content while preserving user statistics
    5. For new katas: creates them in the database
    6. Logs import statistics (imported, updated, skipped, failed)

    Expected directory structure:
        config/katas/
        ├── kata-name-1/
        │   ├── kata.yaml (includes version field)
        │   └── steps.md
        ├── kata-name-2/
        │   ├── kata.yaml
        │   └── steps.md
        └── ...

    Returns:
        None
    """
    katas_dir = config.KATAS_SOURCE_DIR

    # Check if katas directory exists
    if not katas_dir.exists():
        logger.warning(f"Katas directory not found: {katas_dir}. Skipping kata import.")
        return

    # Find kata directories (exclude hidden directories starting with '.')
    kata_dirs = [d for d in katas_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]

    if not kata_dirs:
        logger.info("No kata directories found. Skipping kata import.")
        return

    logger.info(f"Found {len(kata_dirs)} kata directories. Starting import...")

    # Initialize counters
    imported_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    # Initialize repository
    repository = SQLAIKataRepository()

    # Process each kata directory
    for kata_dir in kata_dirs:
        try:
            # First, read the YAML to get the actual kata ID (may differ from directory name)
            yaml_path = kata_dir / KATA_YAML_FILENAME
            if not yaml_path.exists():
                yaml_path = kata_dir / KATA_YAML_ALT_FILENAME

            if not yaml_path.exists():
                logger.warning(f"Skipping '{kata_dir.name}': No kata.yaml or kata.yml found")
                failed_count += 1
                continue

            # Quick parse to get the ID for idempotency check
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)

            kata_id = yaml_data.get("id")
            if not kata_id:
                logger.warning(f"Skipping '{kata_dir.name}': No 'id' field in YAML")
                failed_count += 1
                continue

            # Validate and create kata object (not saved yet)
            logger.debug(f"Validating kata: {kata_id} (from directory '{kata_dir.name}')")
            kata = kata_import_service.create_kata_from_files(kata_dir)

            # Check if kata already exists - use actual YAML ID, not directory name
            existing_kata = repository.get_by_id(kata_id)

            if existing_kata:
                # Kata exists - check if content changed
                if existing_kata.content_checksum == kata.content_checksum:
                    # Content unchanged, skip
                    logger.debug(f"Kata '{kata_id}' unchanged (checksum match). Skipping.")
                    skipped_count += 1
                    continue

                # Content changed - update kata while preserving user statistics
                old_version = existing_kata.version or "unknown"
                new_version = kata.version or "unknown"
                logger.info(f"Kata '{kata_id}' content changed (version: {old_version} -> {new_version}). Updating...")

                # Prepare update with content fields only (preserve user statistics)
                updates = {
                    "title": kata.title,
                    "description": kata.description,
                    "steps": kata.steps,
                    "level": kata.level,
                    "duration_minutes": kata.duration_minutes,
                    "tags": kata.tags,
                    "roles": kata.roles,
                    "status": kata.status,
                    "image_url": kata.image_url,
                    "links": kata.links,
                    "references": kata.references,
                    "creator_name": kata.creator_name,
                    "version": kata.version,
                    "content_checksum": kata.content_checksum,
                }

                # Update in database (repository.update preserves enrollment/reaction counts)
                repository.update(kata_id, updates)
                logger.info(f"Successfully updated kata: {kata_id} (version {new_version})")
                updated_count += 1

            else:
                # New kata - create in database
                repository.create(kata)
                logger.info(f"Successfully imported new kata: {kata_id} (version {kata.version or 'unknown'})")
                imported_count += 1

        except ValidationException as e:
            logger.error(f"Validation error for kata '{kata_dir.name}': {e}")
            failed_count += 1
        except Exception as e:
            logger.error(f"Failed to import kata '{kata_dir.name}': {e}", exc_info=True)
            failed_count += 1

    # Log final statistics
    logger.info(
        f"Kata import completed. "
        f"Imported: {imported_count}, "
        f"Updated: {updated_count}, "
        f"Skipped: {skipped_count}, "
        f"Failed: {failed_count}"
    )
