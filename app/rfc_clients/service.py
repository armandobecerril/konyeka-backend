from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.rfc_clients.models import RfcClient
from app.rfc_clients.schemas import RfcClientCreate, RfcClientUpdate, infer_tipo_persona


def list_clients(
    db: Session,
    *,
    despacho_id: int,
    q: str | None = None,
    solo_activos: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> list[RfcClient]:
    query = db.query(RfcClient).filter(RfcClient.despacho_id == despacho_id)
    if solo_activos:
        query = query.filter(RfcClient.activo.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(RfcClient.rfc.ilike(like), RfcClient.razon_social.ilike(like)))
    return query.order_by(RfcClient.razon_social.asc()).offset(skip).limit(limit).all()


def contar_clientes_activos(db: Session, despacho_id: int) -> int:
    """Total de clientes activos del despacho. Alimenta la tarjeta
    'Clientes activos' del dashboard de inicio."""
    return (
        db.query(func.count(RfcClient.id))
        .filter(RfcClient.activo.is_(True), RfcClient.despacho_id == despacho_id)
        .scalar()
        or 0
    )


def get_client(db: Session, client_id: int) -> RfcClient:
    client = db.query(RfcClient).filter(RfcClient.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    return client


def create_client(db: Session, payload: RfcClientCreate, *, created_by_id: int | None, despacho_id: int) -> RfcClient:
    from app.billing.service import exigir_cupo_rfc
    from app.despachos.models import Despacho
    from app.embudo import service as embudo

    existing = (
        db.query(RfcClient).filter(RfcClient.rfc == payload.rfc, RfcClient.despacho_id == despacho_id).first()
    )
    if existing:
        if existing.activo:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un cliente con ese RFC")
        # Reactivar un cliente dado de baja también ocupa un lugar de la licencia.
        exigir_cupo_rfc(db, db.get(Despacho, despacho_id))
        existing.activo = True
        db.commit()
        db.refresh(existing)
        return existing

    exigir_cupo_rfc(db, db.get(Despacho, despacho_id))
    client = RfcClient(
        despacho_id=despacho_id,
        rfc=payload.rfc,
        razon_social=payload.razon_social,
        tipo_persona=infer_tipo_persona(payload.rfc),
        regimen_fiscal=payload.regimen_fiscal,
        email=payload.email,
        telefono=payload.telefono,
        created_by_id=created_by_id,
    )
    db.add(client)
    db.flush()
    embudo.registrar(db, "client_added", despacho_id=despacho_id, user_id=created_by_id, props={"tipo": client.tipo_persona}, una_vez=True)
    db.commit()
    db.refresh(client)
    return client


def update_client(db: Session, client_id: int, payload: RfcClientUpdate) -> RfcClient:
    client = get_client(db, client_id)
    cambios = payload.model_dump(exclude_unset=True)
    if cambios.get("activo") is True and not client.activo:
        from app.billing.service import exigir_cupo_rfc
        from app.despachos.models import Despacho

        exigir_cupo_rfc(db, db.get(Despacho, client.despacho_id))
    for field, value in cambios.items():
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
