"""Estados de cuenta bancarios por cliente y por mes, y su conciliación
contra los CFDIs descargados del SAT.

El contador carga cada mes el estado de cuenta de cada cuenta del cliente
(BBVA, Santander, Banorte...). Cada renglón es un MovimientoBancario; el
motor de cruce (matching.py) lo relaciona con el CFDI que lo respalda
(factura PUE, complemento de pago REP, factura PPD aún sin REP, nómina) y
el contador aprueba, corrige o clasifica lo que no cruza (efectivo,
comisión, traspaso...). Eso es la base para decidir la deducibilidad.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
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


class CuentaBancaria(Base):
    __tablename__ = "bancos_cuentas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    banco: Mapped[str] = mapped_column(String(60), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(120), nullable=True)
    numero: Mapped[str | None] = mapped_column(String(30), nullable=True)  # últimos dígitos
    clabe: Mapped[str | None] = mapped_column(String(18), nullable=True)
    moneda: Mapped[str] = mapped_column(String(3), nullable=False, default="MXN", server_default="MXN")
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EstadoCuenta(Base):
    """Un archivo de estado de cuenta (un mes de una cuenta)."""

    __tablename__ = "bancos_estados_cuenta"
    __table_args__ = (UniqueConstraint("cuenta_id", "periodo", name="uq_estado_cuenta_periodo"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("bancos_cuentas.id"), nullable=False, index=True)
    periodo: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # "YYYY-MM"

    archivo_nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    formato: Mapped[str] = mapped_column(String(10), nullable=False)  # csv | xlsx | pdf
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    saldo_inicial: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    saldo_final: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_cargos: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_abonos: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    num_movimientos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    banco_detectado: Mapped[str | None] = mapped_column(String(60), nullable=True)
    advertencias: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MovimientoBancario(Base):
    __tablename__ = "bancos_movimientos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("bancos_cuentas.id"), nullable=False, index=True)
    estado_cuenta_id: Mapped[int] = mapped_column(
        ForeignKey("bancos_estados_cuenta.id", ondelete="CASCADE"), nullable=False, index=True
    )
    renglon: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    referencia: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cargo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    abono: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    saldo: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # cargo | abono
    monto: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # siempre positivo

    # pendiente | sugerido | conciliado | clasificado
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente", server_default="pendiente")
    # efectivo | comision | traspaso | impuestos | nomina | sin_cfdi | otro
    clasificacion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)


class VinculoBancario(Base):
    """Relación movimiento bancario ↔ CFDI (propuesta por el motor o hecha
    a mano). Solo los 'aprobada' cuentan como conciliados."""

    __tablename__ = "bancos_vinculos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)
    movimiento_id: Mapped[int] = mapped_column(
        ForeignKey("bancos_movimientos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cfdi_id: Mapped[int | None] = mapped_column(ForeignKey("cfdi_documents.id"), nullable=True, index=True)

    # pue | rep | ppd_sin_rep | nomina | manual
    tipo_vinculo: Mapped[str] = mapped_column(String(20), nullable=False)
    monto: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    motivo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    origen: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")  # auto | manual
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="sugerida")  # sugerida|aprobada|rechazada

    resuelto_por_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resuelto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
