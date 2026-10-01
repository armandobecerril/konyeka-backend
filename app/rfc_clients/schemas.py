import re

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

# RFC de persona física (13 caracteres) o moral (12 caracteres), formato SAT.
RFC_PATTERN = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")

# Catálogo oficial del SAT c_RegimenFiscal (CFDI 4.0), espejo del usado en el
# selector del frontend (app/home/clientes/regimenFiscal.ts). Se valida aquí
# también para que nunca quede guardado un valor que no venga de ese archivo
# oficial (por ejemplo, capturado antes de que existiera el selector).
REGIMENES_FISCALES_VALIDOS = {
    "601 - General de Ley Personas Morales",
    "603 - Personas Morales con Fines no Lucrativos",
    "605 - Sueldos y Salarios e Ingresos Asimilados a Salarios",
    "606 - Arrendamiento",
    "607 - Régimen de Enajenación o Adquisición de Bienes",
    "608 - Demás ingresos",
    "609 - Consolidación",
    "610 - Residentes en el Extranjero sin Establecimiento Permanente en México",
    "611 - Ingresos por Dividendos (socios y accionistas)",
    "612 - Personas Físicas con Actividades Empresariales y Profesionales",
    "614 - Ingresos por intereses",
    "615 - Régimen de los ingresos por obtención de premios",
    "616 - Sin obligaciones fiscales",
    "620 - Sociedades Cooperativas de Producción que optan por diferir sus ingresos",
    "621 - Incorporación Fiscal",
    "622 - Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras",
    "623 - Opcional para Grupos de Sociedades",
    "624 - Coordinados",
    "625 - Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas",
    "626 - Régimen Simplificado de Confianza (RESICO)",
    "628 - Hidrocarburos",
    "629 - De los Regímenes Fiscales Preferentes y de las Empresas Multinacionales",
    "630 - Enajenación de acciones en bolsa de valores",
}


def infer_tipo_persona(rfc: str) -> str:
    return "moral" if len(rfc) == 12 else "fisica"


def validar_regimen_fiscal(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value not in REGIMENES_FISCALES_VALIDOS:
        raise ValueError(
            "Régimen fiscal no válido: debe ser uno de los regímenes oficiales del catálogo del SAT"
        )
    return value


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

    @field_validator("regimen_fiscal")
    @classmethod
    def validar_regimen_fiscal_base(cls, value: str | None) -> str | None:
        return validar_regimen_fiscal(value)


class RfcClientCreate(RfcClientBase):
    pass


class RfcClientUpdate(BaseModel):
    razon_social: str | None = None
    regimen_fiscal: str | None = None
    email: EmailStr | None = None
    telefono: str | None = None
    activo: bool | None = None

    @field_validator("regimen_fiscal")
    @classmethod
    def validar_regimen_fiscal_update(cls, value: str | None) -> str | None:
        return validar_regimen_fiscal(value)


class RfcClientOut(RfcClientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_persona: str
    activo: bool
