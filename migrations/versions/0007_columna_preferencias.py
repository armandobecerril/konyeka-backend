"""agrega cfdi_columna_preferencias para recordar, por usuario y por
cliente, qué columnas extra de la bóveda de facturas quedaron marcadas

Revision ID: 0007_columna_preferencias
Revises: 0006_documentos_sat
Create Date: 2026-10-01
"""
import sqlalchemy as sa

from alembic import op

revision = "0007_columna_preferencias"
down_revision = "0006_documentos_sat"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cfdi_columna_preferencias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("columnas", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("user_id", "rfc_client_id", name="uq_columna_pref_user_cliente"),
    )
    op.create_index("ix_cfdi_columna_preferencias_user_id", "cfdi_columna_preferencias", ["user_id"])
    op.create_index(
        "ix_cfdi_columna_preferencias_rfc_client_id", "cfdi_columna_preferencias", ["rfc_client_id"]
    )


def downgrade():
    op.drop_index("ix_cfdi_columna_preferencias_rfc_client_id", table_name="cfdi_columna_preferencias")
    op.drop_index("ix_cfdi_columna_preferencias_user_id", table_name="cfdi_columna_preferencias")
    op.drop_table("cfdi_columna_preferencias")
