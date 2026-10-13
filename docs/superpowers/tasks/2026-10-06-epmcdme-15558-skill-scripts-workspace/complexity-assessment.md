## Complexity Analysis: EPMCDME-15558 (rework: copy -> reference versioned skill blobs)

Mode: initial. Grounding: technical-analysis.md section 8 (Addendum 2026-10-08). Calibrated against examples m/10202, m/10203, l/10401, xl/10303.

### Component Scope: XXL (6)  [base XL 5, +1 red flag]
- Affected: skill_workspace_seeder (rewrite), AgentWorkspaceRepository (drop 8 seed methods, add one-transaction ON CONFLICT DO NOTHING), AgentWorkspaceService (new seeding method, missing-blob tolerance, reserved-path checks on write and delete), SkillService (versioned storage_key, expose storage_key), models/skill.py (policy and DynamicConfig moved to a service with frozen SeedLimits), skill_tool.py (layering fix), AgentWorkspaceToolkit/ToolkitService/ToolkitSettingService (remove script_tool_selected, explicit preparation step), assistant_service (workflow warning delivery), workspace_path_rules/job_bridge (reserved paths), routers/skill.py.
- Layers: API, Service, Repository, Agent-Tool, Workflow, DB.

### Requirements Clarity: M (3)
- Status: ticket and addendum are detailed and enumerate the change list.
- Gaps: the download_rules.can_download 403 for skill-owned blobs is an open decision. The versioned-key format and the owner-marker semantics need confirmation. Orphan-blob cleanup is declared out of scope.

### Technical Risk: XL (5)  [base L 4, +1 red flag]
- Risk factors: rewriting an unmerged migration in place (single-head assertion, y1z2a3b4c5d6). Content-addressed keys with slashes across the AWS/Azure/GCP/filesystem repos, with key length near the S3 limit. Azure write_file swallows upload errors. Authorization gap for skill-owned blobs in download/sandbox URL paths. Reserved-path hardening (case/Unicode-insensitive, write+delete) is security-adjacent. Concurrency correctness now depends on a single transaction with no lease or retry fallback. Workflow paths now receive skill files.
- Mitigation: existing 30+ seeder tests give a regression baseline. Removing machinery lowers long-term risk.

### File Change Estimate: XXL (6)
- Modified/removed: roughly 25 or more source and test files. These include the seeder, 2 repositories/services, skill.py, skill_tool.py, toolkit chain (4 files), core/models.py, assistant_service.py, workspace_path_rules, job_bridge, router, migration, and docs.
- New: SeedLimits/policy service and the new seeding tests.
- Tests to rewrite or drop: about 12 test modules (seeder 30+2, repository 17, migration 5, skill_tool 10, plan_bundle_seed 12, reserved paths 10+5+2+22, input files 7, toolkit service/toolkit).
- Directories: service, repository, rest_api models/routers, agents/tools, codemie_tools, workflows, alembic, tests, docs.

### Dependencies: S (2)
- New packages: none. Three settings removed. SeedLimits config consolidated. pg_insert on_conflict_do_nothing is a new use of an existing library.

### Affected Layers: XL (5)
- API (router limits, warnings), Service, Repository, Agent-Tool, Workflow, DB (migration rewrite), object storage (versioned keys).
- Schema/migration: yes (rewritten, not added). Cross-system: no.

### Red flags applied
- Component Scope bumped L->XL (base 5->6): multiple workflows/agents plus refactor of a subsystem (seeder, toolkit chain, skill model policy).
- Technical Risk bumped L->XL: authorization-adjacent (download_rules) and security-sensitive reserved-path rules.
- Data-migration flag not applied: the migration is unmerged, so there is no data to migrate.

### Total Score: 27/36
### Size: XL (borderline with L at 26; leaning higher because Technical Risk is XL and Component Scope is XXL)

### Routing
SPLIT REQUIRED. Splitting is strongly recommended. Do not go straight into one plan.

### Suggested split (by dependency/phase)
1. Storage core: versioned storage_key in SkillService, rewritten migration y1z2a3b4c5d6 (owner marker, no status/attempts/seed_entry_id), single-transaction ON CONFLICT DO NOTHING repository method, a new slim seeding service with SeedLimits, removal of retry/lease/polling code and the three settings, and missing-blob tolerance. Rewrite the related tests.
2. Policy and layering cleanup: move policy/DynamicConfig out of models/skill.py, a single skill-folder-name function, skill_tool going through an AgentWorkspaceService method, and the download_rules decision for skill-owned blobs.
3. Reserved paths: root-only, case/Unicode-insensitive, blocking write and delete, shared constants with job_bridge.
4. Preparation step and workflows: seeding as an explicit step in ToolkitService.get_tools (removes script_tool_selected), workflow assistant nodes get skill files, and warning delivery into the chat and workflow prompt builders.
5. Docs and remaining test rewrites.

Order: 1 -> 2/3 (parallel) -> 4 -> 5.

## Addendum 2026-10-13: review round 4

| Part | Total | Size | Routing | Notes |
|---|---|---|---|---|
| A. Blob key (A1 128-bit prefix, A2 skip re-upload) | 12/36 | S | writing-plans | 3 production files; no schema, auth or migration change |
| B. Shared seeding core (B1-B4) | 17/36 | M | brainstorming | ~7 production files, ~8 test files; red flags: shared utility (scope M→L), authorization (risk S→M); no split |
