# EPMCDME-16411 — Local verification

Branch `EPMCDME-16411_skill-subagent-model-whitelist` @ `9f5c57f0c`, local stack (`codemie-dev-codemie-1`, source mounted from the branch, restarted 2026-10-27 17:45 UTC).
Config for the live check: `features:projectModelOverride` enabled (as on codemie.lab), project `demo` with no model whitelist (`allowed_models = None`), `MODELS_ENV=dial`.
Assistant `repro-16411-assistant` (308fb5e3-f291-4d67-9946-548d2c3543a8): no assistant-level subagents, one skill `repro-16411-pptx` with `enabled_builtin_subagents: [GENERAL_PURPOSE_SUBAGENT]`.

## Harness sanity run (run by the developer, 2026-10-27 18:52 local)

Invocation: `uvx codemie-test-harness@0.1.749 --sanity-api` (8 xdist workers, 200 items) against the local backend serving this branch.

```
============ 196 passed, 4 skipped in 194.02s (0:03:14) ============
```

The subagent-related tests passed: `tests/assistant/test_assistant_builtin_subagents.py` (catalog contains General Purpose, display name) and `tests/assistant/test_sub_assistants.py` (chat with sub-assistant and output schema; create/update/remove/nested sub-assistants).

Why `@0.1.749` and not the latest: harness `0.1.750` (published 2026-10-27 14:41 UTC) added `tests/servicenow_budget`, which imports `codemie_sdk.models.servicenow_integration`. It pins `codemie-sdk-python==0.1.704`, the newest SDK on PyPI, which does not contain that module. The run with `0.1.750` gave `196 passed, 4 skipped, 8 errors`: the 8 errors are that one `ModuleNotFoundError` at collection, once per xdist worker. That is a harness packaging issue, unrelated to this change.

Config note: both harness runs were made with the local repro config still active (`features:projectModelOverride` enabled, extra DIAL repro models), not the repo default.

## Live chat calls — `POST /v1/assistants/{id}/model` with an explicit `llm_model`

| `llm_model` | Conversation | Subagent calls | Before the fix |
|---|---|---|---|
| `gpt-4.1` | 0aff751e-a3d0-4950-b9ac-9656cf2ddbf4 | 3 × `Builtin General Purpose Subagent` | 0 (subagent dropped) |
| `repro-claude-sonnet-5-5` (DIAL `claude-sonnet-5-5@default`) | 66d6b3bc-70ba-4688-91c6-09ea132fcfa7 | 3 | 0 (lab conv e3b85c90) |
| `repro-gemini-4-argon` (DIAL `gemini-4-argon`) | b4f380a3-6b8f-4787-9b8e-7c14a0ac8ad5 | 3 | 0 |

Backend log after the restart: no `Failed to create builtin subagent executor` / sub-assistant failure lines.

## UI check (localhost:5173, developer screenshots in `screenshots/`)

Assistant `repro-16411-assistant`, prompt "Create a presentation about AI driven software-development", model chosen under **Configuration → General**:

| Screenshot | LLM Model | Result |
|---|---|---|
| `screenshots/defaultAgent.png` | Default (no `llmModel` sent) | Skill + 2 × Builtin General Purpose Subagent, all SUCCESS |
| `screenshots/gpt5.png` (chat 20222e5f-a068-4537-9e13-4283cb336c6b) | GPT-5 2025-08-07 (explicit) | Skill + 8 × Builtin General Purpose Subagent, all SUCCESS |
| `screenshots/sonnet5.5.png` (chat 3b50c10b-2a92-46c7-8f06-722c98196b28) | REPRO Sonnet 5.5 (DIAL) (explicit) | Skill + 9 × Builtin General Purpose Subagent (#1–#9), all SUCCESS |

Before the fix, an explicit model in the General config produced no subagent steps (lab conversations e3b85c90, 98e64639, 593ca567).

## Failure-log format (AC8), observed live

In chat 20222e5f (17:35 UTC, before the GPT-5 turn shown in the screenshot), a builtin subagent failed to build for an environment reason: a Bedrock model with no AWS region configured locally. The new log line carries the exception type and message:

```
Failed to create builtin subagent executor for GENERAL_PURPOSE_SUBAGENT: NoRegionError You must specify a region…
```

Before the fix the same line ended with an empty message.
