# Backend: Deselectable Datasource Default Tools — Story

**Date**: 2026-10-16
**Status**: Approved
**Ticket**: —
**Parent**: ../../story.md
**Feature**: datasource-default-tools-selection

---

## Context

- The backend adds "Get Repo Tree" and "Search Code" for every code datasource and "Search Knowledge Base" for every knowledge base datasource when the assistant runs. The stored assistant does not mention them, and the tool listing used by the assistant form omits them.
- The "with filtering" variants and other code tools are selected per assistant and apply to all code datasources. Selecting a "with filtering" variant silently replaces the standard one at runtime.
- The direct tool-invoke API has no selection check for knowledge base or standard code tools, and only considers the assistant's first datasource.
- The standard "Get Repo Tree" description tells the model to call it first, and the standard code search description points back to the repo tree.
- Assistant versions, export/import and workflow-referenced assistants share the stored assistant configuration. Workflows also have a separate YAML-only setting that removes both default code tools; it stays unchanged.
- Design decision taken for this feature: the assistant records which default tools were deselected, as one list per assistant that is empty by default. Existing assistants therefore keep today's behaviour with no data migration of their tool selection. Smart tool selection, which only runs when no tools are selected, is not affected.
- This story is delivered first. The SDK and UI stories depend on it.

---

## Complexity

**Size**: L (22/36) · **Recommended flow**: sdlc-standard

A dozen-plus files plus a database migration across models, versioning, tool assembly, listing and direct invoke. There is no exact precedent for an assistant-level deselect, and the direct-invoke rejection is a security-relevant gate.

---

## Story

**As an** assistant author working with large code repositories and knowledge bases, **I want** the platform to remember which datasource default tools I deselected and stop offering them to the assistant, **so that** I can prevent expensive tools such as the repository tree from being used on a huge monorepo.

---

## Background

Philips indexes a very large monorepo as a code datasource. The repository tree tool pulls the whole tree into context, which makes assistants costly on that repo. Authors currently work around it with system prompts, which still send the tool and its "call me first" description with every request. The backend must let an assistant opt out of each default tool and honour that everywhere the assistant configuration is used.

---

## Acceptance Criteria

- [ ] Given a client reads the tool listing used by the assistant form, when the listing is returned, then "Get Repo Tree" and "Search Code" (standard) appear under Codebase Tools and "Search Knowledge Base" appears under Knowledge Base tools.
- [ ] Given an assistant that has deselected "Get Repo Tree" and is saved, when a user chats with it, then no repository tree tool (standard or "with filtering") is available for any of its code datasources.
- [ ] Given an assistant that has deselected "Search Code" or "Search Knowledge Base", when a user chats with it, then that tool is not available for any of its datasources of that type, and the other default tools remain available.
- [ ] Given an assistant with code or knowledge base datasources and no recorded deselection, including every assistant created before this change, when it is chatted with or run in a workflow, then its default datasource tools behave exactly as today.
- [ ] Given "Get Repo Tree" is deselected, when the assistant uses the remaining code tools, then no remaining tool description instructs the model to call the repository tree first.
- [ ] Given an assistant with a deselected default tool, when a new version is saved, a previous version is restored, the assistant is exported and imported, or a workflow step references the assistant, then the tool stays deselected.
- [ ] Given an author changes only the set of deselected default tools, when the assistant is saved, then the change counts as a configuration change and is captured in a new version.
- [ ] Given a default tool is deselected for an assistant, when a client invokes that tool directly through the API for this assistant, then the request is rejected as the tool not being available.
- [ ] Given a save request that selects both the standard and the "with filtering" variant of the same tool, when it is submitted, then it is accepted and, as today, the "with filtering" variant is the one in effect at runtime.
- [ ] Given a save request that names a tool that is not one of the default datasource tools in the deselected list, when it is submitted, then it is rejected with a clear message and nothing is saved.

---

## Out of Scope

- Python/Node SDK changes and UI changes (separate stories).
- Changing which tools exist, or removing the repository tree tool from the platform.
- New code navigation tools.
- Per-datasource tool selection; selection is per assistant.
- The workflow YAML setting that excludes default context tools.
- Provider datasource tools, Git tools and MCP tools.
- Platform-wide or project-wide policies that disable a tool for all assistants.

---

## Decisions

- D1: What if a payload selects both the standard and the "with filtering" variant? → No new validation; existing runtime behaviour stays (the "with filtering" variant replaces the standard one). Both can already coexist today. — user
- D2: Should the server apply defaults or warn when an assistant is created with a datasource and no tool selection? → No. Nothing deselected means all default tools are on, so behaviour matches today. — user
- D5: How should the tool-search index and the assistant generator treat the newly listed default tools? → Like any other tool: indexed, suggestible and valid. — user
- D6: Should direct tool invoke consider only the first datasource or all of them? → All of the assistant's datasources, since selection is per assistant. — user
- D7: Does saving an assistant accept the knowledge base toolkit name, and do versions, export/import and workflow-referenced assistants carry the deselected list? → Yes, required by the acceptance criteria above. Tracing these paths is the first implementation step, not an open product question. — proposed by PO: the criteria already state the required outcome; confirming the code path is verification work

---

## Open Questions

- None
