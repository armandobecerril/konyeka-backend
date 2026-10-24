"""Reporte de combustibles por cliente y meses, insumo de la DIOT.

Recorre los CFDIs marcados como de combustible (tiene_complemento_combustible)
en los meses elegidos, extrae cada carga (parser.py) y arma:
- el detalle carga por carga (estación, litros, combustible, importes),
- el consolidado por RFC de la estación (que es el proveedor real del
  combustible para la DIOT, aunque la factura la emita el monedero),
- totales por tipo de combustible y generales.
"""
import csv
import io
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.combustibles.parser import extraer_cargas
from app.core.storage import get_xml_storage
from app.sat_downloads.models import CfdiDocument
from app.sat_downloads.service import filtro_meses, parsear_meses

CERO = Decimal("0")


def _cfdis_combustible(db: Session, rfc_client_id: int, meses: str | None, tipo: str | None):
    query = db.query(CfdiDocument).filter(
        CfdiDocument.rfc_client_id == rfc_client_id,
        CfdiDocument.tiene_complemento_combustible.is_(True),
        CfdiDocument.tipo_comprobante.in_(("I", "E")),
    )
    if tipo:
        query = query.filter(CfdiDocument.tipo == tipo)
    lista = parsear_meses(meses)
    if lista:
        query = query.filter(filtro_meses(CfdiDocument.fecha_emision, lista))
    return query.order_by(CfdiDocument.fecha_emision.asc()).all()


def reporte_combustibles(db: Session, rfc_client_id: int, *, meses: str | None, tipo: str | None = "recibido") -> dict:
    storage = get_xml_storage()
    cargas: list[dict] = []
    errores: list[str] = []
    for doc in _cfdis_combustible(db, rfc_client_id, meses, tipo):
        try:
            xml_bytes = storage.read(doc.storage_path)
        except Exception:  # noqa: BLE001
            errores.append(doc.uuid)
            continue
        signo = Decimal("-1") if doc.tipo_comprobante == "E" else Decimal("1")
        for carga in extraer_cargas(xml_bytes):
            fila = carga.as_dict()
            fila.pop("traslados", None)
            fila["importe"] = carga.importe * signo
            fila["iva"] = carga.iva * signo
            fila["ieps"] = carga.ieps * signo
            fila["litros"] = (carga.litros * signo) if carga.litros is not None else None
            fila.update(
                {
                    "cfdi_id": doc.id,
                    "uuid": doc.uuid,
                    "fecha_cfdi": doc.fecha_emision,
                    "emisor_rfc": doc.emisor_rfc,
                    "emisor_nombre": doc.emisor_nombre,
                    "tipo_comprobante": doc.tipo_comprobante,
                    "mes": doc.fecha_emision.strftime("%Y-%m"),
                }
            )
            # Una factura directa de gasolinera no trae RFC de estación aparte:
            # la estación es el emisor.
            if not fila.get("rfc_estacion"):
                fila["rfc_estacion"] = doc.emisor_rfc
            cargas.append(fila)

    por_estacion: dict[str, dict] = {}
    por_combustible: dict[str, dict] = defaultdict(lambda: {"litros": CERO, "importe": CERO, "cargas": 0})
    por_mes: dict[str, dict] = defaultdict(lambda: {"litros": CERO, "importe": CERO, "iva": CERO, "ieps": CERO, "cargas": 0})
    totales = {"litros": CERO, "importe": CERO, "iva": CERO, "ieps": CERO, "cargas": 0, "cfdis": len({c["cfdi_id"] for c in cargas})}

    for c in cargas:
        rfc = c["rfc_estacion"] or "SIN RFC"
        est = por_estacion.setdefault(
            rfc,
            {
                "rfc_estacion": rfc,
                "claves_estacion": set(),
                "litros": CERO,
                "importe": CERO,
                "iva": CERO,
                "ieps": CERO,
                "cargas": 0,
            },
        )
        if c.get("clave_estacion"):
            est["claves_estacion"].add(c["clave_estacion"])
        litros = c["litros"] or CERO
        for destino in (est, totales):
            destino["litros"] += litros
            destino["importe"] += c["importe"]
            destino["iva"] += c["iva"]
            destino["ieps"] += c["ieps"]
            destino["cargas"] += 1
        comb = por_combustible[c.get("nombre_combustible") or c.get("tipo_combustible") or "Sin especificar"]
        comb["litros"] += litros
        comb["importe"] += c["importe"]
        comb["cargas"] += 1
        m = por_mes[c["mes"]]
        m["litros"] += litros
        m["importe"] += c["importe"]
        m["iva"] += c["iva"]
        m["ieps"] += c["ieps"]
        m["cargas"] += 1

    estaciones = []
    for est in sorted(por_estacion.values(), key=lambda e: e["importe"], reverse=True):
        est["claves_estacion"] = sorted(est["claves_estacion"])
        # Valor de los actos pagados (base del IVA) = importe sin IVA. En el
        # complemento, Importe ya viene sin IVA (el IVA es su <Traslado>).
        est["base_iva_16"] = est["importe"] if est["iva"] else CERO
        est["base_iva_0"] = CERO if est["iva"] else est["importe"]
        estaciones.append(est)

    return {
        "cargas": cargas,
        "por_estacion": estaciones,
        "por_combustible": [{"combustible": k, **v} for k, v in sorted(por_combustible.items())],
        "por_mes": [{"mes": k, **v} for k, v in sorted(por_mes.items())],
        "totales": totales,
        "errores": errores,
    }


def _fmt(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    return str(valor)


def exportar_csv_detalle(reporte: dict) -> str:
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(
        [
            "Mes", "Fecha carga", "UUID", "Emisor CFDI", "RFC emisor CFDI", "RFC estación", "Clave estación",
            "Combustible", "Litros", "Valor unitario", "Importe", "IVA", "IEPS", "Folio operación", "Origen",
        ]
    )
    for c in reporte["cargas"]:
        w.writerow(
            [
                c["mes"], c.get("fecha") or "", c["uuid"], c.get("emisor_nombre") or "", c["emisor_rfc"],
                c.get("rfc_estacion") or "", c.get("clave_estacion") or "", c.get("nombre_combustible") or "",
                str(c.get("litros") or ""), str(c.get("valor_unitario") or ""), _fmt(c["importe"]), _fmt(c["iva"]),
                _fmt(c["ieps"]), c.get("folio_operacion") or "", c["origen"],
            ]
        )
    return "﻿" + salida.getvalue()  # BOM para que Excel lea bien los acentos


def exportar_csv_diot(reporte: dict) -> str:
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(
        ["RFC proveedor (estación)", "Tipo tercero", "Tipo operación", "Valor actos pagados 16%", "IVA pagado 16%",
         "Valor actos pagados 0%", "IEPS", "Litros", "Cargas"]
    )
    for e in reporte["por_estacion"]:
        w.writerow(
            [e["rfc_estacion"], "04", "85", _fmt(e["base_iva_16"]), _fmt(e["iva"]), _fmt(e["base_iva_0"]),
             _fmt(e["ieps"]), _fmt(e["litros"]), e["cargas"]]
        )
    return "﻿" + salida.getvalue()


def exportar_txt_diot(reporte: dict) -> str:
    """BORRADOR de carga masiva: un renglón por RFC de estación, separado por
    pipes (04 = proveedor nacional, 85 = otros). El layout de la DIOT cambió
    en 2025; antes de subirlo al SAT hay que validarlo contra el layout
    vigente con el contador."""
    lineas = []
    for e in reporte["por_estacion"]:
        if e["rfc_estacion"] == "SIN RFC":
            continue
        lineas.append(
            "|".join(
                [
                    "04",
                    "85",
                    e["rfc_estacion"],
                    "",
                    "",
                    "",
                    "",
                    str(int(e["base_iva_16"].quantize(Decimal("1")))) if e["base_iva_16"] else "",
                    str(int(e["iva"].quantize(Decimal("1")))) if e["iva"] else "",
                    str(int(e["base_iva_0"].quantize(Decimal("1")))) if e["base_iva_0"] else "",
                ]
            )
            + "|"
        )
    return "\r\n".join(lineas) + ("\r\n" if lineas else "")
