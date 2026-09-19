"""Lógica de negocio de la bóveda de e.firma: cargar, validar, cifrar y
recuperar credenciales para firmar las peticiones al SAT."""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_bytes, encrypt_bytes
from app.efirma.models import EfirmaCredential
from app.rfc_clients.models import RfcClient


def _asn1_time_to_datetime(value: bytes) -> datetime:
    # Formato ASN.1 GeneralizedTime que usa OpenSSL para notBefore/notAfter: "YYYYMMDDHHMMSSZ"
    texto = value.decode("ascii")
    return datetime.strptime(texto, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)


def _cargar_signer(cer_bytes: bytes, key_bytes: bytes, password: str):
    from satcfdi.exceptions import CFDIError
    from satcfdi.models import Signer
    from satcfdi.models.certificate import CertificateType

    try:
        signer = Signer.load(certificate=cer_bytes, key=key_bytes, password=password)
    except CFDIError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La llave privada (.key) no corresponde con el certificado (.cer).",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No se pudo abrir la e.firma. Verifica que los archivos .cer/.key "
                "y la contraseña de la llave privada sean correctos."
            ),
        )

    if signer.type == CertificateType.CSD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Este certificado es un Sello Digital (CSD), no una e.firma (FIEL). "
                "Sube el .cer/.key de tu e.firma para poder descargar del SAT."
            ),
        )
    if signer.type != CertificateType.Fiel:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El certificado cargado no es una e.firma (FIEL) válida del SAT.",
        )

    return signer


def cargar_efirma(
    db: Session,
    rfc_client: RfcClient,
    *,
    cer_bytes: bytes,
    key_bytes: bytes,
    password: str,
    uploaded_by_id: int | None,
) -> EfirmaCredential:
    signer = _cargar_signer(cer_bytes, key_bytes, password)

    rfc_titular = str(signer.rfc)
    if rfc_titular.upper() != rfc_client.rfc.upper():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"La e.firma pertenece al RFC {rfc_titular}, pero este cliente es "
                f"{rfc_client.rfc}. Sube la e.firma correspondiente a este cliente."
            ),
        )

    cert = signer.certificate
    vigencia_desde = _asn1_time_to_datetime(cert.get_notBefore())
    vigencia_hasta = _asn1_time_to_datetime(cert.get_notAfter())

    existing = (
        db.query(EfirmaCredential)
        .filter(EfirmaCredential.rfc_client_id == rfc_client.id)
        .first()
    )

    if existing is None:
        existing = EfirmaCredential(rfc_client_id=rfc_client.id)
        db.add(existing)

    existing.cer_bytes = cer_bytes
    existing.key_bytes_encrypted = encrypt_bytes(key_bytes)
    existing.password_encrypted = encrypt_bytes(password.encode())
    existing.rfc_titular = rfc_titular
    existing.nombre_titular = signer.legal_name
    existing.numero_serie = signer.certificate_number
    existing.vigencia_desde = vigencia_desde
    existing.vigencia_hasta = vigencia_hasta
    existing.uploaded_by_id = uploaded_by_id

    db.commit()
    db.refresh(existing)
    return existing


def get_efirma(db: Session, rfc_client_id: int) -> EfirmaCredential | None:
    return (
        db.query(EfirmaCredential)
        .filter(EfirmaCredential.rfc_client_id == rfc_client_id)
        .first()
    )


def get_efirma_or_404(db: Session, rfc_client_id: int) -> EfirmaCredential:
    efirma = get_efirma(db, rfc_client_id)
    if efirma is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Este cliente todavía no tiene una e.firma cargada.",
        )
    return efirma


def eliminar_efirma(db: Session, rfc_client_id: int) -> None:
    efirma = get_efirma_or_404(db, rfc_client_id)
    db.delete(efirma)
    db.commit()


def obtener_signer(db: Session, rfc_client_id: int):
    """Reconstruye el Signer (satcfdi) descifrando la llave y contraseña guardadas.
    Se usa justo antes de llamar al webservice del SAT; nunca se persiste en claro."""
    from satcfdi.models import Signer

    efirma = get_efirma_or_404(db, rfc_client_id)

    ahora = datetime.now(timezone.utc)
    vigencia_hasta = efirma.vigencia_hasta
    if vigencia_hasta.tzinfo is None:
        vigencia_hasta = vigencia_hasta.replace(tzinfo=timezone.utc)
    if ahora > vigencia_hasta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La e.firma de este cliente venció el {vigencia_hasta:%d/%m/%Y}. Sube una vigente.",
        )

    key_bytes = decrypt_bytes(efirma.key_bytes_encrypted)
    password = decrypt_bytes(efirma.password_encrypted).decode()

    return Signer.load(certificate=efirma.cer_bytes, key=key_bytes, password=password)
