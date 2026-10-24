from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.bancos import service
from app.core.db import get_db
from app.core.deps import get_current_user
from app.rfc_clients.service import get_client
from app.users.models import User

router = APIRouter(tags=["Bancos / Estados de cuenta"])


class CuentaIn(BaseModel):
    banco: str
    alias: str | None = None
    numero: str | None = None
    clabe: str | None = None
    moneda: str = "MXN"


class VincularIn(BaseModel):
    cfdi_id: int


class ClasificarIn(BaseModel):
    clasificacion: str | None = None
    nota: str | None = None


def _cuenta_out(c) -> dict:
    return {
        "id": c.id,
        "banco": c.banco,
        "alias": c.alias,
        "numero": c.numero,
        "clabe": c.clabe,
        "moneda": c.moneda,
    }


@router.get("/clientes/{rfc_client_id}/bancos/catalogos")
def catalogos(current_user: User = Depends(get_current_user)):
    return {
        "clasificaciones": [{"valor": k, "etiqueta": v} for k, v in service.CLASIFICACIONES.items()],
        "bancos": [nombre for nombre, _ in __import__("app.bancos.parser", fromlist=["BANCOS"]).BANCOS],
    }


@router.get("/clientes/{rfc_client_id}/bancos/cuentas")
def listar_cuentas(rfc_client_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    get_client(db, rfc_client_id)
    return [_cuenta_out(c) for c in service.listar_cuentas(db, rfc_client_id)]


@router.post("/clientes/{rfc_client_id}/bancos/cuentas", status_code=status.HTTP_201_CREATED)
def crear_cuenta(
    rfc_client_id: int, payload: CuentaIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    get_client(db, rfc_client_id)
    if not payload.banco.strip():
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Indica el banco de la cuenta.")
    return _cuenta_out(service.crear_cuenta(db, rfc_client_id, **payload.model_dump()))


@router.delete("/clientes/{rfc_client_id}/bancos/cuentas/{cuenta_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_cuenta(
    rfc_client_id: int, cuenta_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    service.desactivar_cuenta(db, rfc_client_id, cuenta_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/clientes/{rfc_client_id}/bancos/estados")
def listar_estados(
    rfc_client_id: int,
    meses: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_estados(db, rfc_client_id, meses)


@router.post("/clientes/{rfc_client_id}/bancos/estados", status_code=status.HTTP_201_CREATED)
async def cargar_estado(
    rfc_client_id: int,
    cuenta_id: int = Form(...),
    periodo: str = Form(..., description="AAAA-MM"),
    archivo: UploadFile = File(...),
    conciliar: bool = Form(default=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Carga el estado de cuenta de un mes (CSV, Excel o PDF) y, por defecto,
    corre de inmediato la conciliación contra los CFDIs de ese mes."""
    get_client(db, rfc_client_id)
    contenido = await archivo.read()
    estado = service.cargar_estado_cuenta(
        db,
        rfc_client_id,
        cuenta_id=cuenta_id,
        periodo=periodo,
        nombre_archivo=archivo.filename or "estado_de_cuenta",
        contenido=contenido,
        user_id=current_user.id,
    )
    resultado = service.conciliar(db, rfc_client_id, periodo, current_user.id) if conciliar else None
    return {
        "id": estado.id,
        "periodo": estado.periodo,
        "num_movimientos": estado.num_movimientos,
        "total_cargos": estado.total_cargos,
        "total_abonos": estado.total_abonos,
        "banco_detectado": estado.banco_detectado,
        "advertencias": estado.advertencias or [],
        "conciliacion": resultado,
    }


@router.delete("/clientes/{rfc_client_id}/bancos/estados/{estado_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_estado(
    rfc_client_id: int, estado_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    service.eliminar_estado_cuenta(db, rfc_client_id, estado_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clientes/{rfc_client_id}/bancos/conciliar")
def conciliar(
    rfc_client_id: int,
    meses: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.conciliar(db, rfc_client_id, meses, current_user.id)


@router.get("/clientes/{rfc_client_id}/bancos/movimientos")
def movimientos(
    rfc_client_id: int,
    meses: str | None = Query(default=None),
    cuenta_id: int | None = Query(default=None),
    estado: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.listar_movimientos(db, rfc_client_id, meses=meses, cuenta_id=cuenta_id, estado=estado)


@router.get("/clientes/{rfc_client_id}/bancos/movimientos/{movimiento_id}/candidatos")
def candidatos(
    rfc_client_id: int,
    movimiento_id: int,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.candidatos_movimiento(db, rfc_client_id, movimiento_id, q)


@router.post("/clientes/{rfc_client_id}/bancos/movimientos/{movimiento_id}/vincular")
def vincular(
    rfc_client_id: int,
    movimiento_id: int,
    payload: VincularIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.vincular_manual(db, rfc_client_id, movimiento_id, payload.cfdi_id, current_user.id)
    return {"ok": True}


@router.put("/clientes/{rfc_client_id}/bancos/movimientos/{movimiento_id}/clasificacion")
def clasificar(
    rfc_client_id: int,
    movimiento_id: int,
    payload: ClasificarIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.clasificar_movimiento(db, rfc_client_id, movimiento_id, payload.clasificacion, payload.nota)
    return {"ok": True}


@router.post("/clientes/{rfc_client_id}/bancos/vinculos/{vinculo_id}/{accion}")
def resolver(
    rfc_client_id: int,
    vinculo_id: int,
    accion: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """accion: aprobar | rechazar | desvincular"""
    service.resolver_vinculo(db, rfc_client_id, vinculo_id, accion, current_user.id)
    return {"ok": True}


@router.get("/clientes/{rfc_client_id}/bancos/resumen")
def resumen(
    rfc_client_id: int,
    meses: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_client(db, rfc_client_id)
    return service.resumen(db, rfc_client_id, meses)


@router.get("/clientes/{rfc_client_id}/bancos/export")
def exportar(
    rfc_client_id: int,
    meses: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cliente = get_client(db, rfc_client_id)
    contenido = service.exportar_csv(db, rfc_client_id, meses)
    nombre = f"conciliacion_bancaria_{cliente.rfc}_{(meses or 'todo').replace(',', '_')}.csv"
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
