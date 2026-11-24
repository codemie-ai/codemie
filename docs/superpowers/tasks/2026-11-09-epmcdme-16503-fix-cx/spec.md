# EPMCDME-16503 correction requirements

The user accepted `docs/EPMCDME-16503/fix-plan-cx.md` in the parent workspace on 2026-11-09. This correction run uses that plan and the existing 16503 specification. It does not replace the previous delivery record.

1. Include the EPMCDME-16499 engine fix as a base dependency. Remove crash tolerance and harness skip gates. Check the real reduced context for AC-40.
2. Parse Jinja with the runtime environment settings. Collect only free plain and dotted output references at each occurrence. Keep filters, calls, subscripts, and control-tag expressions excluded. Never execute templates during analysis.
3. Produce immutable semantic source locations at the point where the configuration owner is selected. Carry them through derivation, findings, policy, report serialization, and copies. Keep consumer state IDs and diagnostic paths unchanged.
4. Compare the abstract effects with real node writes, finalization, reducers, transforms, and iteration paths. Assert compatible guarantees and explicit uncertainty, not equality of full runtime values.
5. Correct the 16503 specification and plan. Create the 16503 part of parent specification v6.1 and preserve v6.0.

No new runtime contract checks, general Jinja evaluation, sub-workflow analysis, REST issue fields, YAML line lookup, or engine redesign is included.

## Source-location interface for 16504

`ContractLocation` is immutable. Its `section` is `states`, `tools`, `custom_nodes`, or `workflow`; `owner_id` identifies a section item and is absent for the workflow root. `segments` is a tuple of strict strings and integers. String keys such as `"01"` remain distinct from integer index `1`. `None` explicitly means that no source location is known. Internal locations are not passed through message sanitization or truncation.

Non-empty state bindings replace tool bindings. Empty or absent state bindings use tool bindings. An implicit binding with no source entry points to the tool item. A missing mandatory parameter in explicit bindings points to the selected `tool_args` parent. `consumer_state_id` always identifies the consuming state.
