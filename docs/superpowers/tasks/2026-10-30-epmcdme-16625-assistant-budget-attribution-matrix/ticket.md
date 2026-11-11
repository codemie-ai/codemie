# EPMCDME-16625 — Assistant Budget Attribution Matrix (ticket text)

Verbatim copy of the requirement text supplied with the task (condensed only in whitespace). Open question from the ticket: an earlier partial fix was attempted in GitLab MR 4665 (gitbud.epam.com/epm-cdme/codemie/-/merge_requests/4665) — likely the EPMCDME-16466 branch; ideas reused, not depended on.

## Terms
- Asset project: project the assistant / workflow / datasource belongs to.
- Default project (D): admin-assigned default; else the user's personal project; if not a member of that either, alphabetically first of their projects.
- Personal budget (P): the user's personal / platform budget.
- Funded: project has an active budget AND an allocation for this user. No allocation = not funded.
- Rule: pick the target from the matrix, walk target → D → P, charge the first funded step; skip repeated steps. Exhausted budget is not "unfunded": hard limit blocks, soft limit warns, never rerouted. P is the last resort and is charged without a funded check (flagged as fallback).

## Decision chart
- Marketplace/published assistant or workflow → target D.
- Assistant/workflow/datasource in a project: user member → asset project; non-member (shared) → D.
- Personal assistant in user's personal project → D. No asset (skill, workflow run, generation) → D.
- CLI/IDE: no project → D; project + member → that project; project + non-member → ignore header, log, target D. **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]**
- Target funded → charge it; else D funded → charge D; else charge P flagged as fallback. Then exhausted: hard limit block, soft limit allow with warning.

## Matrix (# | asset | user↔project | target | chain | change)
1 Personal assistant, owner (personal project) → D → D→P. Changes when D≠personal.
2 Personal assistant, owner, no assigned default → D=personal → P. No change.
3 Project assistant, member, project funded → asset project. No change.
4 Project assistant, member, project not funded → asset project → D→P. Changes.
5 Project assistant (shared), non-member → D → D→P (today: asset project→P). Changes.
6 Marketplace assistant, member → D → D→P (supersedes 15109).
7 Marketplace assistant, non-member → D → D→P (EPMCDME-16466).
8 Marketplace assistant from user's personal project, owner → D → D→P.
9 Workflow (project): as rows 3–5 (today workflow project→P).
10 Workflow (marketplace): D → D→P, as rows 6/7.
11 Workflow node / sub-assistant using a saved assistant: rows 1–8 applied to that assistant, for the user who started the run (today its own project→P).
12 Skill run, workflow execution, generation (no asset): D → D→P.
13 Datasource indexing: as rows 3–5 (today index project→P).
14 CLI/IDE, no project: D → D→P.
15 CLI/IDE, explicit project, member: that project → D→P.
16 CLI/IDE, explicit project, non-member: D (header ignored, logged) → D→P. **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]**
17 Scheduled/webhook trigger: evaluated as trigger owner, rows 1–13 (today owner's memberships unknown).
18 User with personal key: own key pays, rules do not apply. No change.
19 Chosen budget exhausted: unchanged target, no reroute.

## Acceptance criteria
Marketplace: funded D + marketplace assistant from non-member project → D (16466); from member project → D, not asset project; D unfunded → P recorded as fallback from D; global workflow same as marketplace assistant.
Project/shared/personal: member + funded → project (unchanged); member + no budget/no allocation → D if funded else P; shared to non-member → asset project never charged, D else P; personal assistant with funded admin-assigned team default → D; personal assistant with no assigned default → P (unchanged).
Nested/asset-less: workflow node with saved assistant from another project → charged by matrix as if used directly; orchestrator sub-assistant charged by matrix for the starting user, never a non-member project; skill/workflow execution/generation with D unfunded → P recorded as fallback; indexing by non-member → datasource project not charged, chain applies.
CLI/IDE: no project → D if funded else P like web; member project unfunded → D if funded else P; non-member project → neither charged nor shown in analytics, D→P chain, ignored project logged **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]**; web and CLI fallbacks land on the same personal budget.
Triggers/limits/edge: scheduled/webhook uses owner's real memberships and default; hard limit exhausted → blocked, not rerouted; soft limit → proceeds with usual warning, not rerouted; personal key → key pays; admin changes default/allocation → applies within normal ~1 min cache delay, history not moved; IDP-claims mode (User Management disabled) → personal project used as D, same as no assigned default.
Analytics: charged request → project shown = project whose budget was charged; fallback → shows originally targeted project and whether charge landed on D or P.

## Out of scope
Migration of existing personal-project budgets; rerouting on exhaustion (D4); per-chat billing project choice or showing payer to users; changing default-project assignment (15109); searching other projects beyond D; re-attributing history; UI changes.

## Decisions
D1 marketplace used by member → D, not asset project (supersedes 15109 and 16466 AC3). D2 workflow nodes/sub-assistants attributed by the same matrix for the starting user. D3 CLI non-member explicit project → ignored, matrix applies, logged as fallback **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]**. D4 exhausted → no reroute (hard block, soft warn). D5 every fallback goes to D before P. D6 funded = active budget + allocation. D7 triggers attributed as owner with real memberships/default. D8 IDP-claims mode uses derived default (personal project). D9 web and CLI fallbacks hit the same personal budget. D10 datasource indexing follows project-asset rows. D11 EPMCDME-16466 closed by this story.
