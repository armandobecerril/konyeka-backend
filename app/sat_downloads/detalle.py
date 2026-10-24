"""Detalle de un CFDI leído del XML bajo demanda (no se guarda en BD): lo
que el contador ve en el panel inferior de MiAdmin al seleccionar un
renglón -- Conceptos, Impuestos, Pagos (documentos relacionados de un
complemento de pago), CFDI relacionados y Combustible."""
from decimal import Decimal


def _codigo(valor) -> str | None:
    """Code de satcfdi -> "02 - Sí objeto de impuesto" (como lo muestra MiAdmin)."""
    if valor is None:
        return None
    if hasattr(valor, "code"):
        descripcion = getattr(valor, "description", None)
        return f"{valor.code} - {descripcion}" if descripcion else str(valor.code)
    return str(valor)


def _solo_codigo(valor) -> str | None:
    if valor is None:
        return None
    if hasattr(valor, "code"):
        return str(valor.code)
    return str(valor)


def _num(valor) -> str | None:
    if valor is None:
        return None
    return str(valor)


def _lista(valor) -> list:
    if valor is None:
        return []
    if isinstance(valor, dict):
        # satcfdi entrega Traslados/Retenciones como dict con llave compuesta.
        valores = list(valor.values())
        if valores and all(hasattr(v, "get") for v in valores):
            return valores
        return [valor]
    return valor if isinstance(valor, list) else [valor]


def _impuestos(nodo, nivel: str, concepto_idx: int | None = None, descripcion: str | None = None) -> list[dict]:
    filas: list[dict] = []
    if not nodo:
        return filas
    for clase, llave in (("Traslado", "Traslados"), ("Retención", "Retenciones")):
        for imp in _lista(nodo.get(llave)):
            if not hasattr(imp, "get"):
                continue
            filas.append(
                {
                    "nivel": nivel,
                    "concepto": concepto_idx,
                    "descripcion_concepto": descripcion,
                    "clase": clase,
                    "impuesto": _codigo(imp.get("Impuesto")),
                    "tipo_factor": _solo_codigo(imp.get("TipoFactor")),
                    "tasa_o_cuota": _num(imp.get("TasaOCuota")),
                    "base": _num(imp.get("Base")),
                    "importe": _num(imp.get("Importe")),
                }
            )
    return filas


def extraer_detalle(xml_bytes: bytes) -> dict:
    from satcfdi.cfdi import CFDI

    from app.combustibles.parser import extraer_cargas

    cfdi = CFDI.from_string(xml_bytes)

    conceptos: list[dict] = []
    impuestos: list[dict] = []
    for idx, concepto in enumerate(cfdi.get("Conceptos") or [], start=1):
        descripcion = concepto.get("Descripcion")
        conceptos.append(
            {
                "num": idx,
                "clave_prod_serv": _solo_codigo(concepto.get("ClaveProdServ")),
                "no_identificacion": concepto.get("NoIdentificacion"),
                "cantidad": _num(concepto.get("Cantidad")),
                "clave_unidad": _solo_codigo(concepto.get("ClaveUnidad")),
                "unidad": concepto.get("Unidad"),
                "descripcion": descripcion,
                "valor_unitario": _num(concepto.get("ValorUnitario")),
                "importe": _num(concepto.get("Importe")),
                "descuento": _num(concepto.get("Descuento")),
                "objeto_imp": _codigo(concepto.get("ObjetoImp")),
            }
        )
        impuestos.extend(_impuestos(concepto.get("Impuestos"), "concepto", idx, descripcion))
    impuestos.extend(_impuestos(cfdi.get("Impuestos"), "comprobante"))

    pagos: list[dict] = []
    complemento = cfdi.get("Complemento") or {}
    nodo_pagos = complemento.get("Pagos")
    if nodo_pagos:
        for num_pago, pago in enumerate(_lista(nodo_pagos.get("Pago")), start=1):
            doctos = _lista(pago.get("DoctoRelacionado"))
            base = {
                "num_pago": num_pago,
                "fecha_pago": _num(pago.get("FechaPago")),
                "forma_pago": _codigo(pago.get("FormaDePagoP")),
                "moneda_p": _solo_codigo(pago.get("MonedaP")),
                "monto": _num(pago.get("Monto")),
                "num_operacion": pago.get("NumOperacion"),
                "rfc_banco_ordenante": pago.get("RfcEmisorCtaOrd"),
                "cuenta_ordenante": pago.get("CtaOrdenante"),
                "rfc_banco_beneficiario": pago.get("RfcEmisorCtaBen"),
                "cuenta_beneficiario": pago.get("CtaBeneficiario"),
            }
            if not doctos:
                pagos.append({**base, "id_documento": None})
            for docto in doctos:
                pagos.append(
                    {
                        **base,
                        "id_documento": str(docto.get("IdDocumento") or "").upper() or None,
                        "serie": docto.get("Serie"),
                        "folio": docto.get("Folio"),
                        "moneda_dr": _solo_codigo(docto.get("MonedaDR")),
                        "num_parcialidad": _num(docto.get("NumParcialidad")),
                        "imp_saldo_ant": _num(docto.get("ImpSaldoAnt")),
                        "imp_pagado": _num(docto.get("ImpPagado")),
                        "imp_saldo_insoluto": _num(docto.get("ImpSaldoInsoluto")),
                    }
                )

    relacionados: list[dict] = []
    for grupo in _lista(cfdi.get("CfdiRelacionados")):
        if not hasattr(grupo, "get"):
            continue
        tipo_relacion = _codigo(grupo.get("TipoRelacion"))
        uuids = grupo.get("CfdiRelacionado") or []
        if not isinstance(uuids, list):
            uuids = [uuids]
        for rel in uuids:
            uuid = rel.get("UUID") if hasattr(rel, "get") else rel
            relacionados.append({"tipo_relacion": tipo_relacion, "uuid": str(uuid).upper()})

    nomina = None
    nodo_nomina = complemento.get("Nomina")
    if nodo_nomina:
        nodos = nodo_nomina if isinstance(nodo_nomina, list) else [nodo_nomina]
        n = nodos[0]
        receptor = n.get("Receptor") or {}
        nomina = {
            "version": n.get("Version"),
            "tipo_nomina": _codigo(n.get("TipoNomina")),
            "fecha_pago": _num(n.get("FechaPago")),
            "fecha_inicial_pago": _num(n.get("FechaInicialPago")),
            "fecha_final_pago": _num(n.get("FechaFinalPago")),
            "num_dias_pagados": _num(n.get("NumDiasPagados")),
            "total_percepciones": _num(n.get("TotalPercepciones")),
            "total_deducciones": _num(n.get("TotalDeducciones")),
            "total_otros_pagos": _num(n.get("TotalOtrosPagos")),
            "num_empleado": receptor.get("NumEmpleado") if hasattr(receptor, "get") else None,
            "curp": receptor.get("Curp") if hasattr(receptor, "get") else None,
        }

    combustible = [c.as_dict() for c in extraer_cargas(xml_bytes)]
    for c in combustible:
        for llave, valor in list(c.items()):
            if isinstance(valor, Decimal):
                c[llave] = str(valor)

    return {
        "conceptos": conceptos,
        "impuestos": impuestos,
        "pagos": pagos,
        "relacionados": relacionados,
        "nomina": nomina,
        "combustible": combustible,
        "complementos": list(complemento.keys()),
    }
