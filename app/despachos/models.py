"""Despachos contables (la cuenta que paga la licencia) y solicitudes de acceso.

Un despacho agrupa a sus usuarios y a sus clientes (RFC): todos los usuarios
de un despacho ven la misma bóveda y los mismos clientes, y nadie ve lo de
otro despacho. Un contador independiente es un despacho de una persona.

Un usuario pertenece a un solo despacho a la vez (users.despacho_id). Para
unirse a otro despacho pide acceso: le llega un correo al dueño y, si aprueba,
el usuario ocupa uno de los lugares (usuarios) de la licencia del despacho.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ROL_DUENO = "dueno"
ROL_ADMIN = "admin"
ROL_MIEMBRO = "miembro"
ROLES_GESTION = {ROL_DUENO, ROL_ADMIN}


class Despacho(Base):
    __tablename__ = "despachos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    # Código corto para que un contador pida unirse ("KON-7Q4M2").
    codigo: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    dueno_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", use_alter=True), nullable=True)
    # Lugares y RFC comprados además de los que incluye el paquete.
    usuarios_extra: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rfc_extra_bloques: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Datos para la factura (CFDI) que ISC emite al despacho.
    datos_fiscales: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # El despacho interno de ISC (usuarios y clientes que existían antes de las licencias).
    interno: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    origen: Mapped[str | None] = mapped_column(String(120), nullable=True)  # utm/fuente de alta
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SolicitudAcceso(Base):
    __tablename__ = "despacho_solicitudes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    despacho_id: Mapped[int] = mapped_column(ForeignKey("despachos.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    # El solicitante ya tenía su propio despacho (p. ej. pagó una licencia
    # individual) y pide que sus clientes se muevan con él al aprobarse.
    traer_clientes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    despacho_origen_id: Mapped[int | None] = mapped_column(ForeignKey("despachos.id"), nullable=True)
    # Solo se guarda el hash del token del enlace del correo.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente")  # pendiente|aprobada|rechazada|cancelada
    expira_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resuelta_por_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resuelta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
