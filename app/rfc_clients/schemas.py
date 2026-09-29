import re

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

# RFC de persona física (13 caracteres) o moral (12 caracteres), formato SAT.
RFC_PATTERN = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


def infer_tipo_persona(rfc: str) -> str:
    return "moral" if len(rfc) == 12 else "fisica"


class ClientesResumenOut(BaseModel):
    total_activos: int


class RfcClientBase(BaseModel):
    rfc: str
    razon_social: str
    regimen_fiscal: str | None = None
    email: EmailStr | None = None
    telefono: str | None = None

    @field_validator("rfc")
    @classmethod
    def validar_rfc(cls, value: str) -> str:
        value = value.strip().upper()
        if not RFC_PATTERN.match(value):
            raise ValueError("RFC inválido: debe tener el formato de persona física o moral del SAT")
        return value

    @field_validator("razon_social")
    @classmethod
    def validar_razon_social(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La razón social es obligatoria")
        return value


class RfcClientCreate(RfcClientBase):
    pass


class RfcClientUpdate(BaseModel):
    razon_social: str | None = None
    regimen_fiscal: str | None = None
    email: EmailStr | None = None
    telefono: str | None = None
    activo: bool | None = None


class RfcClientOut(RfcClientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_persona: str
    activo: bool
