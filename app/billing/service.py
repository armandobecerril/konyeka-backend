"""Licencias anuales de KONYEKA: catálogo, licencia vigente, límites (RFC y
usuarios), cotización con prorrateo, checkout (Stripe o simulado) y el
procesamiento del pago (webhook = fuente de verdad), siguiendo el ADR-004 de
VEMI: el redirect de éxito nunca activa nada; solo el webhook (idempotente,
con la orden bloqueada y verificación de monto/moneda).

Precios: el catálogo guarda el precio anual SIN IVA; se cobra con IVA (16%).

Tipos de compra:
- nueva: sin licencia pagada vigente (prueba, cupón o vencida) -> 12 meses desde hoy.
- renovacion: licencia pagada que vence en <= 45 días -> 12 meses desde su vencimiento.
- mejora / complemento: subir de paquete o agregar usuarios/RFC a mitad del
  periodo -> se cobra la diferencia anual prorrateada por los días que quedan.
  Bajar de paquete solo al renovar.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin.models import auditar
from app.billing.models import Licencia, Orden, Pago, Plan, WebhookEvento
from app.core.config import settings
from app.cupones import service as cupones
from app.despachos.models import ROLES_GESTION, Despacho
from app.embudo import service as embudo
from app.rfc_clients.models import RfcClient
from app.users.models import User

logger = logging.getLogger(__name__)

IVA = 0.16
DIAS_ANIO = 365
DIAS_RENOVACION = 45
STRIPE_PRODUCTO = "konyeka"  # cuenta de Stripe compartida de ISC: etiqueta de producto


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iva_de(base_centavos: int) -> int:
    return int(round(base_centavos * IVA))


# --------------------------------------------------------------------------
# Catálogo y licencia vigente
# --------------------------------------------------------------------------

def catalogo(db: Session) -> list[Plan]:
    return db.query(Plan).filter(Plan.activo.is_(True)).order_by(Plan.orden).all()


def plan_or_422(db: Session, codigo: str, tipo: str | None = None) -> Plan:
    p = db.query(Plan).filter(Plan.codigo == codigo, Plan.activo.is_(True)).first()
    if p is None or (tipo and p.tipo != tipo):
        raise HTTPException(422, "Ese paquete no existe.")
    return p


def licencia_vigente(db: Session, despacho_id: int, momento: datetime | None = None) -> Licencia | None:
    momento = momento or ahora()
    return (
        db.query(Licencia)
        .filter(Licencia.despacho_id == despacho_id, Licencia.inicia <= momento, Licencia.vence > momento)
        .order_by(Licencia.vence.desc(), Licencia.id.desc())
        .first()
    )


def ultima_licencia(db: Session, despacho_id: int) -> Licencia | None:
    return (
        db.query(Licencia)
        .filter(Licencia.despacho_id == despacho_id)
        .order_by(Licencia.vence.desc(), Licencia.id.desc())
        .first()
    )


def rfcs_activos(db: Session, despacho_id: int) -> int:
    return db.query(func.count(RfcClient.id)).filter(
        RfcClient.despacho_id == despacho_id, RfcClient.activo.is_(True)
    ).scalar() or 0


def usuarios_activos(db: Session, despacho_id: int) -> int:
    return db.query(func.count(User.id)).filter(User.despacho_id == despacho_id, User.activo.is_(True)).scalar() or 0


def limites(lic: Licencia | None, despacho: Despacho) -> tuple[int, int]:
    """(rfcs_max, usuarios_max) efectivos: licencia + complementos comprados."""
    if lic is None:
        return 0, 0
    rfcs = lic.rfcs_max
    usuarios = lic.usuarios_max
    if lic.origen in ("pago", "manual"):
        usuarios += despacho.usuarios_extra
        if lic.plan_codigo == "d50p":
            rfcs += 10 * despacho.rfc_extra_bloques
    return rfcs, usuarios


def estado(db: Session, despacho: Despacho) -> dict:
    lic = licencia_vigente(db, despacho.id)
    ult = lic or ultima_licencia(db, despacho.id)
    rfcs_max, usuarios_max = limites(lic, despacho)
    plan = db.query(Plan).filter(Plan.codigo == ult.plan_codigo).first() if ult else None
    vence = _aware(lic.vence) if lic else (_aware(ult.vence) if ult else None)
    return {
        "activa": lic is not None,
        "en_prueba": bool(lic and lic.origen == "prueba"),
        "origen": lic.origen if lic else None,
        "plan_codigo": ult.plan_codigo if ult else None,
        "plan_nombre": plan.nombre if plan else None,
        "vence": vence,
        "dias_restantes": max(0, math.ceil((vence - ahora()).total_seconds() / 86400)) if lic and vence else 0,
        "rfcs_usados": rfcs_activos(db, despacho.id),
        "rfcs_max": rfcs_max,
        "usuarios_usados": usuarios_activos(db, despacho.id),
        "usuarios_max": usuarios_max,
        "usuarios_extra": despacho.usuarios_extra,
        "rfc_extra_bloques": despacho.rfc_extra_bloques,
        "puede_renovar": bool(lic and lic.origen == "pago" and (_aware(lic.vence) - ahora()).days <= DIAS_RENOVACION),
    }


def exigir_licencia_activa(db: Session, despacho_id: int | None) -> Licencia:
    if despacho_id is None:
        raise HTTPException(403, "Tu usuario todavía no pertenece a un despacho.")
    lic = licencia_vigente(db, despacho_id)
    if lic is None:
        raise HTTPException(
            402,
            "La licencia de tu despacho venció. Puedes consultar tu información, pero para seguir "
            "descargando, conciliando o dando de alta clientes renueva en «Mi licencia».",
        )
    return lic


def exigir_cupo_rfc(db: Session, despacho: Despacho) -> None:
    lic = exigir_licencia_activa(db, despacho.id)
    rfcs_max, _ = limites(lic, despacho)
    if rfcs_activos(db, despacho.id) >= rfcs_max:
        raise HTTPException(
            402,
            f"Tu licencia permite hasta {rfcs_max} RFC activos y ya los ocupaste. "
            "Sube de paquete en «Mi licencia» o da de baja un cliente que ya no atiendas.",
        )


def exigir_cupo_usuario(db: Session, despacho: Despacho) -> None:
    lic = exigir_licencia_activa(db, despacho.id)
    _, usuarios_max = limites(lic, despacho)
    if usuarios_activos(db, despacho.id) >= usuarios_max:
        raise HTTPException(
            402,
            f"Tu licencia incluye {usuarios_max} usuario(s) y ya están ocupados. "
            "Agrega un usuario adicional en «Mi licencia» para aprobar este acceso.",
        )


# --------------------------------------------------------------------------
# Cotización
# --------------------------------------------------------------------------

@dataclass
class Cotizacion:
    tipo: str
    plan: Plan
    usuarios_extra: int
    rfc_extra_bloques: int
    inicia: datetime
    vence: datetime
    lineas: list[dict] = field(default_factory=list)
    subtotal_centavos: int = 0
    nota: str | None = None


def _anual(db: Session, plan: Plan, usuarios_extra: int, bloques: int) -> tuple[int, list[dict]]:
    lineas = [{"concepto": f"Licencia anual {plan.nombre}", "cantidad": 1, "precio_centavos": plan.precio_anual_centavos}]
    total = plan.precio_anual_centavos
    if usuarios_extra:
        p = plan_or_422(db, "usuario_extra")
        lineas.append({"concepto": p.nombre, "cantidad": usuarios_extra, "precio_centavos": p.precio_anual_centavos})
        total += p.precio_anual_centavos * usuarios_extra
    if bloques:
        p = plan_or_422(db, "rfc_extra_10")
        lineas.append({"concepto": p.nombre, "cantidad": bloques, "precio_centavos": p.precio_anual_centavos})
        total += p.precio_anual_centavos * bloques
    return total, lineas


def cotizar(db: Session, despacho: Despacho, plan_codigo: str, usuarios_extra: int = 0, rfc_extra_bloques: int = 0,
            momento: datetime | None = None) -> Cotizacion:
    momento = momento or ahora()
    plan = plan_or_422(db, plan_codigo, "paquete")
    if not (0 <= usuarios_extra <= 100):
        raise HTTPException(422, "Usuarios adicionales: entre 0 y 100.")
    if rfc_extra_bloques and plan.codigo != "d50p":
        raise HTTPException(422, "Los bloques de RFC adicionales solo aplican al paquete Despacho 50+.")
    if not (0 <= rfc_extra_bloques <= 100):
        raise HTTPException(422, "Bloques de RFC: entre 0 y 100.")

    rfcs = rfcs_activos(db, despacho.id)
    capacidad = plan.rfcs_max + (10 * rfc_extra_bloques if plan.codigo == "d50p" else 0)
    if rfcs > capacidad:
        raise HTTPException(422, f"Tienes {rfcs} RFC activos y {plan.nombre} permite {capacidad}. Elige un paquete mayor.")
    usuarios = usuarios_activos(db, despacho.id)
    if usuarios > plan.usuarios_incluidos + usuarios_extra:
        raise HTTPException(
            422, f"Tu despacho tiene {usuarios} usuarios: agrega usuarios adicionales hasta cubrirlos."
        )

    total_nuevo, lineas = _anual(db, plan, usuarios_extra, rfc_extra_bloques)
    vig = licencia_vigente(db, despacho.id, momento)
    pagada = vig is not None and vig.origen in ("pago", "manual")
    if not pagada:
        return Cotizacion("nueva", plan, usuarios_extra, rfc_extra_bloques, momento, momento + timedelta(days=DIAS_ANIO),
                          lineas, total_nuevo)

    vence_actual = _aware(vig.vence)
    dias_restantes = max(0, (vence_actual - momento).days)
    if dias_restantes <= DIAS_RENOVACION:
        return Cotizacion("renovacion", plan, usuarios_extra, rfc_extra_bloques, vence_actual,
                          vence_actual + timedelta(days=DIAS_ANIO), lineas, total_nuevo,
                          nota=f"Renovación: el nuevo año empieza al terminar el actual ({vence_actual:%d/%m/%Y}).")

    plan_actual = plan_or_422(db, vig.plan_codigo, "paquete")
    total_actual, _ = _anual(db, plan_actual, despacho.usuarios_extra,
                             despacho.rfc_extra_bloques if plan_actual.codigo == "d50p" else 0)
    if plan.precio_anual_centavos < plan_actual.precio_anual_centavos:
        raise HTTPException(422, "Para bajar de paquete hazlo al renovar (en los 45 días antes de que venza).")
    diferencia = total_nuevo - total_actual
    if diferencia <= 0:
        raise HTTPException(422, "Esa combinación ya está cubierta por tu licencia actual.")
    prorrateo = int(round(diferencia * dias_restantes / DIAS_ANIO))
    tipo = "mejora" if plan.codigo != plan_actual.codigo else "complemento"
    lineas_pro = [{
        "concepto": f"Diferencia prorrateada por {dias_restantes} días ({plan_actual.nombre} → {plan.nombre}"
                    + (f", +{usuarios_extra - despacho.usuarios_extra} usuario(s)" if usuarios_extra != despacho.usuarios_extra else "")
                    + ")",
        "cantidad": 1, "precio_centavos": prorrateo,
    }]
    return Cotizacion(tipo, plan, usuarios_extra, rfc_extra_bloques, momento, vence_actual, lineas_pro, prorrateo,
                      nota=f"Se cobra solo lo que resta de tu periodo actual (vence el {vence_actual:%d/%m/%Y}).")


# --------------------------------------------------------------------------
# Checkout
# --------------------------------------------------------------------------

def _exigir_gestion(user: User) -> None:
    if user.despacho_id is None or user.rol_despacho not in ROLES_GESTION:
        raise HTTPException(403, "Solo el dueño o un administrador del despacho puede contratar.")


def _cotizar_cupon(db: Session, despacho: Despacho, user: User, codigo: str, cot: Cotizacion, bloquear: bool):
    cargado = cupones.cargar(db, codigo, despacho.id, bloquear=bloquear)
    if cargado is None:
        return None, cupones.Cotizacion(False, motivo="No encontramos ese cupón. Revisa que esté bien escrito.")
    cupon, total, mios = cargado
    compras = db.query(func.count(Orden.id)).filter(
        Orden.despacho_id == despacho.id, Orden.estado == "pagada", Orden.total_centavos > 0
    ).scalar() or 0
    return cupon, cupones.evaluar(
        cupon, ahora=ahora(), subtotal_centavos=cot.subtotal_centavos, plan_codigo=cot.plan.codigo, email=user.email,
        compras_previas=compras, canjes_despacho=mios, canjes_totales=total, para_pago=True,
    )


def vista_previa(db: Session, despacho: Despacho, user: User, datos: dict) -> dict:
    cot = cotizar(db, despacho, datos["plan_codigo"], int(datos.get("usuarios_extra") or 0), int(datos.get("rfc_extra_bloques") or 0))
    descuento, cupon_info = 0, None
    if datos.get("cupon"):
        cupon, q = _cotizar_cupon(db, despacho, user, datos["cupon"], cot, bloquear=False)
        cupon_info = {"codigo": cupones.normalizar(datos["cupon"]), "ok": q.ok, "motivo": q.motivo, "etiqueta": q.etiqueta, "nota": q.nota}
        if q.ok:
            descuento = q.descuento_centavos
        embudo.registrar(db, "coupon_applied" if q.ok else "coupon_rejected", despacho_id=despacho.id, user_id=user.id,
                         props={"codigo": cupon_info["codigo"]})
        db.commit()
    base = cot.subtotal_centavos - descuento
    return {
        "tipo": cot.tipo, "plan_codigo": cot.plan.codigo, "plan_nombre": cot.plan.nombre,
        "inicia": cot.inicia, "vence": cot.vence, "lineas": cot.lineas, "nota": cot.nota,
        "subtotal_centavos": cot.subtotal_centavos, "descuento_centavos": descuento,
        "iva_centavos": iva_de(base), "total_centavos": base + iva_de(base), "cupon": cupon_info,
    }


def crear_checkout(db: Session, despacho: Despacho, user: User, datos: dict) -> dict:
    _exigir_gestion(user)
    if settings.es_produccion and settings.PAYMENTS_MODE == "mock":
        raise HTTPException(503, "Los pagos no están configurados.")
    cot = cotizar(db, despacho, datos["plan_codigo"], int(datos.get("usuarios_extra") or 0), int(datos.get("rfc_extra_bloques") or 0))

    # Reintentar el pago libera el cupón apartado antes y expira órdenes pendientes.
    cupones.liberar_apartados_despacho(db, despacho.id)
    db.query(Orden).filter(Orden.despacho_id == despacho.id, Orden.estado == "pendiente").update(
        {"estado": "expirada"}, synchronize_session=False
    )
    descuento, cupon = 0, None
    if datos.get("cupon"):
        cupon, q = _cotizar_cupon(db, despacho, user, datos["cupon"], cot, bloquear=True)
        if not q.ok or cupon is None:
            db.rollback()
            raise HTTPException(422, q.motivo or "Cupón inválido.")
        descuento = q.descuento_centavos
    base = cot.subtotal_centavos - descuento
    iva = iva_de(base)
    if datos.get("datos_fiscales"):
        despacho.datos_fiscales = datos["datos_fiscales"]
    orden = Orden(
        id=str(uuid.uuid4()), despacho_id=despacho.id, user_id=user.id, tipo=cot.tipo, plan_codigo=cot.plan.codigo,
        usuarios_extra=cot.usuarios_extra, rfc_extra_bloques=cot.rfc_extra_bloques,
        periodo_inicia=cot.inicia, periodo_vence=cot.vence, lineas=cot.lineas,
        subtotal_centavos=cot.subtotal_centavos, descuento_centavos=descuento, iva_centavos=iva,
        total_centavos=base + iva, cupon_id=cupon.id if cupon else None, estado="pendiente",
        proveedor="cupon" if base + iva == 0 else ("mock" if settings.PAYMENTS_MODE == "mock" else "stripe"),
        livemode=settings.PAYMENTS_MODE == "stripe_live", requiere_factura=bool(datos.get("requiere_factura")),
    )
    db.add(orden)
    db.flush()
    if cupon:
        cupones.apartar(db, cupon_id=cupon.id, despacho_id=despacho.id, orden_id=orden.id, descuento=descuento, cobrado=orden.total_centavos)
    embudo.registrar(db, "checkout_started", despacho_id=despacho.id, user_id=user.id,
                     props={"plan": cot.plan.codigo, "tipo": cot.tipo, "cupon": cupon.codigo if cupon else None})
    auditar(db, f"user:{user.id}", "orden.creada", despacho.id, {"orden": orden.id, "plan": cot.plan.codigo, "total": orden.total_centavos})
    db.commit()

    if orden.total_centavos == 0:
        procesar_evento(db, EventoPago(proveedor="cupon", evento_id=f"cupon:{orden.id}", tipo="checkout.session.completed",
                                       orden_id=orden.id, pagado=True, monto_centavos=0, moneda="MXN", pago_id=f"cupon:{orden.id}"))
        return {"orden_id": orden.id, "url": f"{settings.APP_BASE_URL.rstrip('/')}/home/licencia/resultado?orden={orden.id}", "gratis": True}

    sesion = proveedor().crear_sesion(orden, despacho, user, cot.plan)
    orden.stripe_session_id = sesion["id"]
    db.commit()
    return {"orden_id": orden.id, "url": sesion["url"], "gratis": False}


# --------------------------------------------------------------------------
# Proveedores de pago
# --------------------------------------------------------------------------

class ProveedorMock:
    """Pasarela simulada (desarrollo): la página /checkout/mock del frontend
    llama a /pagos/mock/{sesion}/{resultado}, que arma el mismo evento que
    mandaría Stripe y lo procesa por el mismo camino que el webhook."""

    nombre = "mock"

    def crear_sesion(self, orden: Orden, despacho: Despacho, user: User, plan: Plan) -> dict:
        sid = f"mock_cs_{uuid.uuid4().hex[:16]}"
        return {"id": sid, "url": f"{settings.APP_BASE_URL.rstrip('/')}/checkout/mock/{sid}?orden={orden.id}"}


def _stripe():
    import stripe

    key = settings.STRIPE_SECRET_KEY
    if not key:
        raise HTTPException(503, "Stripe no está configurado (falta STRIPE_SECRET_KEY).")
    modo = settings.PAYMENTS_MODE
    if modo == "stripe_test" and not key.startswith(("sk_test_", "rk_test_")):
        raise HTTPException(503, "PAYMENTS_MODE=stripe_test requiere una llave sk_test_ o rk_test_.")
    if modo == "stripe_live" and not key.startswith(("sk_live_", "rk_live_")):
        raise HTTPException(503, "PAYMENTS_MODE=stripe_live requiere una llave sk_live_ o rk_live_.")
    stripe.api_key = key
    return stripe


class ProveedorStripe:
    nombre = "stripe"

    def crear_sesion(self, orden: Orden, despacho: Despacho, user: User, plan: Plan) -> dict:
        stripe = _stripe()
        base = settings.APP_BASE_URL.rstrip("/")
        descripcion = f"KONYEKA · {plan.nombre} · {despacho.nombre}"[:200]
        # Una sola línea por el total con IVA (el desglose va en la orden y en
        # el recibo). Con cupón, Stripe muestra el descuento: subtotal con IVA
        # menos un cupón espejo de monto fijo = total exacto de la orden.
        subtotal_con_iva = orden.subtotal_centavos + iva_de(orden.subtotal_centavos)
        descuento_con_iva = subtotal_con_iva - orden.total_centavos
        descuentos = None
        if descuento_con_iva > 0 and orden.cupon_id:
            descuentos = [{"coupon": _cupon_espejo(stripe, f"{orden.id[:8]}", descuento_con_iva)}]
        sesion = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "mxn", "unit_amount": subtotal_con_iva if descuentos else orden.total_centavos,
                    "product_data": {"name": f"Licencia anual {plan.nombre}", "description": "IVA incluido"},
                },
                "quantity": 1,
            }],
            **({"discounts": descuentos} if descuentos else {}),
            payment_method_types=["card"],
            locale="es-419",
            submit_type="pay",
            customer_email=user.email,
            payment_intent_data={
                "description": descripcion,
                "metadata": {"product": STRIPE_PRODUCTO, "orden_id": orden.id, "despacho_id": str(despacho.id)},
                "statement_descriptor_suffix": "KONYEKA",
            },
            metadata={"product": STRIPE_PRODUCTO, "orden_id": orden.id, "despacho_id": str(despacho.id)},
            client_reference_id=orden.id,
            success_url=f"{base}/home/licencia/resultado?orden={orden.id}",
            cancel_url=f"{base}/home/licencia/resultado?orden={orden.id}&cancelado=1",
            expires_at=int((ahora() + timedelta(minutes=60)).timestamp()),
            idempotency_key=f"konyeka-orden:{orden.id}",
        )
        return {"id": sesion.id, "url": sesion.url}


def _cupon_espejo(stripe, sufijo: str, centavos: int) -> str:
    cid = f"konyeka_{sufijo}_{centavos}".lower()
    try:
        stripe.Coupon.retrieve(cid)
    except Exception:  # noqa: BLE001
        try:
            stripe.Coupon.create(id=cid, amount_off=centavos, currency="mxn", duration="once", name="Cupón KONYEKA")
        except Exception as exc:  # noqa: BLE001
            if "already exists" not in str(exc):
                raise
    return cid


def proveedor():
    return ProveedorMock() if settings.PAYMENTS_MODE == "mock" else ProveedorStripe()


# --------------------------------------------------------------------------
# Procesamiento del pago (webhook = fuente de verdad)
# --------------------------------------------------------------------------

@dataclass
class EventoPago:
    proveedor: str
    evento_id: str
    tipo: str  # checkout.session.completed | ...async_payment_succeeded | ...expired | ...async_payment_failed | charge.refunded
    orden_id: str | None = None
    sesion_id: str | None = None
    pago_id: str | None = None
    pagado: bool = False
    monto_centavos: int | None = None
    moneda: str | None = None
    reembolso_total: bool | None = None
    payload: dict | None = None


def procesar_evento(db: Session, e: EventoPago) -> dict:
    """Idempotente (evento único), con la orden bloqueada (FOR UPDATE) y
    verificación de monto/moneda. Los eventos de otros productos de ISC no
    llegan aquí (los filtra parsear_webhook_stripe)."""
    orden_id = e.orden_id
    if not orden_id and e.sesion_id:
        fila = db.query(Orden.id).filter(Orden.stripe_session_id == e.sesion_id).first()
        orden_id = fila[0] if fila else None
    if not orden_id and e.pago_id:
        fila = db.query(Pago.orden_id).filter(Pago.proveedor_pago_id == e.pago_id).first()
        orden_id = fila[0] if fila else None
    if not orden_id:
        return {"estado": "orden_desconocida"}

    if db.query(WebhookEvento.id).filter(WebhookEvento.evento_id == e.evento_id).first():
        return {"estado": "duplicado"}
    evento = WebhookEvento(proveedor=e.proveedor, evento_id=e.evento_id, tipo=e.tipo, payload=e.payload)
    db.add(evento)

    orden = db.query(Orden).filter(Orden.id == orden_id).with_for_update().first()
    if orden is None:
        db.rollback()
        return {"estado": "orden_desconocida"}

    resultado = "ignorado"
    exito = (e.tipo == "checkout.session.completed" and e.pagado) or e.tipo == "checkout.session.async_payment_succeeded"
    if exito and orden.estado == "pendiente":
        if e.monto_centavos is not None and (e.monto_centavos != orden.total_centavos or (e.moneda or "").upper() != orden.moneda):
            orden.estado = "fallida"
            auditar(db, "sistema", "pago.monto_no_coincide", orden.despacho_id, {"orden": orden.id, "recibido": e.monto_centavos})
            resultado = "fallida"
        else:
            _cumplir(db, orden, e)
            resultado = "pagada"
    elif e.tipo == "checkout.session.expired" and orden.estado == "pendiente":
        orden.estado = "expirada"
        cupones.liberar_orden(db, orden.id)
        resultado = "expirada"
    elif e.tipo == "checkout.session.async_payment_failed" and orden.estado == "pendiente":
        orden.estado = "fallida"
        cupones.liberar_orden(db, orden.id)
        resultado = "fallida"
    elif e.tipo == "charge.refunded" and orden.estado == "pagada":
        # Política: se registra; la licencia NO se revoca automáticamente.
        if e.reembolso_total is not False:
            orden.estado = "reembolsada"
            resultado = "reembolsada"
        auditar(db, "sistema", "pago.reembolsado", orden.despacho_id, {"orden": orden.id, "monto": e.monto_centavos})
    evento.procesado_at = ahora()
    db.commit()

    if resultado == "pagada":
        _despues_de_pagar(db, orden.id, e)
    return {"estado": resultado, "orden_id": orden.id}


def _cumplir(db: Session, orden: Orden, e: EventoPago) -> None:
    despacho = db.query(Despacho).filter(Despacho.id == orden.despacho_id).with_for_update().first()
    plan = plan_or_422(db, orden.plan_codigo)
    orden.estado = "pagada"
    orden.pagada_at = ahora()
    db.add(Pago(orden_id=orden.id, proveedor=e.proveedor, proveedor_pago_id=e.pago_id or e.sesion_id or orden.id,
                monto_centavos=orden.total_centavos, comision_centavos=0 if orden.total_centavos == 0 else None,
                neto_centavos=0 if orden.total_centavos == 0 else None))
    despacho.usuarios_extra = orden.usuarios_extra
    despacho.rfc_extra_bloques = orden.rfc_extra_bloques if orden.plan_codigo == "d50p" else 0
    db.add(Licencia(
        despacho_id=despacho.id, plan_codigo=plan.codigo, rfcs_max=plan.rfcs_max, usuarios_max=plan.usuarios_incluidos,
        inicia=orden.periodo_inicia, vence=orden.periodo_vence, origen="pago", orden_id=orden.id, cupon_id=orden.cupon_id,
        nota=f"Orden {orden.tipo}",
    ))
    if orden.cupon_id:
        cupones.canjear_orden(db, orden.id)
    auditar(db, "sistema", "licencia.pagada", despacho.id, {"orden": orden.id, "plan": plan.codigo, "total": orden.total_centavos})


def _despues_de_pagar(db: Session, orden_id: str, e: EventoPago) -> None:
    from app.notificaciones import service as correo

    orden = db.get(Orden, orden_id)
    despacho = db.get(Despacho, orden.despacho_id)
    plan = db.query(Plan).filter(Plan.codigo == orden.plan_codigo).first()
    embudo.registrar(db, "purchase_completed", despacho_id=despacho.id, user_id=orden.user_id,
                     props={"plan": orden.plan_codigo, "tipo": orden.tipo, "total": orden.total_centavos,
                            "descuento": orden.descuento_centavos, "gratis": orden.total_centavos == 0})
    comprador = db.get(User, orden.user_id) if orden.user_id else None
    if comprador:
        correo.correo_pago_recibido(db, destinatario=comprador.email, despacho=despacho, orden=orden, plan_nombre=plan.nombre)
    db.commit()
    if e.proveedor == "stripe" and e.pago_id and orden.total_centavos > 0:
        try:
            registrar_comision_stripe(db, e.pago_id)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo leer la comisión real de Stripe para %s", e.pago_id)


def registrar_comision_stripe(db: Session, payment_intent_id: str) -> None:
    """Comisión REAL de Stripe y neto depositado (con el balance transaction
    para conciliar el depósito compartido de ISC)."""
    stripe = _stripe()
    pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["latest_charge.balance_transaction"])
    bt = pi.latest_charge.balance_transaction if pi.latest_charge else None
    if not bt or isinstance(bt, str):
        return
    db.query(Pago).filter(Pago.proveedor == "stripe", Pago.proveedor_pago_id == payment_intent_id).update(
        {"comision_centavos": bt.fee, "neto_centavos": bt.net, "balance_transaction_id": bt.id}, synchronize_session=False
    )
    db.commit()


def parsear_webhook_stripe(cuerpo: bytes, firma: str | None) -> EventoPago | None:
    stripe = _stripe()
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET no configurado.")
    if not firma:
        raise HTTPException(400, "Falta la cabecera stripe-signature.")
    try:
        stripe.Webhook.construct_event(cuerpo, firma, settings.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Firma de Stripe inválida: {exc}") from exc
    # Firma verificada: se lee el JSON como diccionario simple (en
    # stripe-python 17 los StripeObject ya no se comportan como dict).
    datos = json.loads(cuerpo)
    if bool(datos.get("livemode")) != (settings.PAYMENTS_MODE == "stripe_live"):
        raise HTTPException(400, "El evento no corresponde al modo de pagos configurado (prueba vs. real).")
    tipo = datos.get("type") or ""
    obj = (datos.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if meta.get("product") and meta["product"] != STRIPE_PRODUCTO:
        return None  # pago de VEMI/IRIS u otro producto de ISC: no se guarda
    pi = obj.get("payment_intent")
    if isinstance(pi, dict):
        pi = pi.get("id")
    if tipo == "charge.refunded":
        if not pi:
            return None
        return EventoPago(proveedor="stripe", evento_id=datos["id"], tipo=tipo, pago_id=pi,
                          monto_centavos=obj.get("amount_refunded"), moneda=obj.get("currency"), reembolso_total=obj.get("refunded"))
    if tipo.startswith("checkout.session."):
        if not meta.get("product"):
            return None  # sesión sin etiqueta: no es de KONYEKA
        return EventoPago(proveedor="stripe", evento_id=datos["id"], tipo=tipo, orden_id=meta.get("orden_id"),
                          sesion_id=obj.get("id"), pago_id=pi, pagado=obj.get("payment_status") == "paid",
                          monto_centavos=obj.get("amount_total"), moneda=obj.get("currency"), payload={"session": obj.get("id")})
    return None


# --------------------------------------------------------------------------
# Licencias por prueba, cupón de días gratis o asignación manual
# --------------------------------------------------------------------------

def crear_prueba(db: Session, despacho: Despacho) -> Licencia:
    plan = plan_or_422(db, "prueba")
    lic = Licencia(despacho_id=despacho.id, plan_codigo="prueba", rfcs_max=plan.rfcs_max, usuarios_max=plan.usuarios_incluidos,
                   inicia=ahora(), vence=ahora() + timedelta(days=settings.PRUEBA_DIAS), origen="prueba",
                   nota=f"Prueba de {settings.PRUEBA_DIAS} días")
    db.add(lic)
    return lic


def canjear_dias_gratis(db: Session, despacho: Despacho, user: User, codigo: str) -> Licencia:
    _exigir_gestion(user)
    cargado = cupones.cargar(db, codigo, despacho.id, bloquear=True)
    if cargado is None:
        embudo.registrar(db, "coupon_rejected", despacho_id=despacho.id, user_id=user.id, props={"codigo": cupones.normalizar(codigo)})
        db.commit()
        raise HTTPException(422, "No encontramos ese cupón. Revisa que esté bien escrito.")
    cupon, total, mios = cargado
    q = cupones.evaluar(cupon, ahora=ahora(), subtotal_centavos=0, plan_codigo=cupon.plan_codigo or "d10", email=user.email,
                        compras_previas=0, canjes_despacho=mios, canjes_totales=total, para_pago=False)
    if not q.ok:
        raise HTTPException(422, q.motivo)
    vig = licencia_vigente(db, despacho.id)
    if vig is not None and vig.origen in ("pago", "manual"):
        plan = plan_or_422(db, vig.plan_codigo)
    else:
        plan = plan_or_422(db, cupon.plan_codigo or "d10")
    inicia = _aware(vig.vence) if vig is not None else ahora()
    lic = Licencia(despacho_id=despacho.id, plan_codigo=plan.codigo, rfcs_max=plan.rfcs_max, usuarios_max=plan.usuarios_incluidos,
                   inicia=inicia, vence=inicia + timedelta(days=cupon.dias), origen="cupon", cupon_id=cupon.id,
                   nota=f"Cupón {cupon.codigo}: {cupon.dias} días")
    db.add(lic)
    from app.cupones.models import CuponCanje

    db.add(CuponCanje(cupon_id=cupon.id, despacho_id=despacho.id, estado="canjeado", canjeado_at=ahora(),
                      detalle={"dias": cupon.dias, "plan": plan.codigo}))
    embudo.registrar(db, "free_days_redeemed", despacho_id=despacho.id, user_id=user.id, props={"codigo": cupon.codigo, "dias": cupon.dias})
    auditar(db, f"user:{user.id}", "cupon.dias_gratis", despacho.id, {"cupon": cupon.codigo, "dias": cupon.dias})
    db.commit()
    db.refresh(lic)
    return lic


def otorgar_manual(db: Session, despacho: Despacho, *, plan_codigo: str, dias: int, actor: str, nota: str | None) -> Licencia:
    plan = plan_or_422(db, plan_codigo)
    vig = licencia_vigente(db, despacho.id)
    inicia = _aware(vig.vence) if vig is not None and vig.origen != "prueba" else ahora()
    lic = Licencia(despacho_id=despacho.id, plan_codigo=plan.codigo, rfcs_max=plan.rfcs_max, usuarios_max=plan.usuarios_incluidos,
                   inicia=inicia, vence=inicia + timedelta(days=dias), origen="manual", nota=nota or f"Otorgada por {actor}")
    db.add(lic)
    auditar(db, actor, "licencia.manual", despacho.id, {"plan": plan.codigo, "dias": dias, "nota": nota})
    db.commit()
    db.refresh(lic)
    return lic
