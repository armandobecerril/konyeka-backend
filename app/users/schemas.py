from pydantic import BaseModel, ConfigDict, EmailStr


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    nombre: str
    rol: str
    activo: bool
    despacho_id: int | None = None
    rol_despacho: str | None = None
