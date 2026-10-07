from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.conciliacion import service
from app.conciliacion.schemas import (
    FacturaConciliacionOut,
    GenerarSugerenciasOut,
    PagoHuerfanoOut,
    ResumenConciliacionOut,
    SugerenciaOut,
)
from app.core.db import get_db
from app.core.deps import get_current_user
from app.rfc_clients.service import get_client
from app.users.models import User

router = APIRouter(tags=["Conciliación"])


@router.get("/clientes/{rfc_client_id}/conciliacion/resumen", response_model=ResumenConciliacionOut)
def resumen(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.resumen_conciliacion(db, rfc_client_id)


@router.get("/clientes/{rfc_client_id}/conciliacion/facturas", response_model=list[FacturaConciliacionOut])
def facturas(
    rfc_client_id: int,
    estado: str | None = Query(default=None, description="conciliada | parcial | pendiente"),
    fecha_desde: date | None = Query(
        default=None, description="PPD vs REP: fecha de emisión de la factura, desde (puede cubrir varios meses)"
    ),
    fecha_hasta: date | None = Query(
        default=None, description="PPD vs REP: fecha de emisión de la factura, hasta"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_facturas(db, rfc_client_id, estado=estado, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


@router.get("/clientes/{rfc_client_id}/conciliacion/huerfanos", response_model=list[PagoHuerfanoOut])
def huerfanos(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_pagos_huerfanos(db, rfc_client_id)


@router.post("/clientes/{rfc_client_id}/conciliacion/sugerencias/generar", response_model=GenerarSugerenciasOut)
def generar_sugerencias(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.generar_sugerencias_ia(db, rfc_client_id)


@router.get("/clientes/{rfc_client_id}/conciliacion/sugerencias", response_model=list[SugerenciaOut])
def listar_sugerencias(
    rfc_client_id: int,
    estado: str = Query(default="pendiente", description="pendiente | aprobada | rechazada"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_sugerencias(db, rfc_client_id, estado=estado)


@router.post("/clientes/{rfc_client_id}/conciliacion/sugerencias/{sugerencia_id}/aprobar", response_model=SugerenciaOut)
def aprobar_sugerencia(
    rfc_client_id: int,
    sugerencia_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    sugerencia = service.aprobar_sugerencia(db, rfc_client_id, sugerencia_id, current_user.id)
    return _sugerencia_out(db, sugerencia)


@router.post("/clientes/{rfc_client_id}/conciliacion/sugerencias/{sugerencia_id}/rechazar", response_model=SugerenciaOut)
def rechazar_sugerencia(
    rfc_client_id: int,
    sugerencia_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    sugerencia = service.rechazar_sugerencia(db, rfc_client_id, sugerencia_id, current_user.id)
    return _sugerencia_out(db, sugerencia)


def _sugerencia_out(db: Session, sugerencia) -> SugerenciaOut:
    """El modelo ya trae los datos, pero SugerenciaOut necesita los datos
    enriquecidos (nombres/RFCs de pago y factura), así que reusamos el
    listado filtrando por el estado que acaba de quedar."""
    encontrada = next(
        (s for s in service.listar_sugerencias(db, sugerencia.rfc_client_id, estado=sugerencia.estado) if s.id == sugerencia.id),
        None,
    )
    if encontrada is not None:
        return encontrada
    # Últimos recursos (no debería pasar): arma un Out mínimo.
    return SugerenciaOut(
        id=sugerencia.id,
        score=sugerencia.score,
        motivo=sugerencia.motivo,
        estado=sugerencia.estado,
        created_at=sugerencia.created_at,
        pago_relacionado_id=sugerencia.pago_relacionado_id,
        uuid_pago="",
        imp_pagado=0,
        fecha_pago=None,
        pago_emisor_nombre=None,
        pago_emisor_rfc="",
        pago_receptor_nombre=None,
        pago_receptor_rfc="",
        factura_cfdi_id=sugerencia.factura_cfdi_id,
        factura_uuid="",
        factura_total=0,
        factura_fecha_emision=sugerencia.created_at,
        factura_emisor_nombre=None,
        factura_receptor_nombre=None,
    )
