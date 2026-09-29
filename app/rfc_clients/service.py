from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.rfc_clients.models import RfcClient
from app.rfc_clients.schemas import RfcClientCreate, RfcClientUpdate, infer_tipo_persona


def list_clients(
    db: Session,
    *,
    q: str | None = None,
    solo_activos: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> list[RfcClient]:
    query = db.query(RfcClient)
    if solo_activos:
        query = query.filter(RfcClient.activo.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(RfcClient.rfc.ilike(like), RfcClient.razon_social.ilike(like)))
    return query.order_by(RfcClient.razon_social.asc()).offset(skip).limit(limit).all()


def contar_clientes_activos(db: Session) -> int:
    """Total de clientes activos en el catálogo. Alimenta la tarjeta
    'Clientes activos' del dashboard de inicio."""
    return db.query(func.count(RfcClient.id)).filter(RfcClient.activo.is_(True)).scalar() or 0


def get_client(db: Session, client_id: int) -> RfcClient:
    client = db.query(RfcClient).filter(RfcClient.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    return client


def create_client(db: Session, payload: RfcClientCreate, *, created_by_id: int | None) -> RfcClient:
    existing = db.query(RfcClient).filter(RfcClient.rfc == payload.rfc).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un cliente con ese RFC")

    client = RfcClient(
        rfc=payload.rfc,
        razon_social=payload.razon_social,
        tipo_persona=infer_tipo_persona(payload.rfc),
        regimen_fiscal=payload.regimen_fiscal,
        email=payload.email,
        telefono=payload.telefono,
        created_by_id=created_by_id,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def update_client(db: Session, client_id: int, payload: RfcClientUpdate) -> RfcClient:
    client = get_client(db, client_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(client, field, value)
    db.commit()
    db.refresh(client)
    return client


def deactivate_client(db: Session, client_id: int) -> RfcClient:
    client = get_client(db, client_id)
    client.activo = False
    db.commit()
    db.refresh(client)
    return client
