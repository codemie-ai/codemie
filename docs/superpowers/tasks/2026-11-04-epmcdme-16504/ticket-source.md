## EPMCDME-16504

**Link:** https://jiraeu.epam.com/browse/EPMCDME-16504

| Field | Value |
|---|---|
| **Summary** (verbatim) | Save-time Contract API Surface: Issues, Coverage, Parity and the New Endpoint (codemie) |
| **Issue type** | Sub-task |
| **Status** | In Progress |
| **Assignee** | Oleksandr Cherevach (Oleksandr_Cherevach@epam.com) |
| **Parent** | [EPMCDME-16500](https://jiraeu.epam.com/browse/EPMCDME-16500): Workflow Node Contract Validation — 2. Save-time Validation (Story, In Progress) |
| **Epic Link** | None |
| **Issue links** | None |
| **Attachments** | complexity-assessment.json, story.md, technical-analysis.md |

### Description (verbatim, excluding Acceptance Criteria)

**Context**
- The existing save and dry-run pipeline stops at the first failing validation layer; the new contract-linking stage runs after those layers pass and must return every contract error at once, not stop at the first one.
- The existing warnings list on save and dry-run responses is untyped and carries no severity concept; a WARNING and an UNVERIFIED notice have no way to be told apart today without breaking a client that already reads that list.
- No save or dry-run response carries a coverage summary today, and the only existing dry-run endpoint needs a workflow that has already been saved and write permission on it — there is no way to check a workflow before its first save.
- Create and update already normalise model ids and rewrite the configuration before validating it, while the existing dry-run validates the configuration as sent; these two paths diverge today and must be unified so every entry point produces the same result for the same input.
- No existing platform mechanism has the exact "two independent flags, on in development, off in production" shape this feature needs for its save-time half; a platform-level setting is the closer fit of the mechanisms that exist, compared with a per-customer feature toggle.

**Story**

*As a* caller of the workflow save and dry-run API — the visual editor, the YAML editor, an SDK or the CLI — *I want* contract findings and a coverage summary reported the same way through every entry point, every contract error returned together instead of one at a time, and a way to validate a workflow before it has ever been saved, all switchable by a platform flag, *so that* every caller sees the same outcome for the same workflow, whether it exists yet or not, and the platform can turn the feature on or off without a code change.

**Background**

This child is the API-facing half of save-time validation: the platform flag that switches the whole linker on or off, the new error type and issue fields the contract findings this story's linker sibling produces are reported through, the coverage object, the rule that a rejected save still returns every contract error together, parity between create, update and both dry-run shapes, and a new endpoint that lets a workflow be validated before it has ever been saved. Every finding's outcome and provenance already exist by the time this child runs; this child decides how they are shaped, aggregated and exposed.

**Out of Scope**
- Computing outcomes, provenance and the four-way classification of requirements: save-validation-codemie-linker (this child reports what the linker already decided).
- Editor controls, the issues panel, severity grouping and the field-path registry that consumes `config_path`: save-validation-codemie-ui.
- Documentation of the new keys, outcomes and coverage: save-validation-codemie-docs.
- Runtime input/output checks, runtime findings storage, and the separate runtime-checks platform flag: Story 3 (runtime validation).
- Typed SDK results, typed validation errors, and CLI reporting of the new fields: Story 4 (SDK work).
- Any change to engine behaviour: bug-fix-codemie.

**Decisions**
- D5 (parent): Error type identifier (OQ-2)? → One new contract-validation error type; save-validation-codemie-api fixes the string and save-validation-codemie-ui mirrors it in the whitelist — proposed by PO
- D6 (parent): Coverage on a rejected save (OQ-4)? → Returned on rejected saves too — proposed by PO: keeps dry-run and save shapes identical
- D7 (parent): WARNING vs UNVERIFIED representation (OQ-6)? → An additive, optional outcome indicator on each issue entry — proposed by PO: existing clients keep working
- D8 (parent): Is save-time backend work one subtask? → Two codemie subtasks: the linker (analysis and outcomes) and the API surface (issue fields, coverage, endpoints, flag) — proposed by PO

**Open Questions**
- None

**Constraints**
- Depends on save-validation-codemie-linker for the keys, per-requirement outcomes and provenance this child reports; save-validation-codemie-ui and save-validation-codemie-docs depend on this child in turn for the error type string, the issue field shape and the new endpoint. Source: parent story.md stories/subtasks table.
- Branch from `origin/main`, not from the current EPMCDME-15201 checkout: a brand-new, unrelated workflow-versioning surface (`GET/POST /workflows/{id}/versions[/rollback]`) landed very recently in the same router file this child extends; coordinate with it rather than duplicate it, and do not base this work on a branch that diverges from `origin/main` in unrelated ways. Source: parent story.md `## Constraints`; technical-analysis.md §6.
- No migration is expected for this child: issues and coverage are computed and returned per request, not persisted; the only new column the spec records for this feature's runtime half (findings storage, Story 3) is out of scope here.
- Remotes and review: codemie uses EPAM GitLab merge requests with `EPMCDME-####: Description` commits. Source: workspace AGENTS.md ("Layout"); parent story.md header.
- Produces for save-validation-codemie-ui and save-validation-codemie-docs: the fixed error type string, the issue field shape (`config_path`, the outcome indicator), the `coverage` object, and the new validate-before-save endpoint. Consumes from save-validation-codemie-linker: the per-requirement outcome and provenance. Source: parent story.md stories/subtasks table.

### Acceptance Criteria (verbatim)

**Platform flag**
- ☐ Given the save-time linker platform flag is off, when a workflow is saved or dry-run, then no contract analysis runs, no contract findings or coverage are returned, and saving behaves as today, except that the input contract's structural check still runs in every mode (negative) [AC-39 save half].
- ☐ Given the save-time linker platform flag is on, when a workflow is saved or dry-run, then the criteria below apply.

**Issue envelope**
- ☐ Given a contract validation finding, when it is returned, then it carries a single new error type identifier that distinguishes it from structural and cross-reference validation issues [AC-27, D5].
- ☐ Given a contract validation finding, when it is returned, then it carries the consumer's state id, the existing `path` field with its existing bare-field-name meaning, a new optional configuration path locating the requirement within the consumer's own configuration, a config line when available, an indicator distinguishing WARNING from UNVERIFIED (and ERROR within errors), and a message naming the producing or removing node or the unknown source [AC-27, D7].
- ☐ Given a client that does not read the new configuration-path field or the outcome indicator, when it receives a response carrying them, then it continues to work unchanged (negative) [AC-27].
- ☐ Given a contract validation finding, then it also carries the existing `details` field: `message` is a short title, and `details` names the producer, the removing node and mechanism, the sources checked or the unknown source. The editor shows `details` on the issue card and inline on the field [AC-27, spec §7.2]. *(added 2026-10-29 after the UI review, EPMCDME-16505)*
- ☐ Given a contract finding returned in `warnings[]` of a successful save or dry-run, then the entry carries `kind` set to the contract-validation error type, so a client that routes `warnings[]` by `kind` can tell contract findings from the existing `consumer_slot` and `script_step_owner_integration` entries. The editor relies on this to keep contract warnings out of toasts and in the issues panel [AC-31, spec §8.2]. *(added 2026-10-29 after the UI review, EPMCDME-16505)*
- ☐ Given each kind of requirement, then `config_path` uses one documented format, written as the path inside the consumer state's YAML: declared input `input_schema.properties.<field>`; tool binding `tool_args.<param>`; transform mapping `config.mappings.<index>.<field>`; output contract finding on a producer `output_schema`; node without an input contract under `require_input_contracts` `input_schema`; condition expression, switch case or iteration key: the path of that expression or key inside the state's `next` block. This table is part of this child's deliverable and is what the editor registers for field navigation; a path outside it makes the editor fall back to the YAML view [AC-27, AC-31, spec §7.2, §8.3]. *(added 2026-10-29 after the UI review, EPMCDME-16505)*

h4. All errors at once

* ☐ Given a workflow that passes the structural, schema, missing-states and resource layers, when it has several contract errors across the graph, then a rejected save returns every one of them in a single response \[AC-28\].
* ☐ Given a workflow that fails an earlier validation layer, when it is saved, then it is rejected by that layer exactly as it is today, before contract linking ever runs (negative) \[AC-28\].
* ☐ Given a save rejected by contract errors, when the response is returned, then it also carries every contract WARNING and UNVERIFIED notice found in the same run, in the same issue shape with the outcome indicator, at a documented place in the error envelope. The editor then shows the whole result after one save, instead of revealing the warnings only on the next save \[extends AC-28 and D6\]. _(added 2026-10-29 after the UI review, EPMCDME-16505)_

h4. Coverage and response shape

* ☐ Given a successful save or dry-run with the platform flag on, then the response carries a coverage object reporting the number of reachable states checked, the total reachable states, and the ids of the uncovered ones \[§7.4\].
* ☐ Given a save rejected by a contract error, when the response is returned, then it still carries the coverage object, the same as a successful save \[D6\].
* ☐ Given the same workflow configuration, the same user and the same project submitted through creation, update and both dry-run shapes, then all four return an identical {{warnings\[]}} list and an identical coverage object, and a clean dry-run guarantees that the corresponding save produces the same clean result \[AC-29\].

h4. Single pipeline

* ☐ Given a save or dry-run submitted through the visual editor, the YAML editor, an SDK or the CLI, then all of them invoke the same validation pipeline and receive identical outcomes and response shapes \[AC-30\].

h4. Dry-run before the first save

* ☐ Given a workflow that has never been saved, when its configuration and its project are submitted to a new validation endpoint that needs only read access to the project, then the response carries the same {{warnings\[]}} and coverage shape, with the same parity guarantee, as a save or a saved-workflow dry-run of the same configuration \[AC-41 backend\].
