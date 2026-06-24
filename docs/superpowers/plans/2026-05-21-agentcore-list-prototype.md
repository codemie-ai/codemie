# AgentCore List Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `bedrock-agentcore-control` list-runtimes service under `service/aws_agentcore/` and a runnable prototype script that invokes it using a named AWS integration.

**Architecture:** A new `aws_agentcore/` module holds the raw boto3 call (`agentcore_api.py`) and the credential-lookup service (`agentcore_list_service.py`). A standalone script in `scripts/` wires them together with a hardcoded integration name. Both reuse existing `aws_bedrock` utilities for the boto3 client and settings lookups.

**Tech Stack:** Python 3.12, boto3 ≥1.43.12, `bedrock-agentcore-control` service, existing `codemie_tools`, SQLModel/Elasticsearch settings layer.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/codemie/service/aws_agentcore/__init__.py` | Package marker |
| Create | `src/codemie/service/aws_agentcore/agentcore_api.py` | Raw boto3 call: `list_agent_runtimes` |
| Create | `src/codemie/service/aws_agentcore/agentcore_list_service.py` | Credential lookup + dispatch to `agentcore_api` |
| Create | `scripts/list_agentcore_runtimes.py` | Runnable prototype script |
| Modify | `pyproject.toml` | Bump `boto3` to `^1.43.12` |

---

## Task 1: Bump boto3 dependency

**Test-first:** no — dependency update, no test needed

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update boto3 version**

In `pyproject.toml`, find and change:
```toml
boto3 = "^1.34.147"
```
to:
```toml
boto3 = "^1.43.12"
```

- [ ] **Step 2: Install updated dependency**

```bash
source .venv/bin/activate
poetry lock --no-update
poetry install
```

Expected: lock file updated, `boto3 1.43.x` installed with no errors.

- [ ] **Step 3: Verify boto3 version**

```bash
python -c "import boto3; print(boto3.__version__)"
```

Expected: version `1.43.x` or higher printed.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "chore: bump boto3 to ^1.43.12 for bedrock-agentcore support"
```

---

## Task 2: Create `agentcore_api.py` — raw boto3 call

**Test-first:** no — prototype, no tests required

**Files:**
- Create: `src/codemie/service/aws_agentcore/__init__.py`
- Create: `src/codemie/service/aws_agentcore/agentcore_api.py`

- [ ] **Step 1: Create the package `__init__.py`**

Create `src/codemie/service/aws_agentcore/__init__.py` with contents:

```python
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
```

- [ ] **Step 2: Create `agentcore_api.py`**

Create `src/codemie/service/aws_agentcore/agentcore_api.py`:

```python
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

from __future__ import annotations

from typing import Optional

from codemie.service.aws_bedrock.utils import call_bedrock_listing_api

_SERVICE_NAME = "bedrock-agentcore-control"


def list_agent_runtimes(
    region: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: Optional[str] = None,
    page: int = 0,
    per_page: int = 10,
    next_token: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    return call_bedrock_listing_api(
        service_name=_SERVICE_NAME,
        api_method_name="list_agent_runtimes",
        response_key="agentRuntimes",
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        page=page,
        per_page=per_page,
        next_token=next_token,
    )
```

- [ ] **Step 3: Verify the module imports cleanly**

```bash
source .venv/bin/activate
cd src
python -c "from codemie.service.aws_agentcore.agentcore_api import list_agent_runtimes; print('OK')"
```

Expected: `OK` printed with no import errors.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/service/aws_agentcore/
git commit -m "feat: add aws_agentcore module with list_agent_runtimes api wrapper"
```

---

## Task 3: Create `agentcore_list_service.py` — credential lookup + dispatch

**Test-first:** no — prototype, no tests required

**Files:**
- Create: `src/codemie/service/aws_agentcore/agentcore_list_service.py`

- [ ] **Step 1: Create `agentcore_list_service.py`**

Create `src/codemie/service/aws_agentcore/agentcore_list_service.py`:

```python
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

from __future__ import annotations

from typing import Optional

from codemie_tools.base.models import CredentialTypes

from codemie.rest_api.models.settings import Settings
from codemie.service.aws_bedrock.utils import get_setting_aws_credentials
from codemie.service.aws_agentcore import agentcore_api


class AgentcoreListService:

    @staticmethod
    def run(
        integration_name: str,
        page: int = 0,
        per_page: int = 10,
        next_token: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        all_aws_settings = Settings.get_all(credential_type=CredentialTypes.AWS)
        setting = next(
            (s for s in all_aws_settings if s.alias == integration_name),
            None,
        )
        if setting is None:
            raise ValueError(f"AWS integration '{integration_name}' not found")

        aws_creds = get_setting_aws_credentials(str(setting.id))

        return agentcore_api.list_agent_runtimes(
            region=aws_creds.region,
            access_key_id=aws_creds.access_key_id,
            secret_access_key=aws_creds.secret_access_key,
            page=page,
            per_page=per_page,
            next_token=next_token,
        )
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
source .venv/bin/activate
cd src
python -c "from codemie.service.aws_agentcore.agentcore_list_service import AgentcoreListService; print('OK')"
```

Expected: `OK` printed with no import errors.

- [ ] **Step 3: Commit**

```bash
git add src/codemie/service/aws_agentcore/agentcore_list_service.py
git commit -m "feat: add AgentcoreListService.run with credential lookup by integration name"
```

---

## Task 4: Create the prototype script

**Test-first:** no — prototype script, no tests required

**Files:**
- Create: `scripts/list_agentcore_runtimes.py`

- [ ] **Step 1: Create `scripts/list_agentcore_runtimes.py`**

Create `scripts/list_agentcore_runtimes.py`:

```python
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

"""Prototype script: list Bedrock AgentCore runtimes via a named AWS integration."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from codemie.service.aws_agentcore.agentcore_list_service import AgentcoreListService

INTEGRATION_NAME = "my-aws-integration"


def main() -> None:
    try:
        runtimes, next_token = AgentcoreListService.run(INTEGRATION_NAME)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(runtimes, indent=2, default=str))

    if next_token:
        print(f"\nnext_token: {next_token}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Set `INTEGRATION_NAME` to match a real integration alias in your environment**

Open `scripts/list_agentcore_runtimes.py` and change:
```python
INTEGRATION_NAME = "my-aws-integration"
```
to the actual alias of your AWS integration as it appears in the Codemie settings.

- [ ] **Step 3: Run the script**

```bash
source .venv/bin/activate
python scripts/list_agentcore_runtimes.py
```

Expected: JSON array of AgentCore runtime objects printed to stdout, e.g.:
```json
[
  {
    "agentRuntimeId": "abc123",
    "agentRuntimeName": "my-runtime",
    "status": "READY",
    ...
  }
]
```
If no runtimes exist in the account/region, an empty array `[]` is printed — that is not an error.

- [ ] **Step 4: Commit**

```bash
git add scripts/list_agentcore_runtimes.py
git commit -m "feat: add list_agentcore_runtimes prototype script"
```
