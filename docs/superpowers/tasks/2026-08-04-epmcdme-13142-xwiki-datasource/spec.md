# EPMCDME-13142 — xWiki datasource (backend)

**Branch**: `EPMCDME-13142_xwiki-datasource` · **Repo**: `codemie` · **Date**: 2026-08-04

A user connects one xWiki Space. Its pages — and the pages of every space beneath it — are
fetched over the xWiki REST API, rendered to markdown, chunked, embedded and indexed into
Elasticsearch, so an assistant can answer from them.

Grounded in [`technical-analysis.md`](technical-analysis.md) (verified against source at
`3e71dc072` and against a live xWiki instance) and [`complexity-assessment.md`](complexity-assessment.md)
(XL, 27/36).

## Scope

**In**: stories 1–3 of the assessment's decomposition — the ingest engine (loader, processor,
config, enums, migration, tests), the REST and settings surface, and scheduled reindex.

**Out**: frontend (`codemie-ui`, separate MR) · attachments · page comments · true delta
reindex · `use_bearer` (see [Known limitations](#known-limitations)).

---

## 1. Modules

Three new files:

| File | Purpose |
|---|---|
| `src/codemie/datasource/loader/xwiki_loader.py` | `XWikiLoader(BaseLoader, BaseDatasourceLoader)` — the crawl |
| `src/codemie/datasource/xwiki/xwiki_datasource_processor.py` | `XWikiDatasourceProcessor(BaseDatasourceProcessor)` |
| `src/external/alembic/versions/<rev>_add_xwiki_column_to_index_info.py` | the `xwiki` JSONB column |

**The migration is a separate deliverable, not one of the registration edits.** Each datasource
type owns a JSONB column on `index_info`; the live DB has `azure_devops_wiki`,
`azure_devops_work_item`, `confluence`, `jira` and `sharepoint`, and **no `xwiki`**. Without this
column the datasource cannot be persisted at all — every create call fails. The existing xWiki
migration `3b3358380aa8` added the *credential* type, which is a different thing.

- Donor: `2b461b2f3d10_added_azure_devops_wiki_column_to_the_.py`
- `down_revision = "t1u2v3w4x5y6"`
- `op.add_column('index_info', sa.Column('xwiki', postgresql.JSONB(astext_type=sa.Text()), nullable=True))`

**On the head revision — verified by alembic itself.**

```
$ docker run --rm -v ~/Projects/Work/codemie-dev/codemie:/repo \
      -w /repo/src/external/alembic codemie-dev-codemie sh -c '/venv/bin/alembic heads'
t1u2v3w4x5y6 (head)
```

A single head. This agrees with a text parse of all 147 revision files (tuple `down_revision` in
merge migrations included) at both `HEAD` and `origin/main`: eight branch points exist, all
reconverging through merge migrations.

> **Why the throwaway container.** `alembic` cannot run inside the long-lived
> `codemie-dev-codemie-1` container: `.env`, `pyproject.toml` and `poetry.lock` are each bound as
> *single-file* mounts, which pin the inode at container-creation time. The container was created
> 2026-07-31 12:41 and `.env` was rewritten at 12:44, so the bind now points at a replaced inode —
> `stat` still succeeds (hence `ls -l` works) but `open` returns `EPERM`. It is not a permissions
> or xattr problem: `pyproject.toml` carries the identical `com.apple.provenance` xattr and reads
> fine, and deleting the xattr changes nothing. A throwaway container with a whole-directory mount
> sidesteps it and touches nothing.
>
> **Do not "fix" this by recreating the `codemie` container** — that wipes the redis and langfuse
> packages installed into it by hand (the OSS build of `main` does not install them) and the
> backend stops booting until they are reinstalled.

> **MR drive-by note (no code change here):** `7ca305066800_create_assistant_project_mapping.py`
> has a stale docstring — `Revises: r2s3t4u5v6w7` while the actual variable is
> `down_revision = "p1q2r3s4t5u6"`. Alembic reads the variable, so behaviour is correct, but any
> docstring-based graph analysis builds a different tree.

---

## 2. Loader

Constructed from `XWikiConfig` (url, username, token, use_bearer — the existing credential model,
review gate #1), plus `wiki: str` and `space: str`.

### 2.1 Two path grammars

REST and `/bin` are different APIs and disagree about how to express a nested space. Both builders
percent-encode each segment with `quote(segment, safe="")` — verified live that `%20` is required
(`Vacation Policy`) and that Cyrillic ids (`Довідка`) round-trip once encoded.

| | REST | `/bin` |
|---|---|---|
| `KB.Onboarding.Checklist` | `/spaces/KB/spaces/Onboarding/pages/Checklist` | `/bin/get/KB/Onboarding/Checklist` |
| Builder | reuse `build_spaces_path()` | plain slashes |

Both are composed from `XWikiConfig.url` with `rstrip("/")`. **Never hardcode an `/xwiki` prefix** —
the local Docker instance serves at the servlet root, `xwiki.org` serves under `/xwiki`, and the
difference is the user's to get right in the URL field.

### 2.2 Crawl

1. `GET /rest/wikis/{wiki}/spaces` — a **flat** list of every space with dotted ids. Keep those
   where `id == f"{wiki}:{space}"` or `id.startswith(f"{wiki}:{space}.")`. This yields the target
   space and all descendants without an N-deep walk.

   **This listing paginates and must be paged explicitly.** Verified live: `?number=2` returns 2
   spaces and `?number=2&start=2` returns the next 2. The test instance has 19 spaces and an
   unparameterised call returns all 19, so the server-side default cap is unknown — on a corporate
   wiki with many spaces an unpaged call could silently return only the first page and drop entire
   subtrees. Use the same terminator as pages.
2. Per space: `GET <space_path>/pages` with offset pagination (`number`, `start`). There is no
   `next` link and no total, so the terminator is **"returned fewer than `number`"**.
3. Per page: a REST `GET` is **mandatory** — page summaries carry neither `content` nor `modified`.
4. Per page: `GET /bin/get/<path>?xpage=plain` → HTML → `markdownify` (already a dependency, used
   in `tools.py`). Verified: `/bin/get` honours Basic auth and returns clean HTML with no skin
   chrome; REST has no HTML rendering at all (`Accept: text/html` → 500), so `/bin` is the only
   rendered path.
5. Empty `content` → skip the page, count it under `SKIPPED_DOCUMENTS_KEY`.

`KB.Onboarding` exists simultaneously as a page in `KB` and as a space whose home is
`KB.Onboarding.WebHome`. Both are indexed; their `source` values differ by construction.

### 2.3 Rendered-with-fallback

If the `/bin` fetch returns non-200 or an empty body: fall back to the raw REST `content`, log a
warning, and set `content_format="raw"`. **A page never fails because rendering failed** — `/bin`
is a web endpoint whose behaviour depends on the instance skin and configuration.

### 2.4 Network robustness

The crawl is two requests per page plus one listing per space, so a large space is thousands of
calls. Flakiness is expected; silence is not.

- An explicit per-request timeout.
- One bounded retry on timeout, 429 and 5xx. 4xx is not retried.
- A per-page failure counter surfaced through `get_load_stats()` as `FAILED_DOCUMENTS_KEY`.
- A single failed page does not kill the run. But the run fails loudly rather than reporting a
  partially indexed space as success when `failed_pages > max(5, 0.1 * total_pages)` — the floor
  keeps one flaky page from failing a three-page space, the rate keeps a systematically broken
  crawl from passing as healthy. Both numbers live in `XWikiDatasourceConfig`.

### 2.5 `fetch_remote_stats()`

xWiki returns no total-count field in any list response (unlike Confluence's `totalSize`), so this
enumerates: spaces, then pages per space.

It **must not** swallow exceptions. The donor
(`azure_devops_wiki_loader.py:778-780`) wraps its body in `except Exception: return 0`, which turns
every failure into "0 documents" — the exact behaviour review gate #3 exists to prevent.

### 2.6 Page cap

`loader_max_pages` (precedent: `ConfluenceConfig`). **Truncation is never silent.** On hitting the
cap the loader logs it, records the truncation in load stats, and the health check reports the
datasource as truncated rather than healthy — see §5. A user whose space exceeds the cap must not
see a green datasource that is quietly missing pages.

---

## 3. Chunk metadata

Review gate #2. `_process_chunk` is a **whitelist**: the base class re-applies only `chunk_num`
(`base_datasource_processor.py:637-639`), so anything not explicitly copied is silently dropped.

| Key | Value |
|---|---|
| `source` | `xwikiAbsoluteUrl` — unique, browser-openable |
| `page_id` | full id, e.g. `xwiki:KB.Formatting` |
| `space`, `wiki` | the dotted space id and the wiki id |
| `title` | page title |
| `modified` | epoch **milliseconds** |
| `version` | e.g. `1.1` |
| `author` | e.g. `XWiki.AdminAdmin` |
| `content_format` | `rendered` \| `raw` |

`source` must be unique and stable: `_split_documents` keys chunk identity on it
(`base_datasource_processor.py:624,630`), so two pages collapsing to the same `source` overwrite
each other's chunks.

`modified` / `version` / `author` are carried in v1 even though delta reindex is out of scope —
they are its foundation, and adding them later forces a full reindex of everything already indexed.

---

## 4. Processor

```python
INDEX_TYPE = "knowledge_base_xwiki"
```

| Member | Implementation |
|---|---|
| `_index_name` | `KnowledgeBase(name=f"{project_name}-{datasource_name}", type=INDEX_TYPE).get_identifier()` |
| `_processing_batch_size` | `XWIKI_CONFIG.loader_batch_size` |
| `_init_index()` | `IndexInfo.new(..., xwiki=XWikiIndexInfo(space=..., wiki=...))` |
| `_init_loader()` | `XWikiLoader(config=..., wiki=..., space=...)` |
| `_process_chunk()` | rebuild metadata from the table in §3 |
| `_get_splitter()` | `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="o200k_base", chunk_size=XWIKI_CONFIG.chunk_size, chunk_overlap=XWIKI_CONFIG.chunk_overlap, disallowed_special={})` — the base default is code-oriented |
| `_check_docs_health()` | `loader.fetch_remote_stats()[DOCUMENTS_COUNT_KEY]`; exceptions **propagate** |

Only `_index_name`, `_init_loader` and `_init_index` are actually abstract on the base class.
`_check_docs_health` is a duck-typed convention invented by the two Azure DevOps processors and
called by name from the health-check service — implementing it does nothing unless §6 also adds the
`case DatasourceTypes.XWIKI` arm. Nothing fails loudly if that arm is forgotten, which is why §8
adds a guard test.

---

## 5. Errors — one shared mapper, two callers

| Situation | Exception | What the user sees |
|---|---|---|
| 401 / 403 | `UnauthorizedException("xWiki")` | "Cannot retrieve data from xWiki" |
| non-200 / non-JSON | `ConnectionException("xWiki", details)` | + `field_error` on the URL field — this is what catches a missing or extra `/xwiki` prefix |
| empty `space` | validation error | `field_error` on `space`, with a message saying where to find the value |
| no integration configured | `MissingIntegrationException` | already handled by the service |
| page cap hit | not an exception | `documents_count = <cap>` **and** `error = ErrorMessage(...)` so the datasource is not green |

`index.set_error(str(ex))` means the exception's `str()` is what the user reads
(`base_datasource_processor.py:257`), which is why typed exceptions matter. The health-check
service's existing `try` already maps all four exception types to `ErrorMessage`, so no new handler
is needed there.

Since the frontend form ships in a later MR, **every validation error carries `field_error`** so the
form can map it without further backend changes.

`DatasourceHealthCheckResponse` has no warning slot (`implemented`, `documents_count`, `error`), so
truncation is reported through `error` while `documents_count` still shows the number. No model
change.

**Do not reuse `_XWikiBaseTool._request()`** — it returns `(response, text)` and never raises on
4xx, so a 403 body would be indexed as page content. Reuse is limited to `XWikiConfig`,
`_build_auth_headers()` and `build_spaces_path()`.

### 5.1 What the credential check can and cannot prove

Verified live: `/rest/wikis` returns **200 anonymously and with a wrong password**, and so does
every other read-only endpoint tested. xWiki grants guest read by default and its REST API has no
"current user" resource, so **on a guest-readable wiki a wrong password is not detectable**.

Consequences:

- The datasource health check probes the **target space** and returns its real page count — the one
  place a wrong password shows up, as a suspiciously low number the user can see.
- `SettingsTester` gains the missing `CredentialTypes.XWIKI` handler, reusing the shared mapper. Its
  success message states what was actually verified — **"instance reachable, credentials accepted"** —
  and does not claim the password is valid. That wording lives in the message text, not only in a
  code comment. No refactor of `SettingsTester` itself.

> **For the MR description**: the `SettingsTester` handler is required by the datasource flow, not
> an unrelated change. The `XWIKI` credential type already ships in the product, but its "Test
> connection" button raises `SettingsTesterHandlerNotFound` today.

---

## 6. Registration points

13 edited files, plus one listed only to say it must be left alone. Three of the thirteen — the YAML
block, `get_xwiki_creds`, and the `_RESUME_DISPATCH` entry — are absent from the original hand-off
briefing and each fails at boot or silently.

Total for the run: **3 new files + 13 edited**, plus tests.

| File | Edit |
|---|---|
| `core/constants.py:91` | `DatasourceTypes.XWIKI = "xwiki"` |
| `service/constants.py` | `FullDatasourceTypes.XWIKI = "knowledge_base_xwiki"` |
| `datasource/datasources_config.py` | `XWikiDatasourceConfig` model, `LoadersConfig.xwiki_loader`, module constant, log line |
| `config/datasources/datasources-config.yaml:143` | **`xwiki_loader:` block.** `LoadersConfig` has no defaults — a missing block is a Pydantic error **at import time**, so the whole app fails to boot |
| `rest_api/models/index.py` | `:95` context-type tuple · `:155` `XWikiIndexInfo` · `:285` `IndexInfo.xwiki` SQLField · `:682`/`:707` `IndexInfo.new()` · `:1441`/`:1622` request models · update helper with `flag_modified(self, 'xwiki')` |
| `rest_api/routers/index.py` | `POST` + `PUT /index/knowledge_base/xwiki`, mirroring `:1145` / `:1695` |
| `service/index/datasource_health_check_service.py` | `case DatasourceTypes.XWIKI` (`:49`) · `health_check_xwiki()` · `get_invalid_field()` → `"space"` (`:237`) |
| `service/settings/settings.py` | `get_xwiki_creds()` — every other type has one; `XWikiConfig → CredentialTypes.XWIKI` already maps at `:224`, so this is a pure addition |
| `service/settings/settings_tester.py` | register the `CredentialTypes.XWIKI` handler |
| `service/settings/settings_request_validator.py:56` | **add** `"knowledge_base_xwiki"` to `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` (it is a deny-list) and extend the help string. Do **not** add it to the scheduler deny-list |
| `triggers/trigger_models.py:59` | `XWikiReindexTask(ReindexTaskPayload)` |
| `triggers/actors/datasource.py` | `reindex_xwiki()` actor · `_resume_xwiki` + `_RESUME_DISPATCH["knowledge_base_xwiki"]` (`:917`) — the stale-job watchdog; omitting it means a stuck index is never resumed |
| `triggers/bindings/cron.py:621` | `elif index_type_str == FullDatasourceTypes.XWIKI.value:` branch |
| `triggers/bindings/utils.py:70` | **no change.** That list is the *webhook* allow-list |

### 6.1 API shape

```python
class XWikiIndexInfo(BaseModel):
    space: str            # dotted id as shown in the URL: "KB", or "KB.Onboarding" for a subtree
    wiki: str = "xwiki"
```

`space` is required and validated non-empty. `wiki` is validated non-empty when provided. Base URL
and credentials come from the integration via `setting_id`, never from these fields.

`wiki` is carried because the wiki id is part of **every** REST path (`/rest/wikis/{wiki}/spaces/...`),
so hardcoding `"xwiki"` makes subwiki farms unusable — and the existing xWiki tool library already
exposes `wiki: str = "xwiki"`, so a datasource that hardcodes it would be inconsistent with the
layer it is built on. (Adding the field later would **not** require a migration — the column holds a
pydantic model in JSONB — so that is not the reason.)

---

## 7. Scheduled reindex is a full rebuild

`reindex_xwiki` → `processor.reprocess()` → `is_full_reindex = True` → `_cleanup_data()` **deletes
the entire ES index** → `process()`.

This is what every comparable type does: Azure DevOps Wiki (`actors/datasource.py:425`), Confluence
(`:292`), Google (`:349`), SharePoint (`:498`). Only Jira (`:215`) and Xray (`:551`) call
`incremental_reindex`, and both also implement `_cleanup_data_for_incremental_reindex`.

**The hand-off briefing's claim that incremental refresh is "free — inherited, no extra code" is
false.** Review gate #3 asks for this behaviour to be stated explicitly rather than reimplemented:
v1 xWiki scheduled refresh is a full delete-and-rebuild. True delta reindex needs a
`_cleanup_data_for_incremental_reindex()` implementation plus switching the actor to
`incremental_reindex`, and is a separate ticket. Say so in the MR.

---

## 8. Testing

TDD — test first, RED before GREEN. New tests live in `tests/codemie/datasource/xwiki/`, mirroring
`tests/codemie/datasource/azure_devops_wiki/`. HTTP is mocked with `unittest.mock.MagicMock`, the
house style in `tests/codemie_tools/core/project_management/xwiki/conftest.py`.

**Loader** — space + descendants are discovered from the flat listing · **the space listing itself
paginates** and a second page of spaces is followed · page pagination terminates on a short page · `KB.Onboarding` is indexed as both page and space with distinct `source` · `%20` and
Cyrillic ids are encoded · empty pages are skipped and counted · `/bin` non-200 falls back to raw
and sets `content_format="raw"` · 401/403 raises `UnauthorizedException` · non-JSON raises
`ConnectionException` · `fetch_remote_stats()` propagates rather than returning 0 · a timeout is
retried once then counted · a failure rate above the threshold fails the run · hitting
`loader_max_pages` marks the result truncated.

**Processor** — `_process_chunk` carries all nine metadata keys · `_index_name` formula ·
`_check_docs_health` propagates.

**Guard test (§6)** — a parametrized test over `DatasourceTypes` asserting each member has its
`datasources-config` entry, its `FullDatasourceTypes` counterpart and its health-check branch.

The enums are genuinely partial today — `SVN`, `JSON` and `XRAY` have no `FullDatasourceTypes`
member; `FILE`, `JSON` and `GOOGLE` have no health-check branch; `LoadersConfig` has no `google`
entry — so the test carries an explicit, documented exemption map. The point is not to make the
existing state pretty: it is that **adding a type without wiring it turns a test red instead of
surfacing in production.** A new member must either be wired or consciously added to the exemption
map.

---

## 8.1 No dependency changes

**This ticket adds no packages and must not.** `markdownify` — the only non-stdlib thing the design
reaches for — is already a declared direct dependency (`pyproject.toml:119`, `^1.2.0`), so nothing
needs re-locking.

This is stated rather than assumed because a contributor **cannot run `poetry lock`**: it resolves
against a private GCP Artifact Registry we have no access to (EPMCDME-12313 hit exactly this). If
any implementation step reaches for a new package, that is a **hard stop** — raise it, do not
improvise.

---

## 9. Known limitations

- **`use_bearer` cannot be persisted.** `XWIKI_FIELDS` (`settings.py:219`) stores only url, token
  and username, so credentials resolved through `SettingsService` always fall back to Basic. Bearer
  additionally requires a server-side xWiki plugin. Document the plugin requirement in the MR;
  do not build a second auth path. (Code review found a Bearer branch had been written anyway; it
  was removed, and `_auth_headers` now documents why Basic is the only path.)
- **Basic credentials travel in cleartext over `http://`, and the scheme is deliberately not
  gated.** The project enforces https for SharePoint (`_HTTPS_SCHEME`), but that is a cloud SaaS
  where http never occurs. xWiki is the opposite: typically self-hosted inside a corporate network,
  where http is normal — the test instance is `http://xwiki:8080`. A hard https gate would make the
  feature unusable for its main audience. Instead: state plainly in the integration field help and
  the MR description that Basic auth over http sends credentials in cleartext and that https is
  strongly recommended, and raise on-prem transport policy in the MR as an **open question for the
  core team** — a contributor datasource ticket is not the place to invent a security policy.
- **Wrong passwords are undetectable on guest-readable wikis** — §5.1.
- **Scheduled refresh is a full rebuild** — §7.
- **Base-URL shape is the user's responsibility.** A wrong `/xwiki` prefix produces a connection
  error; wrong credentials on a guest-readable wiki produce a silent partial index. Two failure
  modes, two different `ErrorMessage.help` strings.

---

## 10. Definition of done

- `make ruff`, `make license-check` (run explicitly — `make verify` silently skips the license gate
  on macOS because the Makefile has no `.PHONY` and the `license` target resolves to the `LICENSE`
  file), `make gitleaks`, `make test` all green.
- Manual e2e against the live `KB` space: create the datasource, watch it index, then search the
  indexed content and get a hit from a nested page.
- No `.diff` artifacts from `docs/superpowers/**` committed.
