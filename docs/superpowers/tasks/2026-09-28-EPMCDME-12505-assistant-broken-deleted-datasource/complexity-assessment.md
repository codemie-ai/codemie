## Implementation Analysis: EPMCDME-12505

### Size: M (19/36)

### Dimension Scores:
| Dimension            | Score | Label |
|----------------------|-------|-------|
| Component Scope      | 4     | L (bumped from M) |
| Requirements Clarity | 4     | L (bumped from M) |
| Technical Risk       | 3     | M |
| File Change Estimate | 4     | L |
| Dependencies         | 1     | XS |
| Affected Layers      | 3     | M |

### Key Reasoning:
- **Component Scope (L)**: The failing check sits in the shared `build_agent` path. The fix touches the router error mapping (`_create_assistant_error` always returns 500), `AssistantService.check_context`, and the ToolkitService / ToolkitSettingsService CODE-context paths. It may also touch ToolExecutionService, AssistantFactory (sub-assistants), AssistantVersionService, and for option B the index delete router and ProviderDatasourceDeletionService.
- **Requirements Clarity (L)**: The outcome is concrete: no 500 and regression coverage. The solution is still open between auto-drop at init (A), cleanup on delete (B) and a controlled 4xx (C). Two more choices are open: persist the dropped reference or filter it only for the request, and how the user is notified.
- **File Change Estimate (L)**: About 4-7 source files plus 4-6 test files. `test_assistant_service_check_context.py` must be rewritten.
- **Technical Risk (M)**: Precedent exists. `_filter_invalid_datasources` already drops invalid contexts on create and update, the KB and provider tool builders already skip missing indexes, and guardrail cleanup on delete is precedent for B. The risk is in edge paths: CODE contexts raise a `ToolException` or `AttributeError`, stored versions can bring deleted references back, sub-assistants cascade to the parent, and there are four inconsistent existence checks. There is no schema change or auth change.

### Red Flags Applied:
- Component Scope M to L: affects multiple agents and entry points (streaming, sync, background, resume, hedged, A2A, health check, and sub-assistants through AssistantFactory).
- Requirements Clarity M to L: the scope decision is explicitly open (the TBD equivalent), and the options differ materially.

### Affected Layers:
API/router + Service + Model (read-only `IndexInfo` lookups; option B mutates the JSONB `context` data, with no schema change or migration). Not cross-system.

### Routing:
superpowers:brainstorming. The A/B/C scope decision should be settled there.
