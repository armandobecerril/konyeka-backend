import re

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.accounts.router import router as accounts_router
from app.admin.router import publico as admin_auth_router
from app.admin.router import router as admin_router
from app.bancos.router import router as bancos_router
from app.combustibles.router import router as combustibles_router
from app.conciliacion.router import router as conciliacion_router
from app.core.config import settings
from app.core.tenancy import guardia_despacho
from app.despachos.router import router as despachos_router
from app.efirma.router import router as efirma_router
from app.rfc_clients.router import router as rfc_clients_router
from app.sat_documentos.router import router as sat_documentos_router
from app.sat_downloads.router import router as sat_downloads_router
from app.sat_portal_ciec.router import router as sat_portal_router
from app.sat_xml import simulate_sat_xml_download
from app.users.router import router as users_router
from app.vault import get_origin_account_vault

app = FastAPI(
    title="KONYEKA Backend",
    description=(
        "API de KONYEKA: autenticación, catálogo de clientes, bóveda de e.firma "
        "y descarga masiva real de CFDIs del SAT."
    ),
    version="0.4.0",
    # Separación de datos entre despachos para TODAS las rutas /clientes/{id}/...
    dependencies=[Depends(guardia_despacho)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts_router)
app.include_router(users_router)
app.include_router(rfc_clients_router)
app.include_router(efirma_router)
app.include_router(sat_downloads_router)
app.include_router(sat_documentos_router)
app.include_router(conciliacion_router)
app.include_router(bancos_router)
app.include_router(combustibles_router)
app.include_router(sat_portal_router)
app.include_router(despachos_router)
app.include_router(admin_auth_router)
app.include_router(admin_router)


# Pasos de activación del embudo que ocurren dentro del producto: se
# registran cuando la operación respondió bien (el servidor es la fuente).
_PASOS_PRODUCTO = [
    ("POST", re.compile(r"^/clientes/(\d+)/efirma/?$"), "efirma_uploaded"),
    ("POST", re.compile(r"^/clientes/(\d+)/sat/solicitudes/?$"), "download_requested"),
    ("POST", re.compile(r"^/clientes/(\d+)/bancos/estados/?$"), "bank_statement_uploaded"),
    ("POST", re.compile(r"^/clientes/(\d+)/sat/documentos/[^/]+/?$"), "sat_document_downloaded"),
]


@app.middleware("http")
async def pasos_embudo(request: Request, call_next):
    respuesta = await call_next(request)
    if request.method == "POST" and 200 <= respuesta.status_code < 300:
        for metodo, patron, paso in _PASOS_PRODUCTO:
            m = patron.match(request.url.path)
            if m and metodo == request.method:
                _registrar_paso_producto(paso, int(m.group(1)))
                break
    return respuesta


def _registrar_paso_producto(paso: str, rfc_client_id: int) -> None:
    from app.core.db import SessionLocal
    from app.embudo import service as embudo
    from app.rfc_clients.models import RfcClient

    db = SessionLocal()
    try:
        despacho_id = db.query(RfcClient.despacho_id).filter(RfcClient.id == rfc_client_id).scalar()
        if despacho_id is not None:
            embudo.registrar(db, paso, despacho_id=despacho_id, una_vez=True)
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    finally:
        db.close()


class XmlDownloadRequest(BaseModel):
    rfc: str
    start_date: str
    end_date: str


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "KONYEKA Fiscal AI Backend",
    }


@app.get("/vault/{rfc}")
def vault(rfc: str):
    return get_origin_account_vault(rfc)


@app.post("/sat/xml/download")
def download_xml(payload: XmlDownloadRequest):
    return simulate_sat_xml_download(
        rfc=payload.rfc,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
