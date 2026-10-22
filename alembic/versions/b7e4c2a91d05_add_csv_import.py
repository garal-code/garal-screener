"""add_csv_import

Import templates per user and the broker ID of imported movements (issue #36).

Revision ID: b7e4c2a91d05
Revises: e063f06db30a
Create Date: 2026-10-22 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4c2a91d05"
down_revision: str | None = "e063f06db30a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plantillas_importacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "nombre", name="uq_plantillas_importacion_user_nombre"
        ),
    )
    op.create_index(
        op.f("ix_plantillas_importacion_id"),
        "plantillas_importacion",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plantillas_importacion_user_id"),
        "plantillas_importacion",
        ["user_id"],
        unique=False,
    )

    # Nullable column: existing movements keep NULL, and NULLs never collide in
    # the unique constraint, so no backfill is needed. SQLite needs batch mode
    # to add a constraint.
    with op.batch_alter_table("movimientos", schema=None) as batch_op:
        batch_op.add_column(sa.Column("referencia_externa", sa.String(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_movimientos_cartera_referencia_externa",
            ["cartera_id", "referencia_externa"],
        )


def downgrade() -> None:
    with op.batch_alter_table("movimientos", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_movimientos_cartera_referencia_externa", type_="unique"
        )
        batch_op.drop_column("referencia_externa")

    op.drop_index(
        op.f("ix_plantillas_importacion_user_id"), table_name="plantillas_importacion"
    )
    op.drop_index(
        op.f("ix_plantillas_importacion_id"), table_name="plantillas_importacion"
    )
    op.drop_table("plantillas_importacion")
