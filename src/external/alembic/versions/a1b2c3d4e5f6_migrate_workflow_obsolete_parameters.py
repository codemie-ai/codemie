"""migrate workflow obsolete parameters

Revision ID: a1b2c3d4e5f6
Revises: 772de5bd70b1
Create Date: 2025-10-20 15:30:00.000000

This migration performs the following parameter migrations in workflows table:
- keep_history → include_in_llm_history (both yaml_config and states)
- use_flyweight_history: true → store_in_context: true
- use_flyweight_history: false → (removed as redundant)
- store_iter_task_in_history: true → store_in_context: true
- store_iter_task_in_history: false → (removed as redundant)

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '772de5bd70b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Migrate obsolete workflow parameters to new equivalents."""

    # ========================================================================
    # STEP 1: Migrate keep_history → include_in_llm_history in yaml_config
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'([ \\t]*)keep_history:([ \\t]*)(true|false)',
                E'\\1include_in_llm_history:\\2\\3',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config LIKE '%keep_history%'
    """)

    # ========================================================================
    # STEP 2: Migrate use_flyweight_history in yaml_config
    # ========================================================================

    # Replace use_flyweight_history: true with store_in_context: true
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'([ \\t]*)use_flyweight_history:([ \\t]*)true',
                E'\\1store_in_context:\\2true',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config ~* 'use_flyweight_history:\\s*true'
    """)

    # Remove use_flyweight_history: false (redundant, default is false)
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'[ \\t]*use_flyweight_history:[ \\t]*false[ \\t]*\\n?',
                '',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config ~* 'use_flyweight_history:\\s*false'
    """)

    # ========================================================================
    # STEP 3: Migrate store_iter_task_in_history in yaml_config
    # ========================================================================

    # Replace store_iter_task_in_history: true with store_in_context: true
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'([ \\t]*)store_iter_task_in_history:([ \\t]*)true',
                E'\\1store_in_context:\\2true',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config ~* 'store_iter_task_in_history:\\s*true'
    """)

    # Remove store_iter_task_in_history: false (redundant)
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'[ \\t]*store_iter_task_in_history:[ \\t]*false[ \\t]*\\n?',
                '',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config ~* 'store_iter_task_in_history:\\s*false'
    """)

    # ========================================================================
    # STEP 4: Migrate keep_history → include_in_llm_history in states JSONB
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            states = (
                SELECT jsonb_agg(
                    CASE
                        WHEN state->'next' ? 'keep_history' THEN
                            jsonb_set(
                                jsonb_set(
                                    state,
                                    '{next,include_in_llm_history}',
                                    (state->'next'->'keep_history')
                                ),
                                '{next}',
                                (state->'next') - 'keep_history'
                            )
                        ELSE
                            state
                    END
                )
                FROM jsonb_array_elements(workflows.states) AS state
            ),
            update_date = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(workflows.states) AS state
            WHERE state->'next' ? 'keep_history'
        )
    """)

    # ========================================================================
    # STEP 5: Migrate use_flyweight_history in states JSONB
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            states = (
                SELECT jsonb_agg(
                    CASE
                        WHEN state->'next' ? 'use_flyweight_history'
                         AND (state->'next'->>'use_flyweight_history')::boolean = true THEN
                            jsonb_set(
                                jsonb_set(
                                    state,
                                    '{next,store_in_context}',
                                    'true'::jsonb
                                ),
                                '{next}',
                                (state->'next') - 'use_flyweight_history'
                            )
                        WHEN state->'next' ? 'use_flyweight_history' THEN
                            jsonb_set(
                                state,
                                '{next}',
                                (state->'next') - 'use_flyweight_history'
                            )
                        ELSE
                            state
                    END
                )
                FROM jsonb_array_elements(workflows.states) AS state
            ),
            update_date = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(workflows.states) AS state
            WHERE state->'next' ? 'use_flyweight_history'
        )
    """)

    # ========================================================================
    # STEP 6: Migrate store_iter_task_in_history in states JSONB
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            states = (
                SELECT jsonb_agg(
                    CASE
                        WHEN state->'next' ? 'store_iter_task_in_history'
                         AND (state->'next'->>'store_iter_task_in_history')::boolean = true THEN
                            jsonb_set(
                                jsonb_set(
                                    state,
                                    '{next,store_in_context}',
                                    'true'::jsonb
                                ),
                                '{next}',
                                (state->'next') - 'store_iter_task_in_history'
                            )
                        WHEN state->'next' ? 'store_iter_task_in_history' THEN
                            jsonb_set(
                                state,
                                '{next}',
                                (state->'next') - 'store_iter_task_in_history'
                            )
                        ELSE
                            state
                    END
                )
                FROM jsonb_array_elements(workflows.states) AS state
            ),
            update_date = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(workflows.states) AS state
            WHERE state->'next' ? 'store_iter_task_in_history'
        )
    """)


def downgrade() -> None:
    """Revert the parameter migration (restore old parameters)."""

    # ========================================================================
    # STEP 1: Revert include_in_llm_history → keep_history in yaml_config
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'([ \\t]*)include_in_llm_history:([ \\t]*)(true|false)',
                E'\\1keep_history:\\2\\3',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config LIKE '%include_in_llm_history%'
    """)

    # ========================================================================
    # STEP 2: Revert store_in_context → use_flyweight_history in yaml_config
    # ========================================================================

    # Revert store_in_context: true → use_flyweight_history: true
    # Note: This is approximate - we can't distinguish between store_in_context
    # that came from use_flyweight_history vs store_iter_task_in_history
    op.execute("""
        UPDATE workflows
        SET
            yaml_config = regexp_replace(
                yaml_config,
                E'([ \\t]*)store_in_context:([ \\t]*)true',
                E'\\1use_flyweight_history:\\2true',
                'g'
            ),
            update_date = NOW()
        WHERE yaml_config ~* 'store_in_context:\\s*true'
    """)

    # ========================================================================
    # STEP 3: Revert include_in_llm_history → keep_history in states JSONB
    # ========================================================================
    op.execute("""
        UPDATE workflows
        SET
            states = (
                SELECT jsonb_agg(
                    CASE
                        WHEN state->'next' ? 'include_in_llm_history' THEN
                            jsonb_set(
                                jsonb_set(
                                    state,
                                    '{next,keep_history}',
                                    (state->'next'->'include_in_llm_history')
                                ),
                                '{next}',
                                (state->'next') - 'include_in_llm_history'
                            )
                        ELSE
                            state
                    END
                )
                FROM jsonb_array_elements(workflows.states) AS state
            ),
            update_date = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(workflows.states) AS state
            WHERE state->'next' ? 'include_in_llm_history'
        )
    """)

    # ========================================================================
    # STEP 4: Revert store_in_context → use_flyweight_history in states JSONB
    # ========================================================================
    # Note: This reversion is approximate and may not perfectly restore original state
    op.execute("""
        UPDATE workflows
        SET
            states = (
                SELECT jsonb_agg(
                    CASE
                        WHEN state->'next' ? 'store_in_context'
                         AND (state->'next'->>'store_in_context')::boolean = true THEN
                            jsonb_set(
                                jsonb_set(
                                    state,
                                    '{next,use_flyweight_history}',
                                    'true'::jsonb
                                ),
                                '{next}',
                                (state->'next') - 'store_in_context'
                            )
                        WHEN state->'next' ? 'store_in_context' THEN
                            jsonb_set(
                                state,
                                '{next}',
                                (state->'next') - 'store_in_context'
                            )
                        ELSE
                            state
                    END
                )
                FROM jsonb_array_elements(workflows.states) AS state
            ),
            update_date = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(workflows.states) AS state
            WHERE state->'next' ? 'store_in_context'
        )
    """)
