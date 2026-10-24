"""Extractor de los complementos de combustible del SAT.

Lee directo del XML con lxml, sin depender de que satcfdi reconozca el
namespace exacto (hay versiones 1.1 y 1.2 en circulación y varios emisores
mezclan mayúsculas/minúsculas en los atributos):

- EstadoDeCuentaCombustible 1.1 / 1.2 (ecc11 / ecc12): lo emiten los
  monederos electrónicos de flotillas (Edenred, Efectivale, Sodexo/Pluxee,
  Multisistemas...). Cada <ConceptoEstadoDeCuentaCombustible> es una carga
  en una estación: Rfc y ClaveEstacion de la gasolinera, TipoCombustible,
  Cantidad (litros), ValorUnitario, Importe y sus <Traslado> (IVA, IEPS).
- ConsumoDeCombustibles 1.1 (consumodecombustibles11): el equivalente para
  consumo directo; atributos en camelCase y los impuestos en <Determinado>.
- Factura directa de gasolinera (sin complemento): conceptos con
  ClaveProdServ 151015xx (gasolinas y diésel); la estación es el emisor.

El resultado alimenta el reporte de combustibles y el insumo de la DIOT
(agrupado por RFC de la estación, que es el proveedor real del combustible).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from lxml import etree

# Catálogo c_TipoCombustible del complemento (los más comunes).
TIPOS_COMBUSTIBLE = {
    "1": "Gasolina menor a 91 octanos",
    "2": "Gasolina mayor o igual a 91 octanos",
    "3": "Diésel",
    "4": "Diésel marino",
    "5": "Combustóleo",
    "6": "Gas natural",
    "7": "Turbosina",
    "8": "Gas LP",
    "9": "Gasavión",
    "10": "Otros",
    "11": "Gasolina sin plomo",
    "12": "Etanol",
    "13": "Biodiésel",
}

# ClaveProdServ (c_ClaveProdServ) de combustibles para vehículos.
CLAVES_PROD_SERV_COMBUSTIBLE = {
    "15101505": "Diésel",
    "15101506": "Gasolina",
    "15101514": "Gasolina regular menor a 91 octanos",
    "15101515": "Gasolina premium mayor o igual a 91 octanos",
    "15101516": "Combustible de turbina",
    "15111510": "Gas LP",
    "15111512": "Gas natural",
}


@dataclass
class CargaCombustible:
    origen: str  # "estado_cuenta" | "consumo" | "factura"
    fecha: str | None
    rfc_estacion: str | None
    clave_estacion: str | None
    tipo_combustible: str | None
    nombre_combustible: str | None
    litros: Decimal | None
    valor_unitario: Decimal | None
    importe: Decimal
    iva: Decimal = Decimal("0")
    ieps: Decimal = Decimal("0")
    folio_operacion: str | None = None
    identificador: str | None = None
    traslados: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(el, *nombres: str) -> str | None:
    """Atributo sin importar mayúsculas/minúsculas (Rfc / rfc, Cantidad / cantidad)."""
    if el is None:
        return None
    mapa = {k.lower(): v for k, v in el.attrib.items()}
    for nombre in nombres:
        valor = mapa.get(nombre.lower())
        if valor not in (None, ""):
            return valor.strip()
    return None


def _dec(valor: str | None) -> Decimal | None:
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _hijos(el, nombre_local: str) -> list:
    return [h for h in el.iter() if _local(h.tag).lower() == nombre_local.lower()]


def _clasificar_impuesto(nombre: str | None) -> str:
    if not nombre:
        return "otro"
    n = nombre.strip().upper()
    if n in ("IVA", "002"):
        return "iva"
    if n in ("IEPS", "003"):
        return "ieps"
    return "otro"


def _cargar_traslados(concepto_el, nodo_impuesto: str) -> tuple[list[dict], Decimal, Decimal]:
    traslados = []
    iva = Decimal("0")
    ieps = Decimal("0")
    for t in _hijos(concepto_el, nodo_impuesto):
        impuesto = _attr(t, "Impuesto")
        importe = _dec(_attr(t, "Importe")) or Decimal("0")
        traslados.append(
            {"impuesto": impuesto, "tasa_o_cuota": _attr(t, "TasaOCuota", "TasaoCuota"), "importe": str(importe)}
        )
        clase = _clasificar_impuesto(impuesto)
        if clase == "iva":
            iva += importe
        elif clase == "ieps":
            ieps += importe
    return traslados, iva, ieps


def _parse_root(xml_bytes: bytes):
    parser = etree.XMLParser(recover=True, remove_blank_text=True, resolve_entities=False, no_network=True)
    return etree.fromstring(xml_bytes.lstrip(b"\xef\xbb\xbf"), parser=parser)


def extraer_cargas(xml_bytes: bytes) -> list[CargaCombustible]:
    """Todas las cargas de combustible que trae un CFDI (vacía si no es de combustible)."""
    try:
        root = _parse_root(xml_bytes)
    except etree.XMLSyntaxError:
        return []
    if root is None:
        return []

    cargas: list[CargaCombustible] = []

    # 1) EstadoDeCuentaCombustible (monederos de flotilla)
    for concepto in _hijos(root, "ConceptoEstadoDeCuentaCombustible"):
        traslados, iva, ieps = _cargar_traslados(concepto, "Traslado")
        tipo = _attr(concepto, "TipoCombustible")
        cargas.append(
            CargaCombustible(
                origen="estado_cuenta",
                fecha=_attr(concepto, "Fecha"),
                rfc_estacion=(_attr(concepto, "Rfc") or "").upper() or None,
                clave_estacion=_attr(concepto, "ClaveEstacion"),
                tipo_combustible=tipo,
                nombre_combustible=_attr(concepto, "NombreCombustible") or TIPOS_COMBUSTIBLE.get(tipo or ""),
                litros=_dec(_attr(concepto, "Cantidad")),
                valor_unitario=_dec(_attr(concepto, "ValorUnitario")),
                importe=_dec(_attr(concepto, "Importe")) or Decimal("0"),
                iva=iva,
                ieps=ieps,
                folio_operacion=_attr(concepto, "FolioOperacion"),
                identificador=_attr(concepto, "Identificador"),
                traslados=traslados,
            )
        )

    # 2) ConsumoDeCombustibles (consumo directo)
    for concepto in _hijos(root, "ConceptoConsumoDeCombustibles"):
        traslados, iva, ieps = _cargar_traslados(concepto, "Determinado")
        tipo = _attr(concepto, "tipoCombustible")
        cargas.append(
            CargaCombustible(
                origen="consumo",
                fecha=_attr(concepto, "fecha"),
                rfc_estacion=(_attr(concepto, "rfc") or "").upper() or None,
                clave_estacion=_attr(concepto, "claveEstacion"),
                tipo_combustible=tipo,
                nombre_combustible=_attr(concepto, "nombreCombustible") or TIPOS_COMBUSTIBLE.get(tipo or ""),
                litros=_dec(_attr(concepto, "cantidad")),
                valor_unitario=_dec(_attr(concepto, "valorUnitario")),
                importe=_dec(_attr(concepto, "importe")) or Decimal("0"),
                iva=iva,
                ieps=ieps,
                folio_operacion=_attr(concepto, "folioOperacion"),
                identificador=_attr(concepto, "identificador"),
                traslados=traslados,
            )
        )

    if cargas:
        return cargas

    # 3) Factura directa de la gasolinera: conceptos 151015xx / gas.
    emisor = next(iter(_hijos(root, "Emisor")), None)
    rfc_emisor = (_attr(emisor, "Rfc") or "").upper() or None
    fecha = _attr(root, "Fecha")
    for concepto in _hijos(root, "Concepto"):
        clave = _attr(concepto, "ClaveProdServ")
        if clave not in CLAVES_PROD_SERV_COMBUSTIBLE:
            continue
        traslados, iva, ieps = _cargar_traslados(concepto, "Traslado")
        cargas.append(
            CargaCombustible(
                origen="factura",
                fecha=fecha,
                rfc_estacion=rfc_emisor,
                clave_estacion=None,
                tipo_combustible=clave,
                nombre_combustible=CLAVES_PROD_SERV_COMBUSTIBLE[clave],
                litros=_dec(_attr(concepto, "Cantidad")),
                valor_unitario=_dec(_attr(concepto, "ValorUnitario")),
                importe=_dec(_attr(concepto, "Importe")) or Decimal("0"),
                iva=iva,
                ieps=ieps,
                folio_operacion=_attr(concepto, "NoIdentificacion"),
                traslados=traslados,
            )
        )
    return cargas
