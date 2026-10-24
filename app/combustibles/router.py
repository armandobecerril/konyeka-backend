from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.combustibles import service
from app.core.db import get_db
from app.core.deps import get_current_user
from app.rfc_clients.service import get_client
from app.users.models import User

router = APIRouter(tags=["Combustibles / DIOT"])


@router.get("/clientes/{rfc_client_id}/combustibles/reporte")
def reporte(
    rfc_client_id: int,
    meses: str | None = Query(default=None, description="'2026-08,2026-09' (acumulables)"),
    tipo: str = Query(default="recibido", description="'recibido' (gasto) o 'emitido'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.reporte_combustibles(db, rfc_client_id, meses=meses, tipo=tipo or None)


@router.get("/clientes/{rfc_client_id}/combustibles/export")
def exportar(
    rfc_client_id: int,
    formato: str = Query(default="detalle_csv", description="detalle_csv | diot_csv | diot_txt"),
    meses: str | None = Query(default=None),
    tipo: str = Query(default="recibido"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cliente = get_client(db, rfc_client_id)
    reporte_data = service.reporte_combustibles(db, rfc_client_id, meses=meses, tipo=tipo or None)
    sufijo = (meses or "todo").replace(",", "_")
    if formato == "diot_txt":
        contenido, media, ext = service.exportar_txt_diot(reporte_data), "text/plain; charset=utf-8", "txt"
    elif formato == "diot_csv":
        contenido, media, ext = service.exportar_csv_diot(reporte_data), "text/csv; charset=utf-8", "csv"
    else:
        contenido, media, ext = service.exportar_csv_detalle(reporte_data), "text/csv; charset=utf-8", "csv"
    nombre = f"combustibles_{formato}_{cliente.rfc}_{sufijo}.{ext}"
    return Response(content=contenido, media_type=media, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
