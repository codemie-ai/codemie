# Apache License Header Checker

Automated checking and fixing of Apache License 2.0 headers in CodeMie source files.

## Overview

This tool ensures all source files in the CodeMie project have proper Apache License 2.0 headers. It integrates with the existing development workflow and CI pipeline.

## Quick Start

### Fix and Check (Recommended - Like ruff)

**Fix all files and verify:**
```bash
make license
```

**Fix specific file and verify:**
```bash
make license FILE=src/codemie/service/my_service.py
```

This command:
1. Adds headers to files missing them (`--fix`)
2. Verifies all files have headers (`--check`)

### Individual Commands

**Check only (no changes):**
```bash
make license-check                          # All files
make license-check FILE=path/to/file.py     # Single file
```

**Fix only (add headers):**
```bash
make license-fix                            # All files
make license-fix FILE=path/to/file.py       # Single file
```

### Run Full Verification (CI mode)
The license check runs automatically in CI via the `make verify` target:

```makefile
verify: ruff license-check test
```

When your CI pipeline runs `make verify`, it will:
1. Run ruff formatting and linting
2. **Check Apache license headers** (fails if any missing)
3. Run tests

## Usage

### Command Line

The script can be run directly:

```bash
# Check all files (CI-friendly - returns non-zero if headers missing)
poetry run python scripts/license_headers/check_license_headers.py --check

# Check specific file(s)
poetry run python scripts/license_headers/check_license_headers.py --check src/codemie/service/foo.py
poetry run python scripts/license_headers/check_license_headers.py --check file1.py file2.py

# Fix all files (adds headers to files)
poetry run python scripts/license_headers/check_license_headers.py --fix

# Fix specific file(s)
poetry run python scripts/license_headers/check_license_headers.py --fix src/codemie/service/foo.py
```

### Makefile Commands

Integrated with existing Makefile (mirrors ruff pattern):

```bash
# Combined: fix + check (recommended, like ruff)
make license                                 # All files
make license FILE=path/to/file.py            # Single file

# Individual commands
make license-check                           # Check only (CI mode)
make license-fix                             # Fix only
make license-check FILE=path/to/file.py      # Check single file
make license-fix FILE=path/to/file.py        # Fix single file

# Full verification (for CI)
make verify                                  # ruff + license-check + test
```


## What Files Are Checked?

### Included Files

The tool checks source files in:
- **`src/`** - All source code (includes subdirectories):
  - `src/codemie/` - Main application code ✓
  - `src/external/` - Custom utilities, migrations, deployment scripts ✓
    - `src/external/deployment_scripts/` ✓
    - `src/external/migrations/` ✓
    - `src/external/utility_scripts/` ✓
    - `src/external/alembic/env.py` and config files ✓
    - ⚠️ **Exception**: `src/external/alembic/versions/` - Auto-generated migrations ✗
- **`scripts/`** - Utility scripts
- **`tests/`** - Test files

### File Types

- **Python files** (`.py`)
- **Shell scripts** (`.sh`)
- **YAML files** (`.yaml`, `.yml`) - only in `config/templates/`, `.github/workflows/`, `deploy-templates/`

### Excluded Files

The following are automatically excluded:

**Auto-generated code** (Apache guideline: lacks creative content):
- `src/external/alembic/versions/` - Alembic database migrations (auto-generated, checksummed)
- `src/codemie/clients/provider/client/` - OpenAPI generated client code

**Infrastructure and templates**:
- `terraform/` - Infrastructure as code
- `deploy-templates/` - Deployment templates
- `docs/` - Documentation

**Build artifacts and caches**:
- `.venv`, `venv`, `env` - Virtual environments
- `__pycache__`, `.ruff_cache`, `.pytest_cache` - Python caches
- `node_modules`, `dist`, `build` - Build directories

**Data and storage**:
- `codemie-storage/`, `codemie-repos/` - Storage directories
- `tests/fixtures/`, `tests/data/` - Test data files
- `static/`, `media/` - Static assets and uploads

## Header Format

### Python Files (`.py`)

```python
# Copyright (c) CodeMie Inc. (2023-2025)
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
```

### Shell Scripts (`.sh`)

Same format as Python (uses `#` comments).

## Smart Header Insertion

The tool intelligently handles special cases:

### Preserves Shebang Lines
```python
#!/usr/bin/env python3
# Copyright (c) CodeMie Inc. (2023-2025)
# ...
```

### Preserves Encoding Declarations (PEP 263)
```python
# -*- coding: utf-8 -*-
# Copyright (c) CodeMie Inc. (2023-2025)
# ...
```

### Combined (Shebang + Encoding)
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) CodeMie Inc. (2023-2025)
# ...
```

## Workflow Integration

### Developer Workflow

1. **Write code** (headers optional during development)
2. **Run `make license-fix`** before committing
3. **Review changes** and stage files


