# EPMCDME-11696 — Git-based FAQ Datasource (`knowledge_base_git_faq`)

**Type**: Story · **Size**: L (23/36) · **Initial date**: 2026-08-29 · **Architecture selected**: 2026-09-01

## Problem

Teams keep FAQ articles as Markdown files in Git repositories. CodeMie needs a first-class datasource
that preserves FAQ metadata and Markdown section structure while using the established knowledge-base
indexing and retrieval path.

## Selected approach

A top-level `knowledge_base_git_faq` datasource with Git transport and Confluence-style processing:

- **Transport inherited from Git**: `GitFaqLoader(GitBatchLoader)` — shallow clone, all auth modes
  (PAT / GitHub App / Basic header / public anonymous), branch pinning, restricted walk to
  `faq_path` + `.md`.
- **Processing inherited from Confluence**: parse one logical article per file, split at H1-H3,
  propagate article metadata, join adjacent sections in windows of three with one-section overlap,
  then use the standard size splitter and embeddings pipeline in `BaseDatasourceProcessor`.
- **Separate knowledge-base type, not a `CodeIndexType` subtype**: `knowledge_base_git_faq` follows
  the normal `SearchAndRerankKB` path and remains outside code-index classification and the
  `llm_routing` processor registry.
- **Config on `index_info`** (existing `branch`/`link`/`setting_id` columns; new `faq_path` column,
  default `faq/` normalized to `faq`) — KB-style create/update REST endpoints modeled on the Google
  routes; source-field edits require `full_reindex=true` (422 otherwise).
- **Article format (corpus-validated)**: precedence chain frontmatter → markdown convention →
  filename (title: fm → first H1 → humanized filename; instructions: fm → text after
  `Prompt Instruction:`; reference: fm → normalized path relative to `faq_path`; `---` counts as
  frontmatter only at line 1; first H1 wins on multi-H1 files; bad YAML → skip + warning).
  Article metadata is retained on each generated chunk.
- **Retrieval**: kNN + BM25 fused with RRF, followed by the existing source-selection and coverage
  behavior in `SearchAndRerankKB`. No FAQ TOC is stored in Elasticsearch `_meta`.

The rationale and rejected alternatives are recorded in
`docs/2026-09-01-faq-retrieval-strategy-comparison.md`.

## Functional Requirements

| FR | Requirement |
|---|---|
| FR-1 | User can create/update/delete a Git FAQ datasource (repo link, branch, `faq_path`, Git integration) via UI and API (`POST/PUT /v1/index/knowledge_base/git_faq`) |
| FR-2 | Every selected `.md` file is parsed as one logical article and indexed as one or more embedded chunks |
| FR-3 | Parser implements the precedence chain above; parse failures skip the file with a warning counted in loader stats |
| FR-4 | Articles are split at H1-H3, joined in 3/1 section windows, size-split, embedded, and indexed with source/article metadata |
| FR-5 | Retrieval uses `SearchAndRerankKB`; Git FAQ does not store TOC metadata or register an `_LLM_ROUTING_PROCESSOR_FACTORIES` entry |
| FR-6 | Cron and webhook triggers reindex FAQ datasources (full reprocess); resume is explicitly unsupported (visible error, listed in `UNSUPPORTED_RESUME_TYPES`) |
| FR-7 | Source-field edits (`faq_path`/`branch`/`link`/`setting_id`) require `full_reindex=true`; metadata edits do not |
| FR-8 | No regression: Google Docs remains on its independent whole-document LLM-routing flow; all existing datasource types are unaffected |
| FR-9 | Documentation: internal `.ai-run` integration guide; post-release public docs PR to `codemie-ai/docs` (Tech Writer skill) |

## Acceptance Scenarios

1. Create datasource pointing at a Git FAQ directory: selected articles are parsed and embedded under
  `knowledge_base_git_faq`; progress advances through the standard chunk pipeline.
2. Ask an assistant a question answerable by one section of a large article: hybrid KB retrieval
  returns the relevant chunk with source and coverage information.
3. Edit `faq_path` without full reindex → 422; with full reindex → re-indexed corpus.
4. Push to the repo (webhook) or cron fire → full FAQ reindex, no silent no-op, no stuck-in-progress.
5. Google Docs retains its existing whole-document processor and routing behavior.
