"""Descarga de Opinión de Cumplimiento (32-D) y Constancia de Situación Fiscal
por HTTP directo, autenticando con la e.firma (FIEL) -- sin navegador y sin
CAPTCHA.

Usa los clientes de portal de satcfdi (la misma librería que ya firma la
Descarga Masiva de CFDIs): el login con e.firma del SAT no pide CAPTCHA
porque el portal recibe un reto ("guid") que se firma con la llave privada,
exactamente como lo hace el applet del navegador. Es el método principal;
el RPA con Playwright (rpa.py) queda solo como respaldo.

Verificado contra el portal real (corrida local del 23-oct-2026 con la
e.firma de ISC091120KE1): el login con e.firma funcionó y el SAT entregó la
Opinión en PDF (con el sentido "NEGATIVO" impreso en la primera página).
"""
from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger(__name__)

# Valores posibles del "Sentido" de la Opinión 32-D -> código guardado en
# documentos_sat.resultado (VARCHAR(20)). El orden importa: "INSCRITO SIN
# OBLIGACIONES" y "NO INSCRITO" antes de cualquier coincidencia más corta.
SENTIDOS = {
    "INSCRITO SIN OBLIGACIONES": "sin_obligaciones",
    "NO INSCRITO": "no_inscrito",
    "EN SUSPENSO": "en_suspenso",
    "NEGATIVO": "negativo",
    "POSITIVO": "positivo",
}


class DocumentoInvalido(Exception):
    """El SAT respondió, pero lo que regresó no es el documento esperado."""


def es_pdf(contenido: bytes | None) -> bool:
    return bool(contenido) and contenido.lstrip()[:5] == b"%PDF-"


def texto_pdf(contenido: bytes, paginas: int = 2) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages[:paginas])


def sentido_opinion(contenido_pdf: bytes) -> str | None:
    """Lee el "Sentido" impreso en la Opinión: positivo, negativo, en suspenso,
    no inscrito... (en minúsculas y con guion bajo para guardarlo)."""
    texto = texto_pdf(contenido_pdf).upper()
    # El sentido viene en la línea que sigue al encabezado "Nombre ... Sentido",
    # al final del nombre del contribuyente.
    m = re.search(r"SENTIDO\s*\n(.+)", texto)
    zona = m.group(1) if m else texto[:1500]
    for zona_busqueda in (zona, texto[:2000]):
        for sentido, codigo in SENTIDOS.items():
            if re.search(rf"\b{sentido}\b", zona_busqueda):
                return codigo
    return None


def validar_pdf(contenido: bytes, *, rfc: str, tipo: str) -> None:
    """Confirma que lo descargado es el PDF del documento correcto y del RFC
    correcto (no una página de error del SAT con status 200)."""
    if not es_pdf(contenido):
        inicio = contenido[:200].decode("utf-8", errors="replace") if contenido else "(vacío)"
        raise DocumentoInvalido(f"El SAT no regresó un PDF. Respuesta: {inicio!r}")
    texto = texto_pdf(contenido).upper()
    if rfc.upper() not in texto.replace(" ", ""):
        raise DocumentoInvalido(f"El PDF descargado no corresponde al RFC {rfc}.")
    if tipo == "opinion" and "OPINI" not in texto:
        raise DocumentoInvalido("El PDF descargado no parece ser una Opinión de Cumplimiento.")
    if tipo == "constancia" and "CONSTANCIA" not in texto and "SITUACI" not in texto:
        raise DocumentoInvalido("El PDF descargado no parece ser una Constancia de Situación Fiscal.")


def descargar_opinion(signer, *, rfc: str, timeout: int = 60) -> tuple[bytes, str | None]:
    """(pdf_bytes, sentido) usando login con e.firma."""
    from satcfdi.portal import SATPortalOpinionCumplimiento

    portal = SATPortalOpinionCumplimiento(signer)
    portal.request = _con_timeout(portal.request, timeout)
    try:
        pdf = portal.generar_opinion_cumplimiento()
    finally:
        portal.close()
    validar_pdf(pdf, rfc=rfc, tipo="opinion")
    return pdf, sentido_opinion(pdf)


URL_CONSTANCIA_BROKER = (
    "https://rfcampc.siat.sat.gob.mx/app/seg/SessionBroker"
    "?url=/PTSC/IdcSiat/autc/ReimpresionTramite/ConsultaTramite.jsf&parametro=c&idSessionBit=&idSessionBit=null"
)
URL_CONSTANCIA_PDF = "https://rfcampc.siat.sat.gob.mx/PTSC/IdcSiat/IdcGeneraConstancia.jsf"


def descargar_constancia(signer, *, rfc: str, timeout: int = 60, pasos: list | None = None) -> bytes:
    """Constancia de Situación Fiscal con login por e.firma.

    Flujo verificado en el portal real (11-nov-2026): tras el login, el SAT
    abre "Reimprime tus acuses del RFC y genera tu Constancia" (ConsultaTramite.jsf,
    JSF/PrimeFaces). El botón "Generar Constancia" hace un POST AJAX del
    formulario y después la constancia se obtiene de IdcGeneraConstancia.jsf.

    A diferencia de satcfdi.SATPortalConstancia (que usa un id de botón fijo,
    'formReimpAcuse:j_idt50', y regresó HTML en la prueba real), aquí el id del
    botón y los campos del formulario se leen del HTML que el SAT entrega en
    ese momento. `pasos` (opcional) acumula (nombre, url, status, content-type,
    cuerpo) de cada respuesta para diagnosticar si algo falla."""
    from bs4 import BeautifulSoup
    from satcfdi.portal import SATPortalConstancia
    from satcfdi.portal.utils import get_form, request_ref_headers

    pasos = [] if pasos is None else pasos

    def anotar(nombre, res):
        pasos.append((nombre, res.url, res.status_code, res.headers.get("content-type", ""), res.content))
        return res

    portal = SATPortalConstancia(signer)
    portal.request = _con_timeout(portal.request, timeout)
    try:
        anotar("login", portal.login())
        res = anotar("session_broker", portal.get(URL_CONSTANCIA_BROKER, allow_redirects=True))

        # Formularios de ida y vuelta de SAML (AuthnRequest / Response).
        for i in range(6):
            action, data = get_form(res)
            if not action or ("/saml2/" not in action and "/nidp/" not in action):
                break
            res = anotar(f"saml_{i}", portal.form(action, res.url, data))

        soup = BeautifulSoup(res.text, "html.parser")
        boton = soup.find(
            lambda t: t.name in ("button", "input", "a")
            and "generar constancia" in (t.get_text(" ", strip=True) or t.get("value") or "").lower()
        )
        if boton is None:
            raise DocumentoInvalido("No apareció el botón 'Generar Constancia' en la pantalla del SAT.")
        form = boton.find_parent("form")
        if form is None:
            raise DocumentoInvalido("El botón 'Generar Constancia' no está dentro de un formulario.")

        id_boton = boton.get("id") or boton.get("name")
        id_form = form.get("id") or form.get("name")
        accion = form.get("action") or res.url
        if accion.startswith("/"):
            from urllib.parse import urljoin

            accion = urljoin(res.url, accion)
        campos = {
            i.get("name"): i.get("value") or ""
            for i in form.find_all(["input", "select", "textarea"])
            if i.get("name") and i.get("type") not in ("submit", "button", "image")
        }
        campos.update(
            {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": id_boton,
                "javax.faces.partial.execute": "@all",
                id_boton: id_boton,
                id_form: id_form,
            }
        )
        ajax = anotar(
            "generar_constancia",
            portal.post(
                accion,
                data=campos,
                headers={
                    **request_ref_headers(res.url),
                    "Faces-Request": "partial/ajax",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                allow_redirects=True,
            ),
        )
        if es_pdf(ajax.content):
            return _validada(ajax.content, rfc)

        # La respuesta AJAX puede traer un <redirect url="..."/> o un
        # window.open('...'); se prueban esos y, al final, la URL conocida.
        candidatas = re.findall(r'<redirect\s+url="([^"]+)"', ajax.text)
        candidatas += re.findall(r"(?:window\.open|location(?:\.href)?\s*=)\s*\(?\s*['\"]([^'\"]+)['\"]", ajax.text)
        candidatas.append(URL_CONSTANCIA_PDF)
        from urllib.parse import urljoin

        for n, url in enumerate(dict.fromkeys(urljoin(accion, c.replace("&amp;", "&")) for c in candidatas)):
            pdf = anotar(
                f"pdf_{n}",
                portal.get(url, headers={"Referer": res.url}, allow_redirects=True),
            )
            if es_pdf(pdf.content):
                return _validada(pdf.content, rfc)
    finally:
        portal.close()

    ultimo = pasos[-1] if pasos else None
    detalle = ""
    if ultimo:
        texto = BeautifulSoup(ultimo[4], "html.parser").get_text(" ", strip=True)[:200]
        detalle = f" Última respuesta ({ultimo[0]}, {ultimo[2]}, {ultimo[3]}): {texto!r}"
    raise DocumentoInvalido("El SAT no regresó el PDF de la Constancia." + detalle)


def _validada(pdf: bytes, rfc: str) -> bytes:
    validar_pdf(pdf, rfc=rfc, tipo="constancia")
    return pdf


def _con_timeout(request, timeout: int):
    """requests no tiene timeout por defecto: sin esto, un SAT colgado deja
    el background task esperando para siempre."""

    def envoltura(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return request(method, url, **kwargs)

    return envoltura
