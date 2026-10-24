from decimal import Decimal
from pathlib import Path

from app.combustibles.parser import extraer_cargas


def test_estado_cuenta_combustible_12():
    cargas = extraer_cargas(Path("tests/fixtures/combustible_ecc12.xml").read_bytes())
    assert len(cargas) == 2
    assert cargas[0].rfc_estacion == "GAS010101AB1"
    assert cargas[0].litros == Decimal("100.00")
    assert cargas[0].iva == Decimal("344.83")
    assert {c.origen for c in cargas} == {"estado_cuenta"}


def test_consumo_de_combustibles_11_camelcase():
    cargas = extraer_cargas(Path("tests/fixtures/combustible_consumo11.xml").read_bytes())
    assert len(cargas) == 1
    c = cargas[0]
    assert (c.origen, c.rfc_estacion, c.litros, c.importe, c.iva) == (
        "consumo", "GAS010101AB1", Decimal("45.5"), Decimal("1000"), Decimal("160"))


def test_factura_directa_de_gasolinera():
    xml = b"""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Fecha="2026-09-01T10:00:00">
      <cfdi:Emisor Rfc="GAS010101AB1"/><cfdi:Conceptos>
      <cfdi:Concepto ClaveProdServ="15101505" Cantidad="40" ValorUnitario="22" Importe="880">
        <cfdi:Impuestos><cfdi:Traslados><cfdi:Traslado Impuesto="002" Importe="140.80"/></cfdi:Traslados></cfdi:Impuestos>
      </cfdi:Concepto><cfdi:Concepto ClaveProdServ="15121501" Cantidad="1" Importe="150"/></cfdi:Conceptos></cfdi:Comprobante>"""
    cargas = extraer_cargas(xml)
    assert len(cargas) == 1
    assert cargas[0].nombre_combustible == "Diésel"
    assert cargas[0].iva == Decimal("140.80")


def test_xml_no_combustible():
    assert extraer_cargas(b"<a><b/></a>") == []
