```json
[
  {
    "kind": "acceptance",
    "item": "AC#1 — POST /share with user_ids grants READ access; recipient can converse",
    "status": "partial",
    "notes": "grant() writes Permission rows and is_shared_with gains per-user predicate. Conversation runtime runs under owner context per spec; no diff evidence that the conversation service forwards owner credentials — not visible in this diff."
  },
  {
    "kind": "acceptance",
    "item": "AC#2 — Shared assistant appears in recipient accessible assistant list (VISIBLE_TO_USER scope)",
    "status": "pass",
    "notes": "_get_non_global_base_condition and _filter_for_regular_user_visibility both gain per_user_grant_condition OR clause."
  },
  {
    "kind": "acceptance",
    "item": "AC#3 — Recipient cannot edit; Ability.can(WRITE) returns False",
    "status": "pass",
    "notes": "Permission rows carry permission_level=READ. Existing WRITE-ability check is unchanged and does not include per-user READ grants."
  },
  {
    "kind": "acceptance",
    "item": "AC#4 — DELETE /share revokes access for listed users; subsequent reads return 403",
    "status": "partial",
    "notes": "revoke() deletes Permission rows. Post-revocation 403 relies on is_shared_with returning False and Ability.can(READ) refusing — not explicitly tested in the diff's new tests."
  },
  {
    "kind": "acceptance",
    "item": "AC#5 — After revocation assistant no longer appears in revoked users' list",
    "status": "pass",
    "notes": "per_user_grant_condition in list queries uses a subquery; once Permission row is deleted the OR arm is false."
  },
  {
    "kind": "acceptance",
    "item": "AC#6 — GET /sharable-users returns only is_external_user=False and is_active=True users",
    "status": "partial",
    "notes": "aquery_active_users filters is_active=True and deleted_at IS NULL; external-user filter applied in Python after DB fetch, not in the SQL query. All active records are fetched from DB regardless of user_type."
  },
  {
    "kind": "acceptance",
    "item": "AC#7 — POST/DELETE /share by non-owner returns 403; no Permission row created or deleted",
    "status": "pass",
    "notes": "_require_owner raises 403 before any DB writes; test_grant_non_owner_raises_403 and test_revoke_non_owner_raises_403 cover this."
  },
  {
    "kind": "acceptance",
    "item": "AC#8 — GET /shared-with returns current shared-with list for grant-access view",
    "status": "partial",
    "notes": "Endpoint exists and calls list_shared_with. No ownership guard on GET /shared-with — any authenticated caller can query another owner's list."
  },
  {
    "kind": "acceptance",
    "item": "AC#9 — Shared assistant appears in recipient personal project view (same query path as AC#2)",
    "status": "pass",
    "notes": "_filter_for_regular_user_visibility non-global branch includes per_user_grant_condition; spec confirms same path covers AC#9."
  },
  {
    "kind": "acceptance",
    "item": "AC#10 — shared:true and per-user grants coexist without conflict",
    "status": "pass",
    "notes": "is_shared_with evaluates project-shared first (no DB call), then per-user grant; OR semantics in list queries are independent."
  },
  {
    "kind": "acceptance",
    "item": "AC#11 — Recipient conversation executes against assistant's datasources without error",
    "status": "pending-stage-7",
    "notes": "Runtime context forwarding (owner's datasource permissions) is not in this diff; requires integration or conversation test evidence."
  }
]
```

- **AC#1 (partial)**: Grant and visibility wiring is present but conversation runtime owner-context forwarding is outside this diff; cannot confirm without integration evidence.
- **AC#4 (partial)**: Revoke deletes rows correctly, but post-revocation 403 for single-object access depends on `is_shared_with` returning False feeding `Ability.can(READ)` — no test covers the full revoke-then-read path.
- **AC#6 (partial)**: `aquery_active_users` fetches all active users from DB and the external filter is applied in Python; users with `user_type == external` are returned from the DB and only stripped in the service layer, not excluded at query time.
- **AC#8 (partial)**: `GET /shared-with` is accessible to any authenticated user, not restricted to the assistant owner; any caller learns who has been granted access.
