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
    tipo_comprobante: Mapped[str | None] = mapped_column(String(10), nullable=True)  # I/E/N/P/T

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


class ColumnaPreferencia(Base):
    """Qué columnas extra ('cols' en la URL) dejó marcadas cada usuario para
    la bóveda de facturas de cada cliente -- para que la próxima vez que
    entre (sin filtros en la URL todavía) la tabla ya le aparezca con esa
    personalización, en vez de tener que volver a marcarlas."""

    __tablename__ = "cfdi_columna_preferencias"
    __table_args__ = (
        UniqueConstraint("user_id", "rfc_client_id", name="uq_columna_pref_user_cliente"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    columnas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
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
    tipo_comprobante: Mapped[str | None] = mapped_column(String(10), nullable=True)

    emisor_rfc: Mapped[str] = mapped_column(String(13), nullable=False)
    emisor_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receptor_rfc: Mapped[str] = mapped_column(String(13), nullable=False)
    receptor_nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)

    fecha_emision: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    moneda: Mapped[str | None] = mapped_column(String(10), nullable=True)
    metodo_pago: Mapped[str | None] = mapped_column(String(10), nullable=True)
    forma_pago: Mapped[str | None] = mapped_column(String(10), nullable=True)
    uso_cfdi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    estado_sat: Mapped[str] = mapped_column(String(20), nullable=False, default="vigente")

    # Datos adicionales del comprobante, para que la bóveda de facturas
    # pueda mostrar las mismas columnas que un ERP como MyAdmin sin tener
    # que volver a abrir el XML. El desglose de impuestos por tasa (IVA 8%,
    # IVA 16%, IEPS, retenciones, etc.) no se guarda como una columna fija
    # por cada tasa posible —son decenas y casi siempre vienen vacías—, sino
    # como JSON en `impuestos_desglose`: cada factura trae solo las tasas
    # que de verdad tiene, y la tabla arma las columnas que correspondan.
    serie: Mapped[str | None] = mapped_column(String(25), nullable=True)
    folio: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[str | None] = mapped_column(String(10), nullable=True)
    lugar_expedicion: Mapped[str | None] = mapped_column(String(10), nullable=True)
    exportacion: Mapped[str | None] = mapped_column(String(5), nullable=True)
    condiciones_pago: Mapped[str | None] = mapped_column(String(255), nullable=True)
    descuento: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tipo_cambio: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    total_impuestos_trasladados: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_impuestos_retenidos: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    regimen_fiscal_emisor: Mapped[str | None] = mapped_column(String(10), nullable=True)
    regimen_fiscal_receptor: Mapped[str | None] = mapped_column(String(10), nullable=True)
    domicilio_fiscal_receptor: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Desglose de impuestos tal cual viene en el nodo <Impuestos> del XML:
    # {"traslados": [{"impuesto","tipo_factor","tasa_o_cuota","importe"}, ...],
    #  "retenciones": [...]}
    impuestos_desglose: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Nombres de los complementos presentes en el XML (TimbreFiscalDigital,
    # Pagos, Nomina12, EstadoDeCuentaDeCombustibles, etc.), para el filtro
    # "Leer complementos" de la bóveda.
    complementos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tiene_complemento_combustible: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false", index=True
    )

    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
