"""Resuelve el CAPTCHA del login del portal del SAT, para cuando de verdad
aparece -- el login confirmado para Opinión de Cumplimiento y Constancia es
con e.firma, y entrando así el SAT ya no lo pide (confirmado en el portal
real), así que en el caso normal este módulo ni se llama (ver el `if
page.locator(...).count() > 0` en rpa.py). Se deja como red de seguridad por
si el SAT lo vuelve a mostrar, ya sea con e.firma o si en el futuro se
agregara login con CIEC (RFC + contraseña). No hay forma confiable de
resolverlo nosotros mismos sin un modelo o servicio externo -- dos
proveedores intercambiables, elegidos con CAPTCHA_SOLVER_PROVIDER -- ver
app/core/config.py:

    "2captcha"  -- servicio de terceros compatible con la API de 2Captcha
                   (in.php / res.php): un humano o modelo propio del
                   proveedor resuelve la imagen.
    "azure_llm" -- un modelo con visión desplegado en Azure AI Foundry (o
                   cualquier endpoint compatible con Chat Completions de
                   Azure OpenAI): se le manda la imagen del CAPTCHA en base64
                   y se le pide que responda solo con el texto. Sirve para
                   CAPTCHAs de texto/números distorsionados simples; si el
                   SAT usara un CAPTCHA de otro tipo (basado en clics o
                   comportamiento, tipo reCAPTCHA v2/v3), esto no aplicaría."""
import base64
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class CaptchaSolverError(Exception):
    """Error al resolver el CAPTCHA -- se le muestra tal cual al usuario,
    normalmente porque falta configurar el proveedor elegido."""


def resolver_captcha_imagen(imagen_bytes: bytes) -> str:
    """Envía la imagen del CAPTCHA al proveedor configurado y regresa el
    texto resuelto. Bloqueante -- se llama desde dentro del RPA (que ya corre
    en un background task), nunca directo en el ciclo request/response."""
    proveedor = settings.CAPTCHA_SOLVER_PROVIDER.lower()
    if proveedor == "azure_llm":
        return _resolver_con_azure_llm(imagen_bytes)
    if proveedor == "2captcha":
        return _resolver_con_2captcha(imagen_bytes)
    raise CaptchaSolverError(
        f"CAPTCHA_SOLVER_PROVIDER='{settings.CAPTCHA_SOLVER_PROVIDER}' no es válido "
        "(usa '2captcha' o 'azure_llm')."
    )


def _resolver_con_2captcha(imagen_bytes: bytes, *, intentos_max: int = 24, espera_segundos: int = 5) -> str:
    if not settings.CAPTCHA_SOLVER_API_KEY:
        raise CaptchaSolverError(
            "No hay configurada una llave de 2Captcha (CAPTCHA_SOLVER_API_KEY). "
            "Sin esto no se puede pasar el login del portal del SAT."
        )

    base_url = settings.CAPTCHA_SOLVER_BASE_URL.rstrip("/")
    imagen_b64 = base64.b64encode(imagen_bytes).decode()

    with httpx.Client(timeout=30) as client:
        envio = client.post(
            f"{base_url}/in.php",
            data={
                "key": settings.CAPTCHA_SOLVER_API_KEY,
                "method": "base64",
                "body": imagen_b64,
                "json": 1,
            },
        )
        envio.raise_for_status()
        datos = envio.json()
        if datos.get("status") != 1:
            raise CaptchaSolverError(f"2Captcha rechazó la imagen: {datos.get('request')}")
        captcha_id = datos["request"]

        for _ in range(intentos_max):
            time.sleep(espera_segundos)
            resultado = client.get(
                f"{base_url}/res.php",
                params={
                    "key": settings.CAPTCHA_SOLVER_API_KEY,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
            )
            resultado.raise_for_status()
            datos = resultado.json()
            if datos.get("status") == 1:
                return datos["request"]
            if datos.get("request") != "CAPCHA_NOT_READY":
                raise CaptchaSolverError(f"2Captcha no pudo resolver la imagen: {datos.get('request')}")

    raise CaptchaSolverError("2Captcha tardó demasiado en responder.")


def _resolver_con_azure_llm(imagen_bytes: bytes) -> str:
    if not (settings.AZURE_LLM_ENDPOINT and settings.AZURE_LLM_API_KEY and settings.AZURE_LLM_DEPLOYMENT):
        raise CaptchaSolverError(
            "Falta configurar el modelo de Azure AI Foundry para resolver el CAPTCHA "
            "(AZURE_LLM_ENDPOINT, AZURE_LLM_API_KEY, AZURE_LLM_DEPLOYMENT)."
        )

    imagen_b64 = base64.b64encode(imagen_bytes).decode()
    url = (
        f"{settings.AZURE_LLM_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{settings.AZURE_LLM_DEPLOYMENT}/chat/completions"
        f"?api-version={settings.AZURE_LLM_API_VERSION}"
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Esta imagen es un CAPTCHA de texto. Responde ÚNICAMENTE con "
                            "los caracteres que aparecen en la imagen, sin explicaciones, "
                            "espacios ni puntuación adicional."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{imagen_b64}"}},
                ],
            }
        ],
        "max_tokens": 20,
        "temperature": 0,
    }

    with httpx.Client(timeout=30) as client:
        respuesta = client.post(url, json=payload, headers={"api-key": settings.AZURE_LLM_API_KEY})
        respuesta.raise_for_status()
        datos = respuesta.json()

    try:
        texto = datos["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        raise CaptchaSolverError("El modelo de Azure AI Foundry no regresó una respuesta utilizable para el CAPTCHA.")

    if not texto:
        raise CaptchaSolverError("El modelo de Azure AI Foundry regresó una respuesta vacía para el CAPTCHA.")
    return texto
