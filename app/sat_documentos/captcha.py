"""Cliente mínimo para un servicio externo de resolución de CAPTCHA, compatible
con la API de 2Captcha (in.php / res.php) -- varios proveedores (2Captcha,
Anti-Captcha vía su modo de compatibilidad, CapSolver, etc.) la implementan,
así que basta con cambiar CAPTCHA_SOLVER_BASE_URL para usar otro.

Es necesario porque el login del portal del SAT (con e.firma o CIEC) pide
resolver un CAPTCHA, y no hay forma confiable de resolverlo nosotros mismos."""
import base64
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class CaptchaSolverError(Exception):
    """Error al resolver el CAPTCHA -- se le muestra tal cual al usuario,
    normalmente porque falta configurar CAPTCHA_SOLVER_API_KEY o el proveedor
    no pudo leer la imagen."""


def resolver_captcha_imagen(imagen_bytes: bytes, *, intentos_max: int = 24, espera_segundos: int = 5) -> str:
    """Envía la imagen del CAPTCHA al servicio externo y espera el texto
    resuelto. Bloqueante -- se llama desde dentro del RPA (que ya corre en un
    background task), nunca directo en el ciclo request/response."""
    if not settings.CAPTCHA_SOLVER_API_KEY:
        raise CaptchaSolverError(
            "No hay configurado un servicio de resolución de CAPTCHA "
            "(variable de entorno CAPTCHA_SOLVER_API_KEY). Sin esto no se puede "
            "pasar el login del portal del SAT."
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
            raise CaptchaSolverError(f"El servicio de CAPTCHA rechazó la imagen: {datos.get('request')}")
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
                raise CaptchaSolverError(
                    f"El servicio de CAPTCHA no pudo resolver la imagen: {datos.get('request')}"
                )

    raise CaptchaSolverError("El servicio de CAPTCHA tardó demasiado en responder.")
