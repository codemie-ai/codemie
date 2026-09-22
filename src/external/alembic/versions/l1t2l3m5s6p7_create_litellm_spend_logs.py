"""create_litellm_spend_logs

Revision ID: l1t2l3m5s6p7
Revises: c4d5e6f7a8b9
Create Date: 2026-08-27 00:00:00.000000

Hand-written, and it has to be. `alembic revision --autogenerate` cannot emit `PARTITION BY`,
so a generated migration would create an ordinary unpartitioned table that satisfies every
read identically while losing every property the partitioning was chosen for. The model
(`codemie/repository/spend_logs_model.py`) is deliberately absent from `env.py` for the same
reason, which is also the existing practice for this table family.

Physical design decisions this implements, with the reason attached so none is "tidied" later:

* **Range-partitioned on `start_time`, one partition per UTC calendar month.** `start_time` is
  the only date the row carries - there is no insert or ingest timestamp among the 28 columns,
  and coverage may not be evaluated against one - and it is the column every read predicate
  already filters, which is what makes pruning fire rather than merely be possible. Monthly
  granularity means a partition boundary and a reporting-period boundary are the same instant.
* **`PRIMARY KEY (request_id, start_time)`**, the pair rather than the bare column: PostgreSQL
  requires a partitioned table's primary key to contain every partition column, and the pair is
  the uploader's idempotency key regardless of layout.
* **Partitions from 2026-05 through 2026-12, plus a `DEFAULT` catch.** Backward reaches one
  month earlier than the load is expected to (the load's oldest row is not known until it
  finishes, and an empty partition below the floor is pruned by every read, so slack is free).
  Forward stops where the owner set it. The `DEFAULT` partition is what stops an insert past the
  horizon *failing*: a failed insert is a row that never lands, and a row that never lands above
  a published coverage floor is a silent gap in a period the table is published as covering.
  Measured: `DEFAULT` is pruned for any range a real partition covers, so it costs nothing on
  the normal read path.
* **`spend` is unconstrained `numeric`.** Not `numeric(18,9)`, which is what the neighbouring
  money table uses: the contract's discriminating acceptance vector needs 16 significant digits
  and scale 9 silently stores a different number.
* **No CHECK constraints on the closed-set columns.** A CHECK rejects the uploader's insert,
  which converts a data defect into a coverage gap. The contract's rule is the opposite: store
  an unexpected value verbatim and surface it as a defect.
* **No storage parameters.** PostgreSQL refuses them on a partitioned parent and they do not
  cascade, so any override is a per-partition obligation in perpetuity that the next
  horizon-extension migration will forget. Freezing and statistics are handled by an explicit
  partition-close step instead.

Retention and horizon extension are operational procedures, not code: see
`local/LiteLLM_SpendLogs/physical-design-litellm-spend-logs.md`. In particular, a non-empty
`DEFAULT` partition means the horizon has expired, and `ATTACH PARTITION` fails while `DEFAULT`
holds a row in the incoming range.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l1t2l3m5s6p7"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "litellm_spend_logs"

#: (partition suffix, inclusive lower bound, exclusive upper bound) - half-open, UTC, so
#: adjacent partitions tile exactly and no row belongs to two.
PARTITIONS: tuple[tuple[str, str, str], ...] = (
    ("2026_05", "2026-05-01", "2026-06-01"),
    ("2026_06", "2026-06-01", "2026-07-01"),
    ("2026_07", "2026-07-01", "2026-08-01"),
    ("2026_08", "2026-08-01", "2026-09-01"),
    ("2026_09", "2026-09-01", "2026-10-01"),
    ("2026_10", "2026-10-01", "2026-11-01"),
    ("2026_11", "2026-11-01", "2026-12-01"),
    ("2026_12", "2026-12-01", "2027-01-01"),
)

DEFAULT_PARTITION = f"{TABLE}_default"


def upgrade() -> None:
    # The parent. Raw DDL because PARTITION BY has no SQLAlchemy expression, and the 28 columns
    # are written out literally rather than derived from the model: a migration is frozen
    # history and must not change shape when the model does.
    op.execute(
        f"""
        CREATE TABLE {TABLE} (
            request_id           text        NOT NULL,
            start_time           timestamptz NOT NULL,
            end_time             timestamptz NOT NULL,
            spend                numeric     NOT NULL,
            status               text        NOT NULL,
            call_type            text        NULL,
            model                text        NULL,
            model_group          text        NULL,
            custom_llm_provider  text        NULL,
            prompt_tokens        integer     NOT NULL DEFAULT 0,
            completion_tokens    integer     NOT NULL DEFAULT 0,
            total_tokens         integer     NOT NULL DEFAULT 0,
            request_duration_ms  integer     NULL,
            cache_hit            boolean     NULL,
            session_id           text        NULL,
            api_key              text        NULL,
            user_api_key_alias   text        NULL,
            end_user             text        NULL,
            codemie_request_id   text        NULL,
            codemie_run_id       text        NULL,
            codemie_workflow_id  text        NULL,
            attr_source          text        NOT NULL,
            attr_form            text        NOT NULL,
            attr_project         text        NULL,
            attr_category        text        NULL,
            attr_user_id         text        NULL,
            attr_principal       text        NULL,
            attr_channel         text        NULL,
            PRIMARY KEY (request_id, start_time)
        ) PARTITION BY RANGE (start_time)
        """
    )

    # Created on the parent, so every partition below - and every partition a later
    # horizon-extension migration adds - inherits them without being told to.
    _create_indexes()

    for suffix, lower, upper in PARTITIONS:
        op.execute(
            f"CREATE TABLE {TABLE}_{suffix} PARTITION OF {TABLE} "
            f"FOR VALUES FROM ('{lower} 00:00:00+00') TO ('{upper} 00:00:00+00')"
        )

    # The horizon catch. A row past 2026-12 lands here and a read for its month finds it here,
    # because this partition's constraint is the negation of all the others'.
    op.execute(f"CREATE TABLE {DEFAULT_PARTITION} PARTITION OF {TABLE} DEFAULT")


def _create_indexes() -> None:
    """One primary key and eight secondary indexes, each traceable to an access pattern.

    The partial forms are the point: a partial index costs an insert only when its predicate
    matches, so the sustained write cost on an ordinary post-rollout row is six indexes rather
    than nine. Adding a ninth needs an access pattern, not a hunch.
    """
    # P1 - holder spend by explicit identity. Also serves the project-member holder, with
    # attr_project applied as a filter: attr_user_id is a UUID and far more selective than a
    # project slug, so the selective column leads and a four-column index is not needed.
    op.create_index(
        "ix_spend_logs_user_category_time",
        TABLE,
        ["attr_user_id", "attr_category", "start_time"],
    )
    # P2 - project holder, and the project budget group via attr_project IN (...).
    op.create_index(
        "ix_spend_logs_project_category_time",
        TABLE,
        ["attr_project", "attr_category", "start_time"],
    )
    # P3 - fallback holders. Partial, and that is what makes it nearly free above the identity
    # floor: the uploader's explicit branch leaves attr_principal NULL.
    op.create_index(
        "ix_spend_logs_principal_category_time",
        TABLE,
        ["attr_principal", "attr_category", "start_time"],
        postgresql_where=sa.text("attr_principal IS NOT NULL"),
    )
    # P4 - one CodeMie call. Carries no start_time predicate by design, so it cannot prune and
    # probes one index per partition; that is the cost of a correct answer, and it is what
    # bounds the retention window. Partial because every pre-rollout row carries NULL here by
    # contract and a NULL is never a lookup target.
    op.create_index(
        "ix_spend_logs_run_id",
        TABLE,
        ["codemie_run_id"],
        postgresql_where=sa.text("codemie_run_id IS NOT NULL"),
    )
    # P4 at request grain, and P10's id-set probe. One index serves both.
    op.create_index(
        "ix_spend_logs_request_id",
        TABLE,
        ["codemie_request_id"],
        postgresql_where=sa.text("codemie_request_id IS NOT NULL"),
    )
    # P6 - the ledger branch of the dimension union, and the workflow-execution count. Small:
    # the column is NULL outside a workflow, which is the large majority of rows.
    op.create_index(
        "ix_spend_logs_workflow_time",
        TABLE,
        ["codemie_workflow_id", "start_time"],
        postgresql_where=sa.text("codemie_workflow_id IS NOT NULL"),
    )
    # P7 - the unattributed-spend defect metric, which is expected to match few rows.
    op.create_index(
        "ix_spend_logs_unattributed",
        TABLE,
        ["start_time"],
        postgresql_where=sa.text("attr_form = 'unattributed'"),
    )
    # P9 - the identity-provenance defect metrics. Above the identity floor this holds the
    # defect rows only. On a deployment that never rolls explicit identity out the metric has
    # nothing to count, and this index may be dropped with no read consequence.
    op.create_index(
        "ix_spend_logs_provenance_time",
        TABLE,
        ["attr_source", "start_time"],
        postgresql_where=sa.text("attr_source <> 'explicit'"),
    )


def downgrade() -> None:
    # Dropping the parent drops every partition with it, the DEFAULT one included.
    op.execute(f"DROP TABLE {TABLE}")
