"""Orquesta la descarga de Opinión de Cumplimiento y Constancia de Situación
Fiscal vía RPA (app/sat_documentos/rpa.py), siguiendo el mismo patrón de cola
en background que ya usa la Descarga Masiva de CFDIs
(app/sat_downloads/service.py): se crea el registro, se marca "solicitado",
y un background task hace el trabajo lento y actualiza el estado al terminar."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.storage import get_xml_storage
from app.efirma.service import obtener_signer
from app.rfc_clients.models import RfcClient
from app.sat_documentos.models import TIPO_CONSTANCIA, TIPO_OPINION_CUMPLIMIENTO, DocumentoSat
from app.sat_documentos.rpa import (
    SatPortalError,
    descargar_constancia_situacion_fiscal,
    descargar_opinion_cumplimiento,
)

logger = logging.getLogger(__name__)

TIPOS_VALIDOS = {TIPO_OPINION_CUMPLIMIENTO, TIPO_CONSTANCIA}

# Las solicitudes corren como BackgroundTasks de FastAPI -- es decir, atadas
# al proceso del contenedor que las recibió. Si se hace un redeploy (nueva
# revisión en Azure Container Apps) mientras una solicitud sigue
# "en_proceso", el contenedor viejo se mata junto con la tarea, que nunca
# llega a escribir su estado final: el registro se queda huérfano en
# "en_proceso" para siempre y el botón en la UI parece trabado sin importar
# cuánto se espere. listar_documentos() revisa esto en cada consulta (el
# frontend hace polling cada pocos segundos) y da por fallida cualquier
# solicitud que lleve más de este tiempo sin resolver, para que la UI se
# libere sola en el siguiente refresh. 10 minutos da margen de sobra incluso
# en el peor caso real observado (~5 min) por los timeouts de red
# encadenados del RPA.
TIEMPO_MAXIMO_EN_PROCESO = timedelta(minutes=10)


def _credenciales_efirma(db: Session, rfc_client_id: int) -> tuple[bytes, bytes, str]:
    """Reutiliza la e.firma ya cargada para la Descarga Masiva de CFDIs --
    el RPA necesita los bytes crudos del .cer/.key, no el Signer de satcfdi,
    así que se descifran aquí directo en vez de usar obtener_signer()."""
    from app.core.crypto import decrypt_bytes
    from app.efirma.service import get_efirma_or_404

    # Falla rápido (con el mismo mensaje de "vencida") si no hay e.firma vigente.
    obtener_signer(db, rfc_client_id)

    efirma = get_efirma_or_404(db, rfc_client_id)
    key_bytes = decrypt_bytes(efirma.key_bytes_encrypted)
    password = decrypt_bytes(efirma.password_encrypted).decode()
    return efirma.cer_bytes, key_bytes, password


def solicitar_documento(
    db: Session,
    *,
    rfc_client_id: int,
    tipo: str,
    solicitado_por_id: int | None,
    background_tasks: BackgroundTasks,
) -> DocumentoSat:
    if tipo not in TIPOS_VALIDOS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de documento no válido.")

    # Falla rápido si no hay e.firma vigente, antes de crear el registro.
    obtener_signer(db, rfc_client_id)

    documento = DocumentoSat(
        rfc_client_id=rfc_client_id,
        tipo=tipo,
        estado="solicitado",
        solicitado_por_id=solicitado_por_id,
    )
    db.add(documento)
    db.commit()
    db.refresh(documento)

    background_tasks.add_task(_procesar_documento, documento.id)
    return documento


def _marcar_huerfana_si_quedo_colgada(db: Session, documento: DocumentoSat) -> DocumentoSat:
    """Si `documento` lleva demasiado tiempo en "solicitado"/"en_proceso",
    probablemente el contenedor que lo procesaba se reinició a medio
    trabajo (ver TIEMPO_MAXIMO_EN_PROCESO arriba) -- se marca como error
    aquí mismo para que la UI deje de mostrarlo como "en curso" sin que
    haga falta tocar la base de datos a mano."""
    if documento.estado not in ("solicitado", "en_proceso"):
        return documento

    creado = documento.created_at
    if creado.tzinfo is None:
        creado = creado.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - creado <= TIEMPO_MAXIMO_EN_PROCESO:
        return documento

    documento.estado = "error"
    documento.mensaje_error = (
        "El proceso anterior no llegó a terminar (lo más probable es que el servidor "
        "se haya reiniciado a medio trabajo, por ejemplo por un despliegue nuevo). "
        "Puedes intentar de nuevo."
    )
    documento.completado_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(documento)
    return documento


def listar_documentos(db: Session, rfc_client_id: int) -> list[DocumentoSat]:
    """Último registro de cada tipo (opinión y constancia), para pintar los
    íconos junto al RFC sin tener que disparar una descarga nueva."""
    resultado = []
    for tipo in (TIPO_OPINION_CUMPLIMIENTO, TIPO_CONSTANCIA):
        ultimo = (
            db.query(DocumentoSat)
            .filter(DocumentoSat.rfc_client_id == rfc_client_id, DocumentoSat.tipo == tipo)
            .order_by(DocumentoSat.created_at.desc())
            .first()
        )
        if ultimo is not None:
            ultimo = _marcar_huerfana_si_quedo_colgada(db, ultimo)
            resultado.append(ultimo)
    return resultado


def get_documento_or_404(db: Session, rfc_client_id: int, documento_id: int) -> DocumentoSat:
    documento = (
        db.query(DocumentoSat)
        .filter(DocumentoSat.id == documento_id, DocumentoSat.rfc_client_id == rfc_client_id)
        .first()
    )
    if documento is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")
    return documento


def _procesar_documento(documento_id: int) -> None:
    """Corre en background (fuera del ciclo request/response): hace el login
    RPA al portal del SAT, descarga el PDF, y guarda el resultado. Usa su
    propia sesión de BD porque la del request original ya se cerró."""
    db = SessionLocal()
    try:
        documento = db.query(DocumentoSat).filter(DocumentoSat.id == documento_id).first()
        if documento is None:
            return

        documento.estado = "en_proceso"
        db.commit()

        try:
            cer_bytes, key_bytes, password = _credenciales_efirma(db, documento.rfc_client_id)
        except HTTPException as exc:
            documento.estado = "error"
            documento.mensaje_error = str(exc.detail)
            documento.completado_at = datetime.now(timezone.utc)
            db.commit()
            return

        rfc_client = db.query(RfcClient).filter(RfcClient.id == documento.rfc_client_id).first()
        rfc = rfc_client.rfc if rfc_client else str(documento.rfc_client_id)

        try:
            if documento.tipo == TIPO_OPINION_CUMPLIMIENTO:
                pdf_bytes, resultado = descargar_opinion_cumplimiento(
                    rfc=rfc, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password
                )
                documento.resultado = resultado
            else:
                pdf_bytes = descargar_constancia_situacion_fiscal(
                    rfc=rfc, cer_bytes=cer_bytes, key_bytes=key_bytes, password=password
                )

            storage = get_xml_storage()
            documento.storage_path = storage.save_pdf(
                rfc=rfc, tipo=documento.tipo, nombre=f"doc-{documento.id}", content=pdf_bytes
            )
            documento.estado = "terminado"
        except SatPortalError as exc:
            documento.estado = "error"
            documento.mensaje_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al descargar %s para rfc_client_id=%s", documento.tipo, documento.rfc_client_id)
            documento.estado = "error"
            # Mientras los selectores de rpa.py sigan sin verificar contra el
            # portal real (ver los "# TODO-VERIFICAR"), este es el caso
            # esperado: Playwright no encuentra un elemento y truena con una
            # excepción que no es SatPortalError. Se incluye el detalle
            # técnico (tipo + primeros caracteres del mensaje) en vez de solo
            # el genérico de siempre, para poder ubicar a qué paso del login
            # o la descarga corresponde sin tener que ir a los logs del
            # contenedor cada vez.
            detalle = f"{type(exc).__name__}: {str(exc)}".strip()
            detalle = detalle[:300] + "…" if len(detalle) > 300 else detalle
            documento.mensaje_error = (
                f"Ocurrió un error inesperado al conectar con el portal del SAT. Intenta de nuevo. "
                f"Detalle técnico: {detalle}"
            )

        documento.completado_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
