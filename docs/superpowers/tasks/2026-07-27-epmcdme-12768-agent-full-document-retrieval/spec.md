# Retrieval results do not describe their own completeness

## Problem

Within a single conversation, only the first request reaches the full content of a
retrieved document. On later requests the model does not obtain the subsequent parts, and
it does not attempt to retrieve them: it treats what it already has as the complete
answer. When the retained content is incomplete, the model may present invented content
in place of the missing part rather than reporting a gap or querying again.

This is the second link of EPMCDME-12768. The first link — chunk identity lost during
indexing, so retrieval collapsed a file to a single chunk — is fixed and verified. With
that in place the opening request of a conversation answers correctly, which isolates the
remaining defect to what happens on subsequent requests.

## Root cause

The knowledge-base search response carries no information about its own completeness.

`SearchKBTool.format_response` emits the routed source list followed by one block per
retrieved chunk. Each block states its source and content. Nothing states how many parts
the document has, how many of them are present, or that further parts can be obtained. The
only positional signal is a numeric suffix appended to the source string, which is never
explained and is indistinguishable from part of the identifier.

A complete response is therefore indistinguishable from a partial one. The model receives
a set of self-contained, well-formed blocks and reasonably concludes it has been given the
answer. It has no basis for asking again, and no indication that asking again with a
narrower query would surface different parts — repeating the same query would not.

Conversation-history replay compounds this. A past tool result is cut to a fixed character
budget, and the cut lands mid-list: the surviving text ends with blocks that still look
complete, followed by a bare technical marker. The marker states neither how much was
removed nor what to do about it, so it does not restore the missing signal.

The two effects share one cause. The response describes what was found but never what was
*not* found, so neither the model nor the replay layer can tell a whole result from a
fragment.

## Approach

Make the retrieval response self-describing, and make any later reduction of it explicit
rather than silent.

### The response states its own scope

The search response declares the shape of what it returns: for each source, how many parts
exist and which of them are included, and that the remainder can be obtained by querying
again with a narrower request. This holds for complete and partial responses alike, so the
two are never confusable.

This is the primary change. It fixes the case where nothing was truncated at all — where
the model still could not tell that a document had further parts.

### Reduction happens at block boundaries and says so

Where a response is shortened for replay, whole blocks are kept or dropped rather than the
text being cut at an arbitrary offset, and the result states what was omitted. A shortened
response must remain a truthful description of itself.

### The statement is actionable and forbids invention

The notice accompanying a partial response instructs the model to obtain missing parts by
querying again with a more specific request, and not to supply the missing content from
its own knowledge. The present marker is a technical suffix carrying neither instruction.

### Deliberately not done

The retained-length limits are not raised as the primary remedy, and the flag that exempts
one tool from truncation entirely is not extended. Both were considered and rejected: they
move the point of failure rather than removing it, they leave the no-truncation case
unfixed, and enlarging what is carried through history works against the chunking design,
whose purpose is to retrieve the relevant part on demand rather than to hold whole
documents in context.

## Acceptance criteria

- A repeated request within one conversation obtains document content that the earlier
  response did not include.
- A response that omits part of a document states that it is partial, and a response that
  includes everything is distinguishable from one that does not.
- A partial response conveys what is missing and how to obtain it.
- The model is instructed not to substitute its own content for a missing part.
- Shortening a response for replay preserves whole blocks and leaves the response an
  accurate description of itself.
- Responses that omit nothing gain no misleading completeness claims.

## Testing

Written failing first. The affected surface has no assertions today: neither response
formatting for completeness, nor the replay length resolver, nor the truncation marker,
and every existing fixture uses outputs short enough that no limit is exercised.

1. **Partial response is declared partial.** A response covering some parts of a document
   states that further parts exist.
2. **Complete response is declared complete.** A response covering every part carries no
   claim of omission.
3. **Replay keeps blocks whole.** Shortening for replay drops entire blocks rather than
   cutting inside one.
4. **Shortened replay states its own reduction.** The shortened result reports what it no
   longer contains.
5. **Notice is actionable.** A partial response carries the instruction to query again and
   the prohibition on inventing the missing content.
6. **Unaffected tools unchanged.** Tools outside this path keep their present behaviour.

## Verification

Confirmed against a real datasource: ask for material located near the end of a long
document, first as the opening request of a conversation and then as a later request in
the same conversation. Both must be answered from retrieved content, and neither may be
answered with invented content.

The assistant used for verification must not carry instructions that suppress further tool
use, since such instructions mask the behaviour under test.

Note for the QA baseline: this repository has pre-existing unit-test failures on `main`
unrelated to this area. Compare against that baseline rather than expecting a fully green
suite.

## Related finding, not in scope

With a permissive assistant prompt, an incomplete context led the model to present invented
content. That is a property of incomplete context generally, not of this retrieval path
alone, and it is recorded here only as motivation for the "forbids invention" requirement
above. Handling it across all tools belongs in its own ticket.
