from datetime import datetime, timezone

from pydantic import BaseModel, computed_field


class EfirmaStatusOut(BaseModel):
    rfc_titular: str
    nombre_titular: str | None
    numero_serie: str
    vigencia_desde: datetime
    vigencia_hasta: datetime
    uploaded_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def vigente(self) -> bool:
        ahora = datetime.now(timezone.utc)
        desde = self.vigencia_desde
        hasta = self.vigencia_hasta
        if desde.tzinfo is None:
            desde = desde.replace(tzinfo=timezone.utc)
        if hasta.tzinfo is None:
            hasta = hasta.replace(tzinfo=timezone.utc)
        return desde <= ahora <= hasta

    @computed_field
    @property
    def dias_para_vencer(self) -> int:
        hasta = self.vigencia_hasta
        if hasta.tzinfo is None:
            hasta = hasta.replace(tzinfo=timezone.utc)
        return (hasta - datetime.now(timezone.utc)).days
