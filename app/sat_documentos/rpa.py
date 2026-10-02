"""Automatización (RPA) contra el portal del SAT con e.firma, para descargar
la Opinión del Cumplimiento de Obligaciones Fiscales (32-D) y la Constancia
de Situación Fiscal.

Por qué RPA y no un webservice (a diferencia de la Descarga Masiva de CFDIs,
que sí tiene uno oficial en app/sat_downloads/sat_client.py): el SAT no
expone una API pública para estos dos trámites, solo se obtienen entrando al
portal. Esto simula esa sesión con un navegador headless (Playwright) y
resuelve el CAPTCHA del login con un servicio externo (ver captcha.py).

####################################################################
# AVISO IMPORTANTE -- selectores pendientes de verificar en vivo   #
####################################################################
Los selectores de la página de login y de cada trámite están escritos con
base en la estructura documentada del flujo de e.firma del SAT (archivos
.cer/.key + contraseña + CAPTCHA), pero NO se probaron contra el portal real
-- este entorno de desarrollo no tiene forma de navegarlo. Cada línea
marcada con "# TODO-VERIFICAR" es el punto exacto que hay que confirmar o
ajustar la primera vez que esto se corra contra el SAT de verdad:

    1. Pon SAT_RPA_HEADLESS=false en el .env del backend (así se ve el
       navegador en vez de correr oculto).
    2. Corre una descarga de prueba con un cliente que tenga e.firma vigente.
    3. Donde el navegador se trabe o el selector no encuentre el elemento,
       usa el inspector de Playwright (page.pause() o `playwright codegen
       <url>`) para capturar el selector real y reemplázalo aquí.

El resto del feature (modelo, cola de estado, storage del PDF, endpoints,
alerta en el frontend si la opinión es negativa) ya está completo y no
depende de que estos selectores cambien."""
import logging
import tempfile
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.core.config import settings
from app.sat_documentos.captcha import resolver_captcha_imagen

logger = logging.getLogger(__name__)

# TODO-VERIFICAR: estas tres URLs son las documentadas públicamente para cada
# trámite, pero el SAT las reorganiza con cierta frecuencia.
LOGIN_URL = "https://loginda.siat.sat.gob.mx/nidp/wsfed/ep?id=SATxWEB"
URL_OPINION_CUMPLIMIENTO = "https://siat.sat.gob.mx/PTSC/OpinionCumplimiento/"
URL_CONSTANCIA = "https://rfcampliado.siat.sat.gob.mx/ConstanciaSF/"


class SatPortalError(Exception):
    """Error de negocio al interactuar con el portal del SAT (login
    rechazado, CAPTCHA no resuelto, trámite no disponible, etc.) -- el
    mensaje se le muestra tal cual al usuario, a propósito."""


@contextmanager
def _navegador():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.SAT_RPA_HEADLESS)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _iniciar_sesion_efirma(page: Page, *, cer_bytes: bytes, key_bytes: bytes, password: str) -> None:
    """Sube el .cer/.key a los campos de archivo del login con e.firma del
    portal del SAT y resuelve el CAPTCHA si lo pide."""
    with tempfile.TemporaryDirectory() as tmp:
        cer_path = Path(tmp) / "efirma.cer"
        key_path = Path(tmp) / "efirma.key"
        cer_path.write_bytes(cer_bytes)
        key_path.write_bytes(key_bytes)

        page.goto(LOGIN_URL, wait_until="networkidle")

        # El portal normalmente muestra primero una pestaña/botón para elegir
        # el método de acceso; si existe, hay que seleccionar "e.firma" antes
        # de que aparezcan los campos de archivo.
        selector_tab_efirma = "text=Acceso con e.firma"  # TODO-VERIFICAR
        if page.locator(selector_tab_efirma).count() > 0:
            page.click(selector_tab_efirma)
            page.wait_for_load_state("networkidle")

        page.set_input_files("#certFileInput", str(cer_path))  # TODO-VERIFICAR
        page.set_input_files("#keyFileInput", str(key_path))  # TODO-VERIFICAR
        page.fill("#password", password)  # TODO-VERIFICAR

        selector_captcha_img = "#captchaImg"  # TODO-VERIFICAR
        if page.locator(selector_captcha_img).count() > 0:
            captcha_bytes = page.locator(selector_captcha_img).screenshot()
            texto_captcha = resolver_captcha_imagen(captcha_bytes)
            page.fill("#captchaInput", texto_captcha)  # TODO-VERIFICAR

        page.click("#submitButton")  # TODO-VERIFICAR
        page.wait_for_load_state("networkidle")

        selector_error = "text=no fue posible autenticar"  # TODO-VERIFICAR (texto exacto del error del SAT)
        if page.locator(selector_error).count() > 0:
            raise SatPortalError(
                "El SAT rechazó el login con esta e.firma. Verifica que la e.firma "
                "(no el sello/CSD) esté vigente y que la contraseña de la llave privada "
                "sea correcta."
            )


def _descargar_pdf_actual(page: Page, selector_boton_descarga: str) -> bytes:
    with page.expect_download() as descarga_info:
        page.click(selector_boton_descarga)
    descarga = descarga_info.value
    ruta = descarga.path()
    if ruta is None:
        raise SatPortalError("El SAT no generó el PDF para descargar. Intenta de nuevo más tarde.")
    return Path(ruta).read_bytes()


def descargar_opinion_cumplimiento(*, cer_bytes: bytes, key_bytes: bytes, password: str) -> tuple[bytes, str]:
    """Regresa (pdf_bytes, resultado), con resultado en {"positivo", "negativo"}."""
    with _navegador() as page:
        _iniciar_sesion_efirma(page, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password)

        page.goto(URL_OPINION_CUMPLIMIENTO, wait_until="networkidle")
        page.click("text=Generar Opinión")  # TODO-VERIFICAR
        page.wait_for_load_state("networkidle")

        contenido = page.inner_text("body").lower()
        if "positivo" in contenido:
            resultado = "positivo"
        elif "negativo" in contenido:
            resultado = "negativo"
        else:
            raise SatPortalError(
                "Se generó la opinión pero no se pudo determinar si es positiva o "
                "negativa en la respuesta del SAT; revísala manualmente en el portal."
            )

        pdf_bytes = _descargar_pdf_actual(page, "text=Descargar PDF")  # TODO-VERIFICAR
        return pdf_bytes, resultado


def descargar_constancia_situacion_fiscal(*, cer_bytes: bytes, key_bytes: bytes, password: str) -> bytes:
    with _navegador() as page:
        _iniciar_sesion_efirma(page, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password)

        page.goto(URL_CONSTANCIA, wait_until="networkidle")
        page.click("text=Generar Constancia")  # TODO-VERIFICAR
        page.wait_for_load_state("networkidle")

        return _descargar_pdf_actual(page, "text=Descargar PDF")  # TODO-VERIFICAR
