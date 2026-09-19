"""Cifrado simétrico (Fernet) para datos sensibles en reposo: la llave privada
de la e.firma y su contraseña nunca se guardan en texto plano en la BD."""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EfirmaEncryptionError(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = settings.EFIRMA_ENCRYPTION_KEY
    if not key:
        raise EfirmaEncryptionError(
            "Falta configurar EFIRMA_ENCRYPTION_KEY en el .env. "
            "Genera una con: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise EfirmaEncryptionError("EFIRMA_ENCRYPTION_KEY no es una llave Fernet válida") from exc


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(token)
    except InvalidToken as exc:
        raise EfirmaEncryptionError(
            "No se pudo descifrar el dato (llave EFIRMA_ENCRYPTION_KEY incorrecta o dato dañado)"
        ) from exc
