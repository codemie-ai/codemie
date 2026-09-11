# Work Item: EPMCDME-11696 — Git-based FAQ Datasource (`knowledge_base_git_faq`)

**External Ticket**: https://jiraeu.epam.com/browse/EPMCDME-11696
**Type**: Story
**Priority**: Major
**Status**: Ready for review
**Epic**: Not configured
**Branch**: EPMCDME-11696_git-faq-datasource
**External sync**: pending (final Option A story amendment; see History)

## Summary

Add a top-level knowledge-base datasource type `knowledge_base_git_faq` that clones a Git repository,
parses FAQ articles from Markdown files selected by `faq_path` and optional `files_filter`, and indexes
embedded header-aware chunks. The processor follows the Confluence Markdown flow: H1-H3 splitting,
3/1 section-window joining, standard size splitting, and embeddings. Retrieval uses
`SearchAndRerankKB` (kNN + BM25 + RRF, source selection, and coverage handling). Git FAQ is neither a
`CodeIndexType` subtype nor an `llm_routing_*` datasource and does not store TOC metadata. Source
configuration lives on `IndexInfo`.

## Acceptance Criteria

- User can add a Git FAQ data source by specifying repo, branch, and directory via the UI.
- Every selected `.md` file is parsed as one logical FAQ article and indexed as one or more chunks.
- Articles support both basic markdown and frontmatter (title: frontmatter → first H1 → filename;
  instructions: frontmatter or `Prompt Instruction:` marker; reference: frontmatter or path-derived).
- FAQ articles are split at H1-H3, joined in 3/1 windows, size-split, embedded, and indexed with the
  standard knowledge-base document structure.
- No FAQ table of contents is stored in index metadata and no FAQ LLM-routing processor is registered.
- Changing FAQ options in the UI correctly configures the backend; source changes force full reindex.
- An assistant with the FAQ datasource as context retrieves relevant sections through
  `SearchAndRerankKB` with source coverage handling.
- No regression for other datasources; Google Docs keeps its existing whole-document routing flow.
- Scheduled (cron) and webhook-triggered reindex work for FAQ datasources.
- Documentation updated (internal `.ai-run` guide + post-release public docs PR to codemie-ai/docs).

## Linked Artifacts

- docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/spec.md
- docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/technical-analysis.md
- docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/plan.md
- docs/superpowers/tasks/2026-08-29-epmcdme-11696-faq-git-datasource/complexity-assessment.json
- Superseded B1 history: docs/2026-08-28-faq-git-datasource-analysis.md,
  docs/2026-08-28-faq-git-datasource-b1-checklists.md,
  docs/2026-08-29-faq-git-datasource-implementation-plan.md, .zcode/faq-fixture/

## History

| When | Event | Detail |
|---|---|---|
| 2026-09-03T00:00:00Z | reconciled | Story, spec, plan, work item, internal guide, and MCP wording aligned to the selected Option A architecture; B1 artifacts marked as superseded history. |
| 2026-09-02T11:56:17+03:00 | finalized | Feature commit recorded as `EPMCDME-11696: Git-based FAQ datasource with Confluence-style chunked hybrid retrieval`. |
| 2026-09-01T00:00:00Z | decided | Option A selected as plan of record: `knowledge_base_git_faq`, Confluence-style H1-H3 chunking, embeddings, and standard hybrid KB retrieval. |
| 2026-08-29T15:00:00Z | transitioned | Ready for review — all 9 tasks complete on EPMCDME-11696_git-faq-datasource (BE 9 commits, FE 1 commit, SDK 1 commit); evidence/pr-1..9.json + live-verification.json recorded; awaiting team-lead review of deferred spec/plan approvals |
| 2026-08-29T14:35:00Z | prototype verified | Superseded B1 whole-document/TOC prototype passed its local smoke; retained as historical evidence in `evidence/live-verification.json`. |
| 2026-08-29T00:00:00Z | created | Canonical work item seeded from story EPMCDME-11696 with amended scope (B1 architecture). Full codebase analysis, corpus validation (docs-main/faq, 269 files), and implementation plan completed beforehand; artifacts seeded into the task folder. Spec/plan approval gates intentionally deferred — reconcile via `mode: "sync"` when team-lead review happens. |
