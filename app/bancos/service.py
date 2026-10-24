"""Estados de cuenta bancarios: cuentas, carga mensual, conciliación contra
CFDIs, revisión del contador y resumen para deducibilidad."""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.bancos import matching
from app.bancos.models import CuentaBancaria, EstadoCuenta, MovimientoBancario, VinculoBancario
from app.bancos.parser import ErrorLectura, leer_estado_cuenta
from app.conciliacion.models import ConciliacionSugerencia, PagoRelacionado
from app.core.storage import get_xml_storage
from app.rfc_clients.models import RfcClient
from app.sat_downloads.models import CfdiDocument
from app.sat_downloads.service import filtro_meses, parsear_meses

logger = logging.getLogger(__name__)

CLASIFICACIONES = {
    "efectivo": "Pago/retiro en efectivo",
    "comision": "Comisión o cargo bancario",
    "traspaso": "Traspaso entre cuentas propias",
    "impuestos": "Pago de impuestos / SAT / IMSS",
    "nomina": "Nómina / dispersión",
    "prestamo": "Préstamo o crédito",
    "sin_cfdi": "Sin CFDI (pedir factura)",
    "otro": "Otro",
}
LIMITE_EFECTIVO_DEDUCIBLE = Decimal("2000")  # art. 27 fr. III LISR
CERO = Decimal("0")


# --------------------------------------------------------------------------
# Cuentas
# --------------------------------------------------------------------------

def listar_cuentas(db: Session, rfc_client_id: int) -> list[CuentaBancaria]:
    return (
        db.query(CuentaBancaria)
        .filter(CuentaBancaria.rfc_client_id == rfc_client_id, CuentaBancaria.activa.is_(True))
        .order_by(CuentaBancaria.banco, CuentaBancaria.id)
        .all()
    )


def crear_cuenta(db: Session, rfc_client_id: int, *, banco: str, alias: str | None, numero: str | None,
                 clabe: str | None, moneda: str = "MXN") -> CuentaBancaria:
    cuenta = CuentaBancaria(
        rfc_client_id=rfc_client_id,
        banco=banco.strip(),
        alias=(alias or "").strip() or None,
        numero=(numero or "").strip()[-30:] or None,
        clabe=(clabe or "").strip() or None,
        moneda=(moneda or "MXN").upper()[:3],
    )
    db.add(cuenta)
    db.commit()
    db.refresh(cuenta)
    return cuenta


def _cuenta_or_404(db: Session, rfc_client_id: int, cuenta_id: int) -> CuentaBancaria:
    cuenta = (
        db.query(CuentaBancaria)
        .filter(CuentaBancaria.id == cuenta_id, CuentaBancaria.rfc_client_id == rfc_client_id)
        .first()
    )
    if cuenta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta bancaria no encontrada")
    return cuenta


def desactivar_cuenta(db: Session, rfc_client_id: int, cuenta_id: int) -> None:
    cuenta = _cuenta_or_404(db, rfc_client_id, cuenta_id)
    cuenta.activa = False
    db.commit()


# --------------------------------------------------------------------------
# Estados de cuenta (carga)
# --------------------------------------------------------------------------

def cargar_estado_cuenta(
    db: Session,
    rfc_client_id: int,
    *,
    cuenta_id: int,
    periodo: str,
    nombre_archivo: str,
    contenido: bytes,
    user_id: int | None,
) -> EstadoCuenta:
    if not parsear_meses(periodo):
        raise HTTPException(status_code=422, detail="El periodo debe tener el formato AAAA-MM.")
    cuenta = _cuenta_or_404(db, rfc_client_id, cuenta_id)
    if not contenido:
        raise HTTPException(status_code=422, detail="El archivo está vacío.")
    if len(contenido) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El archivo pesa más de 15 MB.")

    try:
        lectura = leer_estado_cuenta(contenido, nombre_archivo, periodo)
    except ErrorLectura as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Recargar el mismo mes de la misma cuenta reemplaza lo anterior.
    previo = (
        db.query(EstadoCuenta)
        .filter(EstadoCuenta.cuenta_id == cuenta.id, EstadoCuenta.periodo == periodo)
        .first()
    )
    if previo is not None:
        _borrar_estado(db, previo)

    rfc = db.query(RfcClient.rfc).filter(RfcClient.id == rfc_client_id).scalar() or str(rfc_client_id)
    extension = (nombre_archivo.rsplit(".", 1)[-1] if "." in nombre_archivo else lectura.formato).lower()[:5]
    storage_path = None
    try:
        storage_path = get_xml_storage().save_debug(
            rfc,
            "estados_cuenta",
            f"{cuenta.id}_{periodo}_{int(datetime.utcnow().timestamp())}",
            contenido,
            extension=extension,
            content_type="application/octet-stream",
        )
    except Exception:  # noqa: BLE001 - guardar el original es un plus, no debe tumbar la carga
        logger.exception("No se pudo guardar el archivo original del estado de cuenta")

    movimientos = lectura.movimientos
    estado = EstadoCuenta(
        rfc_client_id=rfc_client_id,
        cuenta_id=cuenta.id,
        periodo=periodo,
        archivo_nombre=nombre_archivo[:255] or "estado_de_cuenta",
        formato=lectura.formato,
        storage_path=storage_path,
        saldo_inicial=lectura.saldo_inicial,
        saldo_final=lectura.saldo_final,
        total_cargos=sum((m.cargo or CERO for m in movimientos), CERO),
        total_abonos=sum((m.abono or CERO for m in movimientos), CERO),
        num_movimientos=len(movimientos),
        banco_detectado=lectura.banco_detectado,
        advertencias=lectura.advertencias or None,
        created_by_id=user_id,
    )
    db.add(estado)
    db.flush()
    for m in movimientos:
        db.add(
            MovimientoBancario(
                rfc_client_id=rfc_client_id,
                cuenta_id=cuenta.id,
                estado_cuenta_id=estado.id,
                renglon=m.renglon,
                fecha=m.fecha,
                descripcion=m.descripcion[:2000],
                referencia=m.referencia,
                cargo=m.cargo,
                abono=m.abono,
                saldo=m.saldo,
                tipo=m.tipo,
                monto=m.monto,
                estado="pendiente",
            )
        )
    db.commit()
    db.refresh(estado)
    return estado


def _borrar_estado(db: Session, estado: EstadoCuenta) -> None:
    ids_mov = db.query(MovimientoBancario.id).filter(MovimientoBancario.estado_cuenta_id == estado.id)
    db.query(VinculoBancario).filter(VinculoBancario.movimiento_id.in_(ids_mov)).delete(synchronize_session=False)
    db.query(MovimientoBancario).filter(MovimientoBancario.estado_cuenta_id == estado.id).delete(
        synchronize_session=False
    )
    db.delete(estado)
    db.flush()


def eliminar_estado_cuenta(db: Session, rfc_client_id: int, estado_id: int) -> None:
    estado = (
        db.query(EstadoCuenta)
        .filter(EstadoCuenta.id == estado_id, EstadoCuenta.rfc_client_id == rfc_client_id)
        .first()
    )
    if estado is None:
        raise HTTPException(status_code=404, detail="Estado de cuenta no encontrado")
    _borrar_estado(db, estado)
    db.commit()


def listar_estados(db: Session, rfc_client_id: int, meses: str | None) -> list[dict]:
    query = (
        db.query(EstadoCuenta, CuentaBancaria)
        .join(CuentaBancaria, CuentaBancaria.id == EstadoCuenta.cuenta_id)
        .filter(EstadoCuenta.rfc_client_id == rfc_client_id)
    )
    periodos = _periodos(meses)
    if periodos:
        query = query.filter(EstadoCuenta.periodo.in_(periodos))
    salida = []
    for estado, cuenta in query.order_by(EstadoCuenta.periodo.desc(), CuentaBancaria.banco).all():
        conteos = dict(
            db.query(MovimientoBancario.estado, func.count(MovimientoBancario.id))
            .filter(MovimientoBancario.estado_cuenta_id == estado.id)
            .group_by(MovimientoBancario.estado)
            .all()
        )
        salida.append(
            {
                "id": estado.id,
                "cuenta_id": cuenta.id,
                "banco": cuenta.banco,
                "cuenta_alias": cuenta.alias,
                "cuenta_numero": cuenta.numero,
                "periodo": estado.periodo,
                "archivo_nombre": estado.archivo_nombre,
                "formato": estado.formato,
                "saldo_inicial": estado.saldo_inicial,
                "saldo_final": estado.saldo_final,
                "total_cargos": estado.total_cargos,
                "total_abonos": estado.total_abonos,
                "num_movimientos": estado.num_movimientos,
                "banco_detectado": estado.banco_detectado,
                "advertencias": estado.advertencias or [],
                "conciliados": conteos.get("conciliado", 0),
                "sugeridos": conteos.get("sugerido", 0),
                "clasificados": conteos.get("clasificado", 0),
                "pendientes": conteos.get("pendiente", 0),
                "created_at": estado.created_at,
            }
        )
    return salida


def _periodos(meses: str | None) -> list[str]:
    return [f"{a:04d}-{m:02d}" for a, m in parsear_meses(meses)]


# --------------------------------------------------------------------------
# Candidatos (CFDIs que pueden respaldar un movimiento)
# --------------------------------------------------------------------------

def _contraparte(doc: CfdiDocument) -> tuple[str | None, str | None]:
    if doc.tipo == "recibido":
        return doc.emisor_rfc, doc.emisor_nombre
    return doc.receptor_rfc, doc.receptor_nombre


def _a_fecha(valor) -> date:
    return valor.date() if isinstance(valor, datetime) else valor


def construir_candidatos(
    db: Session, rfc_client_id: int, fecha_min: date, fecha_max: date, *, excluir_cfdi: set[int] | None = None
) -> list[matching.Candidato]:
    excluir_cfdi = excluir_cfdi or set()
    desde = datetime.combine(fecha_min - timedelta(days=120), datetime.min.time())
    hasta = datetime.combine(fecha_max + timedelta(days=60), datetime.min.time())
    docs = (
        db.query(CfdiDocument)
        .filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.fecha_emision >= desde,
            CfdiDocument.fecha_emision < hasta,
            CfdiDocument.tipo_comprobante.in_(("I", "P", "N")),
            or_(CfdiDocument.estado_sat.is_(None), CfdiDocument.estado_sat != "cancelado"),
        )
        .all()
    )
    if not docs:
        return []

    # Fecha y monto reales de cada REP (de su complemento).
    ids_p = [d.id for d in docs if d.tipo_comprobante == "P"]
    info_rep = {}
    if ids_p:
        info_rep = {
            cfdi_id: (fecha, monto)
            for cfdi_id, fecha, monto in db.query(
                PagoRelacionado.cfdi_pago_id, func.min(PagoRelacionado.fecha_pago), func.sum(PagoRelacionado.imp_pagado)
            )
            .filter(PagoRelacionado.cfdi_pago_id.in_(ids_p))
            .group_by(PagoRelacionado.cfdi_pago_id)
            .all()
        }
    # Facturas PPD que ya tienen algún REP (por UUID o sugerencia aprobada).
    uuids_con_rep = {
        u for (u,) in db.query(func.upper(PagoRelacionado.uuid_factura_relacionada))
        .filter(PagoRelacionado.rfc_client_id == rfc_client_id)
        .distinct()
        .all()
    }
    ids_con_rep = {
        i for (i,) in db.query(ConciliacionSugerencia.factura_cfdi_id)
        .filter(ConciliacionSugerencia.rfc_client_id == rfc_client_id, ConciliacionSugerencia.estado == "aprobada")
        .all()
    }

    candidatos: list[matching.Candidato] = []
    for d in docs:
        if d.id in excluir_cfdi:
            continue
        rfc, nombre = _contraparte(d)
        sentido = "cargo" if d.tipo == "recibido" else "abono"
        fecha = _a_fecha(d.fecha_emision)
        etiqueta = " ".join(filter(None, [d.serie, d.folio])) or d.uuid[:8]
        if d.tipo_comprobante == "I":
            if d.metodo_pago == "PPD":
                if d.uuid.upper() in uuids_con_rep or d.id in ids_con_rep:
                    continue  # el pago se cruza contra su REP, no contra la factura
                tipo_vinculo = "ppd_sin_rep"
            else:
                tipo_vinculo = "pue"
            candidatos.append(
                matching.Candidato(d.id, tipo_vinculo, sentido, fecha, d.total, rfc, nombre, etiqueta)
            )
        elif d.tipo_comprobante == "P":
            fecha_pago, monto = info_rep.get(d.id, (None, None))
            monto = d.monto_pago if d.monto_pago else monto
            if not monto:
                continue
            candidatos.append(
                matching.Candidato(
                    d.id, "rep", sentido, _a_fecha(fecha_pago) if fecha_pago else fecha, Decimal(monto), rfc, nombre, etiqueta
                )
            )
        elif d.tipo_comprobante == "N" and d.tipo == "emitido":
            # Nómina timbrada por el cliente: el neto sale de su cuenta.
            candidatos.append(matching.Candidato(d.id, "nomina", "cargo", fecha, d.total, rfc, nombre, etiqueta))
    return candidatos


def _cfdis_ya_conciliados(db: Session, rfc_client_id: int, excepto_movimientos: set[int] | None = None) -> set[int]:
    query = db.query(VinculoBancario.cfdi_id).filter(
        VinculoBancario.rfc_client_id == rfc_client_id,
        VinculoBancario.estado == "aprobada",
        VinculoBancario.cfdi_id.isnot(None),
    )
    if excepto_movimientos:
        query = query.filter(VinculoBancario.movimiento_id.notin_(excepto_movimientos))
    return {i for (i,) in query.all()}


# --------------------------------------------------------------------------
# Conciliación automática
# --------------------------------------------------------------------------

def _query_movimientos(db: Session, rfc_client_id: int, meses: str | None, cuenta_id: int | None = None):
    query = (
        db.query(MovimientoBancario)
        .join(EstadoCuenta, EstadoCuenta.id == MovimientoBancario.estado_cuenta_id)
        .filter(MovimientoBancario.rfc_client_id == rfc_client_id)
    )
    periodos = _periodos(meses)
    if periodos:
        query = query.filter(EstadoCuenta.periodo.in_(periodos))
    if cuenta_id:
        query = query.filter(MovimientoBancario.cuenta_id == cuenta_id)
    return query


def conciliar(db: Session, rfc_client_id: int, meses: str | None, user_id: int | None = None) -> dict:
    from app.conciliacion.service import backfill_pagos_relacionados
    from app.sat_downloads.service import backfill_pagos_moneda

    backfill_pagos_relacionados(db, rfc_client_id)
    backfill_pagos_moneda(db, rfc_client_id)

    movs = (
        _query_movimientos(db, rfc_client_id, meses)
        .filter(MovimientoBancario.estado.in_(("pendiente", "sugerido")))
        .all()
    )
    if not movs:
        return {"evaluados": 0, "conciliados_auto": 0, "sugeridos": 0, "sin_match": 0}
    ids = {m.id for m in movs}

    # Las sugerencias automáticas previas se recalculan; lo aprobado o
    # rechazado por el contador se respeta.
    db.query(VinculoBancario).filter(
        VinculoBancario.movimiento_id.in_(ids),
        VinculoBancario.estado == "sugerida",
        VinculoBancario.origen == "auto",
    ).delete(synchronize_session=False)
    rechazadas = {
        (m, c)
        for m, c in db.query(VinculoBancario.movimiento_id, VinculoBancario.cfdi_id)
        .filter(VinculoBancario.movimiento_id.in_(ids), VinculoBancario.estado == "rechazada")
        .all()
    }

    fecha_min = min(m.fecha for m in movs)
    fecha_max = max(m.fecha for m in movs)
    candidatos = construir_candidatos(
        db, rfc_client_id, fecha_min, fecha_max, excluir_cfdi=_cfdis_ya_conciliados(db, rfc_client_id)
    )
    parejas = matching.asignar(
        [matching.Movimiento(m.id, m.fecha, m.tipo, m.monto, f"{m.descripcion} {m.referencia or ''}") for m in movs],
        candidatos,
        excluir=rechazadas,
    )
    por_id = {m.id: m for m in movs}
    auto = sug = 0
    for p in parejas:
        db.add(
            VinculoBancario(
                rfc_client_id=rfc_client_id,
                movimiento_id=p.movimiento_id,
                cfdi_id=p.cfdi_id,
                tipo_vinculo=p.tipo_vinculo,
                monto=p.monto,
                score=p.score,
                motivo=p.motivo,
                origen="auto",
                estado="aprobada" if p.automatico else "sugerida",
                resuelto_at=datetime.utcnow() if p.automatico else None,
            )
        )
        por_id[p.movimiento_id].estado = "conciliado" if p.automatico else "sugerido"
        if p.automatico:
            auto += 1
        else:
            sug += 1
    con_pareja = {p.movimiento_id for p in parejas}
    for m in movs:
        if m.id not in con_pareja and m.estado == "sugerido":
            m.estado = "pendiente"
    db.commit()
    return {"evaluados": len(movs), "conciliados_auto": auto, "sugeridos": sug, "sin_match": len(movs) - len(parejas)}


# --------------------------------------------------------------------------
# Listado y revisión
# --------------------------------------------------------------------------

def _cfdi_resumen(doc: CfdiDocument | None) -> dict | None:
    if doc is None:
        return None
    rfc, nombre = _contraparte(doc)
    return {
        "id": doc.id,
        "uuid": doc.uuid,
        "tipo": doc.tipo,
        "tipo_comprobante": doc.tipo_comprobante,
        "metodo_pago": doc.metodo_pago,
        "forma_pago": doc.forma_pago,
        "serie": doc.serie,
        "folio": doc.folio,
        "fecha_emision": doc.fecha_emision,
        "total": doc.monto_pago if doc.tipo_comprobante == "P" and doc.monto_pago else doc.total,
        "contraparte_rfc": rfc,
        "contraparte_nombre": nombre,
    }


def listar_movimientos(
    db: Session, rfc_client_id: int, *, meses: str | None, cuenta_id: int | None = None, estado: str | None = None
) -> list[dict]:
    query = _query_movimientos(db, rfc_client_id, meses, cuenta_id)
    if estado:
        query = query.filter(MovimientoBancario.estado == estado)
    movs = query.order_by(MovimientoBancario.fecha.asc(), MovimientoBancario.renglon.asc()).all()
    if not movs:
        return []
    ids = [m.id for m in movs]
    vinculos = (
        db.query(VinculoBancario)
        .filter(VinculoBancario.movimiento_id.in_(ids), VinculoBancario.estado != "rechazada")
        .all()
    )
    ids_cfdi = {v.cfdi_id for v in vinculos if v.cfdi_id}
    cfdis = {d.id: d for d in db.query(CfdiDocument).filter(CfdiDocument.id.in_(ids_cfdi)).all()} if ids_cfdi else {}
    cuentas = {c.id: c for c in db.query(CuentaBancaria).filter(CuentaBancaria.rfc_client_id == rfc_client_id).all()}
    por_mov: dict[int, list[dict]] = {}
    for v in vinculos:
        por_mov.setdefault(v.movimiento_id, []).append(
            {
                "id": v.id,
                "tipo_vinculo": v.tipo_vinculo,
                "estado": v.estado,
                "origen": v.origen,
                "score": v.score,
                "motivo": v.motivo,
                "monto": v.monto,
                "cfdi": _cfdi_resumen(cfdis.get(v.cfdi_id)),
            }
        )
    salida = []
    for m in movs:
        cuenta = cuentas.get(m.cuenta_id)
        salida.append(
            {
                "id": m.id,
                "cuenta_id": m.cuenta_id,
                "banco": cuenta.banco if cuenta else None,
                "cuenta_alias": (cuenta.alias or cuenta.numero) if cuenta else None,
                "fecha": m.fecha,
                "descripcion": m.descripcion,
                "referencia": m.referencia,
                "cargo": m.cargo,
                "abono": m.abono,
                "saldo": m.saldo,
                "tipo": m.tipo,
                "monto": m.monto,
                "estado": m.estado,
                "clasificacion": m.clasificacion,
                "nota": m.nota,
                "vinculos": por_mov.get(m.id, []),
            }
        )
    return salida


def _mov_or_404(db: Session, rfc_client_id: int, movimiento_id: int) -> MovimientoBancario:
    mov = (
        db.query(MovimientoBancario)
        .filter(MovimientoBancario.id == movimiento_id, MovimientoBancario.rfc_client_id == rfc_client_id)
        .first()
    )
    if mov is None:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    return mov


def _vinculo_or_404(db: Session, rfc_client_id: int, vinculo_id: int) -> VinculoBancario:
    v = (
        db.query(VinculoBancario)
        .filter(VinculoBancario.id == vinculo_id, VinculoBancario.rfc_client_id == rfc_client_id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Vínculo no encontrado")
    return v


def candidatos_movimiento(db: Session, rfc_client_id: int, movimiento_id: int, q: str | None = None) -> list[dict]:
    mov = _mov_or_404(db, rfc_client_id, movimiento_id)
    excluir = _cfdis_ya_conciliados(db, rfc_client_id, excepto_movimientos={mov.id})
    candidatos = construir_candidatos(db, rfc_client_id, mov.fecha, mov.fecha, excluir_cfdi=excluir)
    mov_m = matching.Movimiento(mov.id, mov.fecha, mov.tipo, mov.monto, f"{mov.descripcion} {mov.referencia or ''}")
    ranking = matching.rankear(mov_m, candidatos)
    ids = [c.cfdi_id for _, _, c in ranking]
    salida_ids = set(ids)
    resultados = []
    docs = {d.id: d for d in db.query(CfdiDocument).filter(CfdiDocument.id.in_(ids)).all()} if ids else {}
    for score, motivo, cand in ranking:
        resultados.append(
            {"score": score, "motivo": motivo, "tipo_vinculo": cand.tipo_vinculo, "cfdi": _cfdi_resumen(docs.get(cand.cfdi_id))}
        )
    # Búsqueda libre (por si el importe no coincide: anticipos, pagos de varias facturas...).
    if q and q.strip():
        like = f"%{q.strip()}%"
        extra = (
            db.query(CfdiDocument)
            .filter(
                CfdiDocument.rfc_client_id == rfc_client_id,
                CfdiDocument.tipo == ("recibido" if mov.tipo == "cargo" else "emitido"),
                or_(
                    CfdiDocument.uuid.ilike(like),
                    CfdiDocument.emisor_nombre.ilike(like),
                    CfdiDocument.receptor_nombre.ilike(like),
                    CfdiDocument.emisor_rfc.ilike(like),
                    CfdiDocument.receptor_rfc.ilike(like),
                    CfdiDocument.folio.ilike(like),
                ),
            )
            .order_by(CfdiDocument.fecha_emision.desc())
            .limit(20)
            .all()
        )
        for d in extra:
            if d.id in salida_ids or d.id in excluir:
                continue
            tipo_v = "rep" if d.tipo_comprobante == "P" else ("ppd_sin_rep" if d.metodo_pago == "PPD" else "pue")
            resultados.append({"score": 0, "motivo": "Resultado de búsqueda", "tipo_vinculo": tipo_v, "cfdi": _cfdi_resumen(d)})
    return resultados


def vincular_manual(db: Session, rfc_client_id: int, movimiento_id: int, cfdi_id: int, user_id: int | None) -> None:
    mov = _mov_or_404(db, rfc_client_id, movimiento_id)
    doc = db.query(CfdiDocument).filter(CfdiDocument.id == cfdi_id, CfdiDocument.rfc_client_id == rfc_client_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="CFDI no encontrado")
    # Un movimiento conciliado a mano reemplaza cualquier sugerencia pendiente.
    db.query(VinculoBancario).filter(
        VinculoBancario.movimiento_id == mov.id, VinculoBancario.estado == "sugerida"
    ).delete(synchronize_session=False)
    tipo_v = "rep" if doc.tipo_comprobante == "P" else ("nomina" if doc.tipo_comprobante == "N" else (
        "ppd_sin_rep" if doc.metodo_pago == "PPD" else "pue"))
    db.add(
        VinculoBancario(
            rfc_client_id=rfc_client_id,
            movimiento_id=mov.id,
            cfdi_id=doc.id,
            tipo_vinculo=tipo_v,
            monto=doc.monto_pago if doc.tipo_comprobante == "P" and doc.monto_pago else doc.total,
            score=100,
            motivo="Vinculado a mano por el contador.",
            origen="manual",
            estado="aprobada",
            resuelto_por_id=user_id,
            resuelto_at=datetime.utcnow(),
        )
    )
    mov.estado = "conciliado"
    mov.clasificacion = None
    db.commit()


def resolver_vinculo(db: Session, rfc_client_id: int, vinculo_id: int, accion: str, user_id: int | None) -> None:
    v = _vinculo_or_404(db, rfc_client_id, vinculo_id)
    mov = _mov_or_404(db, rfc_client_id, v.movimiento_id)
    if accion == "aprobar":
        v.estado = "aprobada"
        mov.estado = "conciliado"
    elif accion == "rechazar":
        v.estado = "rechazada"
        mov.estado = "pendiente"
    elif accion == "desvincular":
        # Se deja como rechazada para que el motor no la vuelva a proponer.
        v.estado = "rechazada"
        mov.estado = "pendiente"
    else:
        raise HTTPException(status_code=422, detail="Acción inválida")
    v.resuelto_por_id = user_id
    v.resuelto_at = datetime.utcnow()
    db.commit()


def clasificar_movimiento(
    db: Session, rfc_client_id: int, movimiento_id: int, clasificacion: str | None, nota: str | None
) -> None:
    mov = _mov_or_404(db, rfc_client_id, movimiento_id)
    if clasificacion and clasificacion not in CLASIFICACIONES:
        raise HTTPException(status_code=422, detail="Clasificación inválida")
    mov.clasificacion = clasificacion or None
    mov.nota = (nota or "").strip() or None
    tiene_aprobado = (
        db.query(VinculoBancario.id)
        .filter(VinculoBancario.movimiento_id == mov.id, VinculoBancario.estado == "aprobada")
        .first()
        is not None
    )
    if tiene_aprobado:
        mov.estado = "conciliado"
    else:
        mov.estado = "clasificado" if clasificacion else "pendiente"
        if clasificacion:
            db.query(VinculoBancario).filter(
                VinculoBancario.movimiento_id == mov.id, VinculoBancario.estado == "sugerida"
            ).delete(synchronize_session=False)
    db.commit()


# --------------------------------------------------------------------------
# Resumen para deducibilidad
# --------------------------------------------------------------------------

def resumen(db: Session, rfc_client_id: int, meses: str | None) -> dict:
    movs = _query_movimientos(db, rfc_client_id, meses).all()
    por_estado: dict[str, dict] = {
        e: {"cantidad": 0, "monto": CERO} for e in ("conciliado", "sugerido", "pendiente", "clasificado")
    }
    por_clasificacion: dict[str, dict] = {}
    cargos = abonos = CERO
    for m in movs:
        por_estado.setdefault(m.estado, {"cantidad": 0, "monto": CERO})
        por_estado[m.estado]["cantidad"] += 1
        por_estado[m.estado]["monto"] += m.monto
        if m.tipo == "cargo":
            cargos += m.monto
        else:
            abonos += m.monto
        if m.estado == "clasificado" and m.clasificacion:
            c = por_clasificacion.setdefault(
                m.clasificacion, {"clasificacion": m.clasificacion, "etiqueta": CLASIFICACIONES.get(m.clasificacion, m.clasificacion), "cantidad": 0, "monto": CERO}
            )
            c["cantidad"] += 1
            c["monto"] += m.monto

    lista_meses = parsear_meses(meses)
    hay_estados = bool(movs)
    conciliados = _cfdis_ya_conciliados(db, rfc_client_id)

    def _cfdis_periodo(tipo: str):
        query = db.query(CfdiDocument).filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.tipo == tipo,
            CfdiDocument.tipo_comprobante == "I",
            or_(CfdiDocument.metodo_pago == "PUE", CfdiDocument.metodo_pago.is_(None)),
            or_(CfdiDocument.estado_sat.is_(None), CfdiDocument.estado_sat != "cancelado"),
        )
        if lista_meses:
            query = query.filter(filtro_meses(CfdiDocument.fecha_emision, lista_meses))
        return query.order_by(CfdiDocument.total.desc()).all()

    gastos_sin_banco = []
    efectivo_no_deducible = []
    if hay_estados:
        for d in _cfdis_periodo("recibido"):
            if d.id in conciliados:
                continue
            fila = _cfdi_resumen(d)
            if d.forma_pago == "01" and d.total > LIMITE_EFECTIVO_DEDUCIBLE:
                fila["alerta"] = "efectivo_mayor_2000"
                efectivo_no_deducible.append(fila)
            else:
                fila["alerta"] = "efectivo" if d.forma_pago == "01" else "sin_movimiento"
            gastos_sin_banco.append(fila)
    else:
        # Aun sin estados de cuenta, los PUE en efectivo > $2,000 son una
        # alerta por sí mismos.
        for d in _cfdis_periodo("recibido"):
            if d.forma_pago == "01" and d.total > LIMITE_EFECTIVO_DEDUCIBLE:
                fila = _cfdi_resumen(d)
                fila["alerta"] = "efectivo_mayor_2000"
                efectivo_no_deducible.append(fila)

    ingresos_sin_banco = []
    if hay_estados:
        for d in _cfdis_periodo("emitido"):
            if d.id not in conciliados:
                fila = _cfdi_resumen(d)
                fila["alerta"] = "sin_cobro"
                ingresos_sin_banco.append(fila)

    ppd_sin_rep = []
    ids_ppd = [
        cfdi_id
        for (cfdi_id,) in db.query(VinculoBancario.cfdi_id)
        .join(MovimientoBancario, MovimientoBancario.id == VinculoBancario.movimiento_id)
        .join(EstadoCuenta, EstadoCuenta.id == MovimientoBancario.estado_cuenta_id)
        .filter(
            VinculoBancario.rfc_client_id == rfc_client_id,
            VinculoBancario.estado == "aprobada",
            VinculoBancario.tipo_vinculo == "ppd_sin_rep",
            *( [EstadoCuenta.periodo.in_(_periodos(meses))] if lista_meses else [] ),
        )
        .all()
    ]
    if ids_ppd:
        uuids_con_rep = {
            u for (u,) in db.query(func.upper(PagoRelacionado.uuid_factura_relacionada))
            .filter(PagoRelacionado.rfc_client_id == rfc_client_id).distinct().all()
        }
        for d in db.query(CfdiDocument).filter(CfdiDocument.id.in_(ids_ppd)).all():
            if d.uuid.upper() not in uuids_con_rep:
                ppd_sin_rep.append(_cfdi_resumen(d))

    return {
        "hay_estados": hay_estados,
        "movimientos": len(movs),
        "total_cargos": cargos,
        "total_abonos": abonos,
        "por_estado": por_estado,
        "por_clasificacion": sorted(por_clasificacion.values(), key=lambda c: -c["monto"]),
        "gastos_sin_movimiento": gastos_sin_banco,
        "ingresos_sin_movimiento": ingresos_sin_banco,
        "efectivo_no_deducible": efectivo_no_deducible,
        "ppd_pagadas_sin_rep": ppd_sin_rep,
        "avance": round(
            100 * (por_estado["conciliado"]["cantidad"] + por_estado["clasificado"]["cantidad"]) / len(movs)
        ) if movs else 0,
    }


def exportar_csv(db: Session, rfc_client_id: int, meses: str | None) -> str:
    filas = listar_movimientos(db, rfc_client_id, meses=meses)
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(
        ["Banco", "Cuenta", "Fecha", "Descripción", "Referencia", "Cargo", "Abono", "Saldo", "Estado",
         "Clasificación", "Nota", "Tipo vínculo", "UUID", "Serie/Folio", "RFC contraparte", "Contraparte",
         "Fecha CFDI", "Total CFDI", "Método pago", "Forma pago", "Puntaje", "Motivo"]
    )
    for m in filas:
        vinculo = next((v for v in m["vinculos"] if v["estado"] == "aprobada"), None) or (m["vinculos"][0] if m["vinculos"] else None)
        cfdi = vinculo["cfdi"] if vinculo else None
        w.writerow(
            [
                m["banco"] or "", m["cuenta_alias"] or "", m["fecha"].isoformat(), m["descripcion"], m["referencia"] or "",
                f"{m['cargo']:.2f}" if m["cargo"] else "", f"{m['abono']:.2f}" if m["abono"] else "",
                f"{m['saldo']:.2f}" if m["saldo"] is not None else "", m["estado"],
                CLASIFICACIONES.get(m["clasificacion"] or "", ""), m["nota"] or "",
                vinculo["tipo_vinculo"] if vinculo else "", cfdi["uuid"] if cfdi else "",
                " ".join(filter(None, [cfdi.get("serie"), cfdi.get("folio")])) if cfdi else "",
                cfdi["contraparte_rfc"] if cfdi else "", cfdi["contraparte_nombre"] or "" if cfdi else "",
                cfdi["fecha_emision"].date().isoformat() if cfdi else "", f"{cfdi['total']:.2f}" if cfdi else "",
                cfdi["metodo_pago"] or "" if cfdi else "", cfdi["forma_pago"] or "" if cfdi else "",
                vinculo["score"] if vinculo else "", vinculo["motivo"] if vinculo else "",
            ]
        )
    return "﻿" + salida.getvalue()
