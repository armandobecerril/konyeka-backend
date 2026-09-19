"""Solicitudes de descarga masiva al SAT y el índice de CFDIs descargados."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SolicitudDescarga(Base):
    __tablename__ = "sat_solicitudes_descarga"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)

    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # "emitidas" | "recibidas"
    fecha_inicial: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_final: Mapped[date] = mapped_column(Date, nullable=False)
    tipo_comprobante: Mapped[str | None] = mapped_column(String(1), nullable=True)  # I/E/N/P/T

    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="solicitada")
    id_solicitud_sat: Mapped[str | None] = mapped_column(String(80), nullable=True)
    paquetes_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    numero_cfdis: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mensaje_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CfdiDocument(Base):
    __tablename__ = "cfdi_documents"
    __table_args__ = (UniqueConstraint("rfc_client_id", "uuid", name="uq_cfdi_rfc_client_uuid"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    solicitud_id: Mapped[int | None] = mapped_column(
        ForeignKey("sat_solicitudes_descarga.id"), nullable=True
    )

    uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # "emitido" | "recibido"
    tipo_comprobante: Mapped[str | None] = mapped_column(String(1), nullable=True)

    emisor_rfc: Mapped[str] = mapped_column(String(13), nullable=False)
    emisor_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receptor_rfc: Mapped[str] = mapped_column(String(13), nullable=False)
    receptor_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)

    fecha_emision: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(5), nullable=True)
    metodo_pago: Mapped[str | None] = mapped_column(String(5), nullable=True)
    forma_pago: Mapped[str | None] = mapped_column(String(5), nullable=True)
    uso_cfdi: Mapped[str | None] = mapped_column(String(5), nullable=True)
    estado_sat: Mapped[str] = mapped_column(String(20), nullable=False, default="vigente")

    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
