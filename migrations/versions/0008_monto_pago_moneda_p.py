"""agrega cfdi_documents.monto_pago (importe real de los CFDI tipo Pago) y
marca para re-procesar la moneda de los complementos de pago que se
guardaron con el "XXX" del encabezado en vez de MonedaP

Revision ID: 0008_monto_pago_moneda_p
Revises: 0007_columna_preferencias
Create Date: 2026-10-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0008_monto_pago_moneda_p"
down_revision = "0007_columna_preferencias"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cfdi_documents", sa.Column("monto_pago", sa.Numeric(18, 2), nullable=True))
    op.create_index("ix_cfdi_documents_fecha_emision", "cfdi_documents", ["rfc_client_id", "fecha_emision"])
    # La moneda real de un CFDI P vive en MonedaP; el backfill
    # (sat_downloads.service.backfill_pagos_moneda) relee el XML de los que
    # queden con moneda NULL. Aquí solo se "invalida" lo que se guardó mal.
    op.execute(
        "UPDATE cfdi_documents SET moneda = NULL WHERE tipo_comprobante = 'P' AND (moneda IS NULL OR moneda <> 'MXN')"
    )


def downgrade():
    op.drop_index("ix_cfdi_documents_fecha_emision", table_name="cfdi_documents")
    op.drop_column("cfdi_documents", "monto_pago")
