from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.storage import get_xml_storage
from app.rfc_clients.service import get_client
from app.sat_documentos import service
from app.sat_documentos.schemas import DocumentoSatOut
from app.users.models import User

router = APIRouter(tags=["Documentos SAT"])


@router.post(
    "/clientes/{rfc_client_id}/sat/documentos/{tipo}",
    response_model=DocumentoSatOut,
    status_code=status.HTTP_201_CREATED,
)
def solicitar_documento(
    rfc_client_id: int,
    tipo: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.solicitar_documento(
        db,
        rfc_client_id=rfc_client_id,
        tipo=tipo,
        solicitado_por_id=current_user.id,
        background_tasks=background_tasks,
    )


@router.get("/clientes/{rfc_client_id}/sat/documentos", response_model=list[DocumentoSatOut])
def listar_documentos(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_documentos(db, rfc_client_id)


@router.get("/clientes/{rfc_client_id}/sat/documentos/{documento_id}/pdf")
def descargar_documento_pdf(
    rfc_client_id: int,
    documento_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    documento = service.get_documento_or_404(db, rfc_client_id, documento_id)
    if documento.estado != "terminado" or not documento.storage_path:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este documento todavía no está listo.")

    storage = get_xml_storage()
    contenido = storage.read(documento.storage_path)
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{documento.tipo}-{documento.id}.pdf"'},
    )
