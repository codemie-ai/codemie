"""add money spent qualification to conversation_metrics

Revision ID: c9a1b2d3e4f5
Revises: l1t2l3m5s6p7
Create Date: 2026-08-28 00:00:00.000000

`conversation_metrics.total_money_spent` is a settled subtotal that has always been stored as a bare
number, so nothing that reads it - the metrics endpoint, or `leaderboard/collector.py`'s
`SUM(cm.total_money_spent)` - could tell a final cost from a partial one. These two booleans are that
missing qualification, spelled exactly as on `TokensUsage` and `GeneratedMessage` where the same pair
already travels beside the same figure.

Both are nullable with no server default, deliberately. A row written before this migration was never
qualified by anybody, and `FALSE` there would assert "this figure is final" about figures that are
mostly the opposite: on a deployment where chat streams through LiteLLM the pre-existing rows are
overwhelmingly unsettled subtotals. NULL says "unqualified" and the endpoint omits the two fields
(`response_model_exclude_none`).

**What this migration does not fix, stated plainly: the existing figures.** No data is touched, and
NULL withholds only the *qualification* - the number in `total_money_spent` stays exactly as it was
written. The pre-fix code stored `0` for a streamed chat whose cost had not settled, so such a row
still publishes a bare `$0.0000` with no suffix at all: a fabricated zero, on the wire and on screen
indistinguishable from a conversation that genuinely cost nothing. It self-heals only when the
conversation's next turn recalculates the row (`ConversationMetrics.calculate_metrics`, via
`_upsert_conversation_metrics`); a conversation nobody sends another message to keeps its fabricated
zero permanently.

**And that is why there is no data migration here.** A stored `0` carries nothing that separates the
two cases - not the new columns, whose NULL only proves the row predates this revision, and not the
token counts, since a zero-priced model produces real traffic at a real zero. Backfilling
`money_spent_unavailable = TRUE` over those rows would stamp "no figure is coming" onto every measured
free conversation as well, replacing a wrong number on some rows with a wrong claim on all of them -
the larger error, and the one a reader cannot recover from. The bare zero is left visible instead.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9a1b2d3e4f5"
down_revision: Union[str, None] = "l1t2l3m5s6p7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "conversation_metrics"
COLUMNS = ("money_spent_pending", "money_spent_unavailable")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column(TABLE, column)
