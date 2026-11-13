"""API de despacho, registro, solicitudes de acceso, miembros y licencia."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.accounts.schemas import TokenResponse
from app.accounts.service import build_token_for
from app.billing import service as billing
from app.billing.models import Orden, Plan
from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.despachos import service
from app.despachos.models import Despacho
from app.users.models import User

router = APIRouter(tags=["Despacho y licencia"])


# --------------------------------------------------------------------------
# Esquemas
# --------------------------------------------------------------------------

class RegistroIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    telefono: str | None = Field(default=None, max_length=20)
    modo: str = "nuevo"  # nuevo | unirse
    despacho_nombre: str | None = Field(default=None, max_length=160)
    destino: str | None = Field(default=None, max_length=255)  # código KON-XXXXX o correo del responsable
    mensaje: str | None = Field(default=None, max_length=500)
    anonimo_id: str | None = Field(default=None, max_length=64)
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None


class SolicitarIn(BaseModel):
    destino: str = Field(min_length=3, max_length=255)
    mensaje: str | None = Field(default=None, max_length=500)
    traer_clientes: bool = False


class CrearDespachoIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=160)


class ResolverIn(BaseModel):
    token: str
    accion: str  # aprobar | rechazar


class RolIn(BaseModel):
    rol: str


class DespachoUpdateIn(BaseModel):
    nombre: str | None = Field(default=None, max_length=160)
    datos_fiscales: dict | None = None


class CompraIn(BaseModel):
    plan_codigo: str
    usuarios_extra: int = 0
    rfc_extra_bloques: int = 0
    cupon: str | None = Field(default=None, max_length=40)
    requiere_factura: bool = False
    datos_fiscales: dict | None = None


class CanjeIn(BaseModel):
    codigo: str = Field(min_length=3, max_length=40)


def _plan_out(p: Plan) -> dict:
    return {
        "codigo": p.codigo, "nombre": p.nombre, "tipo": p.tipo, "rfcs_min": p.rfcs_min, "rfcs_max": p.rfcs_max,
        "usuarios_incluidos": p.usuarios_incluidos, "precio_anual_centavos": p.precio_anual_centavos,
        "precio_anual_con_iva_centavos": p.precio_anual_centavos + billing.iva_de(p.precio_anual_centavos),
        "descripcion": p.descripcion, "destacado": p.destacado,
    }


def _despacho_out(d: Despacho) -> dict:
    return {"id": d.id, "nombre": d.nombre, "codigo": d.codigo, "datos_fiscales": d.datos_fiscales, "interno": d.interno}


def _miembro_out(u: User) -> dict:
    return {"id": u.id, "nombre": u.nombre, "email": u.email, "rol_despacho": u.rol_despacho, "activo": u.activo}


def _orden_out(o: Orden) -> dict:
    return {
        "id": o.id, "tipo": o.tipo, "plan_codigo": o.plan_codigo, "estado": o.estado, "proveedor": o.proveedor,
        "subtotal_centavos": o.subtotal_centavos, "descuento_centavos": o.descuento_centavos, "iva_centavos": o.iva_centavos,
        "total_centavos": o.total_centavos, "periodo_inicia": o.periodo_inicia, "periodo_vence": o.periodo_vence,
        "lineas": o.lineas, "requiere_factura": o.requiere_factura, "created_at": o.created_at, "pagada_at": o.pagada_at,
    }


def _solicitud_out(s, u: User | None = None, despacho: Despacho | None = None) -> dict:
    out = {
        "id": s.id, "estado": s.estado, "mensaje": s.mensaje, "traer_clientes": s.traer_clientes,
        "created_at": s.created_at, "expira_at": s.expira_at,
    }
    if u is not None:
        out["solicitante"] = {"id": u.id, "nombre": u.nombre, "email": u.email}
    if despacho is not None:
        out["despacho"] = {"nombre": despacho.nombre, "codigo": despacho.codigo}
    return out


# --------------------------------------------------------------------------
# Público
# --------------------------------------------------------------------------

@router.get("/planes")
def planes(db: Session = Depends(get_db)):
    return {"planes": [_plan_out(p) for p in billing.catalogo(db)], "prueba_dias": settings.PRUEBA_DIAS}


@router.post("/registro", response_model=TokenResponse, status_code=201)
def registro(payload: RegistroIn, db: Session = Depends(get_db)):
    user, _ = service.registrar(db, payload.model_dump())
    return TokenResponse(access_token=build_token_for(user), user=user)


# --------------------------------------------------------------------------
# Mi despacho
# --------------------------------------------------------------------------

@router.get("/despacho")
def mi_despacho(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Estado de la cuenta para el frontend: despacho, rol, licencia y, si el
    usuario aún no tiene despacho, su última solicitud."""
    if user.despacho_id is None:
        s = service.mi_solicitud(db, user)
        return {
            "despacho": None, "rol_despacho": None, "licencia": None,
            "solicitud": _solicitud_out(s, despacho=db.get(Despacho, s.despacho_id)) if s else None,
        }
    d = service.despacho_de(db, user)
    s = service.mi_solicitud(db, user)
    return {
        "despacho": _despacho_out(d), "rol_despacho": user.rol_despacho, "licencia": billing.estado(db, d),
        "solicitud": _solicitud_out(s, despacho=db.get(Despacho, s.despacho_id)) if s and s.estado == "pendiente" else None,
        "solicitudes_pendientes": len(service.pendientes(db, d.id)) if user.rol_despacho in ("dueno", "admin") else 0,
    }


@router.put("/despacho")
def actualizar_despacho(payload: DespachoUpdateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _despacho_out(service.actualizar(db, user, payload.model_dump(exclude_unset=True)))


@router.post("/despacho", status_code=201)
def crear_mi_despacho(payload: CrearDespachoIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Para quien se registró para unirse a un despacho y prefiere tener el suyo."""
    if user.despacho_id is not None:
        raise HTTPException(409, "Ya perteneces a un despacho.")
    service.cancelar_mi_solicitud(db, user)
    d = service.crear_despacho(db, user, payload.nombre)
    from app.notificaciones import service as correo

    correo.correo_bienvenida(db, user=user, despacho=d, dias_prueba=settings.PRUEBA_DIAS)
    db.commit()
    return _despacho_out(d)


@router.post("/despacho/unirme", status_code=201)
def solicitar_union(payload: SolicitarIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = service.solicitar_union(db, user, payload.destino, payload.mensaje, payload.traer_clientes)
    return _solicitud_out(s, despacho=db.get(Despacho, s.despacho_id))


@router.delete("/despacho/unirme", status_code=204)
def cancelar_union(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service.cancelar_mi_solicitud(db, user)


@router.get("/despacho/solicitudes")
def solicitudes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service.exigir_gestion(user)
    return [_solicitud_out(s, u) for s, u in service.pendientes(db, user.despacho_id)]


@router.post("/despacho/solicitudes/{solicitud_id}/{accion}")
def resolver_solicitud(solicitud_id: int, accion: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.despachos.models import SolicitudAcceso

    if accion not in ("aprobar", "rechazar"):
        raise HTTPException(404, "Acción inválida.")
    s = db.get(SolicitudAcceso, solicitud_id)
    if s is None or s.despacho_id != user.despacho_id:
        raise HTTPException(404, "Solicitud no encontrada.")
    s = service.resolver(db, user, s, accion == "aprobar")
    return _solicitud_out(s, db.get(User, s.user_id))


@router.get("/despacho/acceso")
def ver_por_token(token: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Para la página /aprobar-acceso del correo (requiere sesión)."""
    s = service.solicitud_por_token(db, token)
    d = db.get(Despacho, s.despacho_id)
    puede = user.despacho_id == s.despacho_id and user.rol_despacho in ("dueno", "admin")
    return {**_solicitud_out(s, db.get(User, s.user_id), d), "puede_resolver": puede,
            "vencida": service._aware(s.expira_at) <= service.ahora()}


@router.post("/despacho/acceso")
def resolver_por_token(payload: ResolverIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.accion not in ("aprobar", "rechazar"):
        raise HTTPException(422, "Acción inválida.")
    s = service.resolver(db, user, service.solicitud_por_token(db, payload.token), payload.accion == "aprobar")
    return _solicitud_out(s, db.get(User, s.user_id), db.get(Despacho, s.despacho_id))


@router.get("/despacho/miembros")
def miembros(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = service.despacho_de(db, user)
    return [_miembro_out(u) for u in service.miembros(db, d.id)]


@router.put("/despacho/miembros/{user_id}")
def cambiar_rol(user_id: int, payload: RolIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _miembro_out(service.cambiar_rol(db, user, user_id, payload.rol))


@router.delete("/despacho/miembros/{user_id}", status_code=204)
def quitar(user_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service.quitar_miembro(db, user, user_id)


# --------------------------------------------------------------------------
# Licencia y pagos
# --------------------------------------------------------------------------

@router.get("/licencia")
def licencia(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = service.despacho_de(db, user)
    ordenes = (
        db.query(Orden).filter(Orden.despacho_id == d.id, Orden.estado.in_(("pagada", "reembolsada", "pendiente")))
        .order_by(Orden.created_at.desc()).limit(30).all()
    )
    return {
        "despacho": _despacho_out(d), "rol_despacho": user.rol_despacho, "estado": billing.estado(db, d),
        "planes": [_plan_out(p) for p in billing.catalogo(db)], "ordenes": [_orden_out(o) for o in ordenes],
        "pagos_modo": settings.PAYMENTS_MODE,
    }


@router.post("/licencia/cotizar")
def cotizar(payload: CompraIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = service.despacho_de(db, user)
    return billing.vista_previa(db, d, user, payload.model_dump())


@router.post("/licencia/checkout")
def checkout(payload: CompraIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = service.despacho_de(db, user)
    return billing.crear_checkout(db, d, user, payload.model_dump())


@router.post("/licencia/canjear")
def canjear(payload: CanjeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = service.despacho_de(db, user)
    lic = billing.canjear_dias_gratis(db, d, user, payload.codigo)
    return {"vence": lic.vence, "plan_codigo": lic.plan_codigo, "estado": billing.estado(db, d)}


@router.get("/licencia/ordenes/{orden_id}")
def orden(orden_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    o = db.get(Orden, orden_id)
    if o is None or o.despacho_id != user.despacho_id:
        raise HTTPException(404, "Orden no encontrada.")
    return _orden_out(o)


# --- Pasarela simulada (solo desarrollo) ------------------------------------

@router.post("/pagos/mock/{sesion_id}/{resultado}")
def pago_mock(sesion_id: str, resultado: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if settings.PAYMENTS_MODE != "mock" or settings.es_produccion:
        raise HTTPException(404, "No disponible.")
    o = db.query(Orden).filter(Orden.stripe_session_id == sesion_id).first()
    if o is None or o.despacho_id != user.despacho_id:
        raise HTTPException(404, "Sesión no encontrada.")
    tipos = {"pagar": "checkout.session.completed", "expirar": "checkout.session.expired", "fallar": "checkout.session.async_payment_failed"}
    if resultado not in tipos:
        raise HTTPException(404, "Resultado inválido.")
    ev = billing.EventoPago(
        proveedor="mock", evento_id=f"mock_evt_{sesion_id}_{resultado}", tipo=tipos[resultado], orden_id=o.id,
        sesion_id=sesion_id, pago_id=f"mock_pi_{sesion_id}", pagado=resultado == "pagar",
        monto_centavos=o.total_centavos, moneda=o.moneda,
    )
    return billing.procesar_evento(db, ev)


@router.get("/pagos/mock/{sesion_id}")
def ver_pago_mock(sesion_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if settings.PAYMENTS_MODE != "mock" or settings.es_produccion:
        raise HTTPException(404, "No disponible.")
    o = db.query(Orden).filter(Orden.stripe_session_id == sesion_id).first()
    if o is None or o.despacho_id != user.despacho_id:
        raise HTTPException(404, "Sesión no encontrada.")
    return _orden_out(o)


# --- Webhook de Stripe -----------------------------------------------------

@router.post("/webhooks/stripe", include_in_schema=False)
async def webhook_stripe(request: Request, db: Session = Depends(get_db)):
    cuerpo = await request.body()
    ev = billing.parsear_webhook_stripe(cuerpo, request.headers.get("stripe-signature"))
    if ev is None:
        return {"recibido": True, "ignorado": True}
    return {"recibido": True, **billing.procesar_evento(db, ev)}


# --- Embudo (público) ------------------------------------------------------

class EventoIn(BaseModel):
    paso: str = Field(max_length=40)
    anonimo_id: str | None = Field(default=None, max_length=64)
    ruta: str | None = Field(default=None, max_length=255)
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    referrer_host: str | None = None
    dispositivo: str | None = None
    props: dict | None = None


@router.post("/embudo/evento", status_code=202)
def evento_embudo(payload: EventoIn, request: Request, db: Session = Depends(get_db)):
    """Pasos que el navegador puede reportar (visita, precios, checkout...).
    Los pasos de producto (alta de cliente, compra...) solo los registra el
    servidor, para que no se puedan inflar desde fuera."""
    from app.embudo import service as embudo

    if payload.paso not in embudo.PASOS_CLIENTE:
        return {"ok": False}
    user_id = despacho_id = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        try:
            from app.core.security import decode_access_token

            u = db.get(User, int(decode_access_token(auth.split(" ", 1)[1])["sub"]))
            if u:
                user_id, despacho_id = u.id, u.despacho_id
        except Exception:  # noqa: BLE001
            pass
    embudo.registrar(
        db, payload.paso, despacho_id=despacho_id, user_id=user_id, anonimo_id=payload.anonimo_id, ruta=payload.ruta,
        utm={"source": payload.utm_source, "medium": payload.utm_medium, "campaign": payload.utm_campaign,
             "referrer_host": payload.referrer_host},
        dispositivo=payload.dispositivo, props=payload.props,
    )
    db.commit()
    return {"ok": True}


def _ahora() -> datetime:
    return service.ahora()
