"""Adaptador delgado sobre satcfdi para el webservice de Descarga Masiva del SAT
(https://cfdidescargamasivasolicitud.clouda.sat.gob.mx). Aquí solo se traduce
entre nuestros tipos y los de la librería; toda la firma WS-Security con la
e.firma la resuelve satcfdi."""
from datetime import date

from app.core.config import settings

# Códigos de respuesta documentados por el SAT para el servicio de Descarga Masiva.
CODIGOS_ESTADO = {
    "5002": "Ya se agotaron las solicitudes disponibles para este mismo rango de fechas (límite del SAT).",
    "5003": "La consulta supera el tope máximo de CFDIs por solicitud; acorta el rango de fechas.",
    "5004": "No se encontró información para los parámetros indicados.",
    "5005": "Ya existe una solicitud vigente con estos mismos parámetros (fechas, emisor/receptor).",
    "404": "El SAT respondió con un error no controlado. Intenta de nuevo más tarde.",
}


def _environment():
    from satcfdi.pacs import Environment

    return Environment.PRODUCTION if settings.SAT_ENVIRONMENT.upper() == "PRODUCTION" else Environment.TEST


def _sat(signer):
    from satcfdi.pacs.sat import SAT

    return SAT(signer=signer, environment=_environment())


def solicitar_descarga(
    signer,
    *,
    tipo: str,
    fecha_inicial: date,
    fecha_final: date,
    tipo_comprobante: str | None,
) -> dict:
    """Crea la solicitud de descarga en el SAT. Regresa un dict con IdSolicitud/CodEstatus/Mensaje.

    Desde la versión 1.5 del webservice (mayo 2025), el SAT exige indicar
    EstadoComprobante explícitamente: si se omite, responde con el error 301
    "XML mal formado". Por ahora solo pedimos comprobantes Vigentes (la
    cancelación/conciliación queda para un sprint futuro).
    """
    from satcfdi.pacs.sat import EstadoComprobante

    sat = _sat(signer)
    kwargs = dict(
        fecha_inicial=fecha_inicial,
        fecha_final=fecha_final,
        tipo_comprobante=tipo_comprobante,
        estado_comprobante=EstadoComprobante.VIGENTE,
    )
    if tipo == "emitidas":
        return sat.recover_comprobante_emitted_request(**kwargs)
    return sat.recover_comprobante_received_request(**kwargs)


def verificar_descarga(signer, id_solicitud: str) -> dict:
    """Consulta el estado de una solicitud ya creada."""
    sat = _sat(signer)
    return sat.recover_comprobante_status(id_solicitud)


def descargar_paquete(signer, id_paquete: str) -> bytes:
    """Descarga un paquete (ZIP con XMLs) y regresa sus bytes ya decodificados de base64."""
    import base64

    sat = _sat(signer)
    _respuesta, paquete_b64 = sat.recover_comprobante_download(id_paquete)
    return base64.b64decode(paquete_b64)


def mensaje_para_codigo(cod_estatus: str | None) -> str | None:
    if not cod_estatus or cod_estatus == "5000":
        return None
    return CODIGOS_ESTADO.get(cod_estatus, f"El SAT respondió con el código {cod_estatus}.")
