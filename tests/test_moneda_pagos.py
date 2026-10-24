"""A1: la moneda de un CFDI de Pago sale de MonedaP/MonedaDR, nunca del 'XXX' del encabezado."""
from pathlib import Path

import pytest
from satcfdi.cfdi import CFDI

from app.sat_downloads.service import moneda_comprobante, monto_pagos

BLOB = Path("data/blob")


def _pagos():
    return [p for p in BLOB.rglob("*.xml") if b'TipoDeComprobante="P"' in p.read_bytes()]


@pytest.mark.skipif(not _pagos(), reason="sin XML de pagos locales")
def test_pagos_xxx_se_leen_como_mxn():
    for ruta in _pagos():
        xml = ruta.read_bytes()
        cfdi = CFDI.from_string(xml)
        assert b'Moneda="XXX"' in xml
        assert moneda_comprobante(cfdi) == "MXN"
        assert monto_pagos(cfdi) is not None and monto_pagos(cfdi) > 0


def test_factura_combustible_conserva_su_moneda():
    cfdi = CFDI.from_string(Path("tests/fixtures/combustible_ecc12.xml").read_bytes())
    assert moneda_comprobante(cfdi) == "MXN"
    assert monto_pagos(cfdi) is None
