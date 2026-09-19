"""bóveda de e.firma + solicitudes de descarga y CFDIs del SAT

Revision ID: 0002_efirma_sat_downloads
Revises: 0001_initial
Create Date: 2026-09-18
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_efirma_sat_downloads"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "efirma_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("cer_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("key_bytes_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("rfc_titular", sa.String(length=13), nullable=False),
        sa.Column("nombre_titular", sa.String(length=255), nullable=True),
        sa.Column("numero_serie", sa.String(length=40), nullable=False),
        sa.Column("vigencia_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigencia_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_efirma_credentials_rfc_client_id",
        "efirma_credentials",
        ["rfc_client_id"],
        unique=True,
    )

    op.create_table(
        "sat_solicitudes_descarga",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("fecha_inicial", sa.Date(), nullable=False),
        sa.Column("fecha_final", sa.Date(), nullable=False),
        sa.Column("tipo_comprobante", sa.String(length=1), nullable=True),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="solicitada"),
        sa.Column("id_solicitud_sat", sa.String(length=80), nullable=True),
        sa.Column("paquetes_ids", sa.JSON(), nullable=True),
        sa.Column("numero_cfdis", sa.Integer(), nullable=True),
        sa.Column("mensaje_error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_sat_solicitudes_descarga_rfc_client_id",
        "sat_solicitudes_descarga",
        ["rfc_client_id"],
    )

    op.create_table(
        "cfdi_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rfc_client_id", sa.Integer(), sa.ForeignKey("rfc_clients.id"), nullable=False),
        sa.Column(
            "solicitud_id", sa.Integer(), sa.ForeignKey("sat_solicitudes_descarga.id"), nullable=True
        ),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("tipo", sa.String(length=10), nullable=False),
        sa.Column("tipo_comprobante", sa.String(length=1), nullable=True),
        sa.Column("emisor_rfc", sa.String(length=13), nullable=False),
        sa.Column("emisor_nombre", sa.String(length=255), nullable=True),
        sa.Column("receptor_rfc", sa.String(length=13), nullable=False),
        sa.Column("receptor_nombre", sa.String(length=255), nullable=True),
        sa.Column("fecha_emision", sa.DateTime(), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=True),
        sa.Column("moneda", sa.String(length=5), nullable=True),
        sa.Column("metodo_pago", sa.String(length=5), nullable=True),
        sa.Column("forma_pago", sa.String(length=5), nullable=True),
        sa.Column("uso_cfdi", sa.String(length=5), nullable=True),
        sa.Column("estado_sat", sa.String(length=20), nullable=False, server_default="vigente"),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("rfc_client_id", "uuid", name="uq_cfdi_rfc_client_uuid"),
    )
    op.create_index("ix_cfdi_documents_rfc_client_id", "cfdi_documents", ["rfc_client_id"])
    op.create_index("ix_cfdi_documents_uuid", "cfdi_documents", ["uuid"])


def downgrade():
    op.drop_table("cfdi_documents")
    op.drop_table("sat_solicitudes_descarga")
    op.drop_table("efirma_credentials")
