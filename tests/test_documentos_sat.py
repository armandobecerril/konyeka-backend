"""Opinión/Constancia: método HTTP con e.firma, respaldo con navegador y
lectura del sentido desde el PDF (sin tocar el SAT real)."""
import base64
from unittest import mock

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.sat_documentos import portal_http


def _pdf(lineas: list[str]) -> bytes:
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    for linea in lineas:
        c.drawString(40, y, linea)
        y -= 16
    c.save()
    return buf.getvalue()


OPINION_NEGATIVA = _pdf([
    "Opinion del cumplimiento de obligaciones fiscales",
    "Nombre, denominacion o razon social Sentido",
    "INTELLIGENCE SOFTWARE CONSULTING S DE RL DE CV NEGATIVO",
    "RFC Folio",
    "ISC091120KE1 26NN9181953",
])
OPINION_POSITIVA = _pdf([
    "Opinion del cumplimiento de obligaciones fiscales",
    "Nombre, denominacion o razon social Sentido",
    "EMPRESA POSITIVA SA DE CV POSITIVO",
    "RFC Folio",
    "EPO010101AB1 26NN0000001",
])
CONSTANCIA = _pdf(["CONSTANCIA DE SITUACION FISCAL", "RFC: ISC091120KE1", "Regimen General de Ley"])


@pytest.mark.parametrize("pdf,esperado", [(OPINION_NEGATIVA, "negativo"), (OPINION_POSITIVA, "positivo")])
def test_sentido_opinion(pdf, esperado):
    assert portal_http.sentido_opinion(pdf) == esperado


def test_sentido_en_suspenso_y_no_inscrito():
    assert portal_http.sentido_opinion(_pdf(["Sentido", "ACME SA EN SUSPENSO"])) == "en_suspenso"
    assert portal_http.sentido_opinion(_pdf(["Sentido", "ACME SA NO INSCRITO"])) == "no_inscrito"


def test_valida_rfc_y_tipo():
    portal_http.validar_pdf(OPINION_NEGATIVA, rfc="ISC091120KE1", tipo="opinion")
    portal_http.validar_pdf(CONSTANCIA, rfc="ISC091120KE1", tipo="constancia")
    with pytest.raises(portal_http.DocumentoInvalido):
        portal_http.validar_pdf(OPINION_NEGATIVA, rfc="XAXX010101000", tipo="opinion")
    with pytest.raises(portal_http.DocumentoInvalido):
        portal_http.validar_pdf(b"<html>Sesion expirada</html>", rfc="ISC091120KE1", tipo="constancia")
    with pytest.raises(portal_http.DocumentoInvalido):
        portal_http.validar_pdf(OPINION_NEGATIVA, rfc="ISC091120KE1", tipo="constancia")


def test_descarga_http_usa_satcfdi():
    signer = object()
    with mock.patch("satcfdi.portal.SATPortalOpinionCumplimiento.generar_opinion_cumplimiento", return_value=OPINION_NEGATIVA):
        pdf, sentido = portal_http.descargar_opinion(signer, rfc="ISC091120KE1")
    assert pdf == OPINION_NEGATIVA and sentido == "negativo"


FORM_CONSTANCIA = """<html><form id="formReimpAcuse" name="formReimpAcuse" method="post"
 action="/PTSC/IdcSiat/autc/ReimpresionTramite/ConsultaTramite.jsf">
<input type="hidden" name="javax.faces.ViewState" value="VS123"/><input name="formReimpAcuse:folio" value=""/>
<button id="formReimpAcuse:j_idt77" name="formReimpAcuse:j_idt77" type="submit"><span>Generar Constancia</span></button>
</form></html>"""


class _Resp:
    def __init__(self, url, body, ctype="text/html"):
        self.url = url
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("latin1")
        self.status_code = 200
        self.headers = {"content-type": ctype}


def test_constancia_lee_el_boton_del_html_y_baja_el_pdf():
    enviados = {}

    def get(self, url, **kw):
        if "SessionBroker" in url:
            return _Resp("https://rfcampc.siat.sat.gob.mx/PTSC/IdcSiat/autc/ReimpresionTramite/ConsultaTramite.jsf", FORM_CONSTANCIA)
        return _Resp(url, CONSTANCIA, "application/pdf")

    def post(self, url, data=None, **kw):
        enviados.update(data)
        return _Resp(url, "<partial-response/>", "text/xml")

    with mock.patch("satcfdi.portal.SATPortalConstancia.login", return_value=_Resp("https://login", b"ok")), \
            mock.patch("satcfdi.portal.SATPortalConstancia.get", get), \
            mock.patch("satcfdi.portal.SATPortalConstancia.post", post):
        pasos = []
        assert portal_http.descargar_constancia(object(), rfc="ISC091120KE1", pasos=pasos) == CONSTANCIA
    assert enviados["javax.faces.source"] == "formReimpAcuse:j_idt77"
    assert enviados["javax.faces.ViewState"] == "VS123"
    assert [p[0] for p in pasos] == ["login", "session_broker", "generar_constancia", "pdf_0"]


def test_constancia_html_en_vez_de_pdf_da_error_claro():
    def get(self, url, **kw):
        if "SessionBroker" in url:
            return _Resp("https://rfcampc.siat.sat.gob.mx/x.jsf", FORM_CONSTANCIA)
        return _Resp(url, "<html>Sesion expirada</html>")

    with mock.patch("satcfdi.portal.SATPortalConstancia.login", return_value=_Resp("https://login", b"ok")), \
            mock.patch("satcfdi.portal.SATPortalConstancia.get", get), \
            mock.patch("satcfdi.portal.SATPortalConstancia.post", lambda self, url, **kw: _Resp(url, "<partial-response/>")):
        with pytest.raises(portal_http.DocumentoInvalido, match="Sesion expirada"):
            portal_http.descargar_constancia(object(), rfc="ISC091120KE1")


def test_iframe_del_spa_de_opinion():
    """El SPA del SAT incrusta la opinión como data:application/pdf;base64 (verificado el 24-oct)."""
    from app.sat_documentos.rpa import _pdf_opinion_desde_iframe
    from playwright.sync_api import sync_playwright

    src = "data:application/pdf;base64," + base64.b64encode(OPINION_NEGATIVA).decode()
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium de Playwright no instalado: {exc}")
        page = b.new_page()
        page.set_content(f'<div>Cargando…</div><iframe title="pdfReporteOpinion" type="application/pdf" src="{src}"></iframe>')
        assert _pdf_opinion_desde_iframe(page, timeout_ms=5000) == OPINION_NEGATIVA
        page.set_content("<div>sin reporte</div>")
        assert _pdf_opinion_desde_iframe(page, timeout_ms=500) is None
        b.close()
