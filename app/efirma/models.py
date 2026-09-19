"""Bóveda de credenciales de e.firma (FIEL) por cliente.

La llave privada (.key) y la contraseña se guardan cifradas (Fernet, ver
app/core/crypto.py); el certificado (.cer) es público por naturaleza y se
guarda en claro. Los datos del certificado (RFC titular, vigencia, número
de serie) se extraen una sola vez al cargarlo, para poder mostrarlos en la
interfaz sin tener que descifrar nada.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EfirmaCredential(Base):
    __tablename__ = "efirma_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rfc_client_id: Mapped[int] = mapped_column(
        ForeignKey("rfc_clients.id"), unique=True, nullable=False, index=True
    )

    cer_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_bytes_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    rfc_titular: Mapped[str] = mapped_column(String(13), nullable=False)
    nombre_titular: Mapped[str | None] = mapped_column(String(255), nullable=True)
    numero_serie: Mapped[str] = mapped_column(String(40), nullable=False)
    vigencia_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vigencia_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
