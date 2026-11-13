"""Bitácora de acciones del portal de superadmin (y de eventos de licencias)."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)  # "admin:<email>" | "user:<id>" | "sistema"
    accion: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    despacho_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detalle: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


def auditar(db, actor: str, accion: str, despacho_id: int | None = None, detalle: dict | None = None) -> None:
    db.add(Auditoria(actor=actor, accion=accion, despacho_id=despacho_id, detalle=detalle))
