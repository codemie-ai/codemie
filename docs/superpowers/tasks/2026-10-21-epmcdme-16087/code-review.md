---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: c73a8072c48ef60a1f6aa57a86b6cfa64a648923
---

# Code review — 2026-10-21-epmcdme-16087

**Decision:** approve (check round, confidence final high · check high)  
**Reviewed head:** `c73a807` · **Rounds:** final → check

**Closed:** 1 fixed · 0 moot · 0 decided

The only prior blocking finding, CR-001, is resolved: in the current source, _HardenedXMLParser registers instances in a weakref.WeakValueDictionary keyed by id(self), and _force_hardened_parser checks membership with `get(id(parser)) is parser`, so the parser's own __hash__/__eq__ is never called and an __eq__-only subclass constructs normally. Guarded subprocess tests cover the __eq__-only subclass (it constructs, parses, blocks XXE) and the __bases__[0] hash/eq spoof (it is not trusted). The fix is uncommitted working-tree changes on top of reviewed_head 180d0d2, and the tests were not run because that is Stage 7 evidence.

## Open — 0

## Pre-existing (not blocking) — 8

- **Mutable guard state reachable via closures** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:802` — `_SAFE_XML_KWARGS` (mutable dict) and `_hardened_parsers` are reachable through `etree.XMLParser.__init__.__closure__` / `etree.iterparse.__init__.__closure__`. A script can clear/flip the kwargs or `.add()` an unsafe parser to the tracked set. Pre-existing: the tracked set was already reachable through wrapper closures before the change, and the re-init bypass (spec non-goal) already gives a tracked unsafe parser. An immutable mapping or inline literals would still harden the new dict.
- **Tracked parser re-init re-enables unsafe options** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:811` — calling the raw base `__init__` on a tracked parser with `resolve_entities=True, load_dtd=True, no_network=False` leaves it in `_hardened_parsers`, so `_force_hardened_parser` trusts it. Pre-existing: the spec lists this bypass as a known pre-existing non-goal.
- **Feed-parser API bypasses parser replacement** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:821` — `etree.XMLPullParser(load_dtd=True, resolve_entities=True)` and raw-base parsers driven via `feed()`/`close()` never reach `_force_hardened_parser`, so external entities resolve. Pre-existing: XMLPullParser was never patched (the spec makes it a non-goal), and before the change `type(etree.XMLParser())` already exposed the raw feedable class.
- **Copied or built-in parsers silently replaced** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:832` — `parser.copy()` of a hardened parser and lxml's built-in `ETCompatXMLParser` are untracked, so the wrappers swap them for a default parser and options like `remove_blank_text`, `recover` and `target` are lost. Pre-existing: the old tracking set had the same untracked-means-replaced rule for copies and built-in subclasses.
- **Hash/eq spoof passes tracking membership** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:832` — `parser in _hardened_parsers` matches on hash/`__eq__`, so an unsafe raw-base subclass with `__eq__` always True and a `__hash__` copied from a live tracked parser is trusted. Pre-existing: the old strong-ref set used the same hash/eq membership, and `type(etree.XMLParser())` already exposed the subclassable raw type. The fix is identity tracking (an id-keyed WeakValueDictionary checked with `is`).
- **Entry points beyond fromstring/XML/parse unwrapped** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:841` — a raw-base parser passed to `etree.XMLID`, `XMLDTDID`, `parseid` or `ElementTree(file=..., parser=raw)` is used directly, which gives XXE. Pre-existing: the wrapper loop is unchanged by this diff, and the raw type was reachable before it via `type(etree.XMLParser())`.
- **lxml.html parsers replaced with strict XML parser** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:841` — `lxml.html.fromstring`/`document_fromstring`/`parse` (and `pandas.read_html` with lxml) pass lxml.html's `HTMLParser` to the patched `etree.fromstring`/`etree.parse`, which replace it with `_HardenedXMLParser`. Non-XML HTML then fails with XMLSyntaxError. No test parses real HTML. Pre-existing: the unchanged wrapper already replaced any HTMLParser. This change only makes `lxml.html` importable (AC3), so the parse path is now reachable.
- **Raw iterparse reachable via __bases__[0]** — `src/codemie_tools/data_management/code_executor/filesystem_policy.py:865` — `etree.iterparse.__bases__[0](src, load_dtd=True, resolve_entities=True, no_network=False)` parses unhardened, and no `_force_hardened_parser` equivalent exists for iterparse. The standards audit flagged this as blocking. Pre-existing: before the change the function wrapper returned a raw iterparse instance, so `type(etree.iterparse(src))` exposed the same class with no guard. The spec excludes hiding originals reachable via `__bases__`. Its "untracked parsers are still replaced" reason does not cover iterparse, so this still needs a real fix.

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Approach: XMLParser replaced by hardened subclass forcing 4 safe kwargs after caller defaults, then registering the instance | pass | filesystem_policy.py:810-816 |
| Approach: iterparse replaced by hardened subclass forcing the same four kwargs | pass | filesystem_policy.py:865-870 |
| Approach: parser tracking keeps its role; storage moved to weakref.WeakSet; stale no-weakref comment removed | pass | filesystem_policy.py:808, 831-839 |
| Approach: _ORIGINALS and fromstring/XML/parse wrappers unchanged; _hardened_default_parser builds the new subclass | pass | filesystem_policy.py:818-819, 841-859 |
| AC1: XMLParser and iterparse subclassable under guard; isinstance holds | pass | new subclass/isinstance tests; post-fix local run PASS |
| AC2: plain XMLParser construction/parsing and iterparse iteration work | pass | new tests; post-fix local run PASS |
| AC3: import lxml.html succeeds under the guard | pass | test_guard_lxml_html_imports; HTML parsing via lxml.html deferred as pre-existing wrapper behaviour |
| AC4: FontTools-style subclass imports, constructs, is tracked, blocks XXE | pass | test_guard_lxml_fonttools_style_subclass_is_tracked_and_blocks_xxe |
| AC5: matplotlib writes non-empty one-page and PdfPages PDFs under guard | pass | test_guard_matplotlib_writes_single_and_multipage_pdf; post-fix pdf_export Succeeded |
| AC6: XXE blocked for default/unsafe-kwargs/user-subclass/untracked/unhashable parsers and iterparse; billion-laughs rejected | pass | new + existing guard tests; post-fix local run 16/16 |
| AC7: existing test_filesystem_policy.py and test_sandbox_guard.py tests pass unchanged | pending-stage-7 | diff adds tests only; suite execution is Stage 7 evidence |
| AC8: new tests follow _run_guarded* subprocess pattern and run on Linux | pending-stage-7 | pattern followed; Linux run is Stage 7 evidence |
| AC9: post-fix Jobs-path verification recorded under 'After the fix' (reporter all Succeeded, lxml_checks 15/15, pdf_export with PDFs) | pass | local-verification.md:151-195: reporter all Succeeded, lxml_checks 16/16 (one added check), pdf_export Succeeded with one_page.pdf and multi_page.pdf artifacts |
| AC10: shared-path verification re-run after user approval, or 'not run' with reason recorded | pass | local-verification.md:103 records user decision 'not run' with rationale (shared pod /tmp read-only, venv lacks lxml) |

Standards: commit-format pass · code-quality pass · security pass
