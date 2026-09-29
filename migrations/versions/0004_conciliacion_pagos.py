"""conciliación de pagos: DoctoRelacionado de complementos de pago + sugerencias de la IA

Revision ID: 0004_conciliacion_pagos
Revises: 0003_ampliar_columnas_catalogo
Create Date: 2026-09-28
"""
import sqlalchemy as sa

from alembic import op

revision = "0004_conciliacion_pagos"
down_revision = "0003_ampliar_columnas_catalogo"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conciliacion_pagos_relacionados",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("cfdi_pago_id", sa.Integer(), sa.ForeignKey("cfdi_documents.id"), nullable=False),
        sa.Column("uuid_pago", sa.String(length=36), nullable=False),
        sa.Column("uuid_factura_relacionada", sa.String(length=36), nullable=False),
        sa.Column("num_parcialidad", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("imp_saldo_ant", sa.Numeric(18, 2), nullable=True),
        sa.Column("imp_pagado", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("imp_saldo_insoluto", sa.Numeric(18, 2), nullable=True),
        sa.Column("moneda_dr", sa.String(length=10), nullable=True),
        sa.Column("fecha_pago", sa.DateTime(), nullable=True),
        sa.Column("forma_pago", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "cfdi_pago_id", "uuid_factura_relacionada", "num_parcialidad", name="uq_pago_relacionado"
        ),
    )
    op.create_index(
        "ix_conciliacion_pagos_relacionados_rfc_client_id",
        "conciliacion_pagos_relacionados",
        ["rfc_client_id"],
    )
    op.create_index(
        "ix_conciliacion_pagos_relacionados_cfdi_pago_id",
        "conciliacion_pagos_relacionados",
        ["cfdi_pago_id"],
    )
    op.create_index(
        "ix_conciliacion_pagos_relacionados_uuid_pago",
        "conciliacion_pagos_relacionados",
        ["uuid_pago"],
    )
    op.create_index(
        "ix_conciliacion_pagos_relacionados_uuid_factura_relacionada",
        "conciliacion_pagos_relacionados",
        ["uuid_factura_relacionada"],
    )

    op.create_table(
        "conciliacion_sugerencias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column(
            "pago_relacionado_id",
            sa.Integer(),
            sa.ForeignKey("conciliacion_pagos_relacionados.id"),
            nullable=False,
        ),
        sa.Column("factura_cfdi_id", sa.Integer(), sa.ForeignKey("cfdi_documents.id"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="pendiente"),
        sa.Column("resuelto_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resuelto_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_conciliacion_sugerencias_rfc_client_id", "conciliacion_sugerencias", ["rfc_client_id"]
    )
    op.create_index(
        "ix_conciliacion_sugerencias_pago_relacionado_id",
        "conciliacion_sugerencias",
        ["pago_relacionado_id"],
    )
    op.create_index(
        "ix_conciliacion_sugerencias_factura_cfdi_id", "conciliacion_sugerencias", ["factura_cfdi_id"]
    )


def downgrade():
    op.drop_index("ix_conciliacion_sugerencias_factura_cfdi_id", table_name="conciliacion_sugerencias")
    op.drop_index("ix_conciliacion_sugerencias_pago_relacionado_id", table_name="conciliacion_sugerencias")
    op.drop_index("ix_conciliacion_sugerencias_rfc_client_id", table_name="conciliacion_sugerencias")
    op.drop_table("conciliacion_sugerencias")

    op.drop_index(
        "ix_conciliacion_pagos_relacionados_uuid_factura_relacionada",
        table_name="conciliacion_pagos_relacionados",
    )
    op.drop_index(
        "ix_conciliacion_pagos_relacionados_uuid_pago", table_name="conciliacion_pagos_relacionados"
    )
    op.drop_index(
        "ix_conciliacion_pagos_relacionados_cfdi_pago_id", table_name="conciliacion_pagos_relacionados"
    )
    op.drop_index(
        "ix_conciliacion_pagos_relacionados_rfc_client_id", table_name="conciliacion_pagos_relacionados"
    )
    op.drop_table("conciliacion_pagos_relacionados")
