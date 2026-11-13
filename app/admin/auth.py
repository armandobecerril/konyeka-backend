"""Acceso al portal de superadmin con la cuenta Microsoft de ISC (Entra ID).

El frontend hace el flujo OpenID Connect (código + PKCE) contra el tenant de
ISC y manda aquí el id_token. El backend lo verifica de forma independiente:

1. Firma con las llaves públicas del tenant (JWKS) y `aud` = client id de la
   app registrada, `iss` = el tenant de ISC (no se aceptan otros tenants).
2. MFA: el claim `amr` debe incluir "mfa" (requiere el optional claim `amr`
   en la app y una política de acceso condicional que exija MFA).
3. Autorización: el rol de app `KONYEKA.SuperAdmin` (o el grupo configurado).
4. Además, si ADMIN_ALLOWLIST tiene correos, el usuario debe estar en ella.

Si todo pasa, se emite un JWT de admin propio (8 h) con `typ=admin`, que no
sirve como token de usuario normal (no tiene `sub` numérico) y viceversa.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

ADMIN_HORAS = 8
bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/discovery/v2.0/keys", cache_keys=True, lifespan=3600
    )


def verificar_id_token_entra(id_token: str, nonce: str | None = None) -> dict:
    if not settings.ENTRA_TENANT_ID or not settings.ENTRA_CLIENT_ID:
        raise HTTPException(503, "El acceso con Microsoft no está configurado (ENTRA_TENANT_ID / ENTRA_CLIENT_ID).")
    try:
        llave = _jwks().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, llave.key, algorithms=["RS256"], audience=settings.ENTRA_CLIENT_ID,
            issuer=f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0",
            options={"require": ["exp", "iat", "aud", "iss", "tid", "oid"]}, leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Token de Microsoft inválido: {exc}") from exc
    return autorizar_claims(claims, nonce)


def autorizar_claims(claims: dict, nonce: str | None = None) -> dict:
    """Reglas de autorización (separadas para poder probarlas sin red)."""
    if claims.get("tid") != settings.ENTRA_TENANT_ID:
        raise HTTPException(403, "La cuenta no pertenece al tenant de ISC.")
    if nonce is not None and claims.get("nonce") != nonce:
        raise HTTPException(401, "El nonce no coincide.")
    amr = claims.get("amr") or []
    if "mfa" not in amr:
        raise HTTPException(403, "Se requiere autenticación multifactor (MFA) para entrar al portal de superadmin.")
    roles = claims.get("roles") or []
    grupos = claims.get("groups") or []
    tiene_rol = settings.ENTRA_APP_ROLE in roles
    tiene_grupo = bool(settings.ENTRA_ADMIN_GROUP_ID) and settings.ENTRA_ADMIN_GROUP_ID in grupos
    if not (tiene_rol or tiene_grupo):
        raise HTTPException(403, f"Tu cuenta no tiene el rol {settings.ENTRA_APP_ROLE} en la app de KONYEKA.")
    email = (claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "").lower()
    if settings.admin_allowlist and email not in settings.admin_allowlist:
        raise HTTPException(403, "Tu cuenta no está en la lista de administradores de KONYEKA.")
    return {"email": email, "nombre": claims.get("name") or email, "oid": claims["oid"], "metodo": "entra", "mfa": True}


def emitir_token_admin(admin: dict) -> tuple[str, datetime]:
    vence = datetime.now(timezone.utc) + timedelta(hours=ADMIN_HORAS)
    token = jwt.encode(
        {"typ": "admin", "adm": admin["email"], "nombre": admin["nombre"], "oid": admin.get("oid"), "metodo": admin["metodo"],
         "exp": vence, "iat": datetime.now(timezone.utc)},
        settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    return token, vence


def dev_login_disponible() -> bool:
    return settings.ADMIN_DEV_LOGIN and not settings.es_produccion


def get_admin(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(401, "No autenticado")
    try:
        p = jwt.decode(cred.credentials, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sesión de admin inválida o expirada")
    if p.get("typ") != "admin" or not p.get("adm"):
        raise HTTPException(403, "Se requiere sesión de superadmin.")
    if p.get("metodo") == "dev" and not dev_login_disponible():
        raise HTTPException(403, "El acceso de desarrollo está deshabilitado.")
    return {"email": p["adm"], "nombre": p.get("nombre"), "metodo": p.get("metodo")}
