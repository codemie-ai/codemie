#!/usr/bin/env python

"""
Apache License Header Checker for CodeMie

Comprehensive tool for checking and adding Apache License 2.0 headers to source files.
Supports multiple file types with appropriate comment styles.

Scope:
    - Includes: All Python files in src/, scripts/, tests/
      - src/codemie/: Main application code ✓
      - src/external/: Custom utilities, migrations, deployment scripts ✓
    - Excludes: Auto-generated files and test data
      - src/external/alembic/versions/: Auto-generated DB migrations ✗
      - src/codemie/clients/provider/client/: OpenAPI generated client ✗

Usage:
    python check_license_headers.py --check  # Check for missing headers (CI mode)
    python check_license_headers.py --fix    # Add missing headers (developer mode)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

# ==============================================================================
# Configuration & Constants
# ==============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB limit to prevent memory issues
HEADER_CHECK_LINES = 50  # Only check first N lines for existing headers
GIT_TIMEOUT = 30  # Git command timeout in seconds

# Compiled regex patterns (cached for performance)
CODING_COOKIE_RE = re.compile(r"coding[:=]\s*[-\w.]+")
SHEBANG_RE = re.compile(r"^#!\s*/.*")
XML_DECLARATION_RE = re.compile(r"^<\?xml.*?\?>")

# Comment style mapping for different file types
COMMENT_STYLES = {
    # Hash-style comments (#)
    ".py": "#",
    ".sh": "#",
    ".bash": "#",
    ".yaml": "#",
    ".yml": "#",
    ".rb": "#",
    ".pl": "#",
    ".r": "#",
    ".dockerfile": "#",
    # Double-slash comments (//)
    ".java": "//",
    ".js": "//",
    ".jsx": "//",
    ".ts": "//",
    ".tsx": "//",
    ".c": "//",
    ".cpp": "//",
    ".cc": "//",
    ".cxx": "//",
    ".h": "//",
    ".hpp": "//",
    ".hxx": "//",
    ".cs": "//",
    ".go": "//",
    ".rs": "//",
    ".swift": "//",
    ".kt": "//",
    ".scala": "//",
    ".dart": "//",
    ".php": "//",
    ".groovy": "//",
    ".gradle": "//",
    ".scss": "//",
    ".sass": "//",
    ".less": "//",
    # SQL-style comments (--)
    ".sql": "--",
    ".psql": "--",
    ".mysql": "--",
    ".hql": "--",
    ".lua": "--",
    # HTML/XML comments (<!-- -->)
    ".html": "<!-- -->",
    ".htm": "<!-- -->",
    ".xml": "<!-- -->",
    ".xhtml": "<!-- -->",
    ".svg": "<!-- -->",
    # Block comments (/* */)
    ".css": "/* */",
    ".m": "/* */",
    # Special cases
    ".vim": '"',
    ".f90": "!",
    ".asm": ";",
}

# Files that should be excluded (Apache guidelines)
EXCLUDE_FILES = {
    # Apache explicitly excludes distribution files
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "NOTICE",
    "NOTICE.txt",
    "NOTICE.md",
    # Short informational files
    "README",
    "README.md",
    "README.txt",
    "README.rst",
    "CHANGELOG",
    "CHANGELOG.md",
    "CHANGES",
    "CHANGES.md",
    "AUTHORS",
    "AUTHORS.md",
    "AUTHORS.txt",
    "INSTALL",
    "INSTALL.md",
    "INSTALL.txt",
    "CONTRIBUTORS",
    "CONTRIBUTORS.md",
    # Dependency/package management (no creative content)
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    # Configuration files (simple key-value, no creative content)
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".env",
    ".env.example",
    ".env.template",
    "env.template",
    "pytest.ini",
    "tox.ini",
    ".coveragerc",
    ".flake8",
    ".pylintrc",
    # OS-specific files
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    # Terraform state files
    "terraform.tfstate",
    "terraform.tfstate.backup",
    ".terraform.lock.hcl",
}

# Data/config extensions to exclude (per Apache: "lacking creative content")
EXCLUDE_EXTENSIONS = {
    ".json",  # Pure data format
    ".lock",  # Lock files
    ".min.js",
    ".min.css",  # Minified (not source)
    ".map",  # Source maps
}

# Directory prefixes to exclude (specific paths within otherwise-included directories)
# Note: These are SUBTRACTIVE - they exclude specific subdirectories from INCLUDE_PREFIXES
# Example: "src/" is included, but "src/external/alembic/versions/" is excluded
EXCLUDE_DIR_PREFIXES = (
    # Auto-generated code (Apache guideline: lacks creative content)
    "src/external/alembic/versions/",  # Alembic DB migrations (auto-generated, checksummed)
    "src/codemie/clients/provider/client/",  # OpenAPI generated client code
    # Documentation and infrastructure (not source code)
    "docs/",  # Documentation files
    "deploy-templates/",  # Deployment templates/infrastructure
    "terraform/",  # Infrastructure as code
    # Assets and data (not source code)
    "static/",  # Static web assets
    "media/",  # User-uploaded media files
    "locale/",
    "locales/",  # Translation files
    # Test data (modifying breaks tests)
    "tests/fixtures/",  # Test fixtures (expected data)
    "tests/data/",  # Test data files
    "tests/__snapshots__/",  # Snapshot test baselines
)

# Directories to skip entirely
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".elasticsearch_data",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".coverage",
    "htmlcov",
    "codemie-storage",
    "codemie-repos",
    ".codemie-storage",
    ".codemie-repos",
    ".claude",
    ".hypothesis",
    ".sonarlint",
    # IDE directories
    ".idea",
    ".vscode",
    ".vs",
    # Build output directories
    "target",
    "out",
    # Log and temporary directories
    "logs",
    "log",
    "tmp",
    "temp",
    "cache",
}

# Inclusion paths for specific file types
# Note: These are PREFIX matches, so "src/" includes ALL subdirectories:
#   - src/codemie/ (main application code)
#   - src/external/ (custom utilities, migrations, deployment scripts)
#   - etc.
# Specific exclusions (like auto-generated files) are handled by EXCLUDE_DIR_PREFIXES
INCLUDE_PREFIXES = {
    ".py": ("src/", "scripts/", "tests/"),
    ".sh": ("scripts/",),
    ".yaml": ("config/templates/", ".github/workflows/", "deploy-templates/"),
    ".yml": ("config/templates/", ".github/workflows/", "deploy-templates/"),
}


# ==============================================================================
# Utility Functions
# ==============================================================================


def load_license_template() -> list[str]:
    """
    Load license template with error handling.

    Returns:
        List of template lines

    Raises:
        SystemExit: If template cannot be loaded
    """
    template_path = SCRIPT_DIR / "license_template.txt"
    try:
        content = template_path.read_text(encoding="utf-8")
        if not content.strip():
            sys.stderr.write("Error: License template is empty\n")
            sys.exit(1)
        return content.splitlines()
    except FileNotFoundError:
        sys.stderr.write(f"Error: License template not found: {template_path}\n")
        sys.exit(1)
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write(f"Error reading license template: {e}\n")
        sys.exit(1)


def path_starts_with(path: Path, prefixes: Iterable[str]) -> bool:
    """
    Check if path starts with any of the given prefixes.

    Args:
        path: Path to check
        prefixes: Iterable of prefix strings

    Returns:
        True if path starts with any prefix
    """
    path_str = path.as_posix()
    return any(path_str.startswith(pref) for pref in prefixes)


# ==============================================================================
# Core Components (Reusable Classes)
# ==============================================================================


class CommentStyleFormatter:
    """Handles formatting of license headers for different comment styles."""

    def __init__(self, license_lines: list[str]):
        """
        Initialize formatter with license template.

        Args:
            license_lines: License template lines
        """
        self.license_lines = license_lines

    def _format_line_prefix(self, prefix: str) -> list[str]:
        """Format lines with a prefix comment style (e.g., #, //, --, etc.)."""
        return [f"{prefix} {line}" if line.strip() else prefix for line in self.license_lines]

    def _format_block_comment(self, start: str, prefix: str, end: str) -> list[str]:
        """Format lines as a block comment (e.g., /* */ or <!-- -->)."""
        formatted = [start]
        for line in self.license_lines:
            formatted.append(f"{prefix}{line}" if line.strip() else prefix.rstrip())
        formatted.append(end)
        return formatted

    def format_header(self, comment_style: str) -> str:
        """
        Format license header for a specific comment style.

        Args:
            comment_style: Comment style identifier (#, //, --, etc.)

        Returns:
            Formatted header string
        """
        # Line-prefix styles
        if comment_style in ("#", "//", "--", '"', "!", ";"):
            formatted_lines = self._format_line_prefix(comment_style)
        # Block comment styles
        elif comment_style == "<!-- -->":
            formatted_lines = self._format_block_comment("<!--", "  ", "-->")
        elif comment_style == "/* */":
            formatted_lines = self._format_block_comment("/*", " * ", " */")
        # Fallback to hash style
        else:
            formatted_lines = self._format_line_prefix("#")

        return "\n".join(formatted_lines)


class HeaderDetector:
    """Detects existing license headers in file content."""

    def __init__(self, license_first_line: str):
        """
        Initialize detector.

        Args:
            license_first_line: First line of license to detect
        """
        self.license_first_line = license_first_line
        self.apache_markers = [
            "Apache License",
            "http://www.apache.org/licenses/LICENSE-2.0",
            "Licensed under the Apache License",
        ]

    def has_header(self, text: str) -> bool:
        """
        Check if file has Apache license header.

        Only checks first 50 lines to avoid false positives in file body.

        Args:
            text: File content

        Returns:
            True if header found in first 50 lines
        """
        if not self.license_first_line:
            return False

        # Check only first N lines
        lines = text.splitlines()[:HEADER_CHECK_LINES]
        header_text = "\n".join(lines)

        # Check for Apache license markers
        return any(marker in header_text for marker in self.apache_markers)

    def has_header_from_file(self, path: Path) -> bool:
        """
        Check if file has Apache license header by reading only first N lines.

        More efficient than reading entire file - reads only ~2KB instead of
        potentially megabytes. Optimized for CI performance.

        Args:
            path: File path

        Returns:
            True if header found in first 50 lines, False otherwise or on error
        """
        if not self.license_first_line:
            return False

        try:
            with path.open('r', encoding='utf-8') as f:
                # Read only first HEADER_CHECK_LINES lines (not entire file)
                lines = []
                for i, line in enumerate(f):
                    if i >= HEADER_CHECK_LINES:
                        break
                    lines.append(line)

                header_text = "".join(lines)
                return any(marker in header_text for marker in self.apache_markers)
        except (OSError, UnicodeDecodeError):
            return False


class FileValidator:
    """Validates files for header eligibility."""

    @staticmethod
    def is_excluded_file(path: Path) -> bool:
        """Check if filename should be excluded."""
        return path.name in EXCLUDE_FILES

    @staticmethod
    def is_excluded_extension(path: Path) -> bool:
        """Check if extension should be excluded."""
        return path.suffix.lower() in EXCLUDE_EXTENSIONS

    @staticmethod
    def is_excluded_directory(path: Path) -> bool:
        """
        Check if path is in excluded directory.

        Applies three types of exclusions:
        1. Explicit directory names in IGNORE_DIRS
        2. Directory prefixes in EXCLUDE_DIR_PREFIXES
        3. Generic rule: Any directory starting with '.' (hidden directories)
           - Excludes virtual environments with any name (.venv, .my-venv, etc.)
           - Excludes cache directories (.cache, .mypy_cache, etc.)
           - Does NOT exclude dot-files at root (like .pre-commit-config.yaml)
        """
        # Check ignored dirs (explicit names)
        if set(path.parts) & IGNORE_DIRS:
            return True

        # Check excluded prefixes (specific paths)
        if path_starts_with(path, EXCLUDE_DIR_PREFIXES):
            return True

        # Generic rule: Exclude any DIRECTORY (not file) starting with '.'
        # This catches customer-specific setups like .venv, .virtualenv, etc.
        for i, part in enumerate(path.parts):
            # Skip the filename itself (last part)
            is_filename = i == len(path.parts) - 1
            # Exclude hidden directories, but not '.' or '..'
            if not is_filename and part.startswith('.') and len(part) > 1 and part not in ('.', '..'):
                return True

        return False

    @staticmethod
    def is_too_large(path: Path) -> bool:
        """Check if file exceeds size limit."""
        try:
            return path.stat().st_size > MAX_FILE_SIZE
        except OSError:
            return True

    @staticmethod
    def is_too_small(path: Path) -> bool:
        """
        Check if file is too small to warrant a license header.

        Files with fewer than 5 non-empty lines are typically trivial
        (e.g., __init__.py, simple config stubs) and don't require headers.

        Args:
            path: File path to check

        Returns:
            True if file has < 5 non-empty lines
        """
        try:
            with path.open('r', encoding='utf-8') as f:
                non_empty_lines = sum(1 for line in f if line.strip())
                return non_empty_lines < 5
        except (OSError, UnicodeDecodeError):
            # If we can't read it, consider it ineligible
            return True

    @staticmethod
    def get_comment_style(path: Path) -> Optional[str]:
        """
        Determine comment style for file.

        Args:
            path: File path

        Returns:
            Comment style or None if unsupported
        """
        extension = path.suffix.lower()

        # Check extension mapping
        if extension in COMMENT_STYLES:
            return COMMENT_STYLES[extension]

        # Special filenames without extensions
        filename_lower = path.name.lower()
        if filename_lower in ("dockerfile", "makefile"):
            return "#"
        if filename_lower.startswith("dockerfile."):
            return "#"

        return None


class HeaderInserter:
    """Handles insertion of headers into file content."""

    def __init__(self, formatter: CommentStyleFormatter):
        """
        Initialize inserter.

        Args:
            formatter: Comment style formatter
        """
        self.formatter = formatter

    def insert(self, text: str, path: Path, comment_style: str) -> str:
        """
        Insert header into file content.

        Preserves:
        - Shebang lines (#!/usr/bin/env python)
        - Encoding declarations (# -*- coding: utf-8 -*-)
        - XML declarations (<?xml version="1.0"?>)

        Args:
            text: Original file content
            path: File path (for determining special handling)
            comment_style: Comment style to use

        Returns:
            Content with header inserted
        """
        # Handle empty files
        if not text.strip():
            return self.formatter.format_header(comment_style) + "\n\n"

        header = self.formatter.format_header(comment_style)
        lines = text.splitlines(keepends=True)

        if not lines:
            return header + "\n\n"

        insert_at = 0

        # Preserve shebang
        if lines and SHEBANG_RE.match(lines[0]):
            insert_at = 1

        # Preserve Python encoding cookie
        if path.suffix == ".py":
            if len(lines) >= 1 and CODING_COOKIE_RE.search(lines[0]):
                insert_at = max(insert_at, 1)
            if len(lines) >= 2 and CODING_COOKIE_RE.search(lines[1]):
                insert_at = max(insert_at, 2)

        # Preserve XML declaration
        if path.suffix in {".xml", ".xhtml", ".svg"} and lines and XML_DECLARATION_RE.match(lines[0]):
            insert_at = 1

        return "".join(lines[:insert_at]) + header + "\n\n" + "".join(lines[insert_at:])


class FileProcessor:
    """Processes files for license header checking and fixing."""

    def __init__(
        self,
        formatter: CommentStyleFormatter,
        detector: HeaderDetector,
        validator: FileValidator,
        inserter: HeaderInserter,
    ):
        """Initialize processor with components."""
        self.formatter = formatter
        self.detector = detector
        self.validator = validator
        self.inserter = inserter

    def is_eligible(self, path: Path) -> tuple[bool, Optional[str]]:
        """
        Check if file is eligible for license header.

        Args:
            path: File path

        Returns:
            Tuple of (is_eligible, comment_style)
        """
        # Check exclusions
        if self.validator.is_excluded_file(path):
            return False, None

        if self.validator.is_excluded_extension(path):
            return False, None

        if self.validator.is_excluded_directory(path):
            return False, None

        if self.validator.is_too_large(path):
            return False, None

        if self.validator.is_too_small(path):
            return False, None

        # Get comment style
        comment_style = self.validator.get_comment_style(path)
        if not comment_style:
            return False, None

        # Check inclusion prefixes for specific extensions
        extension = path.suffix.lower()
        if extension in INCLUDE_PREFIXES:
            prefixes = INCLUDE_PREFIXES[extension]
            if not path_starts_with(path, prefixes):
                return False, None

        return True, comment_style

    def read_file(self, path: Path) -> Optional[str]:
        """
        Read file content safely.

        Args:
            path: File path

        Returns:
            File content or None on error
        """
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, PermissionError) as e:
            sys.stderr.write(f"Warning: Cannot read {path}: {e}\n")
            return None

    def write_file(self, path: Path, content: str) -> bool:
        """
        Write file content safely, preserving permissions.

        Args:
            path: File path
            content: Content to write

        Returns:
            True on success, False on error
        """
        try:
            # Preserve file permissions
            original_mode = path.stat().st_mode
            path.write_text(content, encoding="utf-8")
            path.chmod(original_mode)
            return True
        except (OSError, PermissionError) as e:
            sys.stderr.write(f"Error: Cannot write {path}: {e}\n")
            return False


# ==============================================================================
# Main Operations
# ==============================================================================


def iter_repo_files() -> Iterable[Path]:
    """
    Iterate over all tracked files in the git repository.

    Yields:
        Path objects for each file
    """
    try:
        files = (
            subprocess.check_output(
                ["git", "ls-files", "--no-empty-directory"], stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT
            )
            .decode()
            .strip()
            .splitlines()
        )

        for f in files:
            p = Path(f)
            if p.is_file():
                yield p

    except subprocess.TimeoutExpired:
        sys.stderr.write("Error: Git command timed out\n")
        sys.exit(1)

    except subprocess.CalledProcessError:
        # Not in a git repository, fallback
        sys.stderr.write("Warning: Not in git repository, scanning all files...\n")
        for p in Path(".").rglob("*"):
            if p.is_file() and not any(ignored in p.parts for ignored in IGNORE_DIRS):
                yield p


def check(paths: Iterable[Path], processor: FileProcessor, detector: HeaderDetector, quiet: bool = False) -> int:
    """
    Check files for missing license headers.

    Optimized for CI performance:
    - Streams output immediately (constant memory)
    - Reads only first 50 lines (reduced I/O)
    - Optional quiet mode for CI environments

    Args:
        paths: Paths to check
        processor: File processor
        detector: Header detector
        quiet: Suppress progress output (default: False)

    Returns:
        Number of files missing headers
    """
    missing = 0
    checked = 0

    for p in paths:
        is_eligible, comment_style = processor.is_eligible(p)
        if not is_eligible:
            continue

        checked += 1

        # Progress indicator every 100 files (unless quiet)
        if not quiet and checked % 100 == 0:
            sys.stderr.write(f"Processed {checked} files...\r")
            sys.stderr.flush()

        # Optimized: Read only first 50 lines instead of entire file
        if not detector.has_header_from_file(p):
            # Stream output immediately instead of batching
            sys.stdout.write(f"Missing license header: {p}\n")
            sys.stdout.flush()
            missing += 1

    # Clear progress line (only if shown)
    if not quiet and checked >= 100:
        sys.stderr.write("\n")

    sys.stdout.write(f"\nChecked {checked} files, {missing} missing license headers\n")

    if missing > 0:
        sys.stdout.write("\nTo fix, run: make license-fix\n")

    return missing


def fix(paths: Iterable[Path], processor: FileProcessor, detector: HeaderDetector) -> int:
    """
    Add license headers to files missing them.

    Args:
        paths: Paths to fix
        processor: File processor
        detector: Header detector

    Returns:
        0 if all operations succeeded, 1 if any failures occurred
    """
    changed = 0
    checked = 0
    failed = 0
    failed_files = []

    for p in paths:
        is_eligible, comment_style = processor.is_eligible(p)
        if not is_eligible:
            continue

        checked += 1

        # Progress indicator
        if checked % 100 == 0:
            sys.stderr.write(f"Processed {checked} files...\r")
            sys.stderr.flush()

        content = processor.read_file(p)
        if content is None:
            # File read failed (error already logged in read_file)
            failed += 1
            failed_files.append(p)
            continue

        if detector.has_header(content):
            continue

        new_content = processor.inserter.insert(content, p, comment_style)

        if processor.write_file(p, new_content):
            sys.stdout.write(f"Added license header: {p}\n")
            changed += 1
        else:
            # File write failed (error already logged in write_file)
            failed += 1
            failed_files.append(p)

    # Clear progress line
    if checked >= 100:
        sys.stderr.write("\n")

    sys.stdout.write(f"\nChecked {checked} files, added headers to {changed} files\n")

    if failed > 0:
        sys.stdout.write(f"\n⚠️  WARNING: {failed} files failed (read/write errors)\n")
        sys.stdout.write("Failed files:\n")
        for p in failed_files:
            sys.stdout.write(f"  - {p}\n")
        sys.stdout.write("\nPlease check file permissions and try again.\n")
        return 1

    if changed > 0:
        sys.stdout.write("\nPlease review and stage the changes:\n")
        sys.stdout.write("  git add <files>\n")
        sys.stdout.write("  git commit\n")

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Check or add Apache License 2.0 headers to source files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --check                              # Check all files (CI mode)
  %(prog)s --fix                                # Add headers to all files
  %(prog)s --check src/codemie/service/foo.py   # Check specific file
  %(prog)s --fix tests/test_bar.py              # Fix specific file

Exit codes:
  --check mode: Returns 1 if any files missing headers, 0 if all have headers
  --fix mode:   Returns 1 if any read/write failures, 0 if all succeeded
        """,
    )
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument(
        "--check", action="store_true", help="Check for missing headers and return non-zero if any found (default)"
    )
    mode.add_argument("--fix", action="store_true", help="Add headers where missing")
    parser.add_argument("files", nargs="*", help="Specific files to check/fix (default: all files in repository)")
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output (recommended for CI environments)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv or [])

    # Load license template
    license_lines = load_license_template()
    license_first_line = license_lines[0] if license_lines else ""

    # Initialize components
    formatter = CommentStyleFormatter(license_lines)
    detector = HeaderDetector(license_first_line)
    validator = FileValidator()
    inserter = HeaderInserter(formatter)
    processor = FileProcessor(formatter, detector, validator, inserter)

    # Get files to process
    if args.files:
        # Process specific files
        files = []
        for file_path in args.files:
            p = Path(file_path)
            if not p.exists():
                sys.stderr.write(f"Error: File not found: {file_path}\n")
                return 1
            if not p.is_file():
                sys.stderr.write(f"Error: Not a file: {file_path}\n")
                return 1

            is_eligible, _ = processor.is_eligible(p)
            if not is_eligible:
                sys.stderr.write(f"Warning: File not eligible: {file_path}\n")
                sys.stderr.write("File may be excluded or unsupported type.\n")
                return 1
            files.append(p)
    else:
        # Get all files in repository
        files = iter_repo_files()

    # Run in fix or check mode
    if args.fix:
        return fix(files, processor, detector)

    # Default: check mode
    missing = check(files, processor, detector, quiet=args.quiet)
    return 1 if missing > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
