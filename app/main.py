from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.accounts.router import router as accounts_router
from app.core.config import settings
from app.rfc_clients.router import router as rfc_clients_router
from app.sat_portal_ciec.router import router as sat_portal_router
from app.sat_xml import simulate_sat_xml_download
from app.users.router import router as users_router
from app.vault import get_origin_account_vault

app = FastAPI(
    title="KONYEKA Backend",
    description=(
        "API de KONYEKA: autenticación y catálogo de clientes real, "
        "más los conectores SAT (portal, vault y descarga XML) todavía simulados."
    ),
    version="0.2.0",
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
app.include_router(sat_portal_router)


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
