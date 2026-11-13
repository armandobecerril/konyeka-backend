"""Eventos del embudo de conversión (portado de VEMI). Actor = despacho (si
ya se registró) o visitante anónimo (cookie first-party konyeka_aid). No se
guarda IP ni user-agent completo: solo la clase de dispositivo."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EventoEmbudo(Base):
    __tablename__ = "embudo_eventos"
    __table_args__ = (
        Index("ix_embudo_paso_fecha", "paso", "ocurrio_at"),
        Index("ix_embudo_despacho_fecha", "despacho_id", "ocurrio_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    paso: Mapped[str] = mapped_column(String(40), nullable=False)
    anonimo_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    despacho_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ruta: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    referrer_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dispositivo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    props: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ocurrio_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
