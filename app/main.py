from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.auth import validate_login
from app.vault import get_origin_account_vault
from app.sat_xml import simulate_sat_xml_download
from app.sat_portal_ciec.router import router as sat_portal_router


app = FastAPI(
    title="KONYEKA Backend",
    description="API inicial para simular bóveda fiscal y descarga XML SAT",
    version="0.1.0"
)

class LoginRequest(BaseModel):
    rfc: str
    password: str

class XmlDownloadRequest(BaseModel):
    rfc: str
    start_date: str
    end_date: str

class SatPortalTestRequest(BaseModel):
    rfc: str

app.include_router(sat_portal_router)

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "KONYEKA Fiscal AI Backend"
    }

@app.post("/auth/login")
def login(payload: LoginRequest):
    if not validate_login(payload.rfc, payload.password):
        raise HTTPException(status_code=401, detail="RFC o contraseña incorrectos")

    return {
        "access": True,
        "message": "Acceso correcto",
        "rfc": payload.rfc
    }

@app.get("/vault/{rfc}")
def vault(rfc: str):
    return get_origin_account_vault(rfc)

@app.post("/sat/xml/download")
def download_xml(payload: XmlDownloadRequest):
    return simulate_sat_xml_download(
        rfc=payload.rfc,
        start_date=payload.start_date,
        end_date=payload.end_date
    )
