"""Licencias anuales de KONYEKA: catálogo de paquetes, licencias vigentes,
órdenes de compra, pagos y eventos de webhook (Stripe)."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Plan(Base):
    """Paquete (d10/d20/d50/d50p), complemento (usuario_extra, rfc_extra_10) o
    la prueba gratuita. Precio anual SIN IVA en centavos de MXN."""

    __tablename__ = "planes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # paquete | addon | prueba
    rfcs_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rfcs_max: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usuarios_incluidos: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    precio_anual_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    destacado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Licencia(Base):
    """Periodo de uso de un despacho. La vigente es la de mayor vencimiento
    que ya inició y no ha vencido."""

    __tablename__ = "licencias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    despacho_id: Mapped[int] = mapped_column(ForeignKey("despachos.id"), nullable=False, index=True)
    plan_codigo: Mapped[str] = mapped_column(String(30), nullable=False)
    rfcs_max: Mapped[int] = mapped_column(Integer, nullable=False)
    usuarios_max: Mapped[int] = mapped_column(Integer, nullable=False)
    inicia: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vence: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    origen: Mapped[str] = mapped_column(String(10), nullable=False)  # prueba | pago | cupon | manual
    orden_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ordenes.id"), nullable=True)
    cupon_id: Mapped[int | None] = mapped_column(ForeignKey("cupones.id"), nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Orden(Base):
    __tablename__ = "ordenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    despacho_id: Mapped[int] = mapped_column(ForeignKey("despachos.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)  # nueva | renovacion | mejora | complemento
    plan_codigo: Mapped[str] = mapped_column(String(30), nullable=False)
    usuarios_extra: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rfc_extra_bloques: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    periodo_inicia: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    periodo_vence: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lineas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    subtotal_centavos: Mapped[int] = mapped_column(Integer, nullable=False)  # lista, sin IVA
    descuento_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iva_centavos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)  # lo que se cobra (con IVA)
    moneda: Mapped[str] = mapped_column(String(3), nullable=False, default="MXN")
    cupon_id: Mapped[int | None] = mapped_column(ForeignKey("cupones.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(12), nullable=False, default="pendiente")
    proveedor: Mapped[str] = mapped_column(String(10), nullable=False)  # stripe | mock | cupon
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requiere_factura: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    pagada_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Pago(Base):
    __tablename__ = "pagos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    orden_id: Mapped[str] = mapped_column(String(36), ForeignKey("ordenes.id"), nullable=False, index=True)
    proveedor: Mapped[str] = mapped_column(String(10), nullable=False)
    proveedor_pago_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    monto_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    comision_centavos: Mapped[int | None] = mapped_column(Integer, nullable=True)  # real, de Stripe
    neto_centavos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookEvento(Base):
    __tablename__ = "webhook_eventos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    proveedor: Mapped[str] = mapped_column(String(10), nullable=False)
    evento_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tipo: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    procesado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
