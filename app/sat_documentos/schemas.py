from datetime import datetime

from pydantic import BaseModel


class DocumentoSatOut(BaseModel):
    id: int
    tipo: str
    estado: str
    resultado: str | None
    mensaje_error: str | None
    created_at: datetime
    completado_at: datetime | None

    model_config = {"from_attributes": True}
