# FAQ Git Integration (`knowledge_base_git_faq`)

The Git FAQ datasource loads every Markdown article in a Git repository (optionally narrowed by
`files_filter`) and indexes embedded, header-aware chunks. Its processing and retrieval model follows
the Confluence knowledge-base flow rather than the Google Docs whole-document LLM-routing flow.

The selected design is Option A in
`docs/2026-09-01-faq-retrieval-strategy-comparison.md`: Confluence-style chunking with standard hybrid
knowledge-base retrieval.

## Architecture

```text
GitFaqLoader
  -> one parsed article per Markdown file
GitFaqDatasourceProcessor(BaseDatasourceProcessor)
  -> H1-H3 section splitting
  -> section-window joining (window 3, overlap 1)
  -> standard chunk splitting and embeddings
  -> Elasticsearch knowledge-base index
SearchAndRerankKB
  -> kNN + BM25 + RRF + source selection and coverage handling
```

- **Type**: `FullDatasourceTypes.GIT_FAQ = "knowledge_base_git_faq"`. It is a knowledge-base type,
  not a code type and not an `llm_routing_*` type.
- **Config on `IndexInfo`**: `link`, `branch`, `setting_id`, and optional `files_filter`. There is no
  `GitRepo` row. The index uses KnowledgeBase naming: `{project}-{datasource_name}`.
- **Transport**: `GitFaqLoader(GitBatchLoader)` performs a shallow clone using the supported Git auth
  modes (PAT, GitHub App, Basic header, or public anonymous access) and loads every `.md` file in the
  repo, narrowed only by the same gitignore-style `files_filter` mechanism the Git code datasource
  uses (`check_file_type` in `src/codemie/core/utils.py`).
- **Retrieval**: `search_kb` uses the normal `SearchAndRerankKB` path. Git FAQ does not register an
  `_LLM_ROUTING_PROCESSOR_FACTORIES` entry and does not store a TOC in Elasticsearch `_meta`.

## Article format

The loader parses one logical article per Markdown file before the processor creates searchable
chunks.

| Field | Resolution order |
|---|---|
| `title` | frontmatter `title` -> first `#` heading -> humanized filename |
| `instructions` | frontmatter `instructions` -> text after `Prompt Instruction:` marker |
| `reference` | frontmatter `reference` -> repo-relative path without extension |
| `references` | frontmatter list, passed through as metadata |

`---` is frontmatter only at line 0; a later `---` is a horizontal rule. The first H1 supplies the
fallback title, and invalid frontmatter YAML skips the file with a warning counted in loader stats.

## Chunking and indexing

1. Split each article at Markdown H1, H2, and H3 headings while retaining header metadata.
2. Join neighboring sections in windows of three with an overlap of one section.
3. Pass each joined window through the standard datasource chunk splitter.
4. Generate embeddings and index the resulting chunks through `BaseDatasourceProcessor`.

Small articles may remain effectively whole. Large articles produce multiple overlapping chunks, so
retrieval can return the relevant section without loading the entire article into the model context.

## Where things live

| Piece | Location |
|---|---|
| Markdown parser | `src/codemie/datasource/loader/faq_markdown_parser.py` |
| Loader | `src/codemie/datasource/loader/git_faq_loader.py` |
| Processor | `src/codemie/datasource/git_faq/git_faq_datasource_processor.py` |
| Base embedding pipeline | `src/codemie/datasource/base_datasource_processor.py` |
| Retrieval dispatch | `src/codemie/agents/tools/kb/search_kb.py` |
| REST | `POST/PUT /v1/index/knowledge_base/git_faq` in `src/codemie/rest_api/routers/index.py` |
| Triggers | `reindex_git_faq` actor, webhook/cron FAQ branches, `UNSUPPORTED_RESUME_TYPES` |
| Processor tests | `tests/codemie/datasource/git_faq/test_git_faq_datasource_processor.py` |
| Loader/parser tests | `tests/codemie/datasource/loader/test_git_faq_loader.py`, `test_faq_markdown_parser.py` |

## Behavior contracts

- One Markdown file becomes one parsed article and then one or more embedded ES chunks.
- Resume and incremental reindex are unsupported: `_on_process_start` rejects them, and
  `UNSUPPORTED_RESUME_TYPES` prevents stale-watchdog retry loops.
- Source edits (`files_filter`, `branch`, `link`, or `setting_id`) require
  `full_reindex=true`; otherwise the API returns 422. Metadata-only edits do not reindex.
- Webhooks and cron use the full `reprocess()` path. Public repositories can reindex anonymously.
- Datasource names are unique per `(project, name)` regardless of type. A code index for the same
  repository therefore requires a different Git FAQ datasource name.

## Do not

- Add Git FAQ to `_CODE_INDEX_TYPES`, `IndexTypeByContextTypeMapping.CODE`, or `CodeIndexType`.
- Rename it to an `llm_routing_*` type or add it to `_LLM_ROUTING_PROCESSOR_FACTORIES`.
- Bypass chunking or embeddings, or restore TOC-based whole-document retrieval.
- Treat the dated B1 whole-document design artifacts as the current implementation contract; they
  were superseded by the September 1 retrieval decision.

## Verification

The processor, loader, parser, REST, and trigger tests under `tests/codemie/` define the executable
contract. The retrieval decision and live comparison are recorded in
`docs/2026-09-01-faq-retrieval-strategy-comparison.md`.
