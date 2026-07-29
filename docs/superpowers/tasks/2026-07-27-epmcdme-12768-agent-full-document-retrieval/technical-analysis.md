# Technical Research

**Task**: conversation history replay tool output truncation agent
**Generated**: 2026-07-27
**Research path**: filesystem (codegraph MCP not available in this session)

---

## 1. Original Context

EPMCDME-12768, расширенный объём — комплексный дефект «агент отдаёт только начало документа, продолжение недоступно».

ЗВЕНО 1 (уже исправлено, НЕ предмет исследования): base_datasource_processor._split_documents терял chunk_num, rrf._filter_duplicates схлопывал все чанки файла в один. Исправлено, проверено на живых данных.

ЗВЕНО 2 (предмет этого исследования): симптом воспроизводится и после фикса звена 1, если вопрос задан НЕ первым сообщением диалога. Подтверждено по коду и по логам продакшн-подобного прогона:
- src/codemie/agents/callbacks/callback_utils.py:68-69 — preserve_full_output=True выставляется только при `normalized_name == SKILL_TOOL_NAME` (жёсткий if).
- src/codemie/agents/callbacks/callback_utils.py:73-82 — _summarize_tool_output: лимит AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT только для skill, иначе AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT (600), с добавлением "...[truncated]".
- src/codemie/service/conversation/history_projection_service.py:480-510 (_resolve_tool_result или аналогичный метод) — при повторе истории: preserve_full_output → полный текст; иначе use_full_results → _truncate_text(result_text, 2500); use_summaries → _truncate_text(result_summary, 600).
- src/codemie/service/conversation/history_projection_service.py:174-192 — сборка ToolReplayRecord, где result_text = полный текст мысли, result_summary = 600-символьная сводка, preserve_full_output = metadata.get(...) or tool_name == SKILL_TOOL_NAME.
- src/codemie/service/conversation/history_projection_service.py:80-114 — окна: AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS (4) последних tool-хода идут «полными», AI_AGENT_HISTORY_REPLAY_SUMMARIZED_TOOL_TURNS (6) — сводками.

Эмпирика: вызов search_kb_sharepoint вернул 3207 токенов (~15000 символов, весь документ). На следующем ходу история спроецирована с обрезкой до 2500 символов, хвост документа потерян, агент отвечает «продолжения нет».

УТВЕРЖДЁННОЕ РЕШЕНИЕ, которое надо обосновать и уточнить исследованием: заменить хардкод `== SKILL_TOOL_NAME` на конфигурируемый РЕЕСТР инструментов, чей вывод сохраняется целиком. Дефолт — skill + семейство search_kb* (рантайм-имена вида search_kb_<datasource>; base_name="search_kb" в src/codemie/agents/tools/kb/search_kb.py:84; SKILL_TOOL_NAME="skill" в src/codemie/service/constants.py:50).

ЧТО НУЖНО ИССЛЕДОВАТЬ И ЗАДОКУМЕНТИРОВАТЬ:
1. Полная карта пути: где формируются метаданные tool-мысли при вызове инструмента, где они persist-ятся, где читаются при повторе истории. Все места, где участвует preserve_full_output, result_summary, result_text.
2. Все существующие потребители/проверки SKILL_TOOL_NAME по всему репозиторию — чтобы реестр не сломал skill-инструмент.
3. Как именно нормализуются имена инструментов (регистр, пробелы, MAX_TOOL_NAME_LENGTH, усечение) — критично для матчинга префикса search_kb.
4. Есть ли уже в проекте конвенция для конфигурируемых списков/реестров в config.py (примеры: DYNAMIC_WEB_SEARCH_TOOLS, DYNAMIC_CODE_INTERPRETER_TOOLS, HTTP_BLOCKED_TOOLS) — как они объявлены, как читаются, есть ли хелперы матчинга.
5. Риски по контексту: где ограничивается размер вывода одного инструмента (TOOL_TOKENS_SIZE_LIMIT=30000, MCP_TOOL_TOKENS_SIZE_LIMIT), как работает компактор истории (AI_AGENT_HISTORY_COMPACTION_*, сейчас выключен), что произойдёт при 4 полных выводах поиска в контексте.
6. Существующее тестовое покрытие: tests/codemie/service/conversation/test_history_projection_service.py (577 строк, хелпер _build_conversation_with_tool_turn) и тесты callback_utils, если есть. Какие паттерны используются, как строится Conversation/Thought/GeneratedMessage в тестах.
7. Риск-индикаторы: не сломает ли сохранение полного вывода что-то ещё — например дедупликацию tool-записей (_deduplicate_tool_records), лимиты на сообщения, сохранение истории в БД.

feature_area: conversation history replay tool output truncation agent

run_dir: /Users/evgeniikvasiuk/Projects/codemie/codemie/docs/superpowers/tasks/2026-07-27-epmcdme-12768-agent-full-document-retrieval/

Repository root: /Users/evgeniikvasiuk/Projects/codemie/codemie (Python, poetry, pytest). Write technical-analysis.md into run_dir.

---

## 2. Codebase Findings

### 2.1 Full path map (research question 1)

**Write side — metadata creation during tool invocation**

| Step | Location | What happens |
|---|---|---|
| Tool start (streaming) | `src/codemie/agents/callbacks/agent_streaming_callback.py` (~line 66) | Creates the `Thought`; **does `tool_name.replace('_',' ').title()` before** calling `_build_tool_metadata`. Metadata is built only `if input_text` is non-empty. |
| Tool start (non-streaming) | `src/codemie/agents/callbacks/agent_invoke_callback.py:158` | Same hooks, but passes the **raw** tool name (no title-casing). |
| Metadata builder | `callback_utils.py:54` `_build_tool_metadata` | `normalized_name = tool_name.replace(' ', '_').lower()` (line 58); `metadata["preserve_full_output"] = True` **only** when `normalized_name == SKILL_TOOL_NAME` (lines 68-69). Returns `{}` entirely when replay-v2 is disabled (`_is_conversation_replay_v2_enabled`, line 47). |
| Summary builder | `callback_utils.py:73` `_summarize_tool_output` | Limit = `AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT` (2500) if `tool_name == SKILL_TOOL_NAME` (line 76), else `..._SUMMARY_TOOL_RESULT_LIMIT` (600); appends `"\n...[truncated]"`. |
| Tool end / error | `callback_utils.py:106` `_update_tool_replay_metadata` | Sets `status` and `result_summary` (line 112). Reads `metadata.get("tool_name","").lower()` — lowercases but **does not** replace spaces (asymmetric with `_build_tool_metadata`). |

**Persistence**

- `Thought.metadata: Optional[dict]` — `src/codemie/chains/base.py`. Free-form JSON.
- `GeneratedMessage.thoughts` → `Conversation.history` — `src/codemie/rest_api/models/conversation.py`, a Postgres `PydanticListType(GeneratedMessage)` JSON column. Active-record style (`Conversation.find_by_id`, `update_chat_history` at `:378`); **no separate repository/DAO**.
- Written via `src/codemie/service/conversation_service.py:187` (`ChatTurnData.thoughts`), orchestrated by `AssistantRequestHandler._filter_thoughts` (`assistant_handlers.py:408`) which keeps `metadata` only when replay-v2 is on.
- **No DB migration is required** for a new metadata flag — it is a key inside an existing JSON blob.

**Read side — history replay**

| Step | Location | What happens |
|---|---|---|
| Entry point | `history_projection_service.py:71` `build_for_request` | Called from `assistant_handlers.py`; window defaults read from config at `:80-83`. |
| Record extraction | `:158` `_extract_tool_records` | `result_text` ← `thought.message` (sanitized, `:174`); `result_summary` ← `metadata["result_summary"]` else derived via `_summarize_tool_result` (`:175-176`); **`preserve_full_output = bool(metadata.get("preserve_full_output")) or tool_name == SKILL_TOOL_NAME` (`:178`)**. |
| Dedup | `:197` `_deduplicate_tool_records` | Dedupes by `call_id`, last wins. Independent of content length. |
| Window resolution | `:234` `_resolve_tool_windows` | Full window = last N tool turns; summarized window = the N preceding. |
| Pinning | `:385` `_has_pinned_tool_records` | `preserve_full_output` **or** error/interrupted status forces the turn to replay **even outside both windows**. |
| Truncation decision tree | `:475` `_render_tool_result_content` | `preserve_full_output` → `record.result_text` **uncapped** (`:481-482`); error/interrupted → 600; `use_full_results` → `_truncate_text(result_text, 2500)`; else → `_truncate_text(result_summary, 600)`. |
| Fallback summarizer | `:568` `_summarize_tool_result` | Third `SKILL_TOOL_NAME` check (`:574`) choosing 2500 vs 600 when metadata carries no `result_summary`. |
| Truncator | `:584` `_truncate_text` | Character-based; appends `"\n...[truncated]"`. |

**All identifier hits**

- `preserve_full_output`: `callback_utils.py:69` (only write site); `history_projection_service.py:55` (dataclass field, default `False`), `:178` (re-derived on read), `:189`, `:387`, `:481`. **Zero hits in `tests/`.**
- `result_text`: `history_projection_service.py:53, 174, 187, 402, 482, 491, 497, 503, 508, 568-577`. (Unrelated same-named locals in `codemie_tools/data_management/*`.)
- `result_summary`: `callback_utils.py:112` (only write site); `history_projection_service.py:54, 175-176, 188, 402, 482, 491, 497, 503, 508`; `tests/.../test_history_projection_service.py:54, 215, 229, 280, 295, 346, 361, 412` (fixtures only, all non-skill tools).

**Key architectural insight**: the read-side re-derivation at `:178` means a registry change *there* retroactively repairs already-stored conversations, while the write-side change at `callback_utils.py:68` only affects new turns. Both are needed — the write side also governs how `result_summary` is truncated at ingest (`callback_utils.py:76`), and a 600-char summary written today is unrecoverable later.

### 2.2 All SKILL_TOOL_NAME consumers (research question 2)

`SKILL_TOOL_NAME = "skill"` at `src/codemie/service/constants.py:50` (mirrors `SkillTool.name` at `src/codemie/agents/tools/skill/skill_tool.py:123`). Exactly **6 usages + 3 imports**:

| Location | Role | Registry-relevant? |
|---|---|---|
| `service/constants.py:50` | Definition | — |
| `callback_utils.py:23` | import | — |
| `callback_utils.py:68` | sets `preserve_full_output=True` (LINK 2 root cause) | **Yes — replace** |
| `callback_utils.py:76` | 2500 vs 600 limit in `_summarize_tool_output` | **Yes — replace** |
| `history_projection_service.py:31` | import | — |
| `history_projection_service.py:178` | re-derives `preserve_full_output` for legacy/missing metadata | **Yes — replace** |
| `history_projection_service.py:574` | 2500 vs 600 in fallback `_summarize_tool_result` | **Yes — replace** |
| `assistant_handlers.py:61` | import | — |
| `assistant_handlers.py:337` | adds `"skill"` to `available_tool_names` (`_get_available_replay_tool_names`, `:334`; see `_has_skill_tool` `:324`) | **No — different concern** (native-tool replay vs text-ledger downgrade, not truncation). Leave alone. |

So the registry must cover 4 decision points; the 5th skill check is orthogonal and must **not** be folded in. Skill behavior is preserved as long as `"skill"` remains the default first entry of the registry.

### 2.3 Tool-name normalization (research question 3)

Four different normalizers exist. This asymmetry is the primary correctness trap for prefix matching.

1. **Write side** — `callback_utils.py:58`: `tool_name.replace(' ', '_').lower()`. No invalid-char stripping, no underscore collapsing, no length cap.
2. **Write side (tool end)** — `callback_utils.py:110`: `metadata.get("tool_name","").lower()` only — **does not** replace spaces.
3. **Read side** — `ConversationHistoryProjectionService._normalize_tool_name` (`history_projection_service.py:523-537`), exact rules:
   - falsy → `"unknown_tool"`
   - `.strip().lower()`
   - `re.sub(r'[^a-z0-9_\-]', '_', …)` — hyphens survive, dots do not
   - `re.sub(r'_+', '_', …)` collapse runs
   - `.strip('_')`
   - if `len >= MAX_TOOL_NAME_LENGTH` (64, `src/codemie/core/constants.py:75`) → hard slice `[:64]`, no hash suffix
   - empty result → `"unknown_tool"`
4. **Runtime-name construction** — `src/codemie/agents/utils.py`: `sanitize_tool_name` (`:256`) lower + `[^a-z0-9_-]`→`_`, and if >64 chars truncate to `64-len(hash)-1`, rstrip `_`, append `_<sha256 mod 1e8>`; `sanitize_datasource_name` (`:273`) same regex + `strip("_")`, no cap; `adapt_tool_name` (`:281`) = `template.format(sanitize_datasource_name(alias))`, re-formatting with `generate_tool_hash(alias)` (`:289`) if the result exceeds 64.

**Streaming round-trip**: `search_kb_sharepoint` → title-cased to `Search Kb Sharepoint` → re-normalized back to `search_kb_sharepoint`. Round-trips safely for `_`, `-` and lowercase input. The invoke path skips title-casing entirely.

**Empty-input fallback**: when `input_text` is empty no metadata is created at all; `_extract_tool_records` then falls back to `_normalize_tool_name(thought.author_name)` — display name `"Search Kb Sharepoint"` → `search_kb_sharepoint`. This is why the registry match must be applied to the **normalized** form on both sides.

### 2.4 search_kb naming (research question 3, cont.)

- `SearchKBTool.base_name = "search_kb"` (`src/codemie/agents/tools/kb/search_kb.py:84`); `name_template = base_name + "_{}"` (`:85`); default `name = "search_kb_default"` (`:96`).
- Runtime name assigned in `__init__` at `:104`: `self.name = adapt_tool_name(self.name_template, index_info.repo_name)` → `search_kb_<sanitized repo_name>`, hyphens preserved (`search_kb_my-index`), and `search_kb_<8-digit-hash>` when >64 chars.
- Catalog name is distinct: `ToolMetadata(name="search_kb", …)` = `SEARCH_KB_TOOL` at `search_kb.py:42-48`, exported via `agents/tools/kb/kb_toolkit.py:19`. This is what appears in `assistant.toolkits[].tools[].name`.
- **The hash fallback means an exact-name registry cannot work.** Prefix (`startswith("search_kb")`) or glob/regex entries are mandatory.
- Existing precedent for the same family match: `src/codemie/service/stale_datasource/stale_datasource_service.py:70` `_KB_TOOL_PREFIX = "search_kb"` plus `_CODE_TOOL_PREFIXES`, consumed in `_compute_base_tool_names` (`:84-100`), which also handles the legacy pre-EPMCDME-11979 naming variant.

### 2.5 Architecture and layers affected

- **Config** — `src/codemie/configs/config.py` (`Config(BaseSettings)` singleton) and/or `src/codemie/service/constants.py` (where `SKILL_TOOL_NAME` lives). `DynamicConfigService` (`src/codemie/service/dynamic_config_service.py`) supports **scalar types only** (STRING/BOOL/INT/FLOAT) — a runtime-overridable list must be a delimited/JSON string parsed by hand, as done for `MCP_AUTH_TRUSTED_AS_DOMAINS` in `src/codemie/enterprise/mcp_auth/_trust_policy.py:75-118`.
- **Callback (write)** — `agents/callbacks/callback_utils.py`, driven by `agent_streaming_callback.py` and `agent_invoke_callback.py`.
- **Service (read)** — `service/conversation/history_projection_service.py`; siblings `history_compaction_service.py`, `history_materializer.py`.
- **Handler/API** — `rest_api/handlers/assistant_handlers.py` (persist + replay orchestration).
- **Persistence** — `rest_api/models/conversation.py` (JSON column, active-record).
- **Tool** — `agents/tools/kb/search_kb.py`, `agents/tools/skill/skill_tool.py`, `agents/utils.py`.

### 2.6 Integration points

- `agent_streaming_callback.py` / `agent_invoke_callback.py` → `callback_utils.py` → `service/constants.py` + `configs/config.py` + `service/dynamic_config_service.py`
- `agents/callbacks/*` → `chains/base.Thought` → `rest_api/models/conversation.GeneratedMessage.thoughts` → `Conversation.history` (Postgres JSON)
- `assistant_handlers.py` → `service/conversation_service.upsert_chat_history` → `Conversation.update_chat_history`
- `assistant_handlers.py` → `ConversationHistoryProjectionService.build_for_request` → `configs/config` + `service/constants` + `core/constants.MAX_TOOL_NAME_LENGTH`
- `SearchKBTool` → `agents/utils.adapt_tool_name` → `sanitize_datasource_name` / `generate_tool_hash`
- `service/stale_datasource/stale_datasource_service.py` → `agents/utils.adapt_tool_name` — an independent consumer of the same `search_kb_*` naming; keep in sync if the prefix is centralized.

### 2.7 Existing registry convention (research question 4)

All existing tool-name registries are plain `list[str]` fields on `Config` with literal Python-list defaults. **pydantic-settings parses complex types from env as JSON** (`HTTP_BLOCKED_TOOLS='["a","b"]'`) — there is **no** comma-split for `list[str]` fields and **no shared parser helper**.

| Registry | Declaration | Default | Consumer | Matching |
|---|---|---|---|---|
| `DYNAMIC_WEB_SEARCH_TOOLS` | `config.py:563` | `["google_search_tool_json","tavily_search_results_json","web_scrapper"]` | `service/tools/toolkit_service.py:247` via local `has_any_tool` (`:236-238`) | **exact**, no normalization |
| `DYNAMIC_CODE_INTERPRETER_TOOLS` | `config.py:569` | `["code_executor"]` | `toolkit_service.py:272`, same helper | **exact** |
| `HTTP_BLOCKED_TOOLS` | `config.py:574` | `["code_executor"]` | `rest_api/routers/tool.py:111` → 403 | **exact `in`**, raw path param |

Closest **prefix/partial-match** precedents:

- `DISABLE_PARALLEL_TOOLS_CALLING_MODELS` (`config.py:577`) — `any(model in self.llm_model for model in …)` at `agents/langgraph_agent.py:267` and `agents/supervisor/bootstrap.py:31` (substring).
- `LITELLM_PREMIUM_MODELS_ALIASES` (`config.py:649`, default `[]`) — case-insensitive partial match, `@lru_cache`d, `enterprise/litellm/dependencies.py:509-521`.
- `_KB_TOOL_PREFIX` (`stale_datasource_service.py:60-70`) — module-level, non-config prefix registry.

Other `list[str]` settings confirming the idiom: `KATAS_ALLOWED_EXTENSIONS` (124), `AUTHORIZED_APPS_ALLOWED_KEY_DOMAINS` (132), `INDEXES_PERMITTED_FOR_SEARCH` (177), `EXTERNAL_USER_ALLOWED_PROJECTS` (305), `*_IDENTIFIERS` (397-400), `LANGFUSE_BLOCKED_INSTRUMENTATION_SCOPES` (504), `CONVERSATION_ANALYSIS_PROJECTS_FILTER` (721), `LITE_LLM_PROXY_ENDPOINTS` (611, `list[dict]`).

Comma-separated style exists **only** for `str`-typed settings, split ad hoc at the call site with no shared helper: `FORWARDED_HEADERS_BLOCKLIST` (`config.py:496`) → `{h.strip().lower() for h in ….split(",")}` at `service/provider/provider_header_context.py:47` and `rest_api/utils/request_utils.py:33`; also `LITE_LLM_PROJECTS_TO_TAGS_LIST`, `OTEL_EXCLUDED_URLS` (531).

**Verdict**: `list[str]` on `Config` with a literal default matches the three existing tool registries. No shared matching helper exists — a prefix-matching helper would be new code and should live next to the registry (single source of truth for both call sites).

### 2.8 Patterns and conventions the implementation must follow

- Feature-flag shape: `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` static default (`config.py:662`) + `DynamicConfigService.get_bool_value_safe` override, key constant at `service/constants.py:47`. Consumed at `callback_utils.py:47`, `assistant_handlers.py:450`, `history_compaction_service.py:36`, `langgraph_agent.py:127`, `assistant_agent.py:147`.
- Metadata is written once at tool start and mutated in place at tool end; the projection service re-derives on read.
- `_summarize_tool_output` (`callback_utils.py:73`) and `_summarize_tool_result` (`history_projection_service.py:568`) are **duplicated logic with the same skill-only branch** — both need the registry, otherwise the metadata-less/legacy path still truncates to 600.
- `preserve_full_output` currently bypasses truncation **entirely** (`:481-482`, no cap at all).
- Config imports are always `from codemie.configs import config`, read inline at call time.
- Typing per guides: `from __future__ import annotations`, `X | None`, parameterized generics (`list[str]`, `frozenset[str]`), Apache-2.0 header on new files.

---

## 3. Documentation Findings

### 3.1 Guides and architecture docs

`.ai-run/guides/` is present and complete. Rules that bind this change:

- `AGENTS.md` — load the P0 guide for the category before code search; **only write/run tests when explicitly asked**; **only do git ops when explicitly asked**; report validation commands exactly as run; project-specific exact values must come from a guide, not inferred.
- `.ai-run/guides/development/configuration-patterns.md` — **most directly constraining**: runtime settings belong in `src/codemie/configs/`; do not read env vars directly in feature code; gate optional behavior at assembly points or service boundaries, not scattered across modules.
- `.ai-run/guides/architecture/layered-architecture.md` — "Shared Core": cross-cutting constants and config go in core/config modules, not feature packages. Argues for placing the registry in `src/codemie/service/constants.py` (where `SKILL_TOOL_NAME` already lives) and/or `configs/config.py`, **not duplicated in both call sites**.
- `.ai-run/guides/architecture/service-layer-patterns.md` — reuse existing provider registries/factories rather than duplicating selection rules.
- `.ai-run/guides/standards/code-quality.md` — Ruff config in `pyproject.toml:177` authoritative; new code must be typed; `X | None`; `make license-check` / `make license-fix`.
- `.ai-run/guides/agents/agent-tools.md` — "changing schema behavior without tests" is an explicit anti-pattern; focused tests under `tests/codemie/agents/tools/`.
- `.ai-run/guides/agents/langchain-agent-patterns.md` — reuse existing callback setup; do not drop intermediate steps.
- `.ai-run/guides/testing/testing-patterns.md` — mirror `src/` under an existing tests root: `tests/codemie/service/conversation/` and `tests/codemie/agents/callbacks/` both exist.
- `.ai-run/guides/quality-gates.md` — gates are Makefile targets: `make ruff`, `make build`, `make license-check`, `make gitleaks`, `make test`, `make coverage`, `make sonar-local`, `make verify`.
- `.ai-run/guides/standards/git-workflow.md` — branch `EPMCDME-12768_short-description`; commit `EPMCDME-12768: Description`; squash merge; no proactive commits.
- `.ai-run/guides/project.md` — Jira `EPMCDME`, GitLab `git@gitbud.epam.com:epm-cdme/codemie.git`, target `main`, review artifact = MR via `glab`.

Repo-local skills (`.claude/skills/`): `codemie-jira-assistant`, `sonarqube-mcp-analyzer`, `codemie-onboarding`, `taf-regression-advisor`. **No `.claude/agents/` directory.**

### 3.2 Architectural decisions

- **No ADR practice in this repo.** Decisions live in `docs/superpowers/tasks/<slug>/{spec.md,plan.md,decisions.jsonl}` and in the `.ai-run/guides/` Avoid/Prefer tables.
- `CHANGELOG.md` is dead (last entries are pre-rename `MDTUGPT-*`); **no changelog entry expected**.
- **Existing partial decision already in code**: `preserve_full_output` is already a first-class concept, not something to invent — `ToolReplayRecord.preserve_full_output` field (`history_projection_service.py:55`) and metadata honoring at `:178`. The registry change is really "widen who sets the flag"; the metadata channel is already the sanctioned mechanism.
- **Nearest naming precedent** for a replay-related setting: `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY` at `service/constants.py:47`.

### 3.3 Prior artifacts

- `docs/superpowers/tasks/2026-07-27-epmcdme-12768-agent-full-document-retrieval/.state.json` — **this task's own folder**, contains only state: `flow: sdlc-task`, `branch: EPMCDME-12768_sharepoint-multipage-doc-indexing`, `phase: main`. No spec/plan yet.
- `docs/superpowers/tasks/2026-07-27-epmcdme-12768-sharepoint-multipage-doc-indexing/` — the **sibling** EPMCDME-12768 task (LINK 1), completed and approved. Full artifact set including a 46 KB `technical-analysis.md`, `plan.md`, `code-review-final.json` (request-changes CR-001/CR-002), `code-review-check.json` (approve), `decisions.jsonl`, `qa-report.md`. Establishes:
  - `chunk_num` is now always assigned per file in `_split_documents`; `rrf._filter_duplicates` falls back to the ES `_id`. A KB search now returns all chunks — the remaining loss is downstream in replay. The two tasks are complementary halves of the same ticket.
  - Disclosed behavior change to reuse in MR text: `search_kb.format_document` now labels single-chunk docs as `source-1`.
  - **The same branch is reused** (`EPMCDME-12768_sharepoint-multipage-doc-indexing`) — confirm whether the replay fix lands there rather than creating a second branch.
  - Precedent from `decisions.jsonl`: risk flags `shared-code-blast-radius` / `legacy-index-compatibility`; gitleaks recorded SKIPPED (colima/virtiofs mount block); **47 unit failures are pre-existing on main**, verified by reverting changed files — expect the same noise from `make test`.
- **No design doc or ADR on history projection/replay anywhere in `docs/`.** The only `docs/` hits for "tool output" describe a different mechanism: `docs/workflows/02_configuration_reference.md:38,50,58,68` (`limit_tool_output_tokens`, default 10000) and `:563` (`tools_tokens_size_limit`) — token-based workflow-node truncation, not char-based conversation-replay truncation. **Do not collide with those names.**

### 3.4 Derived conventions

- Tool-name constants belong in `src/codemie/service/constants.py` under the `# Conversation replay metadata keys and statuses` block (line 50 area). Both target files already import from there.
- `search_kb*` is a prefix **family**, not a fixed set — the registry needs prefix/glob matching, unlike the current `==` and unlike all three existing `list[str]` registries.
- Config lists: `list[str]` with literal default on `Config`; env override is JSON for complex types.
- `from __future__ import annotations`, `X | None`, `frozenset[str]` / `tuple[str, ...]` defaults fit the code-quality guide.
- Validation: `make ruff`, then `make test`; `make license-check` for new files; `make verify` for a full pass.
- **No inline TODO/HACK/NOTE/DECISION markers** exist in `src/codemie/service/conversation/` or `src/codemie/agents/callbacks/` (grep returned zero).

---

## 4. Testing Landscape

### 4.1 Existing coverage

- `tests/codemie/service/conversation/test_history_projection_service.py` — 577 lines, **13 tests**, no classes, no fixtures, no parametrize, no mocks (pure construction + assert):
  1. `test_build_for_request_zero_windows_disables_completed_tool_replay` (L77)
  2. `test_build_for_request_skips_duplicate_assistant_message_when_tool_output_matches` (L95)
  3. `test_build_for_request_replays_error_tool_even_when_windows_are_disabled` (L113)
  4. `test_build_for_request_downgrades_tool_replay_from_different_assistant_to_text_summary` (L134)
  5. `test_build_for_request_keeps_native_tool_replay_for_same_assistant_with_available_tool` (L157) — content is the **full** tool output, but the fixture string is ~25 chars so truncation is never exercised
  6. `test_build_for_request_downgrades_same_assistant_tool_replay_when_tool_is_unavailable` (L179)
  7. `test_build_for_request_keeps_supported_native_tool_replay_when_turn_mixes_supported_and_unavailable_tools` (L202)
  8. `test_build_for_request_preserves_original_order_when_native_and_downgraded_tools_mix` (L268)
  9. `test_build_for_request_downgrades_each_tool_to_a_separate_text_message` (L333)
  10. `test_build_for_request_deduplicates_duplicate_subagent_tool_replay_by_call_id` (L400)
  11-13. `_normalize_tool_name` tests (L451, L531, L557) — invalid chars, `MAX_TOOL_NAME_LENGTH`, combined scenarios
- `tests/codemie/service/conversation/test_history_compaction_service.py` — sibling; uses `monkeypatch.setattr(history_compaction_module, …)` and `monkeypatch.setattr(Cls, "_is_enabled", classmethod(lambda cls: True))` — good precedent for module-level monkeypatching in this package.
- `tests/codemie/agents/callbacks/test_agent_streaming_callback.py` — 20 tests covering `on_tool_start`/`on_tool_end`/`on_tool_error` lifecycle and `_preprocess_tool_result`, but **asserts nothing about the replay `metadata` dict**.
- `tests/codemie/agents/callbacks/test_agent_invoke_callback.py` — 9 tests; calls the hooks at L128-134 with **zero** metadata assertions.
- `tests/codemie/agents/tools/kb/test_search_kb.py` — `SearchKBTool` unit tests (image artifacts, datasource health). Helpers `_make_tool` / `_make_doc`, `SearchKBTool.model_construct(...)`, `MagicMock(spec=IndexInfo)`, patch constant `_FILE_SERVICE_PATH`. Nothing about history replay or naming registries. Only `kb`/`search_kb` test dir.
- `tests/codemie/rest_api/handlers/test_assistant_handlers.py:598` — patches `_resolve_history_projection_mode` → `"native_tools"`; only other projection touchpoint.

### 4.2 The reusable helper

`_build_conversation_with_tool_turn` at `test_history_projection_service.py:33-74`, keyword-only, returns `Conversation`:

```python
def _build_conversation_with_tool_turn(
    *,
    assistant_message: str,
    tool_output: str,
    status: str = TOOL_STATUS_COMPLETED,
    thought_error: bool = False,
    assistant_id: str = "assistant-a",
) -> Conversation:
    thought = Thought(
        id="tool-call-1",
        author_name="Search Tool",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"query": "release notes"}',
        message=tool_output,
        error=thought_error,
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "search_tool",
            "tool_args": {"query": "release notes"},
            "tool_args_text": '{"query": "release notes"}',
            "status": status,
            "result_summary": f"summary::{tool_output}",
        },
    )
    return Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(role=ChatRole.USER, message="Find the latest release notes", history_index=0),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message=assistant_message,
                history_index=0,
                assistant_id=assistant_id,
                thoughts=[thought],
            ),
        ],
    )
```

**Reuse note**: the helper hardcodes `tool_name="search_tool"` and sets **no** `preserve_full_output` key. Testing the registry requires either adding a `tool_name` parameter or hand-building `Thought`s inline (tests 7-10 already do the latter).

Imports: `Thought`, `ThoughtAuthorType` from `codemie.chains.base`; `Conversation`, `GeneratedMessage` from `codemie.rest_api.models.conversation`; `ChatRole`, `MAX_TOOL_NAME_LENGTH` from `codemie.core.constants`; `NATIVE_TOOLS_MODE`, `TOOL_REPLAY_TYPE`, `TOOL_STATUS_COMPLETED`, `TOOL_STATUS_ERROR` re-exported from `codemie.service.conversation.history_projection_service`. `build_for_request` is called with kwargs `conversation, mode, max_full_tool_turns, max_summarized_tool_turns, current_assistant_id=…, available_tool_names={…}`.

### 4.3 Framework and patterns

- pytest `^8.3.1`, `pytest-asyncio ^0.23.7`, `pytest-cov ^5.0.0`, `pytest-env ^1.1.3`, `pytest-mock ^3.14.0`, `pytest-httpx ^0.35.0` (`pyproject.toml:177-183`). **No `[tool.pytest.ini_options]`** — config lives in `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `addopts = --import-mode=importlib`, `env = ENV=local / REPOS_LOCAL_DIR=./codemie-repos / PG_URL=…`, plus `filterwarnings` ignores.
- Run: `make test` → `poetry run pytest tests/`; coverage via `make coverage` (`Makefile:27,57`).
- Only global conftest is `tests/conftest.py`: module-level `load_dotenv(tests/.env.test, override=True)` **before** any codemie import (so `Config()` sees env at import time), plus a session-scoped autouse `mock_database_engine` patching `PostgresClient.get_engine`. **No conftest exists** under `tests/codemie/service/conversation/`, `tests/codemie/service/`, `tests/codemie/`, or `tests/codemie/agents/callbacks/` — everything is built inline.
- Config override patterns (three established):
  - `monkeypatch.setattr(<module_under_test>.config, "KEY", value)` — `tests/codemie/service/test_kata_import_service.py:29-30`
  - `with patch.object(config, "KEY", value):` — `tests/codemie/service/test_custom_headers_producer.py:37-38`, `test_ai_kata_service.py:83`
  - Dynamic config: `patch("codemie.agents.assistant_agent.DynamicConfigService.get_typed_value", return_value=True)` — `tests/codemie/agents/test_assistant_agent/test_ai_tools_agent.py:315,332`; async variant `monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(...))` — `tests/enterprise/mcp_auth/test_trust_policy_bridge.py:37`
  - Whole-module config mock: `patch("codemie.rest_api.routers.tool.config")` + `mock_config.HTTP_BLOCKED_TOOLS = [...]` — `tests/codemie/rest_api/routers/test_tool.py:177-213` (the direct precedent for testing a tool registry)
- Style: module-level `def test_*`; private-method testing is accepted (`_normalize_tool_name` bound to a local and asserted); module-level `_`-prefixed builder helpers instead of fixtures; real pydantic domain objects in projection tests; assertion shape `assert [type(m) for m in messages] == [HumanMessage, AIMessage, ToolMessage]`; module-level `TOOL_NAME_REGEX` invariant re-asserted after every normalize; Apache license header (14 lines) required on every new test file.

### 4.4 Coverage gaps

- `_summarize_tool_output` has **zero** coverage anywhere — its skill branch, the `len(text) <= limit` boundary, and the `"\n...[truncated]"` suffix are all untested.
- `_build_tool_metadata` has **zero** direct tests — the `normalized_name == SKILL_TOOL_NAME → preserve_full_output=True` branch and the `replace(' ','_').lower()` normalizer are untested.
- `preserve_full_output` is **never asserted** anywhere: not at `history_projection_service.py:178`, not at the consumption sites `:387` / `:481`, not at `:574`.
- `_truncate_text` and the 2500-vs-600 limits are **untested** — every fixture tool output is ~25 chars, so no existing test would fail if truncation regressed. **This is exactly the EPMCDME-12768 bug surface.**
- The turn-window selection is only tested at degenerate values (`max_full_tool_turns` ∈ {0,1}, `max_summarized_tool_turns=0`); the summarized path (`"summary::…"`) is never asserted in output.
- `SKILL_TOOL_NAME` ("skill") never appears in `tests/`; no test builds a thought with `tool_name="skill"` or `search_kb*`.
- `_update_tool_replay_metadata` untested — note it lowercases but does **not** replace spaces, an asymmetry with `_build_tool_metadata` worth a dedicated test once registry lookup is added.
- No test exercises `search_kb*` in a replay context.
- **No test references any `AI_AGENT_HISTORY_REPLAY_*` key** — the new registry config will need `patch.object(config, …)` or `monkeypatch.setattr(<module>.config, …)` per test.

---

## 5. Configuration and Environment

### 5.1 Configuration module

`src/codemie/configs/config.py` — a single pydantic-settings `class Config(BaseSettings)` (line 42, ~926 lines), flat `NAME: type = default` declarations with inline `#` comments. `model_config = SettingsConfigDict(env_file=find_dotenv(".env", raise_error_if_not_found=False), extra="ignore")` at line 758; module bottom `load_dotenv(...)` + `config = Config()` (line 923) — a process-wide singleton re-exported from `src/codemie/configs/__init__.py`.

`DynamicConfigService` (`src/codemie/service/dynamic_config_service.py`) provides DB-backed runtime overrides via `get_bool_value_safe(KEY, default=config.X)`; `convert_value` supports **only** STRING/BOOL/INT/FLOAT — **no list type**.

### 5.2 Environment variables

| Setting | Default | Type | Declared | Read at |
|---|---|---|---|---|
| `AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS` | 4 | int | `config.py:663` | `history_projection_service.py:81` |
| `AI_AGENT_HISTORY_REPLAY_SUMMARIZED_TOOL_TURNS` | 6 | int | `config.py:664` | `history_projection_service.py:83` |
| `AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT` | 2500 | int (chars) | `config.py:665` | `history_projection_service.py:498,573`; `callback_utils.py:75` |
| `AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT` | 600 | int (chars) | `config.py:666` | `history_projection_service.py:492,504,509,575`; `callback_utils.py:77` |
| `AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT` | 800 | int | `config.py:667` | `history_projection_service.py:608,630`; `callback_utils.py:85`; `agent_log_utils.py:26,28` |
| `AI_AGENT_RECURSION_LIMIT` | 150 | int | `config.py:661` | — |
| `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED` | True | bool | `config.py:662` (+ dynamic key `service/constants.py:47`) | `callback_utils.py:47` etc. |

### 5.3 Tool output size limits (research question 5)

- `TOOL_TOKENS_SIZE_LIMIT` — 30000, `config.py:475`. Bound as class default `tokens_size_limit: int = config.TOOL_TOKENS_SIZE_LIMIT` at `src/codemie_tools/base/codemie_tool.py:34`; enforced at `codemie_tool.py:160-185` — **token-based** (`get_encoding(base_llm_model_name).encode(str(output))`), truncates by decoding `tokens[:limit]`, prepends `self.truncate_message` + `"Ratio limit/used_tokens: {ratio}. Tool output: {truncated_data}"`, raises `TruncatedOutputError` when `throw_truncated_error`.
- Per-tool overrides: **`search_kb.py:86` = 20000 tokens**, gitlab/github 70000, file_analysis 100000. Per-assistant override `assistant.tools_tokens_size_limit` (`agents/langgraph_agent.py:1080-1081`, `agents/assistant_agent.py:330-331`); per-workflow-node `workflows/nodes/tool_node.py:234`.
- `MCP_TOOL_TOKENS_SIZE_LIMIT` — 30000, `config.py:474`; bound at `src/codemie/service/mcp/toolkit.py:98`, same enforcement.
- `MAX_CODE_TOOLS_OUTPUT_SIZE` — 50000, `config.py:681`.
- **Replay-side truncation is character-based, not token-based** (`history_projection_service.py:584-589` `_truncate_text`; `callback_utils.py:73-82`). The two subsystems use different units — a preserved-output cap on the replay side should be expressed in characters for consistency with its neighbours.

**Context-budget math**: with `search_kb` capped at 20000 tokens per call and `AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS = 4`, an uncapped registry entry admits a theoretical worst case of ~80000 tokens of replayed search results, plus `_has_pinned_tool_records` (`:385`) forcing those turns into replay even outside both windows. The observed real case was 3207 tokens per call (~13000 tokens for 4 turns) — well within a 120k-token model window, but the tail risk is real.

### 5.4 History compaction (research question 5, cont.)

Settings at `config.py:668-674`:
- `AI_AGENT_HISTORY_COMPACTION_ENABLED` — **False (off by default)**
- `..._TOKEN_LIMIT` 120000, `..._TRIGGER_RATE` 0.8, `..._TARGET_RATE` 0.5, `..._PRESERVE_GROUPS` 6, `..._BATCH_TOKEN_LIMIT` 24000, `..._SUMMARY_PREFIX` `"[Compacted conversation summary]"`

Location: `src/codemie/service/conversation/history_compaction_service.py` — `ConversationHistoryCompactionService.compact_messages` / `build_langgraph_pre_model_hook`. Counts tokens and, above the trigger, replaces older message groups with an LLM-generated summary `AIMessage`, always keeping the last `PRESERVE_GROUPS` groups. Guard `_is_enabled()` at `:562-567`. Callers: `agents/assistant_agent.py:384`, `agents/langgraph_agent.py:189-190,305`.

**Because compaction is disabled by default, there is no safety net for oversized replayed context today.** This is the single strongest argument for capping preserved output rather than leaving it fully uncapped.

### 5.5 Feature flags and deployment

- No feature flag is strictly needed; the registry default itself is the switch (empty list = old behavior minus skill).
- **No deployment manifest change is required.** Grep for `AI_AGENT_`, `TOOL_TOKENS_SIZE_LIMIT`, `HTTP_BLOCKED_TOOLS` across `deploy-templates/`, `config/`, `docs/`, `README.md`, `.ai-run/`, `.env`, `.env.local`, `docker-compose.yml` returns no hits.
  - `deploy-templates/values.yaml` — generic `extraEnv` (line 32) + `customEnv` (line 160) lists only; no per-setting enumeration.
  - `deploy-templates/templates/deployment.yaml:62-66` (and `rollout.yaml`, `ds-pool/*`, `proxy-pool/*`) — inject only `APP_VERSION` plus merged `extraEnv`/`customEnv` via `codemie.mergeEnvLists`.
  - `docker-compose.yml:41-45` — hardcodes only ELASTIC/PG/KEYCLOAK/GOOGLE.
  - `deploy-templates/README.md` is helm-docs generated from `values.yaml` — no entry needed.
- Defaults ship in `config.py`; operators override via Helm `customEnv` or a local `.env`.

---

## 6. Risk Indicators

**Correctness / blast radius**

- **Two non-equivalent normalizers.** `callback_utils.py:58` (`replace(' ','_').lower()`) vs `history_projection_service.py:523-537` (strip, lower, `[^a-z0-9_\-]`→`_`, collapse `_`, strip `_`, slice to 64) vs `callback_utils.py:110` (`.lower()` only, no space replacement). A registry applied to one form but not the others will silently miss matches. The match must be against the **normalized** name on both sides.
- **Exact-name matching cannot work for `search_kb`.** `adapt_tool_name` (`agents/utils.py:281-298`) falls back to `search_kb_<8-digit sha256-derived number>` when the name exceeds `MAX_TOOL_NAME_LENGTH` (64). Prefix or glob semantics are mandatory — a departure from all three existing `list[str]` registries, which use exact `in` matching. No shared prefix-matching helper exists.
- **Four decision points, not one.** `callback_utils.py:68`, `callback_utils.py:76`, `history_projection_service.py:178`, `history_projection_service.py:574`. Fixing only the flag site (`:68`/`:178`) leaves the summary-generation sites truncating at 600, so the legacy / metadata-less path still loses the tail.
- **Write-side vs read-side asymmetry.** `:178` re-derives the flag on read, so a registry there retroactively repairs already-stored conversations; `callback_utils.py:68` only affects new turns. But `result_summary` written today at 600 chars is unrecoverable — both sides must change for the fix to be complete.
- **Do not touch `assistant_handlers.py:337`.** That `SKILL_TOOL_NAME` usage governs native-tool-replay availability (downgrade to text ledger), an orthogonal concern. Folding it into the registry would change replay mode for every `search_kb` call.
- **Streaming/invoke callback divergence.** `agent_streaming_callback.py` title-cases the tool name before `_build_tool_metadata`; `agent_invoke_callback.py:158` passes it raw. Round-trips safely today for `_`/`-`/lowercase, but any registry logic must be verified on both paths.
- **Empty-input path bypasses metadata entirely.** Metadata is built only `if input_text`; otherwise `_extract_tool_records` falls back to `_normalize_tool_name(thought.author_name)` (display name `"Search Kb Sharepoint"`). Only the read-side registry check covers this case.
- **`stale_datasource_service.py:70` is an independent consumer** of the `search_kb` prefix, including a legacy pre-EPMCDME-11979 naming variant. If the prefix is centralized, keep both in sync; if not, the duplication is a future drift risk.

**Context budget**

- `preserve_full_output` currently bypasses truncation **with no cap at all** (`history_projection_service.py:481-482`). Extending it to `search_kb*` admits up to 20000 tokens per call (`search_kb.py:86`) × 4 full turns.
- `_has_pinned_tool_records` (`:385`) additionally forces preserved turns to replay **outside both windows** — the effective number of full search outputs in context can exceed `AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS`.
- `AI_AGENT_HISTORY_COMPACTION_ENABLED` is **False by default** — no downstream safety net. Strong argument for a separate, larger-but-finite "preserved" limit (e.g. a new `AI_AGENT_HISTORY_REPLAY_PRESERVED_TOOL_RESULT_LIMIT`) rather than unbounded text.
- Replay truncation is char-based while tool-output limits are token-based — mixing units in reasoning about the budget is easy to get wrong.

**Testing**

- **`_summarize_tool_output`, `_build_tool_metadata`, `_truncate_text`, and `preserve_full_output` have zero test coverage.** All fixture tool outputs are ~25 chars, so no existing test would catch a truncation regression — the exact bug surface of this ticket.
- `_build_conversation_with_tool_turn` hardcodes `tool_name="search_tool"` and omits `preserve_full_output`; it must be parameterized or bypassed for registry tests.
- No conftest exists in the affected test packages; config overrides must be set up per test via `patch.object(config, …)` or `monkeypatch.setattr(<module>.config, …)`.
- **~47 unit test failures are pre-existing on `main`** (verified in the sibling task by reverting changed files). `make test` output will be noisy — baseline before/after comparison is required to claim no regression.
- `.ai-run/guides/agents/agent-tools.md` calls out "changing schema behavior without tests" as an anti-pattern, while `AGENTS.md` says to write tests only when explicitly asked — this tension must be resolved by the caller, not assumed.

**Process / naming**

- Naming collision hazard: `docs/workflows/02_configuration_reference.md` documents `limit_tool_output_tokens` (default 10000) and `tools_tokens_size_limit` for a **different** subsystem (workflow-node token truncation). The new setting must not reuse those names.
- Branch ambiguity: `.state.json` records `branch: EPMCDME-12768_sharepoint-multipage-doc-indexing` — the same branch as the completed LINK 1 task. Confirm whether this fix lands there or on a new branch before implementation.
- Gitleaks is likely to be reported SKIPPED (colima/virtiofs mount block), matching the sibling task's `decisions.jsonl` precedent.
- No ADR practice and no design doc for history projection — the decision rationale must be captured in this task's `spec.md` / `decisions.jsonl`.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** This task touches four layers: config (`src/codemie/configs/config.py` and/or `src/codemie/service/constants.py`), callback/write (`src/codemie/agents/callbacks/callback_utils.py`), service/read (`src/codemie/service/conversation/history_projection_service.py`), and test (`tests/codemie/service/conversation/`, `tests/codemie/agents/callbacks/`). No API, no persistence, and no deployment-manifest change: `preserve_full_output` is already a first-class concept stored inside an existing free-form JSON `metadata` dict on `Thought`, so **no DB migration is needed**, and deployment templates enumerate no individual env vars. Expected production-code surface is small and well-bounded — 2 source files plus a config declaration, with 4 hardcoded `== SKILL_TOOL_NAME` decision points to replace (`callback_utils.py:68`, `:76`; `history_projection_service.py:178`, `:574`) and one deliberately left alone (`assistant_handlers.py:337`, which governs native-vs-text replay mode, not truncation). Realistically 3-5 files changed, plus 2 test files.

**Technical novelty.** The mechanism being extended already exists — the change is "widen who sets an existing flag", not "invent a new pathway". However, two aspects are genuinely novel for this codebase. First, the registry needs **prefix/family matching** (`search_kb_<datasource>`, with a `search_kb_<8-digit-hash>` fallback above the 64-char `MAX_TOOL_NAME_LENGTH`), whereas all three existing tool registries (`DYNAMIC_WEB_SEARCH_TOOLS`, `DYNAMIC_CODE_INTERPRETER_TOOLS`, `HTTP_BLOCKED_TOOLS`) use exact `in` membership with no normalization, and no shared matching helper exists anywhere. The nearest precedents are substring matches on model names (`DISABLE_PARALLEL_TOOLS_CALLING_MODELS`, `LITELLM_PREMIUM_MODELS_ALIASES`) and a non-config module constant `_KB_TOOL_PREFIX = "search_kb"` in `stale_datasource_service.py:70`. Second, the codebase carries **three mutually inconsistent tool-name normalizers** across the write and read paths, so a naive registry lookup will match on one path and silently miss on another — this is the main correctness trap and the place where a plausible-looking implementation can ship broken.

**Test posture and risk drivers.** The affected area is effectively **untested where it matters**: `_summarize_tool_output`, `_build_tool_metadata`, `_truncate_text`, and `preserve_full_output` have zero assertions anywhere in `tests/`, and every existing projection fixture uses a ~25-char tool output so no current test would fail if truncation regressed — the precise bug surface of this ticket. Good scaffolding does exist (a 577-line projection test file with a reusable `_build_conversation_with_tool_turn` helper, three established config-override idioms, and a direct precedent for testing a tool registry at `tests/codemie/rest_api/routers/test_tool.py:177-213`), but the helper hardcodes `tool_name="search_tool"` and omits `preserve_full_output`, so it must be parameterized. Complexity scoring should weight three factors upward: (1) the normalizer asymmetry and the exact-vs-prefix matching gap, which make "correct on all four decision points and both callback paths" harder than the diff size suggests; (2) the **unbounded** context risk — `preserve_full_output` bypasses truncation entirely, `_has_pinned_tool_records` forces those turns into replay outside the normal windows, `search_kb` is capped at 20000 tokens per call, and `AI_AGENT_HISTORY_COMPACTION_ENABLED` is `False` by default, so there is no downstream safety net; a separate finite "preserved" limit is likely required rather than raw uncapped text; (3) ~47 pre-existing unit failures on `main` mean a before/after baseline is mandatory before any no-regression claim. Mitigating factors: no migration, no deployment change, retroactive repair of stored conversations via the read-side check at `:178`, and a completed sibling task (`docs/superpowers/tasks/2026-07-27-epmcdme-12768-sharepoint-multipage-doc-indexing/`) that already established the guides, gates, and risk vocabulary for this exact ticket.
