"""Cupones de KONYEKA (reglas portadas del módulo de VEMI).

Tipos:
- dias_gratis: N días de uso de un paquete sin pagar (p. ej. «30 días»). Se
  canjea desde Mi licencia; si el despacho ya tiene licencia, los días se
  suman al final de la vigente.
- porcentaje / monto: descuento sobre el subtotal (sin IVA) al contratar.

Ciclo de canje en el pago (igual que VEMI): al ir a pagar se APARTA (90 min),
el webhook lo CANJEA y si la sesión expira o falla se LIBERA.
"""
from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.cupones.models import Cupon, CuponCanje

MINUTOS_APARTADO = 90
CODIGO_RE = re.compile(r"^[A-Z0-9-]{3,32}$")
ALFABETO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # sin 0/O/1/I/L para dictarlos sin errores
COBRO_MINIMO_CENTAVOS = 1000  # Stripe MXN: $10


def normalizar(codigo: str) -> str:
    return re.sub(r"\s+", "", (codigo or "").strip().upper())


def generar_codigo(prefijo: str = "KONYEKA", largo: int = 6) -> str:
    p = re.sub(r"[^A-Z0-9]", "", normalizar(prefijo))[:14] or "KONYEKA"
    return f"{p}-{''.join(secrets.choice(ALFABETO) for _ in range(largo))}"


def describir(c: Cupon) -> str:
    if c.tipo == "dias_gratis":
        return f"{c.dias} días gratis"
    if c.tipo == "porcentaje":
        return f"{c.porcentaje}% de descuento"
    return f"${(c.monto_centavos or 0) / 100:,.2f} de descuento"


@dataclass
class Cotizacion:
    ok: bool
    descuento_centavos: int = 0
    base_centavos: int = 0
    etiqueta: str = ""
    nota: str | None = None
    motivo: str | None = None


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def evaluar(
    c: Cupon,
    *,
    ahora: datetime,
    subtotal_centavos: int,
    plan_codigo: str,
    email: str | None,
    compras_previas: int,
    canjes_despacho: int,
    canjes_totales: int,
    para_pago: bool = True,
) -> Cotizacion:
    """Reglas puras (sin BD). `para_pago`: el cupón se usa al contratar (los
    de días gratis no aplican ahí, se canjean en Mi licencia)."""
    if c.estado != "activo":
        return Cotizacion(False, motivo="Este cupón no está activo.")
    if ahora < _aware(c.vigente_desde):
        return Cotizacion(False, motivo="Este cupón todavía no está vigente.")
    if c.vigente_hasta and ahora >= _aware(c.vigente_hasta):
        return Cotizacion(False, motivo="Este cupón ya venció.")
    if c.email_asignado and c.email_asignado.lower() != (email or "").lower():
        return Cotizacion(False, motivo="Este cupón es personal y no corresponde a tu cuenta.")
    if para_pago and c.tipo == "dias_gratis":
        return Cotizacion(False, motivo="Este cupón es de días gratis: canjéalo en «Mi licencia».")
    if not para_pago and c.tipo != "dias_gratis":
        return Cotizacion(False, motivo="Este cupón es de descuento: úsalo al contratar tu paquete.")
    if c.planes_aplicables and plan_codigo not in c.planes_aplicables:
        return Cotizacion(False, motivo="El cupón no aplica a este paquete.")
    if c.solo_primera_compra and compras_previas > 0:
        return Cotizacion(False, motivo="Este cupón es solo para la primera compra.")
    if c.max_canjes is not None and canjes_totales >= c.max_canjes:
        return Cotizacion(False, motivo="Este cupón ya se agotó.")
    if canjes_despacho >= c.max_por_despacho:
        return Cotizacion(False, motivo="Tu despacho ya usó este cupón.")

    if c.tipo == "dias_gratis":
        return Cotizacion(True, etiqueta=describir(c))

    if c.tipo == "porcentaje":
        descuento = round(subtotal_centavos * min(100, max(0, c.porcentaje or 0)) / 100)
    else:
        descuento = min(subtotal_centavos, max(0, c.monto_centavos or 0))
    base = subtotal_centavos - descuento
    nota = None
    # El total con IVA no puede quedar entre $0 y el cobro mínimo de Stripe.
    minimo_base = -(-COBRO_MINIMO_CENTAVOS * 100 // 116)
    if 0 < base < minimo_base:
        base = minimo_base
        descuento = subtotal_centavos - base
        nota = "Se ajustó al cobro mínimo de $10."
    if descuento <= 0:
        return Cotizacion(False, motivo="El cupón no genera descuento en esta compra.")
    return Cotizacion(True, descuento_centavos=descuento, base_centavos=base, etiqueta=describir(c), nota=nota)


def _activo(ahora: datetime):
    limite = ahora - timedelta(minutes=MINUTOS_APARTADO)
    return or_(CuponCanje.estado == "canjeado", and_(CuponCanje.estado == "apartado", CuponCanje.created_at > limite))


def cargar(db: Session, codigo: str, despacho_id: int, *, bloquear: bool = False, ahora: datetime | None = None):
    codigo = normalizar(codigo)
    if not CODIGO_RE.match(codigo):
        return None
    q = db.query(Cupon).filter(Cupon.codigo == codigo)
    if bloquear:
        q = q.with_for_update()
    cupon = q.first()
    if cupon is None:
        return None
    ahora = ahora or _ahora()
    total, mios = db.query(
        func.count(CuponCanje.id),
        func.count(CuponCanje.id).filter(CuponCanje.despacho_id == despacho_id),
    ).filter(CuponCanje.cupon_id == cupon.id, _activo(ahora)).one()
    return cupon, total, mios


def liberar_apartados_despacho(db: Session, despacho_id: int) -> None:
    db.query(CuponCanje).filter(CuponCanje.despacho_id == despacho_id, CuponCanje.estado == "apartado").update(
        {"estado": "liberado", "liberado_at": _ahora()}, synchronize_session=False
    )


def apartar(db: Session, *, cupon_id: int, despacho_id: int, orden_id: str, descuento: int, cobrado: int) -> None:
    db.add(CuponCanje(cupon_id=cupon_id, despacho_id=despacho_id, orden_id=orden_id,
                      descuento_centavos=descuento, cobrado_centavos=cobrado, estado="apartado"))


def canjear_orden(db: Session, orden_id: str) -> None:
    db.query(CuponCanje).filter(CuponCanje.orden_id == orden_id, CuponCanje.estado.in_(("apartado", "liberado"))).update(
        {"estado": "canjeado", "canjeado_at": _ahora()}, synchronize_session=False
    )


def liberar_orden(db: Session, orden_id: str) -> None:
    db.query(CuponCanje).filter(CuponCanje.orden_id == orden_id, CuponCanje.estado == "apartado").update(
        {"estado": "liberado", "liberado_at": _ahora()}, synchronize_session=False
    )


# --------------------------------------------------------------------------
# Administración (portal superadmin)
# --------------------------------------------------------------------------

def crear(db: Session, datos: dict, creado_por: str) -> list[Cupon]:
    tipo = datos.get("tipo")
    if tipo not in ("dias_gratis", "porcentaje", "monto"):
        raise HTTPException(422, "Tipo de cupón inválido.")
    if tipo == "porcentaje" and not (1 <= int(datos.get("porcentaje") or 0) <= 100):
        raise HTTPException(422, "El porcentaje debe estar entre 1 y 100.")
    if tipo == "monto" and int(datos.get("monto_centavos") or 0) <= 0:
        raise HTTPException(422, "El monto debe ser mayor a 0.")
    if tipo == "dias_gratis" and not (1 <= int(datos.get("dias") or 0) <= 366):
        raise HTTPException(422, "Los días gratis deben estar entre 1 y 366.")
    cantidad = max(1, min(500, int(datos.get("cantidad") or 1)))
    codigo = normalizar(datos.get("codigo") or "")
    if codigo and not CODIGO_RE.match(codigo):
        raise HTTPException(422, "Código: 3–32 letras, números o guiones.")
    if codigo and cantidad > 1:
        raise HTTPException(422, "Para un lote deja el código vacío y usa un prefijo.")
    nombre = (datos.get("nombre") or "").strip()
    if len(nombre) < 2:
        raise HTTPException(422, "Ponle un nombre interno al cupón.")
    lote = str(uuid.uuid4()) if cantidad > 1 else None
    campana = datos.get("campana")
    base = dict(
        nombre=nombre[:80], tipo=tipo,
        porcentaje=int(datos["porcentaje"]) if tipo == "porcentaje" else None,
        monto_centavos=int(datos["monto_centavos"]) if tipo == "monto" else None,
        dias=int(datos["dias"]) if tipo == "dias_gratis" else None,
        plan_codigo=(datos.get("plan_codigo") or "d10") if tipo == "dias_gratis" else None,
        planes_aplicables=datos.get("planes_aplicables") or None,
        max_canjes=int(datos["max_canjes"]) if datos.get("max_canjes") else None,
        max_por_despacho=max(1, int(datos.get("max_por_despacho") or 1)),
        solo_primera_compra=bool(datos.get("solo_primera_compra")),
        vigente_desde=datos.get("vigente_desde") or _ahora(),
        vigente_hasta=datos.get("vigente_hasta") or None,
        campana=re.sub(r"[^a-z0-9-]+", "-", campana.lower()).strip("-")[:60] if campana else None,
        email_asignado=(datos.get("email_asignado") or "").strip().lower() or None,
        lote=lote, creado_por=creado_por,
    )
    creados = []
    for _ in range(cantidad):
        for _intento in range(5):
            c = codigo or generar_codigo(datos.get("prefijo") or base["campana"] or "KONYEKA")
            if db.query(Cupon.id).filter(Cupon.codigo == c).first():
                if codigo:
                    raise HTTPException(409, f"El código {c} ya existe.")
                continue
            cupon = Cupon(codigo=c, **base)
            db.add(cupon)
            db.flush()
            creados.append(cupon)
            break
    db.commit()
    return creados


def listar_con_metricas(db: Session, *, estado: str | None = None, q: str | None = None) -> list[dict]:
    query = db.query(
        Cupon,
        func.count(CuponCanje.id).filter(CuponCanje.estado == "canjeado").label("canjeados"),
        func.count(CuponCanje.id).filter(
            CuponCanje.estado == "apartado", CuponCanje.created_at > _ahora() - timedelta(minutes=MINUTOS_APARTADO)
        ).label("apartados"),
        func.coalesce(func.sum(CuponCanje.descuento_centavos).filter(CuponCanje.estado == "canjeado"), 0).label("descuento"),
        func.coalesce(func.sum(CuponCanje.cobrado_centavos).filter(CuponCanje.estado == "canjeado"), 0).label("ingreso"),
    ).outerjoin(CuponCanje, CuponCanje.cupon_id == Cupon.id)
    if estado and estado != "todos":
        query = query.filter(Cupon.estado == estado)
    else:
        query = query.filter(Cupon.estado != "archivado")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Cupon.codigo.ilike(like), Cupon.nombre.ilike(like), Cupon.email_asignado.ilike(like)))
    filas = query.group_by(Cupon.id).order_by(Cupon.created_at.desc()).limit(300).all()
    return [
        {"cupon": c, "canjeados": a, "apartados": b, "descuento_centavos": int(d), "ingreso_centavos": int(i), "descripcion": describir(c)}
        for c, a, b, d, i in filas
    ]
