- Loading configuration...
✔ Connected to CodeMie
**Summary:** Backend: step scope and integration alias for script tool calls (codemie)  
**Issue type:** Sub-task  
**Status:** In Progress  
**Parent epic:** Programmatic Tool Composition (EPMCDME-15364)  
**Labels:** `AI-Generated`, `AI/Run`, `codemie_core`, `codemie_implemented`

**Full description (verbatim):**
```text
h2. Scope

Backend part of EPMCDME-15568 (repository: codemie). Policy for the script execution step of a workflow: the author lists the tools the script may call and, per tool, the integration aliases it may use. See the parent story for decisions and acceptance criteria.

h2. Work

* Add the optional {{integration_alias}} to the payload ({{_parse_payload}}), {{authorize_tool_call}}, the {{resolve}} signatures of the scopes, {{WorkflowToolResolver}} and {{resolve_workflow_tool}}.
* Add it to the SDK ({{call_tool}}, {{call_tools}} items) and to the SDK reference in the script tool's description, with a contract test. No new error codes ({{tool_unavailable}} for a tool outside the map, {{bad_arguments}} for an alias outside its list or missing when several are listed).
* A map on the step (tool name to a list of aliases) read by the workflow scope: resolves only listed tools, checks aliases ({{find_setting_for_tool}}, the creator's id for a global workflow, the running user's integrations otherwise); a step with no map calls no tool. {{callable_tools()}} returns the listed tools for the generator and the test run.
* The field on {{WorkflowTool}} and its validation at save time (reuse {{_is_integration_alias_valid}} and the tool checks in {{workflows/validation/resources.py}}): the tool exists and is script-callable, the alias exists for the author, is in the workflow's project and fits the tool's credential type.
* Publish warning for a global workflow whose script step lists aliases, next to {{collect_consumer_slot_integration_warnings}}.
* Refuse an alias in the assistant scope (chat and workflow assistant step) with {{bad_arguments}}.
* Tests for each acceptance criterion of the parent story; update the SDK page and the code executor README (accepted risk for assistants, no owner control).
```

**Acceptance criteria (from parent story EPMCDME-15568, verbatim):**
```text
* Given a script step with no map (or an empty map), when its script calls any tool, then the call fails with {{tool_unavailable}} and the tool is not run.
* Given a map listing tool T, when the script calls T, then it runs; given a tool not in the map, then {{tool_unavailable}}.
* Given tool T listed with no aliases, when the script calls it, then it runs under the running user's automatic lookup in the workflow's project; an alias in that call gives {{bad_arguments}}.
* Given T listed with aliases [a, b] in a global workflow, when the script passes a, then T runs under the creator's integration a; when it passes c, then {{bad_arguments}} listing a and b and T is not run; when it passes none, then {{bad_arguments}}.
* Given T listed with the single alias a, when the script passes none, then T runs under a.
* Given a workflow that is not global, when the script passes a listed alias, then T runs under the running user's integration of that alias in the workflow's project; if that user has none, a clear error.
* Given the script text, a run-start file or a model-written script names an alias the step does not list, when it runs, then the creator's account is not reached.
* Given a workflow is saved, when the map names a tool that does not exist or is not script-callable, or an alias that does not exist for the author, is outside the workflow's project or does not fit the tool's credential type, then the save is refused with a message naming the entry.
* Given a global workflow whose script step lists aliases is published or saved as global, then a warning says that the tool runs under the author's integration for everyone who runs the workflow.
* Given a chat or a workflow assistant step, when a call carries an alias, then {{bad_arguments}} and the tool is not run; calls without an alias behave as before.
* Given the test run and the generator of a script step, then they offer and accept only the tools of the map.
* Given the editor of a workflow script step, then the author picks tools and, per tool, aliases from their own integrations; an empty selection is shown as "no tools".
```

The sub-task description has no separate acceptance-criteria section; it points to its parent story, whose criteria are reproduced above.
