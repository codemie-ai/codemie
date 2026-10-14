# EPMCDME-15600 Clear limit-exceeded messages for member overrides — Implementation Plan

**Goal:** Replace the generic "fixed overrides exceed project budget" message with one general message set for soft-limit, hard-limit (max budget) and both-limits exceedance. Frontend behavior is unchanged: same exception type (`ValidationException`, re-wrapped to `ExtendedHTTPException` 400 by existing callers), same HTTP 400, same response shape; only the message text changes.

**Architecture:** One check, one message set, in `_equal_amounts_with_fixed_overrides` (`src/codemie/service/budget/project_budget_service.py`). No router, repository, model, or `override_member_allocation` changes. Tests in `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`.

**Conventions:** Commit per task using the repository's existing convention. Follow `.ai-run/guides/development/error-handling.md` ("Accurate Error Details": a distinct message per distinct failure reason) and `.ai-run/guides/testing/testing-service-patterns.md` (AsyncMock, SimpleNamespace, pytest-asyncio).

**Message set (fixed strings: no numbers, no placeholders, no format fields; valid for one override or the total of several):**
- Soft only: "Personal soft limits of project members cannot exceed the project soft limit."
- Hard only: "Personal hard limits of project members cannot exceed the project hard limit."
- Both: the soft sentence followed by a space and the hard sentence.

---

### Task 1: Branch the allocator message by exceeded limit

**Files:** Modify `src/codemie/service/budget/project_budget_service.py:261-262` (`_equal_amounts_with_fixed_overrides`); Test `tests/codemie/service/budget/test_project_budget_service_lifecycle.py:510-536`.

- Replace the combined `if fixed_max > dec_max or fixed_soft > dec_soft: raise ValidationException("fixed overrides exceed project budget")` with separate soft and hard comparisons. Collect the matching fixed sentence(s) from the message set above and raise a single `ValidationException` with them joined by a space (soft sentence first). Keep the existing comparison semantics (`Decimal` sums vs project limits) and the callers' `ExtendedHTTPException(400, message=str(exc))` re-wrapping untouched.

Test-first: yes — a soft-only exceedance (fixed soft 21 / max 10 vs project soft 20 / max 25) must raise `ValidationException` with exactly the soft message; fails today because the old generic string is raised.

- [ ] Update the existing test at `test_project_budget_service_lifecycle.py:536` (soft 21 / max 26 vs 20 / 25, both exceeded): keep `code == 400`, assert the message equals the "both" string instead of the old one.
- [ ] Add tests calling the static `_equal_amounts_with_fixed_overrides` directly with `SimpleNamespace` allocations: soft-only (exact soft message), hard-only (exact hard message), both (exact joined message), and a valid case (fixed overrides within both limits, with one equal-mode member: no exception, fixed pair preserved, remainder shared to the equal member).
- [ ] Run the new/updated tests, then `tests/codemie/service/budget/test_project_budget_service*.py` and `tests/codemie/rest_api/routers/test_project_budget_router*.py`.

---

**Negative-constraint pass:**
- "Must NOT affect frontend behavior; no numbers, no placeholders/format fields; keep exception type, HTTP 400, response shape": Task 1 uses fixed strings only and does not touch exception types or wrapping.
- "ONE check and ONE message set; drop the per-user early check; no fixture changes for it": single task in the allocator; `override_member_allocation` untouched.
- "Do not touch router/repository/models": none touched.
- "Not misleadingly a generic project budget violation": the generic string is removed from source and the test.
- "Not a request to allow a higher personal soft limit": rejection preserved; no validation relaxed.
