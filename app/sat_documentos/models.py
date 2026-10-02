"""Historial de descargas de Opinión de Cumplimiento y Constancia de
Situación Fiscal del SAT para cada cliente (vía RPA, ver app/sat_documentos/rpa.py)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

TIPO_OPINION_CUMPLIMIENTO = "opinion_cumplimiento"
TIPO_CONSTANCIA = "constancia_situacion_fiscal"


class DocumentoSat(Base):
    __tablename__ = "documentos_sat"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(ForeignKey("rfc_clients.id"), nullable=False, index=True)

    tipo: Mapped[str] = mapped_column(String(30), nullable=False)  # TIPO_OPINION_CUMPLIMIENTO | TIPO_CONSTANCIA
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="solicitado")
    # estado: solicitado -> en_proceso -> terminado | error

    resultado: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # resultado: "positivo" | "negativo" -- solo aplica a TIPO_OPINION_CUMPLIMIENTO

    mensaje_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    solicitado_por_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
