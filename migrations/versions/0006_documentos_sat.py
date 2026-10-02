"""agrega documentos_sat para el historial de Opinión de Cumplimiento y
Constancia de Situación Fiscal descargados vía RPA

Revision ID: 0006_documentos_sat
Revises: 0005_ampliar_cfdi_columnas
Create Date: 2026-10-01
"""
import sqlalchemy as sa

from alembic import op

revision = "0006_documentos_sat"
down_revision = "0005_ampliar_cfdi_columnas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documentos_sat",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="solicitado"),
        sa.Column("resultado", sa.String(length=20), nullable=True),
        sa.Column("mensaje_error", sa.String(length=500), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("solicitado_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completado_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_documentos_sat_rfc_client_id", "documentos_sat", ["rfc_client_id"])


def downgrade():
    op.drop_index("ix_documentos_sat_rfc_client_id", table_name="documentos_sat")
    op.drop_table("documentos_sat")
