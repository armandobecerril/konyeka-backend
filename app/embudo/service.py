"""Embudo de conversión de KONYEKA (portado del módulo de VEMI).

De la primera visita a la landing hasta pagar la licencia, y después la
activación (valor que recibe el despacho). Los benchmarks son metas
iniciales: ajústalos con datos reales.

Los pasos de negocio SOLO los registra el servidor; el navegador únicamente
puede enviar pasos de vista en lista blanca (POST /embudo/evento).
La analítica nunca rompe el flujo: los errores se registran y se ignoran.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.embudo.models import EventoEmbudo

logger = logging.getLogger(__name__)


@dataclass
class Paso:
    key: str
    label: str
    description: str
    benchmark: float | None = None
    playbook: str | None = None
    client: bool = False


PASOS = [
    Paso("visit", "Visitó la landing", "Abrió la página de KONYEKA.", client=True),
    Paso("pricing_view", "Vio los paquetes", "Llegó a la sección de precios.", 0.35,
         "Lleva tráfico directo a #precios, muestra el ahorro de horas de conciliación con un ejemplo real y el precio por RFC.", True),
    Paso("signup_start", "Quiso registrarse", "Abrió el registro (prueba gratis).", 0.25,
         "Haz más visible «Prueba 14 días sin tarjeta» y ofrece demo de 15 minutos con un contador.", True),
    Paso("signup_completed", "Se registró", "Creó su cuenta y su despacho (o pidió unirse a uno).", 0.6,
         "Reduce campos del registro y explica que no se pide tarjeta para la prueba."),
    Paso("checkout_viewed", "Vio el precio para contratar", "Abrió la pantalla de contratar licencia.", 0.5,
         "Recuerda los días que le quedan de prueba y lo que pierde al vencer; envía cupón de lanzamiento.", True),
    Paso("checkout_started", "Fue a pagar", "Eligió paquete y salió al pago.", 0.5,
         "El precio frena: cupón de primera compra o % por pago anual; explica el precio por RFC."),
    Paso("purchase_completed", "Pagó la licencia", "Pagó (o cubrió con cupón) su licencia anual.", 0.7,
         "Revisa pagos rechazados en Stripe y manda recordatorio con cupón personal a quien abandonó."),
]

# Uso del producto durante la prueba (no es requisito para pagar: hay quien
# compra antes de descargar). Se mide contra «Se registró».
PRUEBA = [
    Paso("client_added", "Dio de alta un RFC", "Registró a su primer cliente.", 0.7,
         "Ofrece cargar la Constancia de Situación Fiscal en PDF para precargar el alta."),
    Paso("efirma_uploaded", "Subió la e.firma", "Cargó la e.firma de un cliente.", 0.6,
         "Explica por qué se necesita la e.firma (descarga masiva y documentos SAT) y cómo se protege (cifrada)."),
    Paso("download_requested", "Descargó CFDI", "Pidió su primera descarga masiva al SAT.", 0.7,
         "Sugiere descargar los últimos 3 meses en un clic al terminar de subir la e.firma."),
]

ACTIVACION = [
    Paso("bank_statement_uploaded", "Cargó un estado de cuenta", "Subió su primer estado de cuenta bancario.", 0.5,
         "Muestra en el onboarding cuántas horas ahorra cruzar banco contra CFDI."),
    Paso("member_joined", "Sumó a su equipo", "Un usuario más se unió al despacho.", 0.3,
         "Recuerda compartir el código del despacho; ofrece un usuario adicional con descuento."),
    Paso("sat_document_downloaded", "Descargó Opinión/Constancia", "Descargó un documento SAT con la e.firma.", 0.4,
         "Destaca los botones de Opinión y Constancia en la bóveda del cliente."),
]

EXTRA = [
    Paso("join_requested", "Pidió unirse a un despacho", "Solicitó acceso a un despacho existente."),
    Paso("coupon_applied", "Aplicó un cupón", "Validó un cupón al contratar."),
    Paso("coupon_rejected", "Cupón rechazado", "Intentó un cupón inválido o vencido."),
    Paso("free_days_redeemed", "Canjeó días gratis", "Canjeó un cupón de días de uso."),
]

CONVERSION = "purchase_completed"
RESCATE_DESDE = "client_added"
PASOS_CLIENTE = {p.key for p in PASOS + EXTRA if p.client}
TODOS = {p.key for p in PASOS + PRUEBA + ACTIVACION + EXTRA}
# Orden para «hasta dónde llegó» en la lista de rescate.
ORDEN_RESCATE = ["signup_completed", "client_added", "efirma_uploaded", "download_requested", "checkout_viewed", "checkout_started"]
ETIQUETAS = {p.key: p.label for p in PASOS + PRUEBA + ACTIVACION + EXTRA}


def registrar(
    db: Session,
    paso: str,
    *,
    despacho_id: int | None = None,
    user_id: int | None = None,
    anonimo_id: str | None = None,
    ruta: str | None = None,
    utm: dict | None = None,
    dispositivo: str | None = None,
    props: dict | None = None,
    una_vez: bool = False,
) -> None:
    """Registra un paso. `una_vez`: no lo duplica para el mismo despacho
    (p. ej. «dio de alta su primer RFC»). Nunca lanza."""
    if paso not in TODOS:
        return
    try:
        if una_vez and despacho_id is not None:
            existe = (
                db.query(EventoEmbudo.id)
                .filter(EventoEmbudo.paso == paso, EventoEmbudo.despacho_id == despacho_id)
                .first()
            )
            if existe:
                return
        utm = utm or {}
        db.add(
            EventoEmbudo(
                paso=paso, despacho_id=despacho_id, user_id=user_id, anonimo_id=(anonimo_id or None),
                ruta=(ruta or None) and ruta[:255], utm_source=_c(utm.get("source")), utm_medium=_c(utm.get("medium")),
                utm_campaign=_c(utm.get("campaign")), referrer_host=_c(utm.get("referrer_host"), 255),
                dispositivo=dispositivo if dispositivo in ("mobile", "tablet", "desktop") else None,
                props={k: v for k, v in (props or {}).items() if isinstance(v, (str, int, float, bool)) or v is None},
            )
        )
        db.flush()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo registrar el paso de embudo %s", paso)


def _c(v, n: int = 120):
    return str(v)[:n] if v else None


def coser_identidad(db: Session, anonimo_id: str | None, despacho_id: int, user_id: int) -> None:
    """Une la navegación anónima previa con el despacho recién registrado."""
    if not anonimo_id:
        return
    db.execute(
        update(EventoEmbudo)
        .where(EventoEmbudo.anonimo_id == anonimo_id, EventoEmbudo.despacho_id.is_(None))
        .values(despacho_id=despacho_id, user_id=user_id)
    )


# --------------------------------------------------------------------------
# Análisis
# --------------------------------------------------------------------------

@dataclass
class StatPaso:
    key: str
    label: str
    description: str
    actores: int
    desde_anterior: float | None
    desde_inicio: float | None
    perdidos: int
    mediana_seg: int | None
    fuga: bool
    benchmark: float | None
    playbook: str | None


@dataclass
class Reporte:
    desde: datetime
    hasta: datetime
    pasos: list[StatPaso]
    prueba: list[StatPaso]
    activacion: list[StatPaso]
    conversion: float | None
    por_fuente: list[dict]
    por_dispositivo: list[dict]
    insights: list[dict]
    rescate: list[dict] = field(default_factory=list)
    tendencia: list[dict] = field(default_factory=list)


def _tasa(a: int, b: int) -> float | None:
    return a / b if b > 0 else None


def reporte(db: Session, desde: datetime, hasta: datetime, *, fuente: str | None = None, dispositivo: str | None = None) -> Reporte:
    filas = db.execute(
        text(
            "select coalesce(despacho_id::text, 'a:' || anonimo_id) as actor, paso, ocurrio_at, "
            "utm_source, referrer_host, dispositivo from embudo_eventos "
            "where coalesce(despacho_id::text, anonimo_id) is not null and ocurrio_at < :hasta order by ocurrio_at"
        ),
        {"hasta": hasta},
    ).all()

    primer_toque: dict[str, tuple[str, str]] = {}
    primera_vez: dict[tuple[str, str], datetime] = {}
    en_rango: dict[str, set[str]] = defaultdict(set)
    for actor, paso, at, src, ref, disp in filas:
        if actor not in primer_toque:
            primer_toque[actor] = ((src or (ref or "directo")).lower(), disp or "desconocido")
        primera_vez.setdefault((actor, paso), at)
        if at >= desde:
            en_rango[paso].add(actor)

    def pasa(actor: str) -> bool:
        f, d = primer_toque.get(actor, ("directo", "desconocido"))
        return (not fuente or f == fuente) and (not dispositivo or d == dispositivo)

    for k in list(en_rango):
        en_rango[k] = {a for a in en_rango[k] if pasa(a)}

    # La adquisición es acumulativa: quien llegó a un paso posterior también
    # pasó por los anteriores aunque su navegador no los reportara (entró
    # directo al registro, bloqueó el beacon, etc.). Así el embudo no sube.
    acumulado: set[str] = set()
    for p in reversed(PASOS):
        acumulado = acumulado | en_rango.get(p.key, set())
        en_rango[p.key] = set(acumulado)

    def construir(lista: list[Paso], inicio: int | None, previo_n: int | None, previo_key: str | None,
                  independientes: bool = False):
        out = []
        prev_n, prev_key = previo_n, previo_key
        for p in lista:
            n = len(en_rango.get(p.key, ()))
            desde_ant = None if prev_n is None else _tasa(n, prev_n)
            tiempos = []
            if prev_key:
                for actor in en_rango.get(p.key, ()):
                    a, b = primera_vez.get((actor, prev_key)), primera_vez.get((actor, p.key))
                    if a and b and b >= a:
                        tiempos.append((b - a).total_seconds())
            out.append(
                StatPaso(
                    key=p.key, label=p.label, description=p.description, actores=n, desde_anterior=desde_ant,
                    desde_inicio=None if inicio is None else _tasa(n, inicio),
                    perdidos=0 if prev_n is None else max(0, prev_n - n),
                    mediana_seg=int(statistics.median(tiempos)) if tiempos else None,
                    fuga=desde_ant is not None and p.benchmark is not None and (prev_n or 0) >= 5 and desde_ant < p.benchmark,
                    benchmark=p.benchmark, playbook=p.playbook,
                )
            )
            if not independientes:
                prev_n, prev_key = n, p.key
        return out

    top_n = len(en_rango.get(PASOS[0].key, ()))
    pasos = construir(PASOS, top_n, None, None)
    registro_n = len(en_rango.get("signup_completed", ()))
    prueba = construir(PRUEBA, registro_n, registro_n, "signup_completed")
    conv_n = len(en_rango.get(CONVERSION, ()))
    activacion = construir(ACTIVACION, conv_n, conv_n, CONVERSION, independientes=True)

    def agrupar(idx: int, nombre: str):
        m: dict[str, dict] = defaultdict(lambda: {"arriba": 0, "convirtieron": 0})
        for actor in en_rango.get(PASOS[0].key, ()):
            m[primer_toque.get(actor, ("directo", "desconocido"))[idx]]["arriba"] += 1
        for actor in en_rango.get(CONVERSION, ()):
            m[primer_toque.get(actor, ("directo", "desconocido"))[idx]]["convirtieron"] += 1
        return sorted(
            ({nombre: k, **v, "tasa": _tasa(v["convirtieron"], v["arriba"])} for k, v in m.items()),
            key=lambda r: (-r["arriba"], -r["convirtieron"]),
        )

    return Reporte(
        desde=desde, hasta=hasta, pasos=pasos, prueba=prueba, activacion=activacion, conversion=_tasa(conv_n, top_n),
        por_fuente=agrupar(0, "fuente"), por_dispositivo=agrupar(1, "dispositivo"),
        insights=_insights(pasos, prueba + activacion),
        rescate=_rescate(db, desde, hasta),
        tendencia=_tendencia(db, desde, hasta),
    )


def _insights(pasos: list[StatPaso], activacion: list[StatPaso]) -> list[dict]:
    if not pasos or pasos[0].actores == 0:
        return [{"severidad": "media", "titulo": "Aún no hay datos en este periodo",
                 "detalle": "Comparte la landing o lanza una campaña con cupón para empezar a medir."}]
    out = []
    candidatos = sorted([p for p in pasos[1:] + activacion if p.desde_anterior is not None], key=lambda p: -p.perdidos)
    for p in candidatos:
        if len(out) >= 4:
            break
        pct = round((p.desde_anterior or 0) * 100)
        if p.fuga:
            out.append({"severidad": "alta", "titulo": f"Fuga en «{p.label}»: solo {pct}% avanza",
                        "detalle": f"Se perdieron {p.perdidos} en este paso (esperado ≥ {round((p.benchmark or 0) * 100)}%).",
                        "playbook": p.playbook})
        elif p.perdidos > 0 and len(out) < 2:
            out.append({"severidad": "media", "titulo": f"Mayor caída: «{p.label}» ({pct}% avanza)",
                        "detalle": f"{p.perdidos} no pasaron de aquí.", "playbook": p.playbook})
    return out


def _rescate(db: Session, desde: datetime, hasta: datetime, idle_min: int = 60, limite: int = 50) -> list[dict]:
    """Despachos que llegaron al menos a «dio de alta un RFC» y no han pagado:
    candidatos a cupón personal, recordatorio o llamada."""
    orden = ORDEN_RESCATE
    min_idx = orden.index(RESCATE_DESDE)
    corte = datetime.now(timezone.utc) - timedelta(minutes=idle_min)
    filas = db.execute(
        text(
            "select e.despacho_id, max(e.ocurrio_at) as ultimo, array_agg(distinct e.paso) as pasos "
            "from embudo_eventos e where e.despacho_id is not null and e.ocurrio_at < :hasta "
            "group by e.despacho_id having not bool_or(e.paso = :conv) and max(e.ocurrio_at) >= :desde "
            "and max(e.ocurrio_at) < :corte"
        ),
        {"hasta": hasta, "desde": desde, "conv": CONVERSION, "corte": corte},
    ).all()
    out = []
    for despacho_id, ultimo, pasos_hechos in filas:
        idx = max((orden.index(p) for p in pasos_hechos if p in orden), default=-1)
        if idx >= min_idx:
            out.append({"despacho_id": despacho_id, "ultimo": ultimo, "paso_idx": idx, "paso": ETIQUETAS[orden[idx]]})
    out.sort(key=lambda r: (-r["paso_idx"], -r["ultimo"].timestamp()))
    return out[:limite]


def _tendencia(db: Session, desde: datetime, hasta: datetime) -> list[dict]:
    claves = ["visit", "signup_completed", "checkout_started", CONVERSION]
    filas = db.execute(
        text(
            "select to_char(date_trunc('day', ocurrio_at at time zone 'America/Mexico_City'), 'YYYY-MM-DD') as dia, paso, "
            "count(distinct coalesce(despacho_id::text, anonimo_id)) as n from embudo_eventos "
            "where ocurrio_at >= :desde and ocurrio_at < :hasta and paso = any(:claves) group by 1, 2 order by 1"
        ),
        {"desde": desde, "hasta": hasta, "claves": claves},
    ).all()
    return [{"dia": d, "paso": p, "n": n} for d, p, n in filas]
