from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.bancos import matching
from app.bancos.parser import ErrorLectura, leer_estado_cuenta, parse_fecha, parse_monto

F = Path("tests/fixtures/bancos")


@pytest.mark.parametrize(
    "texto,esperado",
    [("$1,234.56", "1234.56"), ("(1,234.56)", "-1234.56"), ("1234.56-", "-1234.56"), ("-50", "-50.00"),
     ("1.234,56", "1234.56"), ("", None), ("—", None), (250, "250.00")],
)
def test_parse_monto(texto, esperado):
    r = parse_monto(texto)
    assert (str(r) if r is not None else None) == esperado


@pytest.mark.parametrize(
    "texto,esperado",
    [("03/03/2022", date(2022, 3, 3)), ("2022-03-07", date(2022, 3, 7)), ("07-MAR-22", date(2022, 3, 7)),
     ("05/ENE", date(2026, 1, 5)), ("31/DIC", date(2025, 12, 31)), ("12/25/2026", date(2026, 12, 25)),
     ("basura", None)],
)
def test_parse_fecha(texto, esperado):
    assert parse_fecha(texto, 2026, 1) == esperado


def test_csv_bbva():
    r = leer_estado_cuenta((F / "bbva_2022-03.csv").read_bytes(), "bbva_2022-03.csv", "2022-03")
    assert r.banco_detectado == "BBVA"
    assert len(r.movimientos) == 11  # sin saldo inicial ni renglón de totales
    assert sum(m.cargo or 0 for m in r.movimientos) == Decimal("34231.14")
    assert r.saldo_inicial == Decimal("100000.00")


def test_xlsx_importe_con_signo():
    r = leer_estado_cuenta((F / "santander_2022-03.xlsx").read_bytes(), "mov.xlsx", "2022-03")
    assert [m.tipo for m in r.movimientos] == ["cargo", "cargo", "abono"]


def test_pdf_generico_con_saldo_corrido():
    r = leer_estado_cuenta((F / "banorte_2022-03.pdf").read_bytes(), "edo.pdf", "2022-03")
    tipos = [(m.tipo, m.monto) for m in r.movimientos]
    assert tipos == [("cargo", Decimal("1750.44")), ("cargo", Decimal("1653.60")),
                     ("abono", Decimal("5000.00")), ("cargo", Decimal("3500.00"))]
    assert "SUA" in r.movimientos[1].descripcion  # renglón de continuación
    assert r.advertencias


def test_archivo_sin_encabezados():
    with pytest.raises(ErrorLectura):
        leer_estado_cuenta(b"hola,mundo\n1,2\n", "x.csv", "2022-03")


def _cand(i, tipo, monto, fecha, nombre="PROVEEDOR XYZ SA DE CV", rfc="XYZ010101AAA"):
    return matching.Candidato(i, tipo, "cargo", fecha, Decimal(monto), rfc, nombre)


def test_matching_prefiere_importe_exacto_y_es_1_a_1():
    movs = [matching.Movimiento(1, date(2022, 3, 3), "cargo", Decimal("546.25"), "PAGO INFONAVIT"),
            matching.Movimiento(2, date(2022, 3, 3), "cargo", Decimal("546.81"), "PAGO INFONAVIT")]
    nombre = "Instituto del Fondo Nacional de la Vivienda para los Trabajadores"
    cands = [_cand(10, "pue", "546.81", date(2022, 3, 3), nombre), _cand(11, "pue", "546.25", date(2022, 3, 3), nombre)]
    parejas = {p.movimiento_id: p.cfdi_id for p in matching.asignar(movs, cands)}
    assert parejas == {1: 11, 2: 10}


def test_matching_rep_por_fecha_de_pago_y_sigla():
    mov = matching.Movimiento(1, date(2022, 3, 28), "cargo", Decimal("1569.44"), "CARGO DOMICILIADO TOTALPLAY")
    rep = _cand(5, "rep", "1569.44", date(2022, 3, 28), "TOTAL PLAY TELECOMUNICACIONES SAPI DE CV", "TPT890516JP5")
    [p] = matching.asignar([mov], [rep])
    assert p.automatico and p.tipo_vinculo == "rep"


def test_matching_no_cruza_sentidos_ni_montos_lejanos():
    mov = matching.Movimiento(1, date(2022, 3, 3), "abono", Decimal("100"), "DEPOSITO")
    assert matching.asignar([mov], [_cand(1, "pue", "100", date(2022, 3, 3))]) == []
    mov = matching.Movimiento(1, date(2022, 3, 3), "cargo", Decimal("100"), "PAGO")
    assert matching.asignar([mov], [_cand(1, "pue", "150", date(2022, 3, 3))]) == []


def test_matching_respeta_rechazos():
    mov = matching.Movimiento(1, date(2022, 3, 3), "cargo", Decimal("100"), "PAGO XYZ010101AAA")
    assert matching.asignar([mov], [_cand(1, "pue", "100", date(2022, 3, 3))], excluir={(1, 1)}) == []


def test_siglas_y_alias():
    assert "IMSS" in matching.alias_de("INSTITUTO MEXICANO DEL SEGURO SOCIAL")
    assert "INFONAVIT" in matching.alias_de("Instituto del Fondo Nacional de la Vivienda para los Trabajadores")
