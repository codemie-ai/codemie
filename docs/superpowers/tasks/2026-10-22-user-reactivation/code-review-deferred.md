# Deferred from code review — 2026-10-22-user-reactivation (2026-10-22)

- **Concurrent reactivations can double-log event** — `src/codemie/service/user/user_management_service.py:335` — check-then-write without a row lock lets two simultaneous admin requests both pass the 409 check and stage two USER_REACTIVATED events. Pre-existing: the deactivate and update flows use the same unlocked read-then-write pattern; no locking convention exists in this service layer.
