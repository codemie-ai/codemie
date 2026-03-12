#!/usr/bin/env python3
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

"""
CLI script for manually importing AI Katas from GitHub repository.

This script:
1. Clones the GitHub repository (codemie-ai/codemie-katas)
2. Copies the katas/ directory to config/katas/
3. Imports katas into the database using the preconfigured_katas module

Usage:
    poetry run import-katas
    OR
    python scripts/import_katas.py
"""

import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

# Import config first to ensure proper initialization
from codemie.configs import config, logger


def clone_katas_repo(repo_url: str, target_dir: Path) -> bool:
    """
    Clone GitHub repo to temp directory and copy katas/ to target.

    Note: Only copies .yaml, .yml, and .md files. Images and other files are ignored.
    The metadata/ directory (kata-tags.yaml, kata-roles.yaml) is NOT copied as the
    backend's version in config/categories/ is authoritative.

    Args:
        repo_url: GitHub repository URL
        target_dir: Target directory for katas

    Returns:
        True if successful, False otherwise
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        logger.info(f"Cloning repository: {repo_url}")
        print(f"Cloning repository: {repo_url}")

        # Clone repository with depth=1 for faster cloning
        result = subprocess.run(["git", "clone", "--depth=1", repo_url, str(tmp_path)], capture_output=True, text=True)

        if result.returncode != 0:
            error_msg = f"Git clone failed: {result.stderr}"
            logger.error(error_msg)
            print(error_msg)
            return False

        # Verify katas/ directory exists
        source_katas = tmp_path / "katas"
        if not source_katas.exists():
            error_msg = "Error: katas/ directory not found in repository"
            logger.error(error_msg)
            print(error_msg)
            return False

        # Count kata directories
        kata_dirs = [d for d in source_katas.iterdir() if d.is_dir() and not d.name.startswith('.')]
        kata_count = len(kata_dirs)
        logger.info(f"Found {kata_count} kata directories in repository")
        print(f"Found {kata_count} kata directories")

        # Remove existing katas directory if present
        if target_dir.exists():
            logger.info(f"Removing existing directory: {target_dir}")
            print(f"Removing existing directory: {target_dir}")
            shutil.rmtree(target_dir)

        # Create target directory
        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy only YAML and Markdown files from each kata directory
        allowed_extensions = {".yaml", ".yml", ".md"}
        total_files_copied = 0

        logger.info("Copying kata files (only .yaml, .yml, .md)...")
        print("Copying kata files (only .yaml, .yml, .md)...")

        for kata_dir in kata_dirs:
            # Create corresponding directory in target
            target_kata_dir = target_dir / kata_dir.name
            target_kata_dir.mkdir(exist_ok=True)

            # Copy only allowed files
            files_copied = 0
            for file_path in kata_dir.iterdir():
                if file_path.is_file() and file_path.suffix in allowed_extensions:
                    shutil.copy2(file_path, target_kata_dir / file_path.name)
                    files_copied += 1
                    total_files_copied += 1

            if files_copied == 0:
                logger.warning(f"No YAML/MD files found in {kata_dir.name}")

        # Verify copy
        copied_count = len([d for d in target_dir.iterdir() if d.is_dir() and not d.name.startswith('.')])
        logger.info(f"Successfully copied {copied_count} kata directories with {total_files_copied} files")
        print(f"Successfully copied {copied_count} kata directories with {total_files_copied} files")
        print("(Images and other files were ignored)")

    return True


def validate_katas(target_dir: Path) -> tuple[int, int]:
    """
    Validate all kata directories without importing to database.

    Args:
        target_dir: Directory containing kata subdirectories

    Returns:
        Tuple of (valid_count, invalid_count)
    """
    from codemie.service.kata_import_service import kata_import_service, ValidationException

    kata_dirs = [d for d in target_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]

    valid_count = 0
    invalid_count = 0

    print(f"Validating {len(kata_dirs)} kata directories...")
    print()

    for kata_dir in kata_dirs:
        try:
            # Validate directory structure and content
            is_valid, error_msg = kata_import_service.validate_kata_directory(kata_dir)
            if not is_valid:
                print(f"  ❌ {kata_dir.name}: {error_msg}")
                invalid_count += 1
                continue

            # Validate YAML and create kata object (doesn't save to DB)
            kata_import_service.create_kata_from_files(kata_dir)
            print(f"  ✅ {kata_dir.name}: Valid")
            valid_count += 1

        except ValidationException as e:
            print(f"  ❌ {kata_dir.name}: {e}")
            invalid_count += 1
        except Exception as e:
            print(f"  ❌ {kata_dir.name}: Unexpected error - {e}")
            invalid_count += 1

    return valid_count, invalid_count


def main():
    """Main entry point for kata import command."""
    print("=" * 60)
    print("CodeMie Kata Importer")
    print("=" * 60)
    print()

    repo_url = config.KATAS_REPO_URL
    target_dir = config.KATAS_SOURCE_DIR

    print(f"Repository: {repo_url}")
    print(f"Target directory: {target_dir}")
    print()

    # Step 1: Clone and copy
    print("Step 1: Cloning repository...")
    print("-" * 60)
    if not clone_katas_repo(repo_url, target_dir):
        print()
        print("ERROR: Failed to clone repository.")
        print("Please check the repository URL and your network connection.")
        sys.exit(1)

    print()
    print("Step 2: Validating kata files...")
    print("-" * 60)

    # Step 2: Validate (but don't import to DB)
    try:
        valid_count, invalid_count = validate_katas(target_dir)
        print()
        print("=" * 60)
        print("Validation Summary:")
        print(f"  ✅ Valid katas: {valid_count}")
        print(f"  ❌ Invalid katas: {invalid_count}")
        print("=" * 60)
        print()

        if invalid_count > 0:
            print("⚠️  Some katas failed validation.")
            print("Please review the errors above and fix the issues.")
            print()

        print("✅ Katas copied to config/katas/")
        print()
        print("📝 Note: Katas will be imported to the database automatically")
        print("   when the application starts. Restart the app to see them.")
        print()

    except Exception as e:
        error_msg = f"Error during validation: {e}"
        logger.error(error_msg, exc_info=True)
        print()
        print(f"ERROR: {error_msg}")
        print("Please check the logs for more details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
