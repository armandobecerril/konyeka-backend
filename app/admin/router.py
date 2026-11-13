"""Portal de superadmin de KONYEKA (ISC): finanzas, embudo, cupones,
despachos/licencias, correos y bitácora. Todo requiere sesión de admin
(Microsoft Entra ID con MFA)."""
from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.admin import auth
from app.admin.models import Auditoria, auditar
from app.billing import service as billing
from app.billing.models import Licencia, Orden, Pago, Plan
from app.core.config import settings
from app.core.db import get_db
from app.cupones import service as cupones
from app.cupones.models import Cupon, CuponCanje
from app.despachos.models import Despacho, SolicitudAcceso
from app.embudo import service as embudo
from app.notificaciones.models import Notificacion
from app.rfc_clients.models import RfcClient
from app.users.models import User

router = APIRouter(prefix="/admin", tags=["Superadmin"])
publico = APIRouter(prefix="/admin/auth", tags=["Superadmin"])


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _rango(desde: date | None, hasta: date | None, dias_default: int = 30) -> tuple[datetime, datetime]:
    hoy = _ahora().date()
    hasta = hasta or hoy
    desde = desde or (hasta - timedelta(days=dias_default - 1))
    if desde > hasta:
        raise HTTPException(422, "El rango de fechas es inválido.")
    return (datetime.combine(desde, time.min, tzinfo=timezone.utc), datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=timezone.utc))


# --------------------------------------------------------------------------
# Autenticación
# --------------------------------------------------------------------------

class EntraIn(BaseModel):
    id_token: str
    nonce: str | None = None


class DevIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@publico.get("/config")
def config_auth():
    return {
        "entra": bool(settings.ENTRA_TENANT_ID and settings.ENTRA_CLIENT_ID),
        "dev_login": auth.dev_login_disponible(),
        "rol": settings.ENTRA_APP_ROLE,
    }


@publico.post("/entra")
def login_entra(payload: EntraIn, db: Session = Depends(get_db)):
    try:
        admin = auth.verificar_id_token_entra(payload.id_token, payload.nonce)
    except HTTPException as exc:
        auditar(db, "anonimo", "admin.login_rechazado", None, {"motivo": str(exc.detail)[:300]})
        db.commit()
        raise
    token, vence = auth.emitir_token_admin(admin)
    auditar(db, f"admin:{admin['email']}", "admin.login", None, {"metodo": "entra", "mfa": True})
    db.commit()
    return {"token": token, "vence": vence, "admin": admin}


@publico.post("/dev")
def login_dev(payload: DevIn, db: Session = Depends(get_db)):
    if not auth.dev_login_disponible():
        raise HTTPException(404, "No disponible.")
    admin = {"email": payload.email.lower(), "nombre": payload.email, "oid": None, "metodo": "dev"}
    token, vence = auth.emitir_token_admin(admin)
    auditar(db, f"admin:{admin['email']}", "admin.login", None, {"metodo": "dev"})
    db.commit()
    return {"token": token, "vence": vence, "admin": admin}


@router.get("/yo")
def yo(admin: dict = Depends(auth.get_admin)):
    return admin


# --------------------------------------------------------------------------
# Finanzas
# --------------------------------------------------------------------------

def _comision_estimada(total: int) -> int:
    base = total * settings.FINOPS_STRIPE_PCT + settings.FINOPS_STRIPE_FIJO_CENTAVOS
    return int(round(base * (1 + settings.FINOPS_IVA_COMISION)))


@router.get("/finanzas")
def finanzas(
    desde: date | None = None, hasta: date | None = None, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)
):
    ini, fin = _rango(desde, hasta, 90)
    filas = (
        db.query(Orden, Pago, Despacho.nombre)
        .outerjoin(Pago, Pago.orden_id == Orden.id)
        .join(Despacho, Despacho.id == Orden.despacho_id)
        .filter(Orden.estado.in_(("pagada", "reembolsada")), Orden.pagada_at >= ini, Orden.pagada_at < fin)
        .order_by(Orden.pagada_at.desc())
        .all()
    )
    bruto = sin_iva = iva = descuento = comision = comision_real_n = reembolsado = 0
    por_plan: dict[str, dict] = {}
    por_tipo: dict[str, dict] = {}
    por_mes: dict[str, dict] = {}
    pagos = []
    for o, p, despacho_nombre in filas:
        cobrado = o.total_centavos
        if p is not None and p.comision_centavos is not None:
            com, real = p.comision_centavos, True
            comision_real_n += 1
        else:
            com, real = (_comision_estimada(cobrado) if cobrado > 0 and o.proveedor == "stripe" else 0), False
        es_reemb = o.estado == "reembolsada"
        if es_reemb:
            reembolsado += cobrado
        bruto += cobrado
        iva += o.iva_centavos
        sin_iva += cobrado - o.iva_centavos
        descuento += o.descuento_centavos
        comision += com
        for clave, mapa in ((o.plan_codigo, por_plan), (o.tipo, por_tipo)):
            m = mapa.setdefault(clave, {"clave": clave, "ordenes": 0, "sin_iva_centavos": 0})
            m["ordenes"] += 1
            m["sin_iva_centavos"] += cobrado - o.iva_centavos
        mes = _aware(o.pagada_at).strftime("%Y-%m")
        m = por_mes.setdefault(mes, {"mes": mes, "ordenes": 0, "bruto_centavos": 0, "sin_iva_centavos": 0, "comision_centavos": 0})
        m["ordenes"] += 1
        m["bruto_centavos"] += cobrado
        m["sin_iva_centavos"] += cobrado - o.iva_centavos
        m["comision_centavos"] += com
        if len(pagos) < 100:
            pagos.append({
                "orden_id": o.id, "despacho": despacho_nombre, "despacho_id": o.despacho_id, "plan": o.plan_codigo, "tipo": o.tipo,
                "estado": o.estado, "proveedor": o.proveedor, "livemode": o.livemode, "total_centavos": cobrado,
                "iva_centavos": o.iva_centavos, "descuento_centavos": o.descuento_centavos, "comision_centavos": com,
                "comision_real": real, "pagada_at": o.pagada_at, "requiere_factura": o.requiere_factura,
            })

    meses_rango = max(1.0, (fin - ini).days / 30.4375)
    costo_fijo = int(round(settings.FINOPS_FIJO_MENSUAL_CENTAVOS * meses_rango))
    # La comisión de Stripe trae IVA acreditable; el margen se calcula sin IVA.
    comision_sin_iva = int(round(comision / (1 + settings.FINOPS_IVA_COMISION)))
    margen = sin_iva - comision_sin_iva - costo_fijo

    ahora = _ahora()
    vigentes = (
        db.query(Licencia, Despacho)
        .join(Despacho, Despacho.id == Licencia.despacho_id)
        .filter(Licencia.inicia <= ahora, Licencia.vence > ahora, Despacho.interno.is_(False), Despacho.activo.is_(True))
        .all()
    )
    actual: dict[int, tuple[Licencia, Despacho]] = {}
    for lic, d in vigentes:
        previo = actual.get(d.id)
        if previo is None or _aware(lic.vence) > _aware(previo[0].vence):
            actual[d.id] = (lic, d)
    planes = {p.codigo: p for p in db.query(Plan).all()}
    arr = 0
    pagando = en_prueba = por_cupon = 0
    por_vencer = []
    for lic, d in actual.values():
        if lic.origen == "prueba":
            en_prueba += 1
        elif lic.origen == "cupon":
            por_cupon += 1
        else:
            pagando += 1
            p = planes.get(lic.plan_codigo)
            arr += (p.precio_anual_centavos if p else 0) + d.usuarios_extra * planes["usuario_extra"].precio_anual_centavos
        dias = (_aware(lic.vence) - ahora).days
        if dias <= 45:
            por_vencer.append({"despacho_id": d.id, "despacho": d.nombre, "plan": lic.plan_codigo, "origen": lic.origen,
                               "vence": lic.vence, "dias": dias})
    por_vencer.sort(key=lambda x: x["dias"])
    nuevos = db.query(func.count(Despacho.id)).filter(Despacho.created_at >= ini, Despacho.created_at < fin, Despacho.interno.is_(False)).scalar() or 0

    return {
        "desde": ini, "hasta": fin - timedelta(seconds=1), "pagos_modo": settings.PAYMENTS_MODE,
        "kpis": {
            "bruto_centavos": bruto, "sin_iva_centavos": sin_iva, "iva_centavos": iva, "descuento_centavos": descuento,
            "comision_centavos": comision, "comisiones_reales": comision_real_n, "reembolsado_centavos": reembolsado,
            "costo_fijo_centavos": costo_fijo, "margen_centavos": margen,
            "margen_pct": (margen / sin_iva) if sin_iva else None,
            "ordenes": len(filas), "ticket_promedio_centavos": (bruto // len(filas)) if filas else 0,
            "arr_centavos": arr, "despachos_pagando": pagando, "despachos_en_prueba": en_prueba,
            "despachos_por_cupon": por_cupon, "despachos_nuevos": nuevos,
            "punto_equilibrio_d10": -(-settings.FINOPS_FIJO_MENSUAL_CENTAVOS * 12 // max(1, planes["d10"].precio_anual_centavos)) if "d10" in planes else None,
        },
        "por_mes": sorted(por_mes.values(), key=lambda x: x["mes"]),
        "por_plan": sorted(por_plan.values(), key=lambda x: -x["sin_iva_centavos"]),
        "por_tipo": sorted(por_tipo.values(), key=lambda x: -x["sin_iva_centavos"]),
        "por_vencer": por_vencer[:50],
        "pagos": pagos,
        "supuestos": {
            "fijo_mensual_centavos": settings.FINOPS_FIJO_MENSUAL_CENTAVOS, "stripe_pct": settings.FINOPS_STRIPE_PCT,
            "stripe_fijo_centavos": settings.FINOPS_STRIPE_FIJO_CENTAVOS, "iva_comision": settings.FINOPS_IVA_COMISION,
        },
    }


# --------------------------------------------------------------------------
# Embudo
# --------------------------------------------------------------------------

@router.get("/embudo")
def embudo_reporte(
    desde: date | None = None, hasta: date | None = None, fuente: str | None = None, dispositivo: str | None = None,
    db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin),
):
    ini, fin = _rango(desde, hasta, 30)
    r = asdict(embudo.reporte(db, ini, fin, fuente=fuente or None, dispositivo=dispositivo or None))
    ids = [x["despacho_id"] for x in r["rescate"]]
    if ids:
        filas = db.query(Despacho.id, Despacho.nombre, User.email).outerjoin(User, User.id == Despacho.dueno_id).filter(Despacho.id.in_(ids)).all()
        info = {i: (n, e) for i, n, e in filas}
        for x in r["rescate"]:
            x["despacho"], x["email"] = info.get(x["despacho_id"], (f"#{x['despacho_id']}", None))
    return r


# --------------------------------------------------------------------------
# Cupones
# --------------------------------------------------------------------------

class CuponIn(BaseModel):
    nombre: str
    tipo: str
    codigo: str | None = None
    prefijo: str | None = None
    cantidad: int = 1
    porcentaje: int | None = None
    monto_centavos: int | None = None
    dias: int | None = None
    plan_codigo: str | None = None
    planes_aplicables: list[str] | None = None
    max_canjes: int | None = None
    max_por_despacho: int = 1
    solo_primera_compra: bool = False
    vigente_desde: datetime | None = None
    vigente_hasta: datetime | None = None
    campana: str | None = None
    email_asignado: str | None = None


def _cupon_out(c: Cupon, extra: dict | None = None) -> dict:
    out = {
        "id": c.id, "codigo": c.codigo, "nombre": c.nombre, "tipo": c.tipo, "porcentaje": c.porcentaje,
        "monto_centavos": c.monto_centavos, "dias": c.dias, "plan_codigo": c.plan_codigo, "planes_aplicables": c.planes_aplicables,
        "max_canjes": c.max_canjes, "max_por_despacho": c.max_por_despacho, "solo_primera_compra": c.solo_primera_compra,
        "vigente_desde": c.vigente_desde, "vigente_hasta": c.vigente_hasta, "campana": c.campana, "lote": c.lote,
        "email_asignado": c.email_asignado, "estado": c.estado, "creado_por": c.creado_por, "created_at": c.created_at,
        "descripcion": cupones.describir(c),
    }
    if extra:
        out.update(extra)
    return out


@router.get("/cupones")
def listar_cupones(estado: str | None = None, q: str | None = None, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    filas = cupones.listar_con_metricas(db, estado=estado, q=q)
    return [
        _cupon_out(f["cupon"], {k: f[k] for k in ("canjeados", "apartados", "descuento_centavos", "ingreso_centavos")})
        for f in filas
    ]


@router.post("/cupones", status_code=201)
def crear_cupones(payload: CuponIn, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    if payload.plan_codigo:
        billing.plan_or_422(db, payload.plan_codigo, "paquete")
    for p in payload.planes_aplicables or []:
        billing.plan_or_422(db, p, "paquete")
    creados = cupones.crear(db, payload.model_dump(), creado_por=admin["email"])
    auditar(db, f"admin:{admin['email']}", "cupon.creado", None,
            {"codigos": [c.codigo for c in creados[:20]], "cantidad": len(creados), "tipo": payload.tipo})
    db.commit()
    return {"creados": [_cupon_out(c) for c in creados], "lote": creados[0].lote if creados else None}


class EstadoIn(BaseModel):
    estado: str


@router.put("/cupones/{cupon_id}/estado")
def estado_cupon(cupon_id: int, payload: EstadoIn, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    if payload.estado not in ("activo", "pausado", "archivado"):
        raise HTTPException(422, "Estado inválido.")
    c = db.get(Cupon, cupon_id)
    if c is None:
        raise HTTPException(404, "Cupón no encontrado.")
    c.estado = payload.estado
    auditar(db, f"admin:{admin['email']}", "cupon.estado", None, {"codigo": c.codigo, "estado": payload.estado})
    db.commit()
    return _cupon_out(c)


@router.get("/cupones/{cupon_id}/canjes")
def canjes_cupon(cupon_id: int, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    filas = (
        db.query(CuponCanje, Despacho.nombre).join(Despacho, Despacho.id == CuponCanje.despacho_id)
        .filter(CuponCanje.cupon_id == cupon_id).order_by(CuponCanje.created_at.desc()).limit(300).all()
    )
    return [
        {"id": c.id, "despacho": n, "despacho_id": c.despacho_id, "orden_id": c.orden_id, "estado": c.estado,
         "descuento_centavos": c.descuento_centavos, "cobrado_centavos": c.cobrado_centavos, "created_at": c.created_at,
         "canjeado_at": c.canjeado_at, "detalle": c.detalle}
        for c, n in filas
    ]


@router.get("/cupones/lote/{lote}.csv")
def lote_csv(lote: str, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    filas = db.query(Cupon).filter(Cupon.lote == lote).order_by(Cupon.id).all()
    if not filas:
        raise HTTPException(404, "Lote no encontrado.")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["codigo", "descripcion", "vigente_hasta", "campana"])
    for c in filas:
        w.writerow([c.codigo, cupones.describir(c), c.vigente_hasta.isoformat() if c.vigente_hasta else "", c.campana or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="cupones-{lote[:8]}.csv"'})


# --------------------------------------------------------------------------
# Despachos y licencias
# --------------------------------------------------------------------------

@router.get("/despachos")
def despachos(q: str | None = None, estado: str | None = None, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    query = db.query(Despacho)
    if q:
        like = f"%{q.strip()}%"
        sub = db.query(User.despacho_id).filter(User.email.ilike(like))
        query = query.filter(or_(Despacho.nombre.ilike(like), Despacho.codigo.ilike(like), Despacho.id.in_(sub)))
    lista = query.order_by(Despacho.created_at.desc()).limit(500).all()
    ids = [d.id for d in lista]
    usuarios = dict(db.query(User.despacho_id, func.count(User.id)).filter(User.despacho_id.in_(ids)).group_by(User.despacho_id).all()) if ids else {}
    clientes = dict(
        db.query(RfcClient.despacho_id, func.count(RfcClient.id)).filter(RfcClient.despacho_id.in_(ids), RfcClient.activo.is_(True))
        .group_by(RfcClient.despacho_id).all()
    ) if ids else {}
    duenos = {u.id: u for u in db.query(User).filter(User.id.in_([d.dueno_id for d in lista if d.dueno_id])).all()}
    ahora = _ahora()
    out = []
    for d in lista:
        lic = billing.licencia_vigente(db, d.id)
        ult = lic or billing.ultima_licencia(db, d.id)
        situacion = "sin_licencia" if ult is None else (lic.origen if lic else "vencida")
        if estado and estado != situacion:
            continue
        dueno = duenos.get(d.dueno_id)
        out.append({
            "id": d.id, "nombre": d.nombre, "codigo": d.codigo, "interno": d.interno, "activo": d.activo, "origen": d.origen,
            "created_at": d.created_at, "dueno": {"nombre": dueno.nombre, "email": dueno.email} if dueno else None,
            "usuarios": usuarios.get(d.id, 0), "clientes": clientes.get(d.id, 0), "situacion": situacion,
            "plan": ult.plan_codigo if ult else None, "vence": ult.vence if ult else None,
            "dias": (_aware(ult.vence) - ahora).days if ult else None,
        })
    return out


@router.get("/despachos/{despacho_id}")
def despacho_detalle(despacho_id: int, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    d = db.get(Despacho, despacho_id)
    if d is None:
        raise HTTPException(404, "Despacho no encontrado.")
    licencias = db.query(Licencia).filter(Licencia.despacho_id == d.id).order_by(Licencia.inicia.desc()).all()
    ordenes = db.query(Orden).filter(Orden.despacho_id == d.id).order_by(Orden.created_at.desc()).limit(50).all()
    solicitudes = (
        db.query(SolicitudAcceso, User).join(User, User.id == SolicitudAcceso.user_id)
        .filter(SolicitudAcceso.despacho_id == d.id).order_by(SolicitudAcceso.created_at.desc()).limit(50).all()
    )
    return {
        "despacho": {"id": d.id, "nombre": d.nombre, "codigo": d.codigo, "interno": d.interno, "activo": d.activo,
                     "datos_fiscales": d.datos_fiscales, "usuarios_extra": d.usuarios_extra, "rfc_extra_bloques": d.rfc_extra_bloques,
                     "origen": d.origen, "created_at": d.created_at},
        "estado": billing.estado(db, d),
        "miembros": [{"id": u.id, "nombre": u.nombre, "email": u.email, "rol_despacho": u.rol_despacho, "activo": u.activo}
                     for u in db.query(User).filter(User.despacho_id == d.id).order_by(User.nombre).all()],
        "licencias": [{"id": l.id, "plan_codigo": l.plan_codigo, "origen": l.origen, "inicia": l.inicia, "vence": l.vence,
                       "rfcs_max": l.rfcs_max, "usuarios_max": l.usuarios_max, "orden_id": l.orden_id, "nota": l.nota}
                      for l in licencias],
        "ordenes": [{"id": o.id, "tipo": o.tipo, "plan_codigo": o.plan_codigo, "estado": o.estado, "proveedor": o.proveedor,
                     "total_centavos": o.total_centavos, "descuento_centavos": o.descuento_centavos, "created_at": o.created_at,
                     "pagada_at": o.pagada_at, "requiere_factura": o.requiere_factura} for o in ordenes],
        "solicitudes": [{"id": s.id, "estado": s.estado, "solicitante": u.email, "traer_clientes": s.traer_clientes,
                         "created_at": s.created_at} for s, u in solicitudes],
    }


class LicenciaManualIn(BaseModel):
    plan_codigo: str
    dias: int = Field(ge=1, le=3660)
    nota: str | None = Field(default=None, max_length=500)


@router.post("/despachos/{despacho_id}/licencia", status_code=201)
def licencia_manual(despacho_id: int, payload: LicenciaManualIn, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    d = db.get(Despacho, despacho_id)
    if d is None:
        raise HTTPException(404, "Despacho no encontrado.")
    lic = billing.otorgar_manual(db, d, plan_codigo=payload.plan_codigo, dias=payload.dias, actor=f"admin:{admin['email']}", nota=payload.nota)
    return {"id": lic.id, "plan_codigo": lic.plan_codigo, "inicia": lic.inicia, "vence": lic.vence}


# --------------------------------------------------------------------------
# Correos y bitácora
# --------------------------------------------------------------------------

@router.get("/notificaciones")
def notificaciones(estado: str | None = None, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin)):
    q = db.query(Notificacion)
    if estado:
        q = q.filter(Notificacion.estado == estado)
    return [
        {"id": n.id, "plantilla": n.plantilla, "destinatario": n.destinatario, "asunto": n.asunto, "texto": n.texto,
         "estado": n.estado, "error": n.error, "intentos": n.intentos, "despacho_id": n.despacho_id,
         "created_at": n.created_at, "enviado_at": n.enviado_at}
        for n in q.order_by(Notificacion.created_at.desc()).limit(200).all()
    ]


@router.get("/auditoria")
def auditoria(despacho_id: int | None = None, db: Session = Depends(get_db), admin: dict = Depends(auth.get_admin),
              limite: int = Query(default=200, le=1000)):
    q = db.query(Auditoria)
    if despacho_id:
        q = q.filter(Auditoria.despacho_id == despacho_id)
    return [
        {"id": a.id, "actor": a.actor, "accion": a.accion, "despacho_id": a.despacho_id, "detalle": a.detalle, "created_at": a.created_at}
        for a in q.order_by(Auditoria.created_at.desc()).limit(limite).all()
    ]
