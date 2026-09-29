"""Conciliación de pagos: relación entre CFDIs de pago (complemento) y las
facturas PPD que liquidan, más las sugerencias que arma el motor de
emparejamiento inteligente para los casos que no cruzan por UUID exacto."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
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


class PagoRelacionado(Base):
    """Un renglón `DoctoRelacionado` dentro de un CFDI de tipo Pago (P):
    documenta que ese pago liquidó (total o parcialmente) la factura con
    uuid_factura_relacionada. Se extrae al indexar el XML del complemento."""

    __tablename__ = "conciliacion_pagos_relacionados"
    __table_args__ = (
        UniqueConstraint(
            "cfdi_pago_id", "uuid_factura_relacionada", "num_parcialidad", name="uq_pago_relacionado"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    cfdi_pago_id: Mapped[int] = mapped_column(ForeignKey("cfdi_documents.id"), nullable=False, index=True)

    uuid_pago: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    uuid_factura_relacionada: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    num_parcialidad: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    imp_saldo_ant: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    imp_pagado: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    imp_saldo_insoluto: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    moneda_dr: Mapped[str | None] = mapped_column(String(10), nullable=True)

    fecha_pago: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    forma_pago: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConciliacionSugerencia(Base):
    """Sugerencia del motor de emparejamiento inteligente: un pago huérfano
    (su UUID relacionado no está entre los CFDIs descargados) que probablemente
    corresponde a una factura PPD pendiente/parcial. El contador aprueba o
    rechaza; solo lo aprobado cuenta para la conciliación."""

    __tablename__ = "conciliacion_sugerencias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)

    pago_relacionado_id: Mapped[int] = mapped_column(
        ForeignKey("conciliacion_pagos_relacionados.id"), nullable=False, index=True
    )
    factura_cfdi_id: Mapped[int] = mapped_column(ForeignKey("cfdi_documents.id"), nullable=False, index=True)

    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    motivo: Mapped[str] = mapped_column(Text, nullable=False, default="")

    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente")  # pendiente|aprobada|rechazada

    resuelto_por_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resuelto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
