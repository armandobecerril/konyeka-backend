"""Orquesta el flujo completo de Descarga Masiva del SAT: solicitar, verificar
(con reintentos) y descargar + indexar los CFDIs resultantes."""
import logging
import time
import zipfile
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.storage import get_xml_storage
from app.efirma.service import obtener_signer
from app.sat_downloads import sat_client
from app.sat_downloads.models import CfdiDocument, SolicitudDescarga
from app.sat_downloads.schemas import SolicitudCreate

logger = logging.getLogger(__name__)

MAX_INTENTOS_VERIFICACION = 30
ESPERA_ENTRE_INTENTOS_SEGUNDOS = 20

ESTADO_SAT_A_TEXTO = {
    1: "aceptada",
    2: "en_proceso",
    3: "terminada",
    4: "error",
    5: "rechazada",
    6: "vencida",
}


def crear_solicitud(
    db: Session,
    *,
    rfc_client_id: int,
    payload: SolicitudCreate,
    created_by_id: int | None,
    background_tasks: BackgroundTasks,
) -> SolicitudDescarga:
    # Falla rápido si no hay e.firma vigente, antes de crear el registro.
    obtener_signer(db, rfc_client_id)

    solicitud = SolicitudDescarga(
        rfc_client_id=rfc_client_id,
        tipo=payload.tipo,
        fecha_inicial=payload.fecha_inicial,
        fecha_final=payload.fecha_final,
        tipo_comprobante=payload.tipo_comprobante,
        estado="solicitada",
        created_by_id=created_by_id,
    )
    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)

    background_tasks.add_task(_procesar_solicitud, solicitud.id)
    return solicitud


def listar_solicitudes(db: Session, rfc_client_id: int) -> list[SolicitudDescarga]:
    return (
        db.query(SolicitudDescarga)
        .filter(SolicitudDescarga.rfc_client_id == rfc_client_id)
        .order_by(SolicitudDescarga.created_at.desc())
        .all()
    )


def get_solicitud_or_404(db: Session, rfc_client_id: int, solicitud_id: int) -> SolicitudDescarga:
    solicitud = (
        db.query(SolicitudDescarga)
        .filter(
            SolicitudDescarga.id == solicitud_id,
            SolicitudDescarga.rfc_client_id == rfc_client_id,
        )
        .first()
    )
    if solicitud is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada")
    return solicitud


def _procesar_solicitud(solicitud_id: int) -> None:
    """Corre en background (fuera del ciclo request/response, en un thread aparte):
    pide la descarga al SAT, espera a que esté lista, y guarda los CFDIs resultantes.
    Usa su propia sesión de BD porque la del request original ya se cerró."""
    db = SessionLocal()
    try:
        solicitud = db.query(SolicitudDescarga).filter(SolicitudDescarga.id == solicitud_id).first()
        if solicitud is None:
            return

        try:
            signer = obtener_signer(db, solicitud.rfc_client_id)
        except HTTPException as exc:
            solicitud.estado = "error"
            solicitud.mensaje_error = str(exc.detail)
            db.commit()
            return

        try:
            respuesta = sat_client.solicitar_descarga(
                signer,
                tipo=solicitud.tipo,
                fecha_inicial=solicitud.fecha_inicial,
                fecha_final=solicitud.fecha_final,
                tipo_comprobante=solicitud.tipo_comprobante,
            )
        except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier falla de red/SOAP
            logger.exception("Error solicitando descarga al SAT (solicitud_id=%s)", solicitud_id)
            solicitud.estado = "error"
            solicitud.mensaje_error = f"No se pudo contactar al SAT: {exc}"
            db.commit()
            return

        cod_estatus = respuesta.get("CodEstatus")
        id_solicitud_sat = respuesta.get("IdSolicitud")

        if not id_solicitud_sat:
            solicitud.estado = "error"
            solicitud.mensaje_error = (
                sat_client.mensaje_para_codigo(cod_estatus)
                or respuesta.get("Mensaje")
                or "El SAT no regresó un IdSolicitud."
            )
            db.commit()
            return

        solicitud.id_solicitud_sat = id_solicitud_sat
        solicitud.estado = "aceptada"
        db.commit()

        estado_final = None
        for _intento in range(MAX_INTENTOS_VERIFICACION):
            time.sleep(ESPERA_ENTRE_INTENTOS_SEGUNDOS)
            try:
                verificacion = sat_client.verificar_descarga(signer, id_solicitud_sat)
            except Exception:  # noqa: BLE001
                logger.exception("Error verificando solicitud %s", id_solicitud_sat)
                continue

            estado_num = verificacion.get("EstadoSolicitud")
            estado_texto = ESTADO_SAT_A_TEXTO.get(estado_num, "error")
            solicitud.estado = estado_texto
            solicitud.numero_cfdis = verificacion.get("NumeroCFDIs")
            db.commit()

            if estado_texto in ("terminada", "error", "rechazada", "vencida"):
                estado_final = verificacion
                break

        if estado_final is None:
            solicitud.estado = "error"
            solicitud.mensaje_error = (
                "El SAT tardó demasiado en procesar la solicitud. Intenta de nuevo más tarde."
            )
            db.commit()
            return

        if solicitud.estado != "terminada":
            solicitud.mensaje_error = (
                sat_client.mensaje_para_codigo(estado_final.get("CodigoEstadoSolicitud"))
                or estado_final.get("Mensaje")
                or "El SAT no pudo completar la solicitud."
            )
            db.commit()
            return

        paquetes = estado_final.get("IdsPaquetes") or []
        solicitud.paquetes_ids = paquetes
        db.commit()

        total_guardados = 0
        for id_paquete in paquetes:
            try:
                zip_bytes = sat_client.descargar_paquete(signer, id_paquete)
            except Exception:  # noqa: BLE001
                logger.exception("Error descargando paquete %s", id_paquete)
                continue

            total_guardados += _procesar_paquete(
                db,
                zip_bytes=zip_bytes,
                rfc_client_id=solicitud.rfc_client_id,
                solicitud_id=solicitud.id,
                tipo=solicitud.tipo,
            )

        solicitud.numero_cfdis = total_guardados
        db.commit()
    finally:
        db.close()


def _tipo_documento(tipo_solicitud: str) -> str:
    return "emitido" if tipo_solicitud == "emitidas" else "recibido"


# Los dos complementos del SAT que identifican compras de combustible (lo que
# los contadores llaman, en corto, "facturas de gasolina"): el que emiten las
# gasolineras por consumo directo, y el que emiten los monederos electrónicos
# de flotillas (Edenred, Multisistemas, etc). Empezamos solo con estos dos —
# el resto del catálogo de complementos del SAT se agrega más adelante.
COMPLEMENTOS_COMBUSTIBLE = {"consumodecombustibles", "estadodecuentacombustible"}


def _tiene_complemento_combustible(xml_bytes: bytes, lista_complementos: list[str]) -> bool:
    if any(nombre.lower() in COMPLEMENTOS_COMBUSTIBLE for nombre in lista_complementos):
        return True
    # Respaldo por si satcfdi no reconoce el namespace exacto del complemento:
    # buscamos directo el nombre de la etiqueta raíz en el XML crudo.
    return b"ConsumoDeCombustibles" in xml_bytes or b"EstadoDeCuentaCombustible" in xml_bytes


def _procesar_paquete(
    db: Session,
    *,
    zip_bytes: bytes,
    rfc_client_id: int,
    solicitud_id: int,
    tipo: str,
) -> int:
    from satcfdi.cfdi import CFDI

    from app.rfc_clients.models import RfcClient

    rfc_client = db.query(RfcClient).filter(RfcClient.id == rfc_client_id).first()
    storage = get_xml_storage()
    guardados = 0
    pagos_por_procesar: list[tuple[CfdiDocument, object]] = []

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        for nombre in zf.namelist():
            if not nombre.lower().endswith(".xml"):
                continue
            xml_bytes = zf.read(nombre)

            try:
                cfdi = CFDI.from_string(xml_bytes)
                uuid = str(cfdi["Complemento"]["TimbreFiscalDigital"]["UUID"])
            except Exception:  # noqa: BLE001
                logger.warning("XML sin timbre válido dentro del paquete, se omite: %s", nombre)
                continue

            existente = (
                db.query(CfdiDocument)
                .filter(CfdiDocument.rfc_client_id == rfc_client_id, CfdiDocument.uuid == uuid)
                .first()
            )
            if existente is not None:
                continue

            storage_path = storage.save(
                rfc=rfc_client.rfc if rfc_client else str(rfc_client_id),
                tipo=_tipo_documento(tipo),
                uuid=uuid,
                content=xml_bytes,
            )

            receptor = cfdi.get("Receptor") or {}
            impuestos = cfdi.get("Impuestos") or {}
            complemento = cfdi.get("Complemento") or {}
            lista_complementos = list(complemento.keys())

            doc = CfdiDocument(
                rfc_client_id=rfc_client_id,
                solicitud_id=solicitud_id,
                uuid=uuid,
                tipo=_tipo_documento(tipo),
                tipo_comprobante=_texto(cfdi.get("TipoDeComprobante")),
                serie=_texto(cfdi.get("Serie")),
                folio=_texto(cfdi.get("Folio")),
                version=_texto(cfdi.get("Version")),
                lugar_expedicion=_texto(cfdi.get("LugarExpedicion")),
                exportacion=_texto(cfdi.get("Exportacion")),
                condiciones_pago=_texto(cfdi.get("CondicionesDePago")),
                descuento=_to_decimal(cfdi.get("Descuento")),
                tipo_cambio=_to_decimal(cfdi.get("TipoCambio")),
                emisor_rfc=_texto(cfdi["Emisor"]["Rfc"]),
                emisor_nombre=_texto(cfdi["Emisor"].get("Nombre")),
                regimen_fiscal_emisor=_texto(cfdi["Emisor"].get("RegimenFiscal")),
                receptor_rfc=_texto(cfdi["Receptor"]["Rfc"]),
                receptor_nombre=_texto(cfdi["Receptor"].get("Nombre")),
                regimen_fiscal_receptor=_texto(receptor.get("RegimenFiscalReceptor")),
                domicilio_fiscal_receptor=_texto(receptor.get("DomicilioFiscalReceptor")),
                fecha_emision=_to_datetime(cfdi.get("Fecha")),
                total=_to_decimal(cfdi.get("Total")) or Decimal("0"),
                subtotal=_to_decimal(cfdi.get("SubTotal")),
                moneda=_texto(cfdi.get("Moneda")),
                metodo_pago=_texto(cfdi.get("MetodoPago")),
                forma_pago=_texto(cfdi.get("FormaPago")),
                uso_cfdi=_texto(receptor.get("UsoCFDI")),
                total_impuestos_trasladados=_to_decimal(impuestos.get("TotalImpuestosTrasladados")),
                total_impuestos_retenidos=_to_decimal(impuestos.get("TotalImpuestosRetenidos")),
                impuestos_desglose=_serializar_impuestos(impuestos),
                complementos=lista_complementos or None,
                tiene_complemento_combustible=_tiene_complemento_combustible(xml_bytes, lista_complementos),
                estado_sat="vigente",
                storage_path=storage_path,
            )
            db.add(doc)
            guardados += 1

            if doc.tipo_comprobante == "P":
                pagos_por_procesar.append((doc, cfdi))

    db.commit()

    if pagos_por_procesar:
        from app.conciliacion.service import guardar_pagos_relacionados

        for doc, cfdi in pagos_por_procesar:
            guardar_pagos_relacionados(db, doc, cfdi)

    return guardados


def _texto(value) -> str | None:
    """Convierte un valor de satcfdi a texto plano para guardar en BD.

    Los campos de catálogo (TipoDeComprobante, Moneda, MetodoPago, FormaPago,
    UsoCFDI) vienen como objetos `Code` de satcfdi, cuyo __str__ regresa
    "{codigo} - {descripcion}" (ej. "I - Ingreso"). Para esos casos guardamos
    solo el código bare (`.code`), no la descripción."""
    if value is None:
        return None
    if hasattr(value, "code"):
        return str(value.code)
    return str(value)


def _serializar_impuestos(impuestos) -> dict | None:
    """Convierte el bloque <Impuestos> del comprobante a un dict JSON-serializable
    con el desglose completo por tasa/cuota.

    satcfdi regresa Traslados y Retenciones como DICTS (no listas), con llaves
    compuestas tipo "002|Tasa|0.160000" para traslados o el código del impuesto
    ("001") para retenciones -- verificado directo contra la librería con un
    CFDI de prueba. Guardamos solo los valores (la llave compuesta no aporta
    nada que no esté ya en el propio valor)."""
    if not impuestos:
        return None

    def _valor(v) -> dict:
        return {
            "impuesto": _texto(v.get("Impuesto")),
            "tipo_factor": _texto(v.get("TipoFactor")),
            "tasa_o_cuota": str(v["TasaOCuota"]) if v.get("TasaOCuota") is not None else None,
            "base": str(v["Base"]) if v.get("Base") is not None else None,
            "importe": str(v["Importe"]) if v.get("Importe") is not None else None,
        }

    traslados = impuestos.get("Traslados") or {}
    retenciones = impuestos.get("Retenciones") or {}
    desglose = {
        "traslados": [_valor(v) for v in traslados.values()],
        "retenciones": [_valor(v) for v in retenciones.values()],
    }
    if not desglose["traslados"] and not desglose["retenciones"]:
        return None
    return desglose


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def listar_cfdis(
    db: Session,
    rfc_client_id: int,
    *,
    tipo: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    q: str | None = None,
    complemento: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    query = db.query(CfdiDocument).filter(CfdiDocument.rfc_client_id == rfc_client_id)
    if tipo:
        query = query.filter(CfdiDocument.tipo == tipo)
    if fecha_desde:
        query = query.filter(CfdiDocument.fecha_emision >= fecha_desde)
    if fecha_hasta:
        query = query.filter(CfdiDocument.fecha_emision <= fecha_hasta)
    if complemento == "gasolinas":
        query = query.filter(CfdiDocument.tiene_complemento_combustible.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                CfdiDocument.uuid.ilike(like),
                CfdiDocument.emisor_nombre.ilike(like),
                CfdiDocument.emisor_rfc.ilike(like),
                CfdiDocument.receptor_nombre.ilike(like),
                CfdiDocument.receptor_rfc.ilike(like),
            )
        )

    total = query.count()
    items = query.order_by(CfdiDocument.fecha_emision.desc()).offset(skip).limit(limit).all()
    return items, total


def contar_cfdis_totales(db: Session) -> int:
    """Total de CFDIs descargados en la bóveda, sumando todos los clientes."""
    return db.query(func.count(CfdiDocument.id)).scalar() or 0


def resumen_totales(db: Session, rfc_client_id: int) -> dict:
    """Cuenta e importe por tipo de comprobante y por método de pago, sobre
    todas las facturas descargadas del cliente. Alimenta la barra de
    'Totales' sobre Tus facturas descargadas, al estilo MyAdmin."""
    base = db.query(CfdiDocument).filter(CfdiDocument.rfc_client_id == rfc_client_id)

    def contar_y_sumar(filtro=None) -> tuple[int, Decimal]:
        query = base.filter(filtro) if filtro is not None else base
        fila = query.with_entities(
            func.count(CfdiDocument.id), func.coalesce(func.sum(CfdiDocument.total), 0)
        ).one()
        return fila[0], Decimal(fila[1])

    ingresos_count, ingresos_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "I")
    egresos_count, egresos_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "E")
    traslados_count, traslados_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "T")
    ppd_count, ppd_total = contar_y_sumar(CfdiDocument.metodo_pago == "PPD")
    pue_count, pue_total = contar_y_sumar(CfdiDocument.metodo_pago == "PUE")
    xml_count, xml_total = contar_y_sumar()

    return {
        "ingresos_count": ingresos_count,
        "ingresos_total": ingresos_total,
        "egresos_count": egresos_count,
        "egresos_total": egresos_total,
        "traslados_count": traslados_count,
        "traslados_total": traslados_total,
        "ppd_count": ppd_count,
        "ppd_total": ppd_total,
        "pue_count": pue_count,
        "pue_total": pue_total,
        "xml_count": xml_count,
        "xml_total": xml_total,
    }


def resumen_monedas(db: Session, rfc_client_id: int) -> tuple[int, list[tuple[str, int]]]:
    """Cuenta, para todas las facturas descargadas de un cliente (sin
    paginar), cuántas no están en MXN y el desglose por moneda. Alimenta el
    banner de alerta de 'Tus facturas descargadas'."""
    filas = (
        db.query(CfdiDocument.moneda, func.count(CfdiDocument.id))
        .filter(
            CfdiDocument.rfc_client_id == rfc_client_id,
            CfdiDocument.moneda.isnot(None),
            CfdiDocument.moneda != "MXN",
        )
        .group_by(CfdiDocument.moneda)
        .all()
    )
    desglose = [(moneda, cantidad) for moneda, cantidad in filas]
    total_no_mxn = sum(cantidad for _, cantidad in desglose)
    return total_no_mxn, desglose


def get_cfdi_xml(db: Session, rfc_client_id: int, cfdi_id: int) -> tuple[CfdiDocument, bytes]:
    doc = (
        db.query(CfdiDocument)
        .filter(CfdiDocument.id == cfdi_id, CfdiDocument.rfc_client_id == rfc_client_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CFDI no encontrado")

    storage = get_xml_storage()
    content = storage.read(doc.storage_path)
    return doc, content
