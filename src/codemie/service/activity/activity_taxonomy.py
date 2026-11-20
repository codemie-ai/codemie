# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Activity event taxonomy catalogue.

Maps every known activity `event_type` string to a human-readable label
and a UI-facing category. Used by presentation layers (e.g. the activity
events API) to group and label raw event_type values without duplicating
this knowledge across callers.

Unknown event_type values fall back to the raw event_type as label and
"Other" as category, so a taxonomy gap degrades a single row's presentation
instead of failing the whole listing. Rows written by older code or other
emitters can still be in the table, so lookups must be total.
"""

from __future__ import annotations

CATEGORIES = ("Budget", "Members", "Settings", "Account")
FALLBACK_CATEGORY = "Other"

EVENT_TAXONOMY: dict[str, tuple[str, str]] = {
    # --- ProjectManagementEvent -> Settings ---
    "project.created": ("Project Created", "Settings"),
    "project.updated": ("Project Updated", "Settings"),
    "project.deleted": ("Project Deleted", "Settings"),
    "project.ownership_changed": ("Project Ownership Changed", "Settings"),
    # Categorized as Budget (not Settings) so it groups with the other budget-related events.
    "project.member_spend_enforcement.updated": ("Member Spend Enforcement Updated", "Budget"),
    # --- MarketplaceModerationEvent / AssistantEvent -> Settings ---
    "marketplace.assistant.submitted": ("Marketplace Assistant Submitted", "Settings"),
    "marketplace.assistant.re_submitted": ("Marketplace Assistant Re-submitted", "Settings"),
    "marketplace.assistant.approved": ("Marketplace Assistant Approved", "Settings"),
    "marketplace.assistant.rejected": ("Marketplace Assistant Rejected", "Settings"),
    "marketplace.assistant.force_published": ("Marketplace Assistant Force Published", "Settings"),
    "marketplace.assistant.withdrawn": ("Marketplace Assistant Withdrawn", "Settings"),
    "marketplace.assistant.prompt_viewed": ("Marketplace Assistant Prompt Viewed", "Settings"),
    "marketplace.threshold.updated": ("Marketplace Threshold Updated", "Settings"),
    "marketplace.threshold.reset": ("Marketplace Threshold Reset", "Settings"),
    "assistant.grant.added": ("Assistant Grant Added", "Settings"),
    "assistant.grant.removed": ("Assistant Grant Removed", "Settings"),
    "hosted_application.grant.added": ("Hosted Application Grant Added", "Settings"),
    "hosted_application.grant.removed": ("Hosted Application Grant Removed", "Settings"),
    "hosted_application.share_link.created": ("Hosted Application Share Link Created", "Settings"),
    "hosted_application.share_link.revoked": ("Hosted Application Share Link Revoked", "Settings"),
    # --- CustomerConfigEvent -> Settings ---
    "customer_config.setting.updated": ("Customer Config Setting Updated", "Settings"),
    "customer_config.setting.reset": ("Customer Config Setting Reset", "Settings"),
    # --- UserManagementEvent (USER_PROJECT_*) -> Members ---
    "user.project.assigned": ("User Project Assigned", "Members"),
    "user.project.removed": ("User Project Removed", "Members"),
    "user.project.role_updated": ("User Project Role Updated", "Members"),
    "user.model_override.set": ("User Model Override Set", "Members"),
    "user.model_override.removed": ("User Model Override Removed", "Members"),
    # --- UserManagementEvent (account lifecycle) -> Account ---
    "user.created": ("User Created", "Account"),
    "user.updated": ("User Updated", "Account"),
    "user.deactivated": ("User Deactivated", "Account"),
    "user.reactivated": ("User Reactivated", "Account"),
    "user.remapped": ("User Remapped", "Account"),
    "user.login": ("User Login", "Account"),
    "user.logout": ("User Logout", "Account"),
    # --- BudgetManagementEvent -> Budget ---
    "budget.created": ("Budget Created", "Budget"),
    "budget.updated": ("Budget Updated", "Budget"),
    "budget.deleted": ("Budget Deleted", "Budget"),
    "budget.user_assignment.created": ("User Budget Assigned", "Budget"),
    "budget.user_assignment.deleted": ("User Budget Removed", "Budget"),
    "budget.project_assignment.created": ("Project Budget Assigned", "Budget"),
    "budget.project_assignment.deleted": ("Project Budget Removed", "Budget"),
    "budget.project_budget.created": ("Project Budget Created", "Budget"),
    "budget.project_budget.updated": ("Project Budget Updated", "Budget"),
    "budget.project_budget.deleted": ("Project Budget Deleted", "Budget"),
    "budget.project_budget_group.created": ("Project Budget Group Created", "Budget"),
    "budget.project_budget_group.updated": ("Project Budget Group Updated", "Budget"),
    "budget.project_budget_group.deleted": ("Project Budget Group Deleted", "Budget"),
    "budget.member_allocation.overridden": ("Member Allocation Overridden", "Budget"),
    "budget.member_allocation.override_cleared": (
        "Member Allocation Override Cleared",
        "Budget",
    ),
    "budget.project_budget.rebalanced": ("Project Budget Rebalanced", "Budget"),
    "budget.project_budget.reset": ("Project Budget Reset", "Budget"),
    "budget.project_budget_group.rebalanced": ("Project Budget Group Rebalanced", "Budget"),
    "budget.project_budget_group.reset": ("Project Budget Group Reset", "Budget"),
    "budget.project_budget.stopped": ("Project Budget Stopped", "Budget"),
    "budget.project_budget.restored": ("Project Budget Restored", "Budget"),
}

CATEGORY_EVENT_TYPES: dict[str, set[str]] = {}
for _event_type, (_label, _category) in EVENT_TAXONOMY.items():
    CATEGORY_EVENT_TYPES.setdefault(_category, set()).add(_event_type)


def get_label(event_type: str) -> str:
    """Return the human-readable label, or the raw event_type if it is not in EVENT_TAXONOMY."""
    return EVENT_TAXONOMY.get(event_type, (event_type, FALLBACK_CATEGORY))[0]


def get_category(event_type: str) -> str:
    """Return the UI category, or FALLBACK_CATEGORY if event_type is not in EVENT_TAXONOMY."""
    return EVENT_TAXONOMY.get(event_type, (event_type, FALLBACK_CATEGORY))[1]
