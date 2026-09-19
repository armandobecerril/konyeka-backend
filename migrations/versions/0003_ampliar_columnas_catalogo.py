"""ampliar columnas de catálogo en cfdi_documents y sat_solicitudes_descarga

satcfdi regresa los valores de catálogo (TipoDeComprobante, Moneda,
MetodoPago, FormaPago, UsoCFDI) como objetos `Code`; guardamos solo el
código bare, pero ampliamos las columnas como margen de seguridad para
códigos que no quepan en los anchos originales (ej. String(1), String(5)).

Revision ID: 0003_ampliar_columnas_catalogo
Revises: 0002_efirma_sat_downloads
Create Date: 2026-09-19
"""
import sqlalchemy as sa

from alembic import op

revision = "0003_ampliar_columnas_catalogo"
down_revision = "0002_efirma_sat_downloads"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "sat_solicitudes_descarga",
        "tipo_comprobante",
        type_=sa.String(length=10),
        existing_type=sa.String(length=1),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "tipo_comprobante",
        type_=sa.String(length=10),
        existing_type=sa.String(length=1),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "moneda",
        type_=sa.String(length=10),
        existing_type=sa.String(length=5),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "metodo_pago",
        type_=sa.String(length=10),
        existing_type=sa.String(length=5),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "forma_pago",
        type_=sa.String(length=10),
        existing_type=sa.String(length=5),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "uso_cfdi",
        type_=sa.String(length=10),
        existing_type=sa.String(length=5),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "cfdi_documents",
        "uso_cfdi",
        type_=sa.String(length=5),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "forma_pago",
        type_=sa.String(length=5),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "metodo_pago",
        type_=sa.String(length=5),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "moneda",
        type_=sa.String(length=5),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
    op.alter_column(
        "cfdi_documents",
        "tipo_comprobante",
        type_=sa.String(length=1),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
    op.alter_column(
        "sat_solicitudes_descarga",
        "tipo_comprobante",
        type_=sa.String(length=1),
        existing_type=sa.String(length=10),
        existing_nullable=True,
    )
