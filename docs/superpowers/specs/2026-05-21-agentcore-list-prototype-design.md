# Bedrock AgentCore List Prototype — Design

**Date:** 2026-05-21  
**Branch:** EPMCDME-12240_agentcore  
**Status:** Approved

---

## Overview

A thin `AgentcoreListService` in a new `service/aws_agentcore/` module that lists Bedrock AgentCore runtimes via the `bedrock-agentcore-control` boto3 client, plus a standalone script that invokes it using a predefined integration name.

---

## Architecture

```
src/codemie/service/aws_agentcore/
├── __init__.py
├── agentcore_api.py                ← raw boto3 call: list_agent_runtimes(...)
└── agentcore_list_service.py       ← AgentcoreListService.run(...): credential lookup + calls agentcore_api

scripts/
└── list_agentcore_runtimes.py      ← standalone invocation script

pyproject.toml                      ← boto3 bumped to ^1.43.12
```

---

## Components

### `agentcore_api.py`

Owns the raw boto3 interaction. Single function:

```python
def list_agent_runtimes(
    region: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None = None,
    page: int = 0,
    per_page: int = 10,
    next_token: str | None = None,
) -> tuple[list[dict], str | None]:
```

Delegates to `call_bedrock_listing_api` from `aws_bedrock/utils.py` with `service_name="bedrock-agentcore-control"`, `api_method_name="list_agent_runtimes"`, `response_key="agentRuntimes"`. Returns `(runtimes, next_token)`.

---

### `AgentcoreListService`

Single static method:

```python
@staticmethod
def run(
    integration_name: str,
    page: int = 0,
    per_page: int = 10,
    next_token: str | None = None,
) -> tuple[list[dict], str | None]:
```

**Credential lookup flow:**
1. Call `Settings.get_all(credential_type=CredentialTypes.AWS)` to retrieve all AWS settings.
2. Find the first entry whose `alias == integration_name`. Raise `NotFoundException` if none matches.
3. Call `get_setting_aws_credentials(str(setting.id))` to obtain `AWSCredentials(access_key_id, secret_access_key, region)`.

**AWS call:**  
Calls `agentcore_api.list_agent_runtimes(...)` from within the same module.

Returns `(runtimes: list[dict], next_token: str | None)`.

---

### `scripts/list_agentcore_runtimes.py`

Standalone runnable script. Structure:

```python
INTEGRATION_NAME = "my-aws-integration"   # predefined constant

def main():
    runtimes, next_token = AgentcoreListService.run(INTEGRATION_NAME)
    # pretty-print results; print next_token if present
    # exit(1) on exception
```

Requires the app's virtualenv to be active (uses the same settings/DB layer as the service).

---

## Dependency Update

`pyproject.toml`: bump `boto3 = "^1.34.147"` → `boto3 = "^1.43.12"`.

---

## Error Handling

| Condition | Behaviour |
|---|---|
| Integration name not found | Raise `NotFoundException(integration_name)` |
| AWS `ClientError` | Propagate; script catches and prints + exits 1 |
| Empty result | Return `([], None)` — not an error |

---

## Out of Scope

- No HTTP endpoint / router wiring.
- No tests (prototype only).
- No pagination loop in the script — single page call.
