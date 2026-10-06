# Spec: Concise vs. Full DOCX/PDF Conversation Export (EPMCDME-14774)

## Context

The codemie-ui frontend (branch `hide-tool-outputs-docx-pdf-export`, already merged) calls
`GET v1/conversations/{id}/export?export_format={docx|pdf}&include_tool_outputs={true|false}`.
The backend does not yet read this parameter: `export_conversation`
(`src/codemie/rest_api/routers/conversation.py:820-861`) always produces today's full export for
`pdf`/`docx`, and `json` export is untouched and stays that way (no parameter is ever sent for
`json`).

## Decision: what "concise" means

Concise mode drops **all** `Thought` nodes unconditionally for each message pair — no thought
heading, no `Input`/`Output` sub-heading, no thought content of any kind. The surviving document
contains, per pair: the `User` heading + the user message, and the `CodeMie Assistant` heading +
the final `ai_message.message` — **except** when that pair's `ai_message.message` is empty and all
of its substantive content lived in thought/tool-output nodes; that pair is skipped entirely (see
Acceptance Criteria). This is a deliberate choice over reusing the existing
`ExportUtils.should_include_thought(thought, export_full_thought=False)` author-name heuristic
(which would keep "CodeMie Thoughts"-named entries) — that heuristic solves a different problem
(thought-vs-tool-step) and is left untouched and unused by this ticket.

**Confirmed, no change:** the query parameter name stays `include_tool_outputs`. Renaming it to
`include_thoughts` was considered (the semantic drift is real: concise mode now also drops thought
nodes, not just tool outputs) but rejected by human review to avoid a required frontend follow-up.
The parameter keeps its current name and wire contract for this ticket.

## Approach

1. **Router** (`export_conversation`, `conversation.py:820-861`): add
   `include_tool_outputs: bool = False` as a plain function parameter (no `Query`/alias — matches
   this router's existing convention, e.g. `remove_folder`'s `remove_conversations: bool = False`,
   and matches the frontend's wire contract exactly). Read only by the `pdf`/`docx` branch of
   `_get_streaming_response` (`conversation.py:864-926`); the `json` branch does not consume it.
2. **`MessageExporter.__init__`**: add the same-named boolean parameter, defaulting to `True`
   (include tool outputs / today's behavior). The default of `True` — not `False` — is intentional:
   it preserves current unconditional-full behavior for every caller that does not pass the
   parameter explicitly, in particular the per-message export path
   (`export_conversation_message` → `build_single_message_document`) and every existing test
   fixture that constructs `MessageExporter`/`DocumentBuilder` directly. Only the full-conversation
   router path passes the flag explicitly, forwarding the query param's value (default `False`).
3. **`DocumentBuilder`**: thread the flag from `MessageExporter` down to
   `build_conversation_document` → `_add_message_pair_to_document`. Gate the existing call that
   builds the thought section: when the flag is `False`, skip calling `_add_thoughts_to_document`
   entirely for that pair (no thought nodes are constructed or filtered); when `True`, the call
   happens exactly as it does today (unchanged filtering via `should_include_thought`'s default).
   In the `False` branch, additionally skip emitting the pair's `User`/`CodeMie Assistant` headings
   and bodies altogether when `ai_message.message` is empty (see Acceptance Criteria) — no heading,
   no empty body, no placeholder text for that pair. The skip is conditional on concise mode being
   what emptied the pair: at least one of its thoughts must pass `ExportUtils.should_include_thought`,
   i.e. would have rendered in full mode. A pair whose thoughts are all non-exportable anyway renders
   identically in both modes, so its user question must survive concise mode.
4. **No change** to `ExportUtils.should_include_thought`, `build_single_message_document`, or the
   `json` export path.

Formatting (headings/lists/tables/links/code blocks) is unaffected by this change: Pandoc renders
whatever markdown remains in `ai_message.message`/surviving nodes exactly as it does today.
Concise mode changes *what content is present*, not how surviving content is serialized.

## Acceptance Criteria

- `GET .../export?export_format=docx` (or `pdf`) with `include_tool_outputs` omitted or `=false`
  returns a document where, for every message pair, only the user heading+message and the
  assistant heading+final message appear — no thought-derived heading or content of any kind.
- In concise mode, for a message pair whose `ai_message.message` is empty and whose only
  substantive content lived in thought/tool-output nodes, the pair is skipped entirely — no
  `User`/`CodeMie Assistant` heading, no empty body, and no placeholder text is emitted for that
  pair. The no-placeholder rule is **per pair**. It does not extend to the whole document: when
  *every* pair is filtered out, the export emits a single document-level explanatory paragraph
  ("No content to export in concise mode.") in place of an otherwise empty file, so a user who
  receives a conversation of nothing but tool output gets an intelligible document rather than a
  blank one. A pair-level placeholder is still forbidden.
- Same endpoint with `include_tool_outputs=true` returns a document identical to today's current
  full-export behavior (unchanged).
- `export_format=json` behavior is unaffected whether or not `include_tool_outputs` is present on
  the query string (the frontend never sends it for `json`; the backend must not error if it is
  sent anyway).
- The per-message export endpoint (`.../history/{history_index}/{message_index}/export`) and any
  direct `MessageExporter`/`DocumentBuilder` construction that omits the new parameter retain
  today's unconditional-full behavior (regression guard on the shared constructor's default).
- Headings, lists, tables, and links already present in the content that survives filtering
  continue to render correctly in both DOCX and PDF output for both variants. Code-block rendering
  is verified correct in DOCX for both variants. Code-block rendering in **PDF** is **not** claimed
  as met by this ticket: it is affected by a known pre-existing, unrelated defect (see Open Risks)
  that this ticket neither introduces nor fixes, because it does not touch code-block
  serialization.
- New router-level tests cover `pdf` and `docx` × {omitted, `false`, `true`} for
  `include_tool_outputs`, asserting thought content is absent/present as expected in the generated
  document (serialized Pandoc tree or rendered text), closing the existing zero-coverage gap on
  this branch, and including a case for the empty-pair-skip rule above.

## Non-Goals

- The per-message export endpoint and `build_single_message_document` are not changed.
- The `json` export format and its serialization path are not changed.
- `ExportUtils.should_include_thought`'s `export_full_thought` heuristic is not modified, removed,
  or repurposed for this ticket — it remains exactly as it is today, unused in production.
- No new Pandoc node types (`List`/`Table`/`Link`) are introduced; formatting preservation relies
  entirely on Pandoc's existing markdown rendering of unmodified surviving content.
- No new query parameter validation beyond a plain boolean (no enum, no alias); the parameter is
  not renamed (see Decision above).
- Fixing the pre-existing PDF code-block rendering defect (the `--listings`/`fvextra` mismatch
  described in Open Risks) is explicitly out of scope for this ticket. It is being filed as a
  separate ticket.
- Any query-layer or DB-read-layer filtering to address large-payload export failures is out of
  scope for this spec (see Open Risks — the underlying claim is unverified, and this spec's fix,
  which operates purely at document-rendering time, would not address a failure occurring earlier
  in the pipeline).

## Open Risks

- Zero pre-existing router-level test coverage exists today for the `pdf`/`docx` branch of this
  endpoint; new tests introduced for this ticket are the first to exercise it, so there is no
  existing template to copy beyond the `json`-only pagination tests and the isolated
  `ExportUtils` unit tests.
- The shared `MessageExporter`/`DocumentBuilder` constructor now carries a parameter consumed by
  two call sites (full-conversation, in scope; per-message, out of scope) — implementers must
  verify the per-message path's existing test suite still passes unchanged to confirm the default
  did not leak into that path.
- **PDF code-block rendering is a pre-existing, unrelated defect, confirmed in this session by
  hands-on testing against production pypandoc settings, independent of this ticket's changes.**
  The PDF pipeline's `--listings` pandoc argument makes pandoc emit LaTeX's `lstlisting`
  environment, but the header-includes actually configure `fvextra`'s `Highlighting`/`Verbatim`
  environment instead — so `lstlisting` gets no font/spacing configuration and renders visibly
  letter-spaced garbage (e.g. `def g r e e t ( name ) :`). Verified via `pdftotext` and a rendered
  page image against current `main`, before any change from this ticket. This ticket only skips
  `_add_thoughts_to_document`; it does not touch code-block serialization and therefore neither
  causes nor fixes this defect. Being filed as a separate ticket; the "preserve formatting"
  acceptance criterion above is scoped to exclude PDF code-block rendering rather than silently
  claim it as met.
- **The ticket's claim that "large tool outputs can cause failed exports" is UNVERIFIED.** Jira
  access in this session was scoped to a different project and could not reach EPMCDME-14774 to
  confirm whether this claim implies a payload-size acceptance criterion. If the real failure mode
  is upstream of rendering — a DB read or the in-memory conversation object exceeding some limit —
  this spec's plan (skipping `_add_thoughts_to_document` at render time) does not address it; that
  would require separate query-layer filtering work not covered here.
- A repo-wide search found no consumers of `GET /conversations/{id}/export` besides the codemie-ui
  frontend and the two internal router handlers (single-message export, full-conversation export)
  already discussed in this spec. Flipping the default to `False` (concise) at the
  `MessageExporter` per-call-site level as described in Approach step 2 is considered safe on that
  basis, but external API consumers outside this codebase's visibility cannot be fully ruled out.
