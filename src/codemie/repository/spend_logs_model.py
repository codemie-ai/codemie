# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The `litellm_spend_logs` table model - 31 columns, a published contract.

**The logical schema is not this module's to change.** These 31 columns, their types and their
nullability are contract v2.1 plus the two cache-token columns of migration a8f23cf09c37, handed to
the team that writes the table and already being built against. A column added, renamed, retyped or
made nullable here is a contract break, not a refactor - `codemie_origin` is here because the
contract was bumped to carry it, not because this module grew a column.

**Two places this deliberately departs from the surrounding code, both for reasons that would
otherwise corrupt money:**

1. `spend` is **unconstrained `Numeric()`**, not the `Numeric(18, 9)` that
   `project_spend_tracking` uses for its money columns. Scale 9 is not enough: the contract's
   discriminating acceptance vector is a spend of `0.07573200000000001`, whose shortest
   round-trip decimal needs **16 significant digits**. Measured on PostgreSQL 17 - a
   `numeric(18,9)` column stores it as `0.075732000`, which is a different number and no longer
   round-trips to the source double. Copying the neighbouring convention here silently fails
   the one vector written to catch exactly that.
2. The table is **range-partitioned on `start_time`**, which SQLAlchemy cannot express, so the
   partitioning, the partitions and the `DEFAULT` catch live only in the migration. The indexes
   below are meant to mirror the migrations; nothing checks that mechanically.

**This model is intentionally absent from `src/external/alembic/env.py`.** That is the existing
practice for this table family - `spend_models.py` and `budget_models.py` are absent too, and
every migration touching them is hand-written. Here it is not merely practice but necessary:
autogenerate cannot emit `PARTITION BY`, so a generated migration would create an ordinary
unpartitioned table that silently satisfies every read and loses every property the partitioning
was chosen for.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ColumnElement,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    func,
    literal,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlmodel import Field, SQLModel

SPEND_LOGS_TABLE = "litellm_spend_logs"
SPEND_HOURLY_TABLE = "litellm_spend_hourly"


class LiteLLMSpendLog(SQLModel, table=True):
    """One LiteLLM-billed request attempt.

    A retried call writes **one row per upstream attempt**, so several rows can share a
    `codemie_run_id`. That is correct - each attempt that reached a provider may have cost
    money - and it is why cost is **summed** while requests are **counted distinct**.
    """

    __tablename__ = SPEND_LOGS_TABLE

    # --- identity -----------------------------------------------------------------
    # A row is identified by the pair, never by request_id alone: `request_id` changes
    # namespace with the outcome (a success row's is the response body id, a failure row's is
    # the x-litellm-call-id header), and PostgreSQL requires a partitioned table's primary key
    # to contain every partition column.
    request_id: str = Field(sa_column=Column(Text, primary_key=True, nullable=False))
    start_time: datetime = Field(sa_column=Column(TIMESTAMP(timezone=True), primary_key=True, nullable=False))

    end_time: datetime = Field(sa_column=Column(TIMESTAMP(timezone=True), nullable=False))

    # --- the authoritative figure -------------------------------------------------
    # Declaration order below follows the contract's DDL column-for-column, so the model and
    # the migration can be read side by side.
    spend: Decimal = Field(sa_column=Column(Numeric, nullable=False))

    # Closed set `success` | `failure`, and deliberately **not** CHECK-constrained: any other
    # value is stored verbatim and surfaced as a defect metric. A CHECK would reject the
    # uploader's insert instead, and a row that never lands above the coverage floor is a
    # silent gap in a period the table is published as covering.
    status: str = Field(sa_column=Column(Text, nullable=False))
    call_type: str | None = Field(default=None, sa_column=Column(Text))

    # --- model ---------------------------------------------------------------------
    model: str | None = Field(default=None, sa_column=Column(Text))
    #: The CodeMie-facing alias, and the grouping dimension. Not the same as `model`, which is
    #: fully qualified - grouping by the wrong one splits or merges models unintentionally.
    model_group: str | None = Field(default=None, sa_column=Column(Text))
    custom_llm_provider: str | None = Field(default=None, sa_column=Column(Text))

    # --- volume only, never cost ---------------------------------------------------
    # A sampled row bills 149,444 of 149,854 prompt tokens from cache, so re-pricing from these
    # overstates it by roughly 100x.
    prompt_tokens: int = Field(sa_column=Column(Integer, nullable=False, server_default=text("0")))
    completion_tokens: int = Field(sa_column=Column(Integer, nullable=False, server_default=text("0")))
    total_tokens: int = Field(sa_column=Column(Integer, nullable=False, server_default=text("0")))
    request_duration_ms: int | None = Field(default=None, sa_column=Column(Integer))

    #: A real boolean, normalized at the uploader boundary. The source column is text whose
    #: ordinary value is the literal string "None", which silently defeats a boolean filter.
    #: Kept because it is the only way to observe whether cached requests produce rows at all.
    cache_hit: bool | None = Field(default=None, sa_column=Column(Boolean))

    #: LiteLLM's own session id. **Not a CodeMie conversation id, and never joined on.**
    session_id: str | None = Field(default=None, sa_column=Column(Text))

    api_key: str | None = Field(default=None, sa_column=Column(Text))
    user_api_key_alias: str | None = Field(default=None, sa_column=Column(Text))

    #: Retained verbatim as the audit trail. Opaque to consumers: only the `attr_*` columns are
    #: queried, and only through the holder resolver.
    end_user: str | None = Field(default=None, sa_column=Column(Text))

    # --- correlation ---------------------------------------------------------------
    codemie_request_id: str | None = Field(default=None, sa_column=Column(Text))
    codemie_run_id: str | None = Field(default=None, sa_column=Column(Text))

    #: The first dimension column. NULL outside a workflow and on every pre-rollout row, and
    #: that absence is the normal state rather than a defect.
    codemie_workflow_id: str | None = Field(default=None, sa_column=Column(Text))

    #: The second dimension column: what the request was for, in the closed vocabulary
    #: `TrafficOrigin` names. NULL on every row written before the sending release and on any
    #: thread that published no origin - a hole the class predicates read as "classify this row
    #: by its native columns", never a value to infer. The uploader stores an unrecognised word
    #: verbatim rather than validating it, so the vocabulary can grow without a contract bump and
    #: an unknown value stays visible as a defect instead of being mapped into a known one.
    codemie_origin: str | None = Field(default=None, sa_column=Column(Text))

    # --- attribution ---------------------------------------------------------------
    #: Provenance: `explicit` | `parsed` | `none`. A consumer never branches on
    #: it - the `attr_*` columns mean the same thing either way - but it is what makes an
    #: attribution regression visible instead of silent.
    attr_source: str = Field(sa_column=Column(Text, nullable=False))
    #: Shape of `end_user`: `project_scoped` | `principal` | `unattributed`.
    attr_form: str = Field(sa_column=Column(Text, nullable=False))
    attr_project: str | None = Field(default=None, sa_column=Column(Text))
    attr_category: str | None = Field(default=None, sa_column=Column(Text))
    attr_user_id: str | None = Field(default=None, sa_column=Column(Text))
    #: Populated on fallback rows only - the explicit branch leaves it NULL, which is what
    #: makes the index on it nearly free above the identity floor.
    attr_principal: str | None = Field(default=None, sa_column=Column(Text))
    #: Fallback-only diagnostic, **never a bucketing key**. The explicit branch leaves it NULL
    #: on every row, CLI rows included, so a CLI bucket keyed on it empties as the rollout
    #: proceeds - and an empty bucket is indistinguishable from no CLI spend. Bucket on
    #: `attr_category`, which both paths populate.
    attr_channel: str | None = Field(default=None, sa_column=Column(Text))

    # --- cache token volume --------------------------------------------------------
    # Added after the original DDL by migration a8f23cf09c37, so they follow `attr_channel` here
    # as they do in the table. Subsets of `prompt_tokens`, not additions to it. NULL means the
    # provider reported nothing; 0 means it reported no cache activity - hence no default.
    cache_read_input_tokens: int | None = Field(default=None, sa_column=Column(BigInteger))
    cache_creation_input_tokens: int | None = Field(default=None, sa_column=Column(BigInteger))

    # Mirrors the migrations exactly. Every index traces to a numbered access pattern; the
    # partial forms are what keep the sustained write cost at seven indexes rather than ten,
    # because a partial index costs an insert only when its predicate matches.
    __table_args__ = (
        # P1 holder spend by explicit identity; also the project-member holder (attr_project
        # applied as a filter, since attr_user_id is a UUID and far more selective) and the
        # analytics person-total.
        Index("ix_spend_logs_user_category_time", "attr_user_id", "attr_category", "start_time"),
        # P2 project holder and project budget group.
        Index("ix_spend_logs_project_category_time", "attr_project", "attr_category", "start_time"),
        # P3 fallback holders. Partial: the explicit branch leaves attr_principal NULL.
        Index(
            "ix_spend_logs_principal_category_time",
            "attr_principal",
            "attr_category",
            "start_time",
            postgresql_where=text("attr_principal IS NOT NULL"),
        ),
        # P4 run-grain lookup and the distinct-request count. Partial: every row written before
        # the header shipped carries NULL by contract, and a NULL is never a lookup target.
        Index("ix_spend_logs_run_id", "codemie_run_id", postgresql_where=text("codemie_run_id IS NOT NULL")),
        # P4 request-grain lookup and P10's id-set probe.
        Index(
            "ix_spend_logs_request_id",
            "codemie_request_id",
            postgresql_where=text("codemie_request_id IS NOT NULL"),
        ),
        # P6 ledger branch of the dimension union, and the workflow-execution count.
        Index(
            "ix_spend_logs_workflow_time",
            "codemie_workflow_id",
            "start_time",
            postgresql_where=text("codemie_workflow_id IS NOT NULL"),
        ),
        # P7 unattributed-spend defect metric - expected to match few rows, which is exactly
        # when a partial index is right.
        Index("ix_spend_logs_unattributed", "start_time", postgresql_where=text("attr_form = 'unattributed'")),
        # P9 identity-provenance defect metrics. Small above the identity floor, where the
        # defect rows are the only ones it holds.
        Index(
            "ix_spend_logs_provenance_time",
            "attr_source",
            "start_time",
            postgresql_where=text("attr_source <> 'explicit'"),
        ),
        # P11 window-bounded aggregates (EPMCDME-15537). They filter on start_time alone, so
        # without this every one was a full scan of the month partition. INCLUDE carries the
        # columns the unscoped reads filter or sum, which makes those scans index-only; model and
        # access-scoped or filtered reads still visit the heap for their extra columns.
        Index(
            "ix_spend_logs_time_cover",
            "start_time",
            postgresql_include=[
                "spend",
                "attr_category",
                "codemie_origin",
                "call_type",
                "codemie_workflow_id",
                "codemie_request_id",
                "attr_project",
                "attr_source",
            ],
        ),
    )


#: The ledger columns the hourly projection keeps as its key, in the projection's column order: every
#: column an attribution, access, report-filter, holder, traffic-class or summary-group predicate reads.
#: `end_user` is among them for the personal holder's charged-customer test, at a measured cost of 41 more
#: keys in 138,876. Never a correlation id or a run id - a read naming one is answered from raw rows.
PROJECTED_DIMENSIONS: tuple[str, ...] = (
    "attr_source",
    "attr_form",
    "attr_user_id",
    "attr_principal",
    "attr_project",
    "attr_category",
    "codemie_origin",
    "call_type",
    "codemie_workflow_id",
    "model_group",
    "end_user",
)

#: The two dimensions the ledger declares NOT NULL; the other eight keep the ledger's NULLs.
_REQUIRED_DIMENSIONS = frozenset({"attr_source", "attr_form"})

#: **The exact hourly projection of the ledger (EPMCDME-15537)** - one row per UTC hour and distinct
#: dimension tuple, carrying the exact `numeric` sum of `spend` and the number of ledger rows behind it.
#:
#: A Core `Table` and not a mapped model: the key holds nullable columns, so no primary key exists for a
#: mapper's identity, and nothing here is ever loaded as an object - it is only aggregated. The ledger
#: stays the authority; this table is derived from it in the same transaction as every ledger write, by
#: the statement-level triggers migration `c7f15537b2c3` creates, and can be rebuilt from it at any time.
#: Mirrors that migration, as the ledger model mirrors its own.
LiteLLMSpendHourly = Table(
    SPEND_HOURLY_TABLE,
    LiteLLMSpendLog.metadata,
    Column("hour", TIMESTAMP(timezone=True), nullable=False),
    *(Column(name, Text, nullable=name not in _REQUIRED_DIMENSIONS) for name in PROJECTED_DIMENSIONS),
    Column("spend", Numeric, nullable=False),
    Column("row_count", BigInteger, nullable=False),
)


def _nullable_key_part(column: Column) -> ColumnElement[str]:
    """`coalesce('v' || column, 'n')`: injective per column - NULL, `''` and `'x'` become `n`, `v` and `vx`.

    A plain `UNIQUE` treats NULLs as distinct, and `NULLS NOT DISTINCT` needs PostgreSQL 15.
    """
    return func.coalesce(literal("v").op("||")(column), literal("n"))


# The key the maintenance trigger's `ON CONFLICT` infers, and the time range every projected read scans.
Index(
    "ux_spend_hourly_key",
    LiteLLMSpendHourly.c.hour,
    LiteLLMSpendHourly.c.attr_source,
    LiteLLMSpendHourly.c.attr_form,
    *(
        _nullable_key_part(LiteLLMSpendHourly.c[name])
        for name in PROJECTED_DIMENSIONS
        if name not in _REQUIRED_DIMENSIONS
    ),
    unique=True,
)
# The ledger's own access patterns P1, P2, P3 and P6, at hourly grain: a holder or workflow read narrows the
# projection the way it narrows the ledger instead of scanning every projected row in the window.
Index(
    "ix_spend_hourly_user_time",
    LiteLLMSpendHourly.c.attr_user_id,
    LiteLLMSpendHourly.c.hour,
    postgresql_where=text("attr_user_id IS NOT NULL"),
)
Index(
    "ix_spend_hourly_project_time",
    LiteLLMSpendHourly.c.attr_project,
    LiteLLMSpendHourly.c.attr_category,
    LiteLLMSpendHourly.c.hour,
    postgresql_where=text("attr_project IS NOT NULL"),
)
Index(
    "ix_spend_hourly_principal_time",
    LiteLLMSpendHourly.c.attr_principal,
    LiteLLMSpendHourly.c.hour,
    postgresql_where=text("attr_principal IS NOT NULL"),
)
Index(
    "ix_spend_hourly_workflow_time",
    LiteLLMSpendHourly.c.codemie_workflow_id,
    LiteLLMSpendHourly.c.hour,
    postgresql_where=text("codemie_workflow_id IS NOT NULL"),
)
