"""amplía cfdi_documents con más columnas del XML (serie, folio, regímenes,
desglose de impuestos, complementos) para la bóveda de facturas

Revision ID: 0005_ampliar_cfdi_columnas
Revises: 0004_conciliacion_pagos
Create Date: 2026-10-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0005_ampliar_cfdi_columnas"
down_revision = "0004_conciliacion_pagos"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cfdi_documents", sa.Column("serie", sa.String(length=25), nullable=True))
    op.add_column("cfdi_documents", sa.Column("folio", sa.String(length=40), nullable=True))
    op.add_column("cfdi_documents", sa.Column("version", sa.String(length=10), nullable=True))
    op.add_column("cfdi_documents", sa.Column("lugar_expedicion", sa.String(length=10), nullable=True))
    op.add_column("cfdi_documents", sa.Column("exportacion", sa.String(length=5), nullable=True))
    op.add_column("cfdi_documents", sa.Column("condiciones_pago", sa.String(length=255), nullable=True))
    op.add_column("cfdi_documents", sa.Column("descuento", sa.Numeric(18, 2), nullable=True))
    op.add_column("cfdi_documents", sa.Column("tipo_cambio", sa.Numeric(18, 6), nullable=True))
    op.add_column("cfdi_documents", sa.Column("total_impuestos_trasladados", sa.Numeric(18, 2), nullable=True))
    op.add_column("cfdi_documents", sa.Column("total_impuestos_retenidos", sa.Numeric(18, 2), nullable=True))
    op.add_column("cfdi_documents", sa.Column("regimen_fiscal_emisor", sa.String(length=10), nullable=True))
    op.add_column("cfdi_documents", sa.Column("regimen_fiscal_receptor", sa.String(length=10), nullable=True))
    op.add_column("cfdi_documents", sa.Column("domicilio_fiscal_receptor", sa.String(length=10), nullable=True))
    op.add_column("cfdi_documents", sa.Column("impuestos_desglose", sa.JSON(), nullable=True))
    op.add_column("cfdi_documents", sa.Column("complementos", sa.JSON(), nullable=True))
    op.add_column(
        "cfdi_documents",
        sa.Column("tiene_complemento_combustible", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_cfdi_documents_tiene_complemento_combustible",
        "cfdi_documents",
        ["tiene_complemento_combustible"],
    )


def downgrade():
    op.drop_index("ix_cfdi_documents_tiene_complemento_combustible", table_name="cfdi_documents")
    op.drop_column("cfdi_documents", "tiene_complemento_combustible")
    op.drop_column("cfdi_documents", "complementos")
    op.drop_column("cfdi_documents", "impuestos_desglose")
    op.drop_column("cfdi_documents", "domicilio_fiscal_receptor")
    op.drop_column("cfdi_documents", "regimen_fiscal_receptor")
    op.drop_column("cfdi_documents", "regimen_fiscal_emisor")
    op.drop_column("cfdi_documents", "total_impuestos_retenidos")
    op.drop_column("cfdi_documents", "total_impuestos_trasladados")
    op.drop_column("cfdi_documents", "tipo_cambio")
    op.drop_column("cfdi_documents", "descuento")
    op.drop_column("cfdi_documents", "condiciones_pago")
    op.drop_column("cfdi_documents", "exportacion")
    op.drop_column("cfdi_documents", "lugar_expedicion")
    op.drop_column("cfdi_documents", "version")
    op.drop_column("cfdi_documents", "folio")
    op.drop_column("cfdi_documents", "serie")
