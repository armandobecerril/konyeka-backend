"""Cupones (portado del módulo de VEMI): días gratis de uso, % o $ de
descuento sobre la licencia. El canje se ata a una orden (o a la licencia
creada por un cupón de días gratis) y al despacho que lo usa."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Cupon(Base):
    __tablename__ = "cupones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)  # dias_gratis | porcentaje | monto
    porcentaje: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monto_centavos: Mapped[int | None] = mapped_column(Integer, nullable=True)  # sin IVA
    dias: Mapped[int | None] = mapped_column(Integer, nullable=True)  # dias_gratis
    plan_codigo: Mapped[str | None] = mapped_column(String(30), nullable=True)  # dias_gratis: paquete que se usa
    planes_aplicables: Mapped[list | None] = mapped_column(JSON, nullable=True)  # null = todos
    max_canjes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_por_despacho: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    solo_primera_compra: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    vigente_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    campana: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    lote: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    email_asignado: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="activo")  # activo|pausado|archivado
    creado_por: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CuponCanje(Base):
    __tablename__ = "cupon_canjes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cupon_id: Mapped[int] = mapped_column(ForeignKey("cupones.id"), nullable=False, index=True)
    despacho_id: Mapped[int] = mapped_column(ForeignKey("despachos.id"), nullable=False, index=True)
    orden_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    descuento_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cobrado_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="apartado")  # apartado|canjeado|liberado
    detalle: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    canjeado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    liberado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
