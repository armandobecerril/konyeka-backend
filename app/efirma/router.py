from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.efirma import service
from app.efirma.schemas import EfirmaStatusOut
from app.rfc_clients.service import get_client
from app.users.models import User

router = APIRouter(prefix="/clientes/{rfc_client_id}/efirma", tags=["e.firma"])


@router.get("", response_model=EfirmaStatusOut)
def estado(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_efirma_or_404(db, rfc_client_id)


@router.post("", response_model=EfirmaStatusOut, status_code=status.HTTP_201_CREATED)
async def cargar(
    rfc_client_id: int,
    cer: UploadFile = File(..., description="Archivo .cer de la e.firma"),
    key: UploadFile = File(..., description="Archivo .key de la e.firma"),
    password: str = Form(..., description="Contraseña de la llave privada"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rfc_client = get_client(db, rfc_client_id)
    cer_bytes = await cer.read()
    key_bytes = await key.read()
    return service.cargar_efirma(
        db,
        rfc_client,
        cer_bytes=cer_bytes,
        key_bytes=key_bytes,
        password=password,
        uploaded_by_id=current_user.id,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def eliminar(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.eliminar_efirma(db, rfc_client_id)
