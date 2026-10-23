# ServiceNow budget request flow

`POST /v1/integrations/servicenow/budgets`. Every request checks first and writes second: a request that fails a check changes nothing.

```mermaid
flowchart TD
    start(["POST /budgets"]) --> validate

    validate{"Validate request<br/>names · emails · amounts shape<br/>set: platform > 0<br/>cancel: amounts & admin_emails ignored"}
    validate -- invalid --> e422["422 Unprocessable"]
    validate -- valid --> find

    find{"Find project<br/>(read-only)"}
    find -- deleted --> e409["409 Project deleted"]
    find -- personal --> e403["403 Personal project"]
    find -- "found / not found" --> action

    action{"action"}
    action -- set --> setCheck
    action -- adjust --> adjCheck
    action -- cancel --> canCheck

    subgraph SET["SET — replace"]
        direction TB
        setCheck{"Project missing?"}
        setCheck -- yes --> setNames["Validate project name<br/>+ cost center code if names differ"]
        setCheck -- no --> setGroup
        setNames -- invalid --> e400a["400 Invalid name"]
        setNames -- valid --> setGroup["Read budget group<br/>target = request<br/>(missing categories → 0)"]
        setGroup --> setWrite["WRITE<br/>create cost center + project if missing<br/>assign admin_emails<br/>create or replace group"]
    end

    subgraph ADJUST["ADJUST — merge"]
        direction TB
        adjCheck{"Project missing?"}
        adjCheck -- yes --> adjNames["Validate project name<br/>+ cost center code if names differ"]
        adjCheck -- no --> adjGroup
        adjNames -- invalid --> e400b["400 Invalid name"]
        adjNames -- valid --> adjGroup["Read budget group<br/>target = current + request<br/>(no budget yet → current = 0)"]
        adjGroup --> adjPlatform{"platform > 0?"}
        adjPlatform -- no --> e400c["400 Platform required"]
        adjPlatform -- yes --> adjWrite["WRITE<br/>create cost center + project if missing<br/>assign admin_emails<br/>create or merge-update group"]
    end

    subgraph CANCEL["CANCEL — delete only"]
        direction TB
        canCheck{"Project missing?"}
        canCheck -- yes --> e404["404 Project does not exist"]
        canCheck -- no --> canGroup{"Budget group<br/>exists?"}
        canGroup -- yes --> canWrite["WRITE<br/>delete group<br/>(same call as the UI's<br/>DELETE /admin/project-budget-groups/:group_id)"]
        canGroup -- no --> canNoop["No-op"]
    end

    setWrite --> ok
    adjWrite --> ok
    canWrite --> ok
    canNoop --> ok
    ok(["200 · project, cost_center, created_*,<br/>budget, admins_assigned / admins_pending"])

    classDef error fill:#fde2e1,stroke:#d93025,color:#7a1c16
    classDef write fill:#e6f4ea,stroke:#1e8e3e,color:#0d652d
    classDef success fill:#e8f0fe,stroke:#1a73e8,color:#174ea6
    class e422,e409,e403,e400a,e400b,e400c,e404 error
    class setWrite,adjWrite,canWrite write
    class ok,start success
```

## At a glance

| | `set` | `adjust` | `cancel` |
|---|---|---|---|
| Project missing | create it (+ cost center if names differ) | create it (+ cost center if names differ) | 404 |
| Budget missing | create from request | create from request (merge with nothing) | 200, no-op |
| Budget present | replace: categories not sent are removed | merge: categories not sent are kept; `0` removes one | delete it |
| `admin_emails` | assigned | assigned | ignored |
| `amounts` | required, `platform > 0` (else 422) | required; resulting `platform > 0` (else 400) | ignored |
| Budget service call (same as the UI) | `create_project_budget_group` / `update_project_budget_group` | `create_project_budget_group` / `update_project_budget_group` | `delete_project_budget_group` |
| Cost center code validated | only when creating a cost center | only when creating a cost center | never |
| Existing project's cost center | untouched | untouched | untouched |
| Failed check | nothing written | nothing written | nothing written |
