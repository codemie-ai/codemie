# Handoff: AgentCore List Prototype

**Branch:** `EPMCDME-12240_agentcore`  
**Date:** 2026-05-21  
**Status:** Complete

---

## What Was Built

A prototype for listing AWS Bedrock AgentCore runtimes using a named Codemie AWS integration.

### New Files

| File | Purpose |
|---|---|
| `src/codemie/service/aws_agentcore/__init__.py` | Package marker |
| `src/codemie/service/aws_agentcore/agentcore_api.py` | Raw boto3 call — `list_agent_runtimes()` via `bedrock-agentcore-control` |
| `src/codemie/service/aws_agentcore/agentcore_list_service.py` | `AgentcoreListService.run(integration_name)` — looks up AWS credentials by integration alias, delegates to `agentcore_api` |
| `scripts/list_agentcore_runtimes.py` | Standalone runnable script |

### Modified Files

| File | Change |
|---|---|
| `pyproject.toml` | `boto3` bumped from `^1.34.147` → `^1.43.12` |
| `poetry.lock` | Updated to reflect new boto3 |

---

## How to Run

1. Set `INTEGRATION_NAME` in `scripts/list_agentcore_runtimes.py` to the alias of your AWS integration in Codemie settings.
2. Run:

```bash
poetry run python scripts/list_agentcore_runtimes.py
```

Expected output — JSON array of AgentCore runtimes:

```json
[
  {
    "agentRuntimeId": "abc123",
    "agentRuntimeName": "my-runtime",
    "status": "READY"
  }
]
```

Empty array `[]` means no runtimes in the account/region — not an error.

---

## Architecture

```
scripts/list_agentcore_runtimes.py
  └─ AgentcoreListService.run(integration_name)
        ├─ Settings.get_all(credential_type=AWS) → find by alias
        ├─ get_setting_aws_credentials(setting.id) → AWSCredentials
        └─ agentcore_api.list_agent_runtimes(region, key, secret)
              └─ call_bedrock_listing_api("bedrock-agentcore-control", "list_agent_runtimes", ...)
```

---

## Notes

- `AgentcoreListService` raises `ValueError` if the integration name is not found in Codemie settings.
- `session_token` is optional — omit for long-term IAM keys, required for STS/SSO temporary credentials.
- No HTTP endpoint wired — prototype only.
