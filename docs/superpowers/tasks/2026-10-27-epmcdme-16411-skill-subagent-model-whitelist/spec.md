# EPMCDME-16411: Skill-enabled subagents dropped when the chat names an llm_model

## Intent

When the `projectModelOverride` customer feature is on, a skill such as `epam-pptx` loses the builtin subagent it enables (`GENERAL_PURPOSE_SUBAGENT`) whenever the chat request carries an explicit `llm_model`. Sub-assistants configured through `assistant_ids` are lost the same way. Users see this as "Sonnet 5.5 / Gemini Argon ignore the skill's subagents". The bug is not model-specific: GPT-4.1 fails too.

Root cause (see technical-analysis.md, section 2): `AssistantFactory.build` (`src/codemie/service/tools/assistant_factory.py:77-95`) runs its own whitelist pre-check against `ModelAvailabilityService.get_project_models`, and that call collapses an unconfigured whitelist (`None`) into `[]`. Every model is rejected as a result. The parent path, `enforce_allowed_model` (`src/codemie/core/dependecies.py:231-269`), treats `None` as "no restriction". The failure is swallowed, and the log line is empty because `ExtendedHTTPException` has an empty `str()`.

## Decided approach (user decisions)

1. **Delete the pre-check** in `AssistantFactory.build`. The child call `AssistantService.build_agent(is_subagent=True)` (`src/codemie/service/assistant_service.py:512+`) already runs `enforce_allowed_model` against the sub-assistant's own project. That call becomes the single policy for subagents and sub-assistants:
   - unconfigured whitelist (`None`): unrestricted
   - `[]`: deny-all, raises `ModelNotWhitelistedException`
   - populated whitelist: falls back via `pick_allowed_model`
   - flag off: no check
2. **Rewrite the `AssistantFactory.build` docstring** so it no longer claims "hard block ... No fallback". It should say that model policy is enforced by `build_agent`/`enforce_allowed_model`.
3. **Global assistants** get the parent's exemption with no new code: `build_agent` already skips project enforcement for `assistant.is_global`, and this applies to the sub-assistant itself. The exemption must be covered by a test.
4. **Cross-project sub-assistant** whose populated whitelist excludes the requested model: it falls back to an allowed model and is no longer dropped.
5. **Informative failure logs** at the swallow sites: `LangGraphAssistantBuilder.create_subagent_executors` (`src/codemie/service/assistant/assistant_engine_builder.py:238-247`), `create_assistant_executors` (`assistant_factory.py:192-193`) and the re-raise log in `AssistantFactory.build` (`assistant_factory.py:115-117`), if that one is still present. Each must include the exception type and, when present, its `code`/`message` attributes. The formatting happens locally, at the log sites or in one small shared helper, and never by changing `ExtendedHTTPException`.

## Acceptance criteria

- AC1: With the flag on, an explicit `llm_model` and project `allowed_models=None`, `AssistantFactory.build` delegates to `build_agent` without raising. The builtin subagent enabled by the skill is built and appears among the supervisor handoff tools.
- AC2: The same holds for sub-assistants built via `create_assistant_executors`.
- AC3: `AssistantFactory.build` no longer reads `get_project_models` or the feature flag. Its docstring describes delegation to `enforce_allowed_model`.
- AC4: For a configured `[]` whitelist, the sub-assistant build still fails with `ModelNotWhitelistedException`, raised from `build_agent`, and the subagent is skipped with an informative log.
- AC5: With a populated whitelist that excludes the model, the sub-assistant is built on the fallback model chosen by `pick_allowed_model` (see AC10 for the order).
- AC6: A global sub-assistant is built without project enforcement, whatever its project's whitelist.
- AC7: With the flag off, behaviour is unchanged: the chat-picked model applies to every child, as before.
- AC8: The failure logs in `create_subagent_executors` and `create_assistant_executors` contain the exception class name and its code/message. A test asserts the text is not empty for a `ModelNotWhitelistedException`.
- AC9: Unit tests cover AC1-AC8 and AC10, in `tests/codemie/service/tools/test_assistant_factory.py`, `tests/codemie/core/test_dependecies.py` plus a builder test for the log content. The existing tests in those files and in `tests/codemie/service/llm/test_model_availability_service.py` pass unchanged.
- AC10 (added after review): every child, builtin subagent or `assistant_ids` sub-assistant, picks its model by the same rule. With a populated whitelist of the child's project: the chat-picked model if allowed, otherwise the child's own `llm_model_type` if allowed, otherwise the project default, otherwise the first allowed model. `pick_allowed_model` and `enforce_allowed_model` take the own model as an optional `assistant_model` argument, which `build_agent` passes; callers that omit it keep the previous order. Flag off, an unconfigured (`None`) whitelist, `[]` and global assistants keep their handling.
- AC11 (added after review): `describe_exception` also logs `details`, which carries the operator-facing reason of `ExtendedHTTPException` subclasses such as `ModelNotWhitelistedException`.

## Non-goals

- No change to `ExtendedHTTPException` or its `__str__`/`__init__`.
- No change to `resolve_requested_model`, `get_project_models` or `validate_model_for_asset_creation` semantics. `enforce_allowed_model` and `pick_allowed_model` change only through the optional `assistant_model` step in AC10.
- No change to supervisor subagent descriptions (`get_subagent_descriptions_with_contributions`).
- No UI, REST API contract or DB schema change.
- No model-specific handling for Sonnet 5.5, Gemini Argon or any other model.
- No change to skill or builtin-subagent registry configuration (`config/subagents/subagents.yaml`).

## Known risks

- `get_subagent_descriptions_with_contributions` (`assistant_engine_builder.py:252-277`) may still describe a builtin whose executor failed to build, for example under a `[]` whitelist. The supervisor could then name a subagent it cannot hand off to. This was observed but not verified at runtime, and is out of scope by user decision.
- Removing the hard block relaxes behaviour for cross-project sub-assistants: they now fall back instead of being dropped. This is intentional and matches the parent policy.

## Left to the builder

Whether the log formatting lives inline or in a small helper, and the exact log wording, within AC8. Where the global-assistant and `[]`/populated tests sit (factory tests with `build_agent` exercised, or `build_agent`-level tests), as long as they cover AC4-AC6 for the subagent path.
