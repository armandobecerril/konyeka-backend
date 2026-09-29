from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.rfc_clients import service
from app.rfc_clients.schemas import ClientesResumenOut, RfcClientCreate, RfcClientOut, RfcClientUpdate
from app.users.models import User

router = APIRouter(prefix="/clientes", tags=["Catálogo de Clientes"])


@router.get("", response_model=list[RfcClientOut])
def listar(
    q: str | None = Query(default=None, description="Busca por RFC o razón social"),
    solo_activos: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_clients(db, q=q, solo_activos=solo_activos, skip=skip, limit=limit)


@router.post("", response_model=RfcClientOut, status_code=201)
def crear(
    payload: RfcClientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.create_client(db, payload, created_by_id=current_user.id)


@router.get("/resumen", response_model=ClientesResumenOut)
def resumen(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ClientesResumenOut(total_activos=service.contar_clientes_activos(db))


@router.get("/{client_id}", response_model=RfcClientOut)
def obtener(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_client(db, client_id)


@router.put("/{client_id}", response_model=RfcClientOut)
def actualizar(
    client_id: int,
    payload: RfcClientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.update_client(db, client_id, payload)


@router.delete("/{client_id}", response_model=RfcClientOut)
def eliminar(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Baja lógica: marca el cliente como inactivo (no se borra el registro)."""
    return service.deactivate_client(db, client_id)
