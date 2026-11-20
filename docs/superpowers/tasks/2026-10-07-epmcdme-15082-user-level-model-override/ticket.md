# EPMCDME-15082: User-Level Model Override

- Link: https://jiraeu.epam.com/browse/EPMCDME-15082
- Type: Story
- Status: Ready for Review
- Assignee: Egor Popov
- Updated in Jira: 2026-11-17T12:38:54+0000
- Linked: EPMCDME-14349 "Project-Level Model Availability" (Story, Closed) — Cloners: this ticket clones it
- Comments: none
- Attachment: `Platform Model Management.png` by Aliaksei Lyzo (scope diagram, not saved here)
- Snapshot of the description as returned by the Jira assistant on 2026-11-17 (raw wiki markup, not checked against Jira directly)

## Description

```
h1. User Model Access Control — Story
----
h2. Context
 * Model access is currently layered platform → project → user. The user tier is documented as configurable (admin can override a user's model set within a project) but the resolution/enforcement side is underspecified.
 * Model invocation is always resolved by {*}caller{*}:
 ** *Access check* (can the caller use the asset's selected model): the user's override applies regardless of the asset's scope — for a project-scoped asset, that project's user override applies;
 ** *Fallback* order for project assets: The fallback model is determined in the following sequence—user override first (if it exists), followed by the project default (if defined), and finally the platform default (if defined).
 * Precedence between a user override and its project's model set is undefined in the source design doc — this story resolves it as a product decision: *user override wins* at the access-check step described above.
 * Platform-disabled models remain a hard stop at every scope — no override at any level re-enables them. A "hard stop" at a specific scope means that once a disabled/blocked model blocks progression at that layer, the resolution mechanism skips to the next fallback tier rather than throwing an immediate error, following the sequence: User Override ➔ Project Default ➔ Platform Default
 * Deprecated models (platform model management) stay usable where they are already selected but cannot be selected anew. A user-level override follows the same rule as every other place a model is saved (project default, assistant, workflow, conversation):
** *Saving an override:* a deprecated model cannot be added to a user's override, as an allowed model or as its default. A deprecated model the user's override already contains is kept, so an admin can still edit the override after one of its models was deprecated.
** *Bulk override:* a deprecated model is accepted only when every selected user's override already contains it; otherwise the whole bulk action is rejected and no user is changed.
** *Invocation:* a deprecated model already in a user's override keeps working; this story does not change how a deprecated model is resolved at run time.
 * A sibling Phase 1 story ({{{}docs/stories/2026-09-16-model-catalog-lifecycle/{}}}) is assumed to cover platform/project-level catalog configuration; this story is Phase 2, scoped to the user tier.

----
 
h2. Story

*As a* project or platform admin, *I want* to set model access overrides — expanding or narrowing a user's available models — for individual users or a bulk-selected group of users, and have those overrides actually honored when a user invokes an asset, *so that* I can grant power users access beyond their project's default set, or restrict specific users below it, independent of the project-level configuration.
----
h2. Background

Today, model access is configured at platform and project level, and the design allows a further user-level override — but it is unclear whether that override is actually enforced at invocation time, or only stored as configuration. Admins need to both expand access for some users (e.g. power users trialing new models beyond their project's set) and narrow it for others (e.g. restricting a specific user below the project default), independent of what the rest of the project can use. Without enforcement, a user-level override would have no runtime effect, defeating the purpose of the feature. This story closes that gap: it covers both the admin-facing configuration action (in either direction) and the runtime resolution logic that must read and apply it.
----
h2. Acceptance Criteria
 * [ ] Given a project admin is viewing a single user within their project, when they set a model access override for that user, then the override is saved and associated with that user within that project.
 * [ ] Given a project admin has selected multiple users (bulk selection), when they apply a model access override to the selection, then the same override is saved for every selected user.
 * [ ] Given a user has a model-access override granting a model that is outside their project's model set, when that user invokes a project-scoped asset whose selected model is in the user's override, then the invocation resolution check passes and that model is used — the user is not blocked by the project-level restriction.
 * [ ] Given a user has both a project-level model set and a user-level override, when the system resolves whether the caller can invoke the asset's selected model, then the user-level override takes precedence over the project's model set for that determination — whether the override expands or narrows the user's available models.
 * [ ] Given a user has a model-access override that excludes a model otherwise available at the project level, when that user invokes an asset whose selected model is excluded by their override, then the invocation resolution check fails for that user, even though other users in the same project can access that model.
 * [ ] Given a user's access check fails and fallback is required, when the system selects the fallback model, then it uses the default associated with the user's scope (user-level override if it exists and has a default, otherwise falling back to the project default for a project-scoped asset, or the platform default for all other cases)

----
h2. Out of Scope
 * Platform-level and project-level model configuration UI/logic, but keeps fallback to that levels.
 * Model resolution for callers with no caller/user identity at all (e.g. webhooks) — there is no user to apply an override for.
 * {color:#172b4d}Market-level (global) resolution{color}
```
