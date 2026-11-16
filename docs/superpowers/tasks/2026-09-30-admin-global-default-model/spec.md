# Admin-selected global default model

## Goal

Allow an administrator to choose one global default LLM model from the current catalog. The choice persists across restarts and catalog reloads. When no admin choice exists, the configured global default remains in effect. Model management must show the saved choice and the currently effective default so administrators can understand a temporary fallback.

The existing `code` category remains driven by model configuration. Its category-specific default continues to take priority when a caller asks for `code`; the global default remains the fallback.

## Behavior

### Selection and persistence

- An admin can read the saved global choice, its editor and update time, and the currently effective global default. The response distinguishes an admin choice from a configured fallback and indicates when a saved choice cannot currently be used.
- An admin can set the global choice to one model or clear it. Setting replaces the previous choice; clearing restores configuration-driven selection. The same model may also be the configured `code` default.
- A set request accepts a current, selectable model, including a LiteLLM auto-router. Reject an unknown, disabled, deprecated, or provider-unavailable model with an actionable validation response. Existing admin authorization rules protect all write operations.
- A saved choice remains stored if the model later becomes unavailable, disabled, deprecated, or disappears during a catalog reload. While invalid, resolution uses a valid configured global default. If that is also unavailable, resolution follows the existing safe fallback among selectable models. The saved choice becomes effective again when selectable. Clearing is the only operation that removes it.
- Read and write behavior must remain correct with both YAML-backed and live LiteLLM catalogs. A database outage must not silently turn a known invalid selection into an effective one.

### Effective defaults

- One backend resolution path determines the effective global default used by internal model fallback and the public default-model endpoints. It honors the saved admin choice when selectable, then the configured fallback behavior. Auto-router eligibility follows the same rules for admin selection and effective resolution.
- Model catalog responses report the effective `default` and `default_for_categories` values. At most one selectable model in a returned catalog is labeled `global`; an old configured global label is removed when superseded. Other category labels, including `code`, are preserved. Repeated reads, set/clear operations, and catalog reloads must not leave stale labels or mutate configuration data.
- User-scoped catalog and default endpoints resolve within the models that user may select. If the admin choice is outside a user's permitted catalog, show a selectable default from that user's catalog and do not label the inaccessible choice as that user's global default. Existing behavior for users with no selectable models remains unchanged.

### Administration UI

- Model management identifies the saved admin choice and the currently effective global default, including when they differ because of temporary fallback. An administrator can set a selectable model as global default and clear the saved choice from the same page.
- After a successful action, the page refreshes its model metadata and default state. Failed actions show the backend validation error without implying the change succeeded. Help → Available models reflects the effective global label from the catalog API.

## Acceptance criteria

1. **AC-1 — Admin control:** An authorized admin can read, set, replace, and clear the single persisted global choice; non-admin writes are denied.
2. **AC-2 — Validation:** Setting accepts a selectable concrete model or auto-router and rejects unknown, disabled, deprecated, or provider-unavailable entries without changing the saved choice.
3. **AC-3 — Configured fallback:** With no admin choice, global resolution matches the configured behavior. An invalidated saved choice remains stored while a valid configured global default is used, and becomes effective again when selectable.
4. **AC-4 — Consistent resolution:** Internal global fallback and public default endpoints select the same effective model for the same accessible catalog, including when the choice is an auto-router.
5. **AC-5 — Consistent metadata:** Catalog `default` and `default_for_categories` identify the effective global default once, remove superseded global labels, and retain unrelated category labels such as `code`.
6. **AC-6 — User permissions:** User-scoped default endpoints and catalog labels never nominate a model outside that user's selectable catalog.
7. **AC-7 — Admin UI:** Model management shows saved and effective values, offers set and clear actions, refreshes after success, and displays validation failures. Help → Available models shows the effective global label.
8. **AC-8 — Lifecycle:** The saved choice survives process restart and LiteLLM catalog reload; repeated reads and set/clear cycles do not accumulate stale default flags.

## Non-goals

- Admin management of `code`, CLI, or any other category default.
- Changing how coding requests decide when to ask for the `code` category.
- Per-user or per-project administrator overrides.
- Replacing model status management or changing ordinary model availability rules.

## Open risks

- Live LiteLLM availability can change between validation and use. Runtime resolution must recheck selectability and degrade to a permitted fallback.
- A user's limited model list can exclude both the admin choice and configured default. The existing safe fallback must stay within that user's list.
- The frontend is a separate repository and outside this session's current writable roots; implementation needs an authorized write path there.
