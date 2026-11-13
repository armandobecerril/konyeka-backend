"""Separación de datos entre despachos (multi-tenant).

Toda la información de un cliente cuelga de rutas /clientes/{rfc_client_id}/...
o /clientes/{client_id}. Esta dependencia global revisa, en CUALQUIER ruta que
reciba uno de esos parámetros, que el cliente pertenezca al despacho del
usuario autenticado; si no, responde 404 (no revela que exista). Así ningún
router nuevo puede olvidarse del filtro.

Además, con la licencia vencida, el despacho queda en solo lectura: se puede
consultar todo, pero no se puede escribir (descargar, conciliar, subir
estados de cuenta, dar de alta clientes...). Dar de baja un cliente y guardar
preferencias de columnas siguen permitidos.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_access_token

PARAMS_CLIENTE = ("rfc_client_id", "client_id")
METODOS_ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}


def _user_id(request: Request) -> int:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "No autenticado")
    try:
        return int(decode_access_token(auth.split(" ", 1)[1].strip())["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise HTTPException(401, "Token inválido o expirado")


def _escritura_permitida_sin_licencia(request: Request) -> bool:
    ruta = request.url.path
    if ruta.endswith("/columnas"):
        return True
    # Baja lógica de un cliente: DELETE /clientes/{id}
    partes = [p for p in ruta.split("/") if p]
    return request.method == "DELETE" and len(partes) == 2 and partes[0] == "clientes"


def guardia_despacho(request: Request, db: Session = Depends(get_db)) -> None:
    params = request.path_params
    clave = next((p for p in PARAMS_CLIENTE if p in params), None)
    ruta = request.url.path
    es_alta_cliente = request.method == "POST" and ruta.rstrip("/") == "/clientes"
    if clave is None and not es_alta_cliente:
        return

    from app.billing.service import licencia_vigente
    from app.rfc_clients.models import RfcClient
    from app.users.models import User

    user = db.get(User, _user_id(request))
    if user is None or not user.activo:
        raise HTTPException(401, "Usuario no válido")
    if user.despacho_id is None:
        raise HTTPException(403, "Tu usuario todavía no pertenece a un despacho.")

    if clave is not None:
        try:
            cid = int(params[clave])
        except (TypeError, ValueError):
            raise HTTPException(404, "Cliente no encontrado")
        dueno = db.query(RfcClient.despacho_id).filter(RfcClient.id == cid).scalar()
        if dueno != user.despacho_id:
            raise HTTPException(404, "Cliente no encontrado")

    if request.method in METODOS_ESCRITURA and not _escritura_permitida_sin_licencia(request):
        if licencia_vigente(db, user.despacho_id) is None:
            raise HTTPException(
                402,
                "La licencia de tu despacho venció: puedes consultar tu información, pero para seguir trabajando "
                "renueva en «Mi licencia».",
            )
