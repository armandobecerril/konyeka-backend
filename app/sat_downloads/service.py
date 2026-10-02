"""Orquesta el flujo completo de Descarga Masiva del SAT: solicitar, verificar
(con reintentos) y descargar + indexar los CFDIs resultantes."""
import logging
import time
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.storage import get_xml_storage
from app.efirma.service import obtener_signer
from app.sat_downloads import sat_client
from app.sat_downloads.models import CfdiDocument, ColumnaPreferencia, SolicitudDescarga
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


def _query_cfdis_filtrados(
    db: Session,
    rfc_client_id: int,
    *,
    tipo: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    q: str | None = None,
    complemento: str | None = None,
):
    """Arma la consulta de CfdiDocument con los mismos filtros que usa la
    tabla "Tus facturas descargadas" -- la comparten listar_cfdis() (para
    pintar las filas) y resumen_totales() (para que la barra de Totales
    siempre sume justo lo que está filtrado/visible abajo, nunca todo el
    historial del cliente)."""
    query = db.query(CfdiDocument).filter(CfdiDocument.rfc_client_id == rfc_client_id)
    if tipo:
        query = query.filter(CfdiDocument.tipo == tipo)
    if fecha_desde:
        query = query.filter(CfdiDocument.fecha_emision >= fecha_desde)
    if fecha_hasta:
        # fecha_emision es DateTime y fecha_hasta es un date "pelón": comparar
        # con <= lo trata como medianoche de ese día, así que cualquier
        # factura emitida después de las 00:00 del último día del rango
        # quedaba excluida (el bug reportado con las facturas de combustible
        # de fin de mes). Por eso el corte es "antes del día siguiente".
        query = query.filter(CfdiDocument.fecha_emision < fecha_hasta + timedelta(days=1))
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
    return query


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
    query = _query_cfdis_filtrados(
        db,
        rfc_client_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        complemento=complemento,
    )
    total = query.count()
    items = query.order_by(CfdiDocument.fecha_emision.desc()).offset(skip).limit(limit).all()
    return items, total


def contar_cfdis_totales(db: Session) -> int:
    """Total de CFDIs descargados en la bóveda, sumando todos los clientes."""
    return db.query(func.count(CfdiDocument.id)).scalar() or 0


def resumen_totales(
    db: Session,
    rfc_client_id: int,
    *,
    tipo: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    q: str | None = None,
    complemento: str | None = None,
) -> dict:
    """Cuenta e importe por tipo de comprobante y por método de pago. Recibe
    los MISMOS filtros que listar_cfdis() para que la barra de Totales sobre
    "Tus facturas descargadas" siempre sume lo que está filtrado/visible en
    la tabla de abajo, no todo el historial del cliente."""
    base = _query_cfdis_filtrados(
        db,
        rfc_client_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        complemento=complemento,
    )

    def contar_y_sumar(filtro=None) -> tuple[int, Decimal]:
        query = base.filter(filtro) if filtro is not None else base
        fila = query.with_entities(
            func.count(CfdiDocument.id), func.coalesce(func.sum(CfdiDocument.total), 0)
        ).one()
        return fila[0], Decimal(fila[1])

    ingresos_count, ingresos_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "I")
    egresos_count, egresos_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "E")
    traslados_count, traslados_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "T")
    pagos_count, pagos_total = contar_y_sumar(CfdiDocument.tipo_comprobante == "P")
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
        "pagos_count": pagos_count,
        "pagos_total": pagos_total,
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


def _conceptos_desde_xml(xml_bytes: bytes) -> list[dict]:
    """Extrae la lista de Conceptos del XML para mostrarla en el PDF genérico.

    No se guarda en BD (solo se usa el header del CFDI para las columnas de
    la bóveda), así que aquí se vuelve a parsear el XML bajo demanda -- el
    PDF se genera una vez por clic, así que el costo es insignificante."""
    from satcfdi.cfdi import CFDI

    try:
        cfdi = CFDI.from_string(xml_bytes)
    except Exception:  # noqa: BLE001
        return []

    conceptos = cfdi.get("Conceptos") or []
    filas: list[dict] = []
    for concepto in conceptos:
        filas.append(
            {
                "cantidad": _texto(concepto.get("Cantidad")) or "",
                "unidad": _texto(concepto.get("Unidad")) or "",
                "clave_prod_serv": _texto(concepto.get("ClaveProdServ")) or "",
                "descripcion": _texto(concepto.get("Descripcion")) or "",
                "valor_unitario": _to_decimal(concepto.get("ValorUnitario")),
                "importe": _to_decimal(concepto.get("Importe")),
            }
        )
    return filas


def get_cfdi_pdf(db: Session, rfc_client_id: int, cfdi_id: int) -> tuple[CfdiDocument, bytes]:
    """Genera un PDF genérico tipo factura a partir de los datos ya guardados
    del CFDI (y sus Conceptos, releídos del XML). No es la representación
    impresa oficial del SAT (esa requiere la plantilla XSLT certificada):
    es un resumen legible pensado para que el contador pueda imprimir o
    compartir la factura sin tener que abrir el XML."""
    doc, xml_bytes = get_cfdi_xml(db, rfc_client_id, cfdi_id)
    conceptos = _conceptos_desde_xml(xml_bytes)
    pdf_bytes = _renderizar_pdf_cfdi(doc, conceptos)
    return doc, pdf_bytes


def _renderizar_pdf_cfdi(doc: CfdiDocument, conceptos: list[dict]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    def money(valor: Decimal | None) -> str:
        if valor is None:
            return "—"
        moneda = doc.moneda or "MXN"
        return f"${valor:,.2f} {moneda}"

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle("titulo", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#061A35"))
    subtitulo = ParagraphStyle("subtitulo", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"))
    etiqueta = ParagraphStyle("etiqueta", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#64748B"))
    valor_style = ParagraphStyle("valor", parent=styles["Normal"], fontSize=10, textColor=colors.HexColor("#061A35"))

    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        title=f"CFDI {doc.uuid}",
    )

    elementos = []
    elementos.append(Paragraph("Comprobante Fiscal Digital (CFDI)", titulo))
    elementos.append(Paragraph("Representación genérica -- no es el formato oficial timbrado por el SAT.", subtitulo))
    elementos.append(Spacer(1, 10 * mm))

    datos_generales = [
        [Paragraph("Emisor", etiqueta), Paragraph("Receptor", etiqueta)],
        [
            Paragraph(f"{doc.emisor_nombre or doc.emisor_rfc}<br/>RFC: {doc.emisor_rfc}", valor_style),
            Paragraph(f"{doc.receptor_nombre or doc.receptor_rfc}<br/>RFC: {doc.receptor_rfc}", valor_style),
        ],
    ]
    tabla_partes = Table(datos_generales, colWidths=[85 * mm, 85 * mm])
    tabla_partes.setStyle(
        TableStyle(
            [
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
            ]
        )
    )
    elementos.append(tabla_partes)
    elementos.append(Spacer(1, 8 * mm))

    folio = " ".join(filter(None, [doc.serie, doc.folio])) or "—"
    meta_filas = [
        ["UUID", doc.uuid, "Fecha", doc.fecha_emision.strftime("%d/%m/%Y %H:%M")],
        ["Serie / Folio", folio, "Tipo", doc.tipo_comprobante or "—"],
        ["Uso CFDI", doc.uso_cfdi or "—", "Régimen emisor", doc.regimen_fiscal_emisor or "—"],
        ["Forma de pago", doc.forma_pago or "—", "Método de pago", doc.metodo_pago or "—"],
        ["Moneda", doc.moneda or "MXN", "Estado SAT", doc.estado_sat],
    ]
    tabla_meta = Table(meta_filas, colWidths=[30 * mm, 55 * mm, 30 * mm, 55 * mm])
    tabla_meta.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748B")),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#64748B")),
                ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#061A35")),
                ("TEXTCOLOR", (3, 0), (3, -1), colors.HexColor("#061A35")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
            ]
        )
    )
    elementos.append(tabla_meta)
    elementos.append(Spacer(1, 8 * mm))

    if conceptos:
        encabezado = ["Cant.", "Clave", "Descripción", "V. unitario", "Importe"]
        filas_conceptos = [encabezado]
        for c in conceptos:
            filas_conceptos.append(
                [
                    c["cantidad"],
                    c["clave_prod_serv"],
                    Paragraph(c["descripcion"], valor_style),
                    money(c["valor_unitario"]),
                    money(c["importe"]),
                ]
            )
        tabla_conceptos = Table(
            filas_conceptos,
            colWidths=[15 * mm, 22 * mm, 73 * mm, 30 * mm, 30 * mm],
            repeatRows=1,
        )
        tabla_conceptos.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5FAFF")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#64748B")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#E2E8F0")),
                ]
            )
        )
        elementos.append(tabla_conceptos)
    else:
        elementos.append(Paragraph("No fue posible leer los conceptos del XML.", subtitulo))
    elementos.append(Spacer(1, 6 * mm))

    totales_filas = [["Subtotal", money(doc.subtotal)]]
    if doc.descuento:
        totales_filas.append(["Descuento", money(doc.descuento)])
    if doc.total_impuestos_trasladados:
        totales_filas.append(["Impuestos trasladados", money(doc.total_impuestos_trasladados)])
    if doc.total_impuestos_retenidos:
        totales_filas.append(["Impuestos retenidos", money(doc.total_impuestos_retenidos)])
    totales_filas.append(["Total", money(doc.total)])

    tabla_totales = Table(totales_filas, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    tabla_totales.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 0), (-1, -2), colors.HexColor("#64748B")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, -1), (-1, -1), 11),
                ("TOPPADDING", (0, -1), (-1, -1), 6),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#061A35")),
            ]
        )
    )
    elementos.append(tabla_totales)

    pdf.build(elementos)
    return buffer.getvalue()


def obtener_preferencia_columnas(db: Session, user_id: int, rfc_client_id: int) -> list[str]:
    pref = (
        db.query(ColumnaPreferencia)
        .filter(ColumnaPreferencia.user_id == user_id, ColumnaPreferencia.rfc_client_id == rfc_client_id)
        .first()
    )
    return list(pref.columnas) if pref else []


def guardar_preferencia_columnas(db: Session, user_id: int, rfc_client_id: int, columnas: list[str]) -> list[str]:
    pref = (
        db.query(ColumnaPreferencia)
        .filter(ColumnaPreferencia.user_id == user_id, ColumnaPreferencia.rfc_client_id == rfc_client_id)
        .first()
    )
    if pref is None:
        pref = ColumnaPreferencia(user_id=user_id, rfc_client_id=rfc_client_id, columnas=columnas)
        db.add(pref)
    else:
        pref.columnas = columnas
    db.commit()
    return columnas
