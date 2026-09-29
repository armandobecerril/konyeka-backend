"""Conciliación de pagos.

Cruza cada factura PPD con los complementos de pago que la liquidan (por
UUID exacto, tal como los reporta el SAT). Cuando un complemento de pago no
encuentra su factura entre lo descargado (UUID "huérfano" — puede ser una
factura de un periodo no descargado, o una sustitución), un motor de
emparejamiento por reglas sugiere la factura pendiente más probable, con un
puntaje y una explicación en español; el contador aprueba o rechaza cada
sugerencia y solo lo aprobado cuenta para la conciliación.
"""
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.conciliacion.models import ConciliacionSugerencia, PagoRelacionado
from app.conciliacion.schemas import (
    FacturaConciliacionOut,
    GenerarSugerenciasOut,
    PagoHuerfanoOut,
    ResumenConciliacionOut,
    SugerenciaOut,
)
from app.sat_downloads.models import CfdiDocument
from app.sat_downloads.service import _texto, _to_datetime, _to_decimal

logger = logging.getLogger(__name__)

TOLERANCIA_SALDO = Decimal("1.00")
UMBRAL_SUGERENCIA = 50


# --------------------------------------------------------------------------
# Extracción de los complementos de pago (al descargar, y backfill histórico)
# --------------------------------------------------------------------------

def _extraer_pagos_de_cfdi(cfdi) -> list[dict]:
    complemento = cfdi.get("Complemento") or {}
    pagos = complemento.get("Pagos")
    if not pagos:
        return []

    pago_list = pagos.get("Pago")
    if pago_list is None:
        return []
    if not isinstance(pago_list, list):
        pago_list = [pago_list]

    filas: list[dict] = []
    for pago in pago_list:
        fecha_pago = pago.get("FechaPago")
        forma_pago = _texto(pago.get("FormaDePagoP"))

        docs = pago.get("DoctoRelacionado")
        if docs is None:
            continue
        if not isinstance(docs, list):
            docs = [docs]

        for doc in docs:
            id_documento = doc.get("IdDocumento")
            if not id_documento:
                continue
            filas.append(
                {
                    "uuid_factura_relacionada": str(id_documento).strip().upper(),
                    "num_parcialidad": int(doc.get("NumParcialidad") or 1),
                    "imp_saldo_ant": _to_decimal(doc.get("ImpSaldoAnt")),
                    "imp_pagado": _to_decimal(doc.get("ImpPagado")) or Decimal("0"),
                    "imp_saldo_insoluto": _to_decimal(doc.get("ImpSaldoInsoluto")),
                    "moneda_dr": _texto(doc.get("MonedaDR")),
                    "fecha_pago": _to_datetime(fecha_pago) if fecha_pago else None,
                    "forma_pago": forma_pago,
                }
            )
    return filas


def guardar_pagos_relacionados(db: Session, cfdi_document: CfdiDocument, cfdi) -> int:
    """Extrae y guarda los DoctoRelacionado de un CFDI de pago (P) ya indexado.
    Es idempotente y seguro ante llamadas concurrentes: usa INSERT ... ON
    CONFLICT DO NOTHING (en vez de revisar-y-luego-insertar) porque el
    resumen, las facturas y los huérfanos se piden en paralelo desde el
    frontend y más de una petición puede disparar el backfill al mismo
    tiempo sobre el mismo CFDI."""
    filas = _extraer_pagos_de_cfdi(cfdi)
    if not filas:
        return 0

    valores = [
        {
            "rfc_client_id": cfdi_document.rfc_client_id,
            "cfdi_pago_id": cfdi_document.id,
            "uuid_pago": cfdi_document.uuid,
            **fila,
        }
        for fila in filas
    ]

    stmt = pg_insert(PagoRelacionado).values(valores)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["cfdi_pago_id", "uuid_factura_relacionada", "num_parcialidad"]
    )
    resultado = db.execute(stmt)
    db.commit()
    return resultado.rowcount or 0


def backfill_pagos_relacionados(db: Session, rfc_client_id: int) -> int:
    """Rellena PagoRelacionado para los CFDIs de pago ya descargados antes de
    que existiera esta tabla. Vuelve a leer el XML guardado en la bóveda.
    Seguro de llamar repetidas veces: solo procesa lo que falte."""
    from satcfdi.cfdi import CFDI

    from app.core.storage import get_xml_storage

    pendientes = (
        db.query(CfdiDocument)
        .filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.tipo_comprobante == "P",
            ~db.query(PagoRelacionado.id)
            .filter(PagoRelacionado.cfdi_pago_id == CfdiDocument.id)
            .exists(),
        )
        .all()
    )
    if not pendientes:
        return 0

    storage = get_xml_storage()
    total = 0
    for doc in pendientes:
        try:
            xml_bytes = storage.read(doc.storage_path)
            cfdi = CFDI.from_string(xml_bytes)
        except Exception:  # noqa: BLE001
            logger.warning("No se pudo releer el XML para backfill de pagos (cfdi_document_id=%s)", doc.id)
            continue
        total += guardar_pagos_relacionados(db, doc, cfdi)
    return total


# --------------------------------------------------------------------------
# Estado de conciliación de las facturas PPD
# --------------------------------------------------------------------------

def _categoria(saldo: Decimal, total: Decimal) -> str:
    if saldo <= TOLERANCIA_SALDO:
        return "conciliada"
    if saldo < total:
        return "parcial"
    return "pendiente"


def _facturas_ppd_con_estado(db: Session, rfc_client_id: int) -> list[dict]:
    facturas = (
        db.query(CfdiDocument)
        .filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.tipo_comprobante == "I",
            CfdiDocument.metodo_pago == "PPD",
        )
        .order_by(CfdiDocument.fecha_emision.desc())
        .all()
    )
    if not facturas:
        return []

    pagos_exactos = dict(
        db.query(func.upper(PagoRelacionado.uuid_factura_relacionada), func.sum(PagoRelacionado.imp_pagado))
        .filter(PagoRelacionado.rfc_client_id == rfc_client_id)
        .group_by(func.upper(PagoRelacionado.uuid_factura_relacionada))
        .all()
    )

    pagos_aprobados = dict(
        db.query(ConciliacionSugerencia.factura_cfdi_id, func.sum(PagoRelacionado.imp_pagado))
        .join(PagoRelacionado, PagoRelacionado.id == ConciliacionSugerencia.pago_relacionado_id)
        .filter(
            ConciliacionSugerencia.rfc_client_id == rfc_client_id,
            ConciliacionSugerencia.estado == "aprobada",
        )
        .group_by(ConciliacionSugerencia.factura_cfdi_id)
        .all()
    )

    resultado = []
    for factura in facturas:
        monto_pagado = (pagos_exactos.get(factura.uuid.upper()) or Decimal("0")) + (
            pagos_aprobados.get(factura.id) or Decimal("0")
        )
        saldo = factura.total - monto_pagado
        if saldo < 0:
            saldo = Decimal("0")
        resultado.append(
            {
                "factura": factura,
                "monto_pagado": monto_pagado,
                "saldo": saldo,
                "estado": _categoria(saldo, factura.total),
            }
        )
    return resultado


def _pagos_huerfanos_query(db: Session, rfc_client_id: int):
    facturas_uuids = db.query(func.upper(CfdiDocument.uuid)).filter(CfdiDocument.rfc_client_id == rfc_client_id)
    aprobadas_ids = db.query(ConciliacionSugerencia.pago_relacionado_id).filter(
        ConciliacionSugerencia.rfc_client_id == rfc_client_id,
        ConciliacionSugerencia.estado == "aprobada",
    )
    return db.query(PagoRelacionado).filter(
        PagoRelacionado.rfc_client_id == rfc_client_id,
        ~PagoRelacionado.uuid_factura_relacionada.in_(facturas_uuids),
        ~PagoRelacionado.id.in_(aprobadas_ids),
    )


def resumen_conciliacion(db: Session, rfc_client_id: int) -> ResumenConciliacionOut:
    backfill_pagos_relacionados(db, rfc_client_id)

    filas = _facturas_ppd_con_estado(db, rfc_client_id)
    conteo = {"conciliada": 0, "parcial": 0, "pendiente": 0}
    monto = {"conciliada": Decimal("0"), "parcial": Decimal("0"), "pendiente": Decimal("0")}
    monto_total = Decimal("0")
    for fila in filas:
        conteo[fila["estado"]] += 1
        monto[fila["estado"]] += fila["factura"].total
        monto_total += fila["factura"].total

    huerfanos = _pagos_huerfanos_query(db, rfc_client_id).all()
    monto_huerfanos = sum((h.imp_pagado for h in huerfanos), Decimal("0"))

    pue = (
        db.query(func.count(CfdiDocument.id), func.coalesce(func.sum(CfdiDocument.total), 0))
        .filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.tipo_comprobante == "I",
            CfdiDocument.metodo_pago == "PUE",
        )
        .first()
    )

    sugerencias_pendientes = (
        db.query(func.count(ConciliacionSugerencia.id))
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id, ConciliacionSugerencia.estado == "pendiente")
        .scalar()
        or 0
    )

    return ResumenConciliacionOut(
        total_facturas_ppd=len(filas),
        monto_total_ppd=monto_total,
        conciliadas=conteo["conciliada"],
        monto_conciliado=monto["conciliada"],
        parciales=conteo["parcial"],
        monto_parcial=monto["parcial"],
        pendientes=conteo["pendiente"],
        monto_pendiente=monto["pendiente"],
        pagos_huerfanos=len(huerfanos),
        monto_pagos_huerfanos=monto_huerfanos,
        facturas_pue=pue[0] or 0,
        monto_pue=pue[1] or Decimal("0"),
        sugerencias_pendientes=sugerencias_pendientes,
    )


def listar_facturas(db: Session, rfc_client_id: int, estado: str | None = None) -> list[FacturaConciliacionOut]:
    backfill_pagos_relacionados(db, rfc_client_id)
    filas = _facturas_ppd_con_estado(db, rfc_client_id)
    if estado:
        filas = [f for f in filas if f["estado"] == estado]

    ahora = datetime.utcnow()
    resultado = []
    for fila in filas:
        factura = fila["factura"]
        fecha = factura.fecha_emision.replace(tzinfo=None) if factura.fecha_emision.tzinfo else factura.fecha_emision
        dias = (ahora - fecha).days
        resultado.append(
            FacturaConciliacionOut(
                id=factura.id,
                uuid=factura.uuid,
                tipo=factura.tipo,
                emisor_rfc=factura.emisor_rfc,
                emisor_nombre=factura.emisor_nombre,
                receptor_rfc=factura.receptor_rfc,
                receptor_nombre=factura.receptor_nombre,
                fecha_emision=factura.fecha_emision,
                total=factura.total,
                monto_pagado=fila["monto_pagado"],
                saldo=fila["saldo"],
                estado=fila["estado"],
                dias_desde_emision=dias,
            )
        )
    return resultado


def listar_pagos_huerfanos(db: Session, rfc_client_id: int) -> list[PagoHuerfanoOut]:
    backfill_pagos_relacionados(db, rfc_client_id)
    pagos = (
        _pagos_huerfanos_query(db, rfc_client_id).order_by(PagoRelacionado.fecha_pago.desc().nullslast()).all()
    )

    pendientes_ids = {
        row[0]
        for row in db.query(ConciliacionSugerencia.pago_relacionado_id)
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id, ConciliacionSugerencia.estado == "pendiente")
        .all()
    }

    resultado = []
    for pago in pagos:
        cfdi_pago = db.query(CfdiDocument).filter(CfdiDocument.id == pago.cfdi_pago_id).first()
        resultado.append(
            PagoHuerfanoOut(
                id=pago.id,
                cfdi_pago_id=pago.cfdi_pago_id,
                uuid_pago=pago.uuid_pago,
                uuid_factura_relacionada=pago.uuid_factura_relacionada,
                num_parcialidad=pago.num_parcialidad,
                imp_pagado=pago.imp_pagado,
                fecha_pago=pago.fecha_pago,
                forma_pago=pago.forma_pago,
                emisor_rfc=cfdi_pago.emisor_rfc if cfdi_pago else "",
                emisor_nombre=cfdi_pago.emisor_nombre if cfdi_pago else None,
                receptor_rfc=cfdi_pago.receptor_rfc if cfdi_pago else "",
                receptor_nombre=cfdi_pago.receptor_nombre if cfdi_pago else None,
                tipo=cfdi_pago.tipo if cfdi_pago else "",
                tiene_sugerencia=pago.id in pendientes_ids,
            )
        )
    return resultado


# --------------------------------------------------------------------------
# Motor de sugerencias ("Conciliar con IA"): reglas de emparejamiento sobre
# los pagos huérfanos, con puntaje y explicación en español.
# --------------------------------------------------------------------------

def _sin_tz(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def _score_candidato(
    pago: PagoRelacionado, cfdi_pago: CfdiDocument, factura: CfdiDocument, saldo_factura: Decimal
) -> tuple[int, str]:
    if cfdi_pago.tipo != factura.tipo:
        return 0, ""

    contraparte_pago = cfdi_pago.emisor_rfc if cfdi_pago.tipo == "recibido" else cfdi_pago.receptor_rfc
    contraparte_factura = factura.emisor_rfc if factura.tipo == "recibido" else factura.receptor_rfc
    if (contraparte_pago or "").strip().upper() != (contraparte_factura or "").strip().upper():
        return 0, ""

    score = 40
    razones = ["es el mismo RFC de la contraparte"]

    monto = pago.imp_pagado
    if saldo_factura > 0:
        diferencia = abs(saldo_factura - monto)
        if diferencia <= max(Decimal("1"), saldo_factura * Decimal("0.02")):
            score += 35
            razones.append("el monto pagado casi es idéntico al saldo pendiente")
        elif diferencia <= max(Decimal("5"), saldo_factura * Decimal("0.05")):
            score += 22
            razones.append("el monto pagado es muy cercano al saldo pendiente")
        elif diferencia <= max(Decimal("20"), saldo_factura * Decimal("0.10")):
            score += 10
            razones.append("el monto pagado es parecido al saldo pendiente")

    fecha_pago = pago.fecha_pago or cfdi_pago.fecha_emision
    if fecha_pago and factura.fecha_emision:
        dias = (_sin_tz(fecha_pago) - _sin_tz(factura.fecha_emision)).days
        if 0 <= dias <= 30:
            score += 25
            razones.append("el pago llegó dentro del mismo mes que la factura")
        elif 0 <= dias <= 60:
            score += 18
            razones.append("el pago llegó un par de meses después de la factura")
        elif 0 <= dias <= 120:
            score += 10
            razones.append("el pago llegó varios meses después de la factura")
        elif 0 <= dias <= 365:
            score += 3
            razones.append("el pago llegó dentro del mismo año que la factura")

    motivo = "Coincide porque " + ", ".join(razones) + "."
    return min(score, 99), motivo


def generar_sugerencias_ia(db: Session, rfc_client_id: int) -> GenerarSugerenciasOut:
    backfill_pagos_relacionados(db, rfc_client_id)

    ya_evaluados = {
        row[0]
        for row in db.query(ConciliacionSugerencia.pago_relacionado_id)
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id)
        .all()
    }

    total_pendientes_previo = (
        db.query(func.count(ConciliacionSugerencia.id))
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id, ConciliacionSugerencia.estado == "pendiente")
        .scalar()
        or 0
    )

    huerfanos = [p for p in _pagos_huerfanos_query(db, rfc_client_id).all() if p.id not in ya_evaluados]
    if not huerfanos:
        return GenerarSugerenciasOut(nuevas=0, total_pendientes=total_pendientes_previo)

    candidatos = [fila for fila in _facturas_ppd_con_estado(db, rfc_client_id) if fila["estado"] != "conciliada"]

    cfdi_pago_cache: dict[int, CfdiDocument | None] = {}
    nuevas = 0
    for pago in huerfanos:
        cfdi_pago = cfdi_pago_cache.get(pago.cfdi_pago_id)
        if cfdi_pago is None and pago.cfdi_pago_id not in cfdi_pago_cache:
            cfdi_pago = db.query(CfdiDocument).filter(CfdiDocument.id == pago.cfdi_pago_id).first()
            cfdi_pago_cache[pago.cfdi_pago_id] = cfdi_pago
        if cfdi_pago is None:
            continue

        mejor_score = 0
        mejor_factura = None
        mejor_motivo = ""
        for fila in candidatos:
            score, motivo = _score_candidato(pago, cfdi_pago, fila["factura"], fila["saldo"])
            if score > mejor_score:
                mejor_score, mejor_factura, mejor_motivo = score, fila["factura"], motivo

        if mejor_factura is not None and mejor_score >= UMBRAL_SUGERENCIA:
            db.add(
                ConciliacionSugerencia(
                    rfc_client_id=rfc_client_id,
                    pago_relacionado_id=pago.id,
                    factura_cfdi_id=mejor_factura.id,
                    score=mejor_score,
                    motivo=mejor_motivo,
                    estado="pendiente",
                )
            )
            nuevas += 1

    if nuevas:
        db.commit()

    return GenerarSugerenciasOut(nuevas=nuevas, total_pendientes=total_pendientes_previo + nuevas)


def listar_sugerencias(db: Session, rfc_client_id: int, estado: str = "pendiente") -> list[SugerenciaOut]:
    sugerencias = (
        db.query(ConciliacionSugerencia)
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id, ConciliacionSugerencia.estado == estado)
        .order_by(ConciliacionSugerencia.score.desc())
        .all()
    )

    resultado = []
    for s in sugerencias:
        pago = db.query(PagoRelacionado).filter(PagoRelacionado.id == s.pago_relacionado_id).first()
        cfdi_pago = db.query(CfdiDocument).filter(CfdiDocument.id == pago.cfdi_pago_id).first() if pago else None
        factura = db.query(CfdiDocument).filter(CfdiDocument.id == s.factura_cfdi_id).first()
        if pago is None or cfdi_pago is None or factura is None:
            continue
        resultado.append(
            SugerenciaOut(
                id=s.id,
                score=s.score,
                motivo=s.motivo,
                estado=s.estado,
                created_at=s.created_at,
                pago_relacionado_id=pago.id,
                uuid_pago=pago.uuid_pago,
                imp_pagado=pago.imp_pagado,
                fecha_pago=pago.fecha_pago,
                pago_emisor_nombre=cfdi_pago.emisor_nombre,
                pago_emisor_rfc=cfdi_pago.emisor_rfc,
                pago_receptor_nombre=cfdi_pago.receptor_nombre,
                pago_receptor_rfc=cfdi_pago.receptor_rfc,
                factura_cfdi_id=factura.id,
                factura_uuid=factura.uuid,
                factura_total=factura.total,
                factura_fecha_emision=factura.fecha_emision,
                factura_emisor_nombre=factura.emisor_nombre,
                factura_receptor_nombre=factura.receptor_nombre,
            )
        )
    return resultado


def _get_sugerencia_or_404(db: Session, rfc_client_id: int, sugerencia_id: int) -> ConciliacionSugerencia:
    sugerencia = (
        db.query(ConciliacionSugerencia)
        .filter(ConciliacionSugerencia.id == sugerencia_id, ConciliacionSugerencia.rfc_client_id == rfc_client_id)
        .first()
    )
    if sugerencia is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sugerencia no encontrada")
    return sugerencia


def aprobar_sugerencia(
    db: Session, rfc_client_id: int, sugerencia_id: int, user_id: int | None
) -> ConciliacionSugerencia:
    sugerencia = _get_sugerencia_or_404(db, rfc_client_id, sugerencia_id)
    sugerencia.estado = "aprobada"
    sugerencia.resuelto_por_id = user_id
    sugerencia.resuelto_at = datetime.utcnow()
    db.commit()
    db.refresh(sugerencia)
    return sugerencia


def rechazar_sugerencia(
    db: Session, rfc_client_id: int, sugerencia_id: int, user_id: int | None
) -> ConciliacionSugerencia:
    sugerencia = _get_sugerencia_or_404(db, rfc_client_id, sugerencia_id)
    sugerencia.estado = "rechazada"
    sugerencia.resuelto_por_id = user_id
    sugerencia.resuelto_at = datetime.utcnow()
    db.commit()
    db.refresh(sugerencia)
    return sugerencia
