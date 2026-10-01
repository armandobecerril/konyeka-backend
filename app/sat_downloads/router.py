from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.rfc_clients.service import get_client
from app.sat_downloads import service
from app.sat_downloads.schemas import (
    CfdiListOut,
    CfdisResumenOut,
    MonedaConteo,
    ResumenMonedasOut,
    ResumenTotalesOut,
    SolicitudCreate,
    SolicitudOut,
)
from app.users.models import User

router = APIRouter(tags=["Descarga SAT"])


@router.post(
    "/clientes/{rfc_client_id}/sat/solicitudes",
    response_model=SolicitudOut,
    status_code=status.HTTP_201_CREATED,
)
def crear_solicitud(
    rfc_client_id: int,
    payload: SolicitudCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)  # 404 si el cliente no existe
    return service.crear_solicitud(
        db,
        rfc_client_id=rfc_client_id,
        payload=payload,
        created_by_id=current_user.id,
        background_tasks=background_tasks,
    )


@router.get("/clientes/{rfc_client_id}/sat/solicitudes", response_model=list[SolicitudOut])
def listar_solicitudes(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.listar_solicitudes(db, rfc_client_id)


@router.get(
    "/clientes/{rfc_client_id}/sat/solicitudes/{solicitud_id}",
    response_model=SolicitudOut,
)
def obtener_solicitud(
    rfc_client_id: int,
    solicitud_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_solicitud_or_404(db, rfc_client_id, solicitud_id)


@router.get("/cfdis/resumen", response_model=CfdisResumenOut)
def resumen_cfdis_global(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Total de XMLs descargados en la bóveda, sumando todos los clientes.
    Alimenta la tarjeta 'XML descargados' del dashboard de inicio."""
    return CfdisResumenOut(total_xml=service.contar_cfdis_totales(db))


@router.get("/clientes/{rfc_client_id}/cfdis/resumen-monedas", response_model=ResumenMonedasOut)
def resumen_monedas_cliente(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resumen de monedas distintas a MXN entre todas las facturas
    descargadas del cliente (no solo la página actual). Alimenta el banner
    de alerta sobre 'Tus facturas descargadas'."""
    get_client(db, rfc_client_id)
    total_no_mxn, desglose = service.resumen_monedas(db, rfc_client_id)
    return ResumenMonedasOut(
        total_no_mxn=total_no_mxn,
        monedas=[MonedaConteo(moneda=moneda, cantidad=cantidad) for moneda, cantidad in desglose],
    )


@router.get("/clientes/{rfc_client_id}/cfdis/resumen-totales", response_model=ResumenTotalesOut)
def resumen_totales_cliente(
    rfc_client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Totales por tipo de comprobante y método de pago, sobre todas las
    facturas descargadas del cliente. Alimenta la barra de resumen tipo
    MyAdmin sobre 'Tus facturas descargadas'."""
    get_client(db, rfc_client_id)
    return ResumenTotalesOut(**service.resumen_totales(db, rfc_client_id))


@router.get("/clientes/{rfc_client_id}/cfdis", response_model=CfdiListOut)
def listar_cfdis(
    rfc_client_id: int,
    tipo: str | None = Query(default=None, description="'emitido' o 'recibido'"),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    q: str | None = Query(default=None, description="Busca por UUID, RFC o nombre"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = service.listar_cfdis(
        db,
        rfc_client_id,
        tipo=tipo,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        skip=skip,
        limit=limit,
    )
    return CfdiListOut(items=items, total=total)


@router.get("/clientes/{rfc_client_id}/cfdis/{cfdi_id}/xml")
def descargar_xml(
    rfc_client_id: int,
    cfdi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc, content = service.get_cfdi_xml(db, rfc_client_id, cfdi_id)
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{doc.uuid}.xml"'},
    )
