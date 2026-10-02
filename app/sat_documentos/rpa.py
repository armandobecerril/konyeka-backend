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
    """Además del `page`, entrega una lista `consola_log` que va acumulando
    los mensajes de consola del navegador (console.log/warn/error) y los
    errores de JavaScript no capturados de la página -- muy útil para
    diagnosticar un SPA (como el de Opinión de Cumplimiento) que se queda en
    blanco: si Angular truena con un error de JS, aparece ahí aunque el
    screenshot no muestre nada."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.SAT_RPA_HEADLESS)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        consola_log: list[str] = []
        page.on(
            "console",
            lambda msg: consola_log.append(f"[console.{msg.type}] {msg.text}"),
        )
        page.on(
            "pageerror",
            lambda err: consola_log.append(f"[pageerror] {err}"),
        )
        try:
            yield page, consola_log
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

        # Algunas pantallas post-login (sobre todo la de Opinión, un SPA de
        # Angular) siguen haciendo llamadas propias después de que la
        # navegación ya se consideró "networkidle" una vez -- se vuelve a
        # esperar por si acaso, sin fallar si ya no hay más actividad que
        # esperar, más un margen fijo para que el framework termine de
        # renderizar.
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(2000)


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


def _generar_y_descargar_pdf(page: Page, texto_boton_generar: str) -> bytes:
    """Hace clic en el botón que genera el documento (Generar Opinión /
    Generar Constancia) y regresa los bytes del PDF resultante.

    El texto oficial del SAT para Constancia dice que el resultado "se
    muestra en otra ventana del navegador" -- no hay forma de saber de
    antemano (sin e.firma real para probar) si eso significa (a) el clic
    dispara una descarga directa en la misma pestaña, (b) se abre una
    pestaña nueva que ya trae el PDF (como descarga, o mostrado inline por
    el visor de PDF del navegador), (c) el clic solo genera el documento y
    aparece un botón/enlace "Descargar PDF" aparte en la misma pestaña, o
    (d) la MISMA pestaña navega en el lugar directo a la URL del PDF (p.ej.
    el servidor responde con Content-Disposition: inline y Chromium lo
    renderiza con su visor interno, sin disparar ni un evento "download" ni
    un evento "page" nuevo). Se cubren las cuatro, en ese orden."""
    boton = _buscar_en_cualquier_frame(page, texto_boton_generar)
    contexto = page.context

    descargas: list = []
    pestanas_nuevas: list = []
    # OJO: pasar list.append directo (contexto.on("download", descargas.append))
    # truena con AttributeError dentro de Playwright -- un método acoplado a
    # una lista (list.append) es un builtin_function_or_method de C, y el
    # despachador de eventos de Playwright espera una función de Python
    # normal. Por eso se envuelve en un lambda.
    contexto.on("download", lambda d: descargas.append(d))
    contexto.on("page", lambda p: pestanas_nuevas.append(p))

    boton.first.click()
    page.wait_for_timeout(3000)  # dale tiempo a que la descarga/pestaña nueva aparezca

    # (a) Descarga directa (en esta pestaña o en una nueva -- el evento
    # "download" del contexto se dispara sin importar en cuál pestaña pasó).
    if descargas:
        ruta = descargas[-1].path()
        if ruta is not None:
            return Path(ruta).read_bytes()

    # (b) Se abrió una pestaña nueva con el resultado, sin disparar un
    # evento de descarga (p.ej. el navegador muestra el PDF inline). Se baja
    # por HTTP directo, reusando las cookies ya autenticadas del contexto.
    if pestanas_nuevas:
        pestana = pestanas_nuevas[-1]
        try:
            pestana.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        if descargas:  # pudo haber llegado tarde
            ruta = descargas[-1].path()
            if ruta is not None:
                return Path(ruta).read_bytes()
        respuesta = contexto.request.get(pestana.url)
        if respuesta.ok and "pdf" in respuesta.headers.get("content-type", "").lower():
            return respuesta.body()

    # (c) Ni descarga ni pestaña nueva -- puede que solo haya aparecido un
    # botón/enlace "Descargar PDF" aparte en la misma pestaña.
    boton_descarga = _buscar_en_cualquier_frame(page, "Descargar PDF")
    if boton_descarga.count() > 0:
        with page.expect_download(timeout=15000) as descarga_info:
            boton_descarga.first.click()
        ruta = descarga_info.value.path()
        if ruta is not None:
            return Path(ruta).read_bytes()

    # (d) Ni descarga, ni pestaña nueva, ni botón "Descargar PDF" -- puede
    # que la MISMA pestaña haya navegado en el lugar directo a la URL del
    # PDF (el visor interno de PDF de Chromium no dispara "download" ni
    # "page"). Se revisa la URL actual de la página principal.
    try:
        respuesta_misma_pestana = contexto.request.get(page.url)
    except Exception:  # noqa: BLE001
        respuesta_misma_pestana = None
    if (
        respuesta_misma_pestana is not None
        and respuesta_misma_pestana.ok
        and "pdf" in respuesta_misma_pestana.headers.get("content-type", "").lower()
    ):
        return respuesta_misma_pestana.body()

    # Ningún escenario conocido aplicó. Se usa una excepción genérica (no
    # SatPortalError) a propósito: así el llamador la captura en su rama
    # `except Exception` -- que sí guarda un screenshot de diagnóstico --
    # en lugar de la rama `except SatPortalError: raise`, que lo omitiría.
    raise RuntimeError(
        "El SAT no generó el PDF para descargar (no hubo descarga directa, pestaña "
        "nueva con el documento, botón de descarga visible, ni la pestaña actual "
        "navegó a un PDF)."
    )


def _guardar_diagnostico(page: Page, consola_log: list[str], *, rfc: str, tipo: str) -> str:
    """Si un paso posterior al login (todavía marcado # TODO-VERIFICAR) truena, guarda
    un screenshot de la página completa en el momento exacto del error -- así la
    siguiente vez no hace falta adivinar el selector a ciegas, se puede ver tal cual
    qué le mostró el SAT al RPA. No hay riesgo de capturar credenciales: para cuando
    se llega aquí el login ya se intentó, y de cualquier forma los navegadores pintan
    los campos type="password" como puntos incluso en un screenshot.

    También guarda el HTML completo de la página (útil cuando el screenshot se ve
    en blanco -- un SPA como el de Opinión puede tener contenido oculto, o un
    toast/mensaje que ya desapareció antes del screenshot pero sigue en el DOM) y
    el log de consola del navegador (console.log/warn/error + errores de
    JavaScript no capturados) -- si Angular truena con un error de JS, esto lo
    muestra aunque el screenshot no muestre nada.

    También reporta las pestañas abiertas en ese momento -- el SAT avisa que el
    resultado de Generar Opinión/Constancia "se muestra en otra ventana del
    navegador", así que esto dice de inmediato, sin bajar el screenshot, si se
    abrió una pestaña nueva y a qué URL."""
    pestanas = [p.url for p in page.context.pages]
    nombre = f"debug-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    storage = get_xml_storage()
    partes: list[str] = []

    try:
        captura = page.screenshot(full_page=True)
        ruta_png = storage.save_debug(
            rfc=rfc, tipo=tipo, nombre=nombre, content=captura, extension="png", content_type="image/png"
        )
        partes.append(f"screenshot: {ruta_png}")
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo tomar/guardar el screenshot de diagnóstico")
        partes.append("screenshot: (falló)")

    try:
        html = page.content().encode("utf-8")
        ruta_html = storage.save_debug(
            rfc=rfc, tipo=tipo, nombre=nombre, content=html, extension="html", content_type="text/html"
        )
        partes.append(f"html: {ruta_html}")
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el HTML de diagnóstico")
        partes.append("html: (falló)")

    # Las últimas líneas de consola/errores de JS, directo en el mensaje de
    # error para no tener que bajar nada si basta con eso.
    if consola_log:
        ultimas = consola_log[-10:]
        partes.append("consola: " + " | ".join(ultimas))
    else:
        partes.append("consola: (sin mensajes)")

    partes.append(f"pestañas abiertas: {pestanas}")
    return "; ".join(partes)


def descargar_opinion_cumplimiento(
    *, rfc: str, cer_bytes: bytes, key_bytes: bytes, password: str
) -> tuple[bytes, str]:
    """Regresa (pdf_bytes, resultado), con resultado en {"positivo", "negativo"}."""
    with _navegador() as (page, consola_log):
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
            # OJO: a diferencia de Constancia, acá todavía no está confirmado si
            # el resultado (positivo/negativo) aparece en la misma pestaña antes
            # de descargar el PDF, o si -- como Constancia -- todo pasa en una
            # pestaña nueva. _generar_y_descargar_pdf cubre ambos casos para el
            # PDF; si el resultado positivo/negativo solo aparece en una pestaña
            # nueva, esta lectura de `contenido` en la pestaña original puede
            # necesitar ajustarse después de ver el próximo diagnóstico.
            pdf_bytes = _generar_y_descargar_pdf(page, "Generar Opinión")  # TODO-VERIFICAR

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

            return pdf_bytes, resultado
        except SatPortalError:
            raise
        except Exception as exc:
            ruta_diagnostico = _guardar_diagnostico(page, consola_log, rfc=rfc, tipo=TIPO_OPINION_CUMPLIMIENTO)
            raise SatPortalError(
                "El login con el SAT funcionó, pero el paso para generar/descargar la "
                "Opinión de Cumplimiento todavía no está verificado contra el portal real. "
                f"Se guardó un screenshot del momento del error en: {ruta_diagnostico}. "
                f"Detalle técnico: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc


def descargar_constancia_situacion_fiscal(
    *, rfc: str, cer_bytes: bytes, key_bytes: bytes, password: str
) -> bytes:
    with _navegador() as (page, consola_log):
        _iniciar_sesion_efirma(
            page, URL_CONSTANCIA, rfc=rfc, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password
        )

        # Igual que en Opinión: `page` ya quedó en la pantalla del trámite
        # tras el login, no se vuelve a navegar a URL_CONSTANCIA. Lo
        # siguiente sigue sin verificar contra el portal real -- igual que
        # en Opinión, si truena se guarda un screenshot del momento exacto.
        try:
            return _generar_y_descargar_pdf(page, "Generar Constancia")  # TODO-VERIFICAR
        except SatPortalError:
            raise
        except Exception as exc:
            ruta_diagnostico = _guardar_diagnostico(page, consola_log, rfc=rfc, tipo=TIPO_CONSTANCIA)
            raise SatPortalError(
                "El login con el SAT funcionó, pero el paso para generar/descargar la "
                "Constancia de Situación Fiscal todavía no está verificado contra el "
                f"portal real. Se guardó un screenshot del momento del error en: "
                f"{ruta_diagnostico}. Detalle técnico: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
