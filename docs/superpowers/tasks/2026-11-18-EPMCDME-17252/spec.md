# EPMCDME-17252 — Categories for hosted apps (backend)

## Goal

Hosted apps carry 0–3 categories from the shared platform list (`categories` table,
`rest_api/models/category.py:39`), need 1–3 to be on the marketplace, can be filtered by
category in every catalog scope, and hold a category against deletion while not deleted.
Behaviour mirrors workflows: they store ids the same way and refuse duplicates the same way
(`workflow_marketplace.py:26`, `workflow_marketplace_service.py:39-115`). Hosted-specific
mechanics (async session, `HostedApplicationError`, digest idempotency, signed cursor) stay
as they are.

## Behaviour

**Storage.** New column `hosted_applications.categories`: a JSONB array of category ids,
`NOT NULL DEFAULT '[]'`, with a GIN index `ix_hosted_applications_categories`, the same as
workflows (`k6l7m8n9o0p1_add_workflow_marketplace_columns.py:29-48`). No FK, same as
assistants and workflows. A new Alembic revision adds it. Existing rows, including published
ones, get `[]`. Order is kept as the client sent it.

**Request shape.** One shared definition of a category selection lives in
`rest_api/models/category.py`, next to `Category`. It owns the cap, the id length, the
uniqueness rule and their message constants:

```python
CATEGORY_ID_MAX_LENGTH = 256   # the one owner of the id length
CategoryId = Annotated[StrictStr, Field(min_length=1, max_length=CATEGORY_ID_MAX_LENGTH)]
CategoryIds = Annotated[list[CategoryId], AfterValidator(...)]          # 0..3, unique
RequiredCategoryIds = Annotated[list[CategoryId], AfterValidator(...)]  # 1..3, unique
```

`AppCreate` and `AppPatch` (`hosted_application_requests.py:57-104`) gain
`categories: CategoryIds`. Create defaults to `[]`. The create idempotency payload (the model
dump hashed at `catalog.py:389`) leaves `categories` out when it is empty, so a create without
categories hashes exactly as before the deploy and a retry across the deploy replays instead of
returning `IDEMPOTENCY_CONFLICT`. Only this field is excluded; the rest of the dump is
unchanged. A non-empty `categories` is part of the payload. On PATCH it is optional, `[]` clears, and it
is added to `AppPatch.has_changes`. Publish gets its own body model in place of the empty
`RequestModel` (`routers/hosted_applications.py:516-525`): `PublishRequest(RequestModel)` with
`categories: RequiredCategoryIds`, default `[]` and the default validated, so `{}` is refused
with the required-message rather than pydantic's "Field required". Workflow and assistant
request models keep their own declarations.

**Where each rule is enforced.** Request-model failures reach the client through the hosted
handler as `INVALID_REQUEST` "Invalid application request" with one `fields[]` entry per
problem (`hosted_apps_gateway/management.py:109-134`). A validator's text is kept there because it
does not quote the input. Pydantic prefixes it with "Value error, ".

| Rule | Layer | Code (422) | Message |
|---|---|---|---|
| More than 3 ids | shared type | `INVALID_REQUEST` | "Maximum 3 categories allowed" |
| Repeated id | shared type | `INVALID_REQUEST` | "category IDs must be unique" |
| Publish with no ids (`{}` or `[]`) | shared type | `INVALID_REQUEST` | "At least one category is required" |
| Id empty, over 256 chars or not a string | shared type | `INVALID_REQUEST` | pydantic's string text |
| Unknown id sent | service | `INVALID_CATEGORIES` | "Invalid category IDs: [...]" |
| Published app left with none | service | `INVALID_CATEGORIES` | "A published application needs at least one category" |

`INVALID_CATEGORIES` is used only for these two database-dependent rules.

**Existence check.** Create, patch and publish check that every id **the request sends**
exists, by querying the `Category` model through the hosted transaction's async session. PATCH
does not re-check ids already stored. This runs after the idempotency replay check and before
any change. Unknown ids → `HostedApplicationError("INVALID_CATEGORIES", "Invalid category IDs: [...]", 422)`,
and the app is unchanged. This message echoes up to three caller-sent ids, which the hosted
gateway's other request errors do not do. It is an accepted exception: it goes only to the
sender and is neither logged nor stored.

**Published needs one.** On PATCH, if the app is published (`is_global`) and none of its
categories after the change still exists in `categories`, the request is refused with
`INVALID_CATEGORIES` "A published application needs at least one category". Surviving ids are
counted with the same `IN` query the reply uses, so a published app whose only category was
deleted is refused on its next edit, like a legacy app whose PATCH does not touch categories.
The app stays published and unchanged. Stale ids stay stored and are skipped in the reply.

**Publish / unpublish.** `PublicationServiceMixin.publish` (`publication.py:104-111`) takes the
request and sets `is_global = True` and `categories = request.categories`. Publishing an app
that is already published replaces its categories, the same as workflow publish. The operation payload is
`{"categories": [...]}` as sent, so `HostedTransaction.operation` (`repository.py:169-225`)
returns `IDEMPOTENCY_CONFLICT` 409 for a retry under the same key with different categories
or a different order. Unpublish is unchanged and keeps the categories.

**Reply.** `app_projection` (`projections.py:33`) adds `categories: [{"id", "name"}]` in
stored order, for every viewer. Callers resolve names in one `IN` query. On a page, the batch
goes on the transaction the way `tx.covered` does (`catalog.py:550-560`). Ids missing from the
table are skipped, as `enrich_categories` does (`category_service.py:132-152`).

**Catalog filter.** `GET /hosted-applications` gains a repeated `category` query param, named
like `project` (`routers/hosted_applications.py:183-214`). `list_apps` normalizes it to a
sorted list of distinct ids (≤50, each ≤ `CATEGORY_ID_MAX_LENGTH`, the constant the shared type uses). A malformed value or any unknown id →
`INVALID_FILTER` "Invalid application filters". The existence read runs before the cursor is
decoded, so an unknown id is never reported as `INVALID_CURSOR`. The filter works in every
scope and combines with the other filters. Its term is an OR of `categories @> [<id>]`, each
value a bound parameter, so apps with `[]` never match. The normalized list enters
`cursor_scope` (`catalog.py:498-509`) **only when it is non-empty**, so cursors issued before
the deploy stay valid. A changed selection fails the signature check (`INVALID_CURSOR`).
Without a filter the list behaves as today.

**Deletion guard.** `DatabaseCategoryService.delete_category` (`category_service.py:238-287`)
adds hosted applications to its `(label, model)` usage loop. Only rows with
`deleted_at IS NULL` count, so deleted (purging) and purged apps never block, and the refusal
can name only live apps. The 409 message format stays as it is, including the listing of up to
10 app names from any project. `get_category_stats` is untouched.

**Docs and contracts.** Update `docs/hosted-applications/architecture.md`: publication (:551-561)
and lists (:563-569). Regenerate the OpenAPI baseline. Add the new revision to
`HOSTED_MIGRATION` and to the parity and head tests (`conftest.py:102`, `test_schema_parity.py:112`).
The hosted test fixture also creates the `categories` table.

## Acceptance criteria

1. Create or patch with 0–3 existing, distinct ids on an unpublished app is accepted, and the reply lists `{id, name}` in order. (story AC1)
2. Publish with 1–3 existing, distinct ids publishes the app with exactly those categories. (story AC2)
3. More than 3 ids, a repeated id, or an unknown id on create, patch or publish, or a publish without categories, is refused with a message that names the problem. The app is unchanged. (story AC3)
4. A PATCH that leaves a published app (legacy included) with no category is refused with "A published application needs at least one category". The app stays published and unchanged. (story AC4)
5. Unpublish keeps the categories. (story AC5)
6. A publish retry under the same idempotency key with different categories returns 409 `IDEMPOTENCY_CONFLICT`. (story AC6)
7. In every scope, `category=a&category=b` returns only apps carrying a or b, combined with the other filters. Uncategorized apps are excluded. (story AC7)
8. A cursor issued under one category selection is refused under another. Paging under one selection neither repeats nor skips apps. (story AC8)
9. An unknown category in the filter → `INVALID_FILTER`. (story AC9)
10. Deleting a category carried by a hosted app that is not deleted is refused as in use (409, same format). Deleted or purged apps never block, and a purged app is never named. (story AC10)
11. Without a category filter, published uncategorized apps are still listed. (story AC11)
12. The model/migration schema-parity test passes with the new revision.
13. Each rule in the enforcement table returns its code and message: publish `{}` returns `INVALID_REQUEST` with "At least one category is required", and four ids return "Maximum 3 categories allowed".
14. A category deleted from under a published app: a PATCH that does not send categories is accepted when another stored category still exists and refused with "A published application needs at least one category" when none does. A PATCH never fails with "Invalid category IDs" for an id it did not send.
15. The same create body without categories produces the same idempotency digest before and after this change, so a retry under the same key replays the first reply.

## Non-goals

- Any UI change. (story NG1)
- SDK and CodeMie Code changes. (story NG2)
- Usage counts on the categories admin page: `get_category_stats` is unchanged. (story NG3)
- Bulk-assigning categories to apps published before this change. (story NG4)
- Changing `validate_category_ids` or its callers. Its silent de-duplication stays for assistants and workflows.
- Adding row locks to `delete_category`.
- Moving workflow or assistant request models onto the shared category type.
- Removing stale category ids from stored rows.

## Decisions

- JSONB array `NOT NULL DEFAULT '[]'` + GIN (workflow shape) over a nullable column (assistant shape) — how to store categories.
- Existence check through the hosted async session over calling the sync `validate_category_ids` in a thread — how to validate ids inside the hosted transaction.
- Refuse duplicates in the request model (workflow publish rule) over the shared validator's de-duplication — how to meet the AC3 "repeated category" rule.
- One shared annotated type with its message constants in `rest_api/models/category.py` over a declaration in each hosted model — where the cap, id length and uniqueness rule live.
- Workflow and assistant request models keep their own declarations over pointing them at the shared type — how far the reuse reaches (workflow publish has no id-length cap, so moving it changes its behaviour).
- Shape rules return `INVALID_REQUEST` from the request model with the named literals over moving them to the service under `INVALID_CATEGORIES` — which code a malformed selection gets.
- New error code `INVALID_CATEGORIES` (422), kept for the database-dependent rules, over reusing `INVALID_FILTER` for request bodies — which error a bad category in a body gets.
- `PublishRequest.categories` defaults to `[]` and the default is validated, over a required field — so `{}` names the problem instead of "Field required".
- Publish replaces the categories of an app that is already published over refusing or merging — what a re-publish does.
- Publish digest over the categories as sent, order included, over a sorted set — what counts as "different categories" on a retry.
- Leave `categories` out of the create idempotency payload when empty over hashing the full dump with the new default — so create digests and replays survive the deploy.
- The published-needs-one rule runs on every PATCH of a published app, including PATCHes that leave categories alone, over checking only PATCHes that send categories — how to enforce "legacy apps need a category on their next edit".
- PATCH checks existence only for the ids it sends over re-checking stored ids — what a PATCH does when a stored category was deleted.
- Published-needs-one counts stored ids that still exist over counting every stored id — so a published app is never left with no visible category.
- Every viewer sees categories in the reply over managers only — who sees categories.
- Query param `category` (repeated, like `project`) over `categories` — what to call the filter.
- `category` enters `cursor_scope` only when non-empty over always — so cursors in flight survive the deploy.
- Filter cap of 50 ids (as `project`) and the shared `CATEGORY_ID_MAX_LENGTH` (256) over no cap or a second literal — how to bound the filter.
- Deleted apps release categories through a `deleted_at IS NULL` term in the guard over clearing the column on delete — `_begin_purge` leaves the marketplace columns alone (`catalog.py:739`).
- Hosted apps are labelled in the existing deletion-refusal message and keep its name listing, as for assistants and workflows, over a count-only refusal or published-only names — how a refusal reports hosted apps; accepted with the role-source precondition below — user, answers AR-4.

## Assumptions and risks

- The platform sync engine reaches `hosted_applications` in the same schema: both engines use the same URL and `search_path` (`postgres.py:144-161`, `runtime.py:79`), and a sync read of the table already exists (`grant_descriptor.py:41-65`).
- **Precondition for the deletion refusal's name listing (accepted by the user):** the request user's admin role agrees with the `users` row hosted reads (`core/hosted_applications/access.py:58-86`). It agrees with `ENABLE_USER_MANAGEMENT=True` outside `ENV=local`, where the request `User` is built from the `users` row (`authentication_service.py:165-172`, `user_by_id_loader.py:54-55`). It may not agree when `ENV=local`, where every request user is admin (`security/user.py:168-169`, `idp/local.py:111-112`), or when `ENABLE_USER_MANAGEMENT=False`, where admin comes from `ADMIN_USER_ID` or the IdP role `ADMIN_ROLE_NAME` (`security/user.py:170-173`). In those modes the 409 can name private hosted apps to a caller hosted does not treat as admin.
- A category can be deleted while a publish that uses it is in flight. Neither side takes a lock, which is today's behaviour for assistants and workflows too. Old pods during a deploy also skip the hosted check. The resulting stale ids are handled as in "Published needs one".
- Clients that send `{}` to publish now get 422. This is intended (story AC3), but categories-ui must ship after the backend rollout completes, since old pods reject the `categories` field.
- Category names in a stored `replay_body` are frozen at the time of the first reply. A reply replayed from before the deploy has no `categories` field; a missing `categories` field means `[]`.
- The generated OpenAPI shows `PublishRequest.categories` as optional with no `minItems`, while the server refuses a missing or empty value with a clear 422. A client built from the baseline (NG2) compiles a publish call without categories and fails at run time. Accepted; no `json_schema_extra` override.
- Hosted service tests run only with `HOSTED_TEST_DATABASE_URL`.
