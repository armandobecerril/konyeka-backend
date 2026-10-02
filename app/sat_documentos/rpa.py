"""Automatización (RPA) contra el portal del SAT con e.firma, para descargar
la Opinión del Cumplimiento de Obligaciones Fiscales (32-D) y la Constancia
de Situación Fiscal.

Por qué RPA y no un webservice (a diferencia de la Descarga Masiva de CFDIs,
que sí tiene uno oficial en app/sat_downloads/sat_client.py): el SAT no
expone una API pública para estos dos trámites, solo se obtienen entrando al
portal. Esto simula esa sesión con un navegador headless (Playwright).

Login confirmado: SIEMPRE con e.firma (.cer/.key + contraseña de la llave),
nunca con RFC+contraseña (CIEC) -- así lo decidió el despacho después de que
el contador confirmó en el portal real que entrando con e.firma el SAT ya no
pide CAPTCHA. Por eso _iniciar_sesion_efirma() solo intenta resolverlo si el
elemento del CAPTCHA de verdad aparece en la página (ver el `if
page.locator(...).count() > 0` más abajo): en el caso esperado (e.firma, sin
CAPTCHA) ese bloque nunca se ejecuta y resolver_captcha_imagen() (captcha.py)
ni siquiera se llama, así que CAPTCHA_SOLVER_PROVIDER/API_KEY no son
obligatorios para que este feature funcione -- se dejan como red de
seguridad por si el SAT lo vuelve a pedir (lo mostró en el pasado para otros
flujos de login) o lo hace de forma intermitente.

####################################################################
# Selectores de LOGIN verificados en vivo (2026-10-02)             #
####################################################################
Las URLs de entrada de cada trámite y el formulario de login con e.firma SÍ
se verificaron navegando el portal público real de sat.gob.mx (sin escribir
ninguna credencial, solo inspeccionando la estructura de la página pública).
Lo que NO se pudo verificar todavía es la pantalla POSTERIOR al login (qué
botón genera la Opinión/Constancia y cómo se descarga el PDF), porque esa
pantalla solo aparece ya autenticado con una e.firma real, y no hay forma de
probarla sin una. Cada línea que sigue sin confirmar sigue marcada con
"# TODO-VERIFICAR": la primera corrida real contra producción (revisando
documento.mensaje_error si truena, o con SAT_RPA_HEADLESS=false en un
entorno con display) va a decir exactamente qué falta ajustar ahí.

Hallazgo de arquitectura importante (y la causa real del bug anterior, además
de los selectores): el login del SAT NO es una URL fija reutilizable para
ambos trámites. Cada trámite tiene su propia URL de entrada
(URL_OPINION_CUMPLIMIENTO / URL_CONSTANCIA) que, sin sesión, redirige sola a
una pantalla de login con un parámetro `target` que ya apunta de regreso a
ese mismo trámite (dominios de login distintos por trámite:
loginda.siat.sat.gob.mx para Opinión, login.siat.sat.gob.mx para Constancia
-- pero es el mismo widget de NetIQ/Access Manager, con los mismos ids de
campo en ambos). Por eso _iniciar_sesion_efirma() navega directo a la URL
del trámite (nunca a una URL de login separada y fija como se hacía antes):
al autenticarse, el SAT redirige solo de vuelta al trámite con la sesión ya
iniciada -- no hace falta (ni es correcto) hacer un segundo page.goto() a una
URL del trámite después del login."""
import logging
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.core.config import settings
from app.core.storage import get_xml_storage
from app.sat_documentos.captcha import resolver_captcha_imagen
from app.sat_documentos.models import TIPO_CONSTANCIA, TIPO_OPINION_CUMPLIMIENTO

logger = logging.getLogger(__name__)

# URLs de entrada de cada trámite -- verificadas navegando el portal público
# de sat.gob.mx (Trámites y Servicios > "Opinión del cumplimiento" /
# "Constancia de Situación Fiscal" > sección "En línea" > enlace "Ingresa al
# servicio"). Sin sesión, ambas redirigen solas a la pantalla de login.
URL_OPINION_CUMPLIMIENTO = "https://ptsc32d.clouda.sat.gob.mx/?/reporteOpinion32DContribuyente"
URL_CONSTANCIA = (
    "https://wwwmat.sat.gob.mx/app/seg/faces/pages/lanzador.jsf"
    "?url=/operacion/43824/reimprime-tus-acuses-del-rfc"
    "&tipoLogeo=c&target=principal&hostServer=https://wwwmat.sat.gob.mx"
)


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


def _iniciar_sesion_efirma(
    page: Page, tramite_url: str, *, rfc: str, cer_bytes: bytes, key_bytes: bytes, password: str
) -> None:
    """Navega directo a la URL de entrada del trámite (que redirige sola al
    login) y se autentica con e.firma. Al terminar, `page` queda en la
    pantalla del trámite ya con sesión -- el SAT redirige solo de regreso ahí
    (ver el aviso de arriba sobre el parámetro `target`), así que el llamador
    NO debe volver a navegar a la URL del trámite después de esta función."""
    with tempfile.TemporaryDirectory() as tmp:
        cer_path = Path(tmp) / "efirma.cer"
        key_path = Path(tmp) / "efirma.key"
        cer_path.write_bytes(cer_bytes)
        key_path.write_bytes(key_bytes)

        page.goto(tramite_url, wait_until="networkidle")

        # Verificado: el SAT siempre abre primero en la vista "Acceso por
        # contraseña" (RFC + Contraseña + CAPTCHA); hay que cambiar a la
        # vista "Acceso con e.firma" con este botón antes de que aparezcan
        # los campos de archivo. id="buttonFiel" confirmado en ambos
        # trámites (Opinión y Constancia, mismo widget de login). Se deja el
        # texto como respaldo por si el SAT cambia el id en algún despliegue.
        boton_efirma = (
            page.locator("#buttonFiel")
            .or_(page.get_by_text("Acceso con e.firma"))
            .or_(page.get_by_text("e.firma", exact=True))
        )
        if boton_efirma.count() > 0:
            boton_efirma.first.click()
            page.wait_for_load_state("networkidle")

        # Verificado contra el portal real: estos son los ids reales del
        # formulario "Acceso con e.firma" (iguales en ambos trámites).
        page.set_input_files("#fileCertificate", str(cer_path))
        page.wait_for_timeout(500)
        page.set_input_files("#filePrivateKey", str(key_path))
        page.wait_for_timeout(500)
        page.fill("#privateKeyPassword", password)

        # Confirmado en producción (error reportado por el usuario): en
        # cuanto el SAT logra leer el .cer, extrae el RFC del certificado y
        # deja el campo #rfc con `disabled` -- ya no se puede (ni hace
        # falta) escribirlo a mano, por eso un fill() incondicional aquí
        # tronaba con TimeoutError esperando a que se "habilitara". Si por
        # algún motivo el SAT cambia este comportamiento y el campo sigue
        # editable, sí lo llenamos nosotros como respaldo.
        campo_rfc = page.locator("#rfc")
        campo_rfc.wait_for(state="visible")
        if campo_rfc.is_enabled():
            campo_rfc.fill(rfc)

        # Verificado: la vista "Acceso con e.firma" NO muestra CAPTCHA (a
        # diferencia de "Acceso por contraseña", que sí lo pide) -- por eso
        # ya no es necesario para el flujo normal. Se deja este bloque solo
        # como red de seguridad por si el SAT lo llega a agregar aquí.
        selector_captcha_img = "img[id*='captcha' i]"  # TODO-VERIFICAR si el SAT llega a mostrarlo en esta vista
        if page.locator(selector_captcha_img).count() > 0:
            captcha_bytes = page.locator(selector_captcha_img).first.screenshot()
            texto_captcha = resolver_captcha_imagen(captcha_bytes)
            page.fill("#userCaptcha", texto_captcha)  # TODO-VERIFICAR selector exacto en esta vista

        page.click("#submit")
        page.wait_for_load_state("networkidle")

        # TODO-VERIFICAR: texto exacto del mensaje de error del SAT cuando
        # rechaza la e.firma (e.firma vencida, contraseña de la llave
        # incorrecta, etc.) -- no se pudo confirmar sin autenticar de verdad.
        error_login = page.get_by_text(
            re.compile(
                r"no fue posible autenticar|no fue posible iniciar( la)? sesi[oó]n|"
                r"usuario o contrase[ñn]a incorrectos",
                re.IGNORECASE,
            )
        )
        if error_login.count() > 0:
            raise SatPortalError(
                "El SAT rechazó el login con esta e.firma. Verifica que la e.firma "
                "(no el sello/CSD) esté vigente y que la contraseña de la llave privada "
                "sea correcta."
            )


def _buscar_en_cualquier_frame(page: Page, texto: str):
    """Busca `texto` primero en el frame principal y, si no aparece ahí, en
    cada iframe de la página. Confirmado con un screenshot real (ver
    _guardar_diagnostico): el trámite de Constancia (y probablemente el de
    Opinión, mismo portal) se carga dentro de un iframe -- la URL de entrada
    es literalmente un "lanzador.jsf" (launcher) que monta el contenido real
    del trámite en un frame hijo bajo el shell del Buzón Tributario. Un
    page.click()/page.get_by_text() sobre el frame principal nunca encuentra
    esos botones aunque se vean perfectamente en un screenshot, porque un
    screenshot pinta los pixeles de todos los frames pero page.locator()
    solo busca en el documento principal."""
    principal = page.get_by_text(texto, exact=False)
    if principal.count() > 0:
        return principal
    for frame in page.frames:
        candidato = frame.get_by_text(texto, exact=False)
        if candidato.count() > 0:
            return candidato
    return principal  # vacío a propósito: que el error de arriba lo reporte


def _descargar_pdf_actual(page: Page, texto_boton_descarga: str) -> bytes:
    boton = _buscar_en_cualquier_frame(page, texto_boton_descarga)
    with page.expect_download() as descarga_info:
        boton.first.click()
    descarga = descarga_info.value
    ruta = descarga.path()
    if ruta is None:
        raise SatPortalError("El SAT no generó el PDF para descargar. Intenta de nuevo más tarde.")
    return Path(ruta).read_bytes()


def _guardar_diagnostico(page: Page, *, rfc: str, tipo: str) -> str:
    """Si un paso posterior al login (todavía marcado # TODO-VERIFICAR) truena, guarda
    un screenshot de la página completa en el momento exacto del error -- así la
    siguiente vez no hace falta adivinar el selector a ciegas, se puede ver tal cual
    qué le mostró el SAT al RPA. No hay riesgo de capturar credenciales: para cuando
    se llega aquí el login ya se intentó, y de cualquier forma los navegadores pintan
    los campos type="password" como puntos incluso en un screenshot."""
    try:
        captura = page.screenshot(full_page=True)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo tomar el screenshot de diagnóstico")
        return "(no se pudo capturar el screenshot)"

    nombre = f"debug-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    try:
        return get_xml_storage().save_debug(
            rfc=rfc, tipo=tipo, nombre=nombre, content=captura, extension="png", content_type="image/png"
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el screenshot de diagnóstico")
        return "(no se pudo guardar el screenshot)"


def descargar_opinion_cumplimiento(
    *, rfc: str, cer_bytes: bytes, key_bytes: bytes, password: str
) -> tuple[bytes, str]:
    """Regresa (pdf_bytes, resultado), con resultado en {"positivo", "negativo"}."""
    with _navegador() as page:
        _iniciar_sesion_efirma(
            page, URL_OPINION_CUMPLIMIENTO, rfc=rfc, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password
        )

        # A partir de aquí `page` ya está en la pantalla del trámite (el
        # login redirigió solo de vuelta) -- NO se vuelve a navegar a
        # URL_OPINION_CUMPLIMIENTO. Lo siguiente SÍ sigue sin verificar
        # contra el portal real ya autenticado -- si algo truena aquí, se
        # guarda un screenshot del momento exacto (ver _guardar_diagnostico)
        # para no tener que adivinar el selector a ciegas la próxima vez.
        try:
            _buscar_en_cualquier_frame(page, "Generar Opinión").first.click()  # TODO-VERIFICAR
            page.wait_for_load_state("networkidle")
            # El click puede recargar solo el iframe del trámite (no el frame
            # principal), y en ese caso wait_for_load_state("networkidle") del
            # `page` no necesariamente refleja esa carga interna -- este margen
            # es una red de seguridad mientras no se confirme el comportamiento
            # real contra el portal autenticado.
            page.wait_for_timeout(800)

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

            pdf_bytes = _descargar_pdf_actual(page, "Descargar PDF")  # TODO-VERIFICAR
            return pdf_bytes, resultado
        except SatPortalError:
            raise
        except Exception as exc:
            ruta_diagnostico = _guardar_diagnostico(page, rfc=rfc, tipo=TIPO_OPINION_CUMPLIMIENTO)
            raise SatPortalError(
                "El login con el SAT funcionó, pero el paso para generar/descargar la "
                "Opinión de Cumplimiento todavía no está verificado contra el portal real. "
                f"Se guardó un screenshot del momento del error en: {ruta_diagnostico}. "
                f"Detalle técnico: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc


def descargar_constancia_situacion_fiscal(
    *, rfc: str, cer_bytes: bytes, key_bytes: bytes, password: str
) -> bytes:
    with _navegador() as page:
        _iniciar_sesion_efirma(
            page, URL_CONSTANCIA, rfc=rfc, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password
        )

        # Igual que en Opinión: `page` ya quedó en la pantalla del trámite
        # tras el login, no se vuelve a navegar a URL_CONSTANCIA. Lo
        # siguiente sigue sin verificar contra el portal real -- igual que
        # en Opinión, si truena se guarda un screenshot del momento exacto.
        try:
            _buscar_en_cualquier_frame(page, "Generar Constancia").first.click()  # TODO-VERIFICAR
            page.wait_for_load_state("networkidle")
            # Mismo margen que en Opinión: el click puede recargar solo el
            # iframe del trámite, no el frame principal.
            page.wait_for_timeout(800)

            return _descargar_pdf_actual(page, "Descargar PDF")  # TODO-VERIFICAR
        except SatPortalError:
            raise
        except Exception as exc:
            ruta_diagnostico = _guardar_diagnostico(page, rfc=rfc, tipo=TIPO_CONSTANCIA)
            raise SatPortalError(
                "El login con el SAT funcionó, pero el paso para generar/descargar la "
                "Constancia de Situación Fiscal todavía no está verificado contra el "
                f"portal real. Se guardó un screenshot del momento del error en: "
                f"{ruta_diagnostico}. Detalle técnico: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
