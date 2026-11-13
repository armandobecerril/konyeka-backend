"""Bandeja de salida de correos. Si Azure Communication Services está
configurado se envían; si no, quedan aquí con estado "bandeja" para verlos
en el portal de superadmin (útil en desarrollo y para auditar)."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plantilla: Mapped[str] = mapped_column(String(60), nullable=False)
    destinatario: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    asunto: Mapped[str] = mapped_column(String(255), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="pendiente")  # pendiente|enviado|bandeja|error
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    despacho_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enviado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
