from pydantic import BaseModel

class SatPortalLoginRequest(BaseModel):
    rfc: str
    password: str