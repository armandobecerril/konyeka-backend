"""estados de cuenta bancarios por cliente y mes + conciliación contra CFDIs

Revision ID: 0009_bancos
Revises: 0008_monto_pago_moneda_p
Create Date: 2026-10-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0009_bancos"
down_revision = "0008_monto_pago_moneda_p"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bancos_cuentas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("banco", sa.String(60), nullable=False),
        sa.Column("alias", sa.String(120), nullable=True),
        sa.Column("numero", sa.String(30), nullable=True),
        sa.Column("clabe", sa.String(18), nullable=True),
        sa.Column("moneda", sa.String(3), nullable=False, server_default="MXN"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bancos_cuentas_rfc_client_id", "bancos_cuentas", ["rfc_client_id"])

    op.create_table(
        "bancos_estados_cuenta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("cuenta_id", sa.Integer(), sa.ForeignKey("bancos_cuentas.id"), nullable=False),
        sa.Column("periodo", sa.String(7), nullable=False),
        sa.Column("archivo_nombre", sa.String(255), nullable=False),
        sa.Column("formato", sa.String(10), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=True),
        sa.Column("saldo_inicial", sa.Numeric(18, 2), nullable=True),
        sa.Column("saldo_final", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_cargos", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_abonos", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("num_movimientos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("banco_detectado", sa.String(60), nullable=True),
        sa.Column("advertencias", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("cuenta_id", "periodo", name="uq_estado_cuenta_periodo"),
    )
    op.create_index("ix_bancos_estados_cuenta_rfc_client_id", "bancos_estados_cuenta", ["rfc_client_id"])
    op.create_index("ix_bancos_estados_cuenta_cuenta_id", "bancos_estados_cuenta", ["cuenta_id"])
    op.create_index("ix_bancos_estados_cuenta_periodo", "bancos_estados_cuenta", ["periodo"])

    op.create_table(
        "bancos_movimientos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("cuenta_id", sa.Integer(), sa.ForeignKey("bancos_cuentas.id"), nullable=False),
        sa.Column(
            "estado_cuenta_id",
            sa.Integer(),
            sa.ForeignKey("bancos_estados_cuenta.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("renglon", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False, server_default=""),
        sa.Column("referencia", sa.String(120), nullable=True),
        sa.Column("cargo", sa.Numeric(18, 2), nullable=True),
        sa.Column("abono", sa.Numeric(18, 2), nullable=True),
        sa.Column("saldo", sa.Numeric(18, 2), nullable=True),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("clasificacion", sa.String(30), nullable=True),
        sa.Column("nota", sa.Text(), nullable=True),
    )
    op.create_index("ix_bancos_movimientos_rfc_client_id", "bancos_movimientos", ["rfc_client_id"])
    op.create_index("ix_bancos_movimientos_cuenta_id", "bancos_movimientos", ["cuenta_id"])
    op.create_index("ix_bancos_movimientos_estado_cuenta_id", "bancos_movimientos", ["estado_cuenta_id"])
    op.create_index("ix_bancos_movimientos_fecha", "bancos_movimientos", ["fecha"])

    op.create_table(
        "bancos_vinculos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column(
            "movimiento_id",
            sa.Integer(),
            sa.ForeignKey("bancos_movimientos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cfdi_id", sa.Integer(), sa.ForeignKey("cfdi_documents.id"), nullable=True),
        sa.Column("tipo_vinculo", sa.String(20), nullable=False),
        sa.Column("monto", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("motivo", sa.Text(), nullable=False, server_default=""),
        sa.Column("origen", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("estado", sa.String(20), nullable=False, server_default="sugerida"),
        sa.Column("resuelto_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resuelto_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bancos_vinculos_rfc_client_id", "bancos_vinculos", ["rfc_client_id"])
    op.create_index("ix_bancos_vinculos_movimiento_id", "bancos_vinculos", ["movimiento_id"])
    op.create_index("ix_bancos_vinculos_cfdi_id", "bancos_vinculos", ["cfdi_id"])


def downgrade():
    op.drop_table("bancos_vinculos")
    op.drop_table("bancos_movimientos")
    op.drop_table("bancos_estados_cuenta")
    op.drop_table("bancos_cuentas")
