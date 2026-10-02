from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

TIPOS_SOLICITUD = ("emitidas", "recibidas")


class CfdisResumenOut(BaseModel):
    total_xml: int


class MonedaConteo(BaseModel):
    moneda: str
    cantidad: int


class ResumenMonedasOut(BaseModel):
    total_no_mxn: int
    monedas: list[MonedaConteo]


class ResumenTotalesOut(BaseModel):
    """Resumen tipo 'Totales' de MyAdmin: cuenta e importe por tipo de
    comprobante y por método de pago, sobre las facturas que cumplen los
    filtros activos (los mismos que la tabla de abajo), no todo el
    historial del cliente."""

    ingresos_count: int
    ingresos_total: Decimal
    egresos_count: int
    egresos_total: Decimal
    traslados_count: int
    traslados_total: Decimal
    pagos_count: int
    pagos_total: Decimal
    ppd_count: int
    ppd_total: Decimal
    pue_count: int
    pue_total: Decimal
    xml_count: int
    xml_total: Decimal


class SolicitudCreate(BaseModel):
    tipo: str
    fecha_inicial: date
    fecha_final: date
    tipo_comprobante: str | None = None

    @field_validator("tipo")
    @classmethod
    def validar_tipo(cls, v: str) -> str:
        if v not in TIPOS_SOLICITUD:
            raise ValueError("tipo debe ser 'emitidas' o 'recibidas'")
        return v

    @field_validator("fecha_final")
    @classmethod
    def validar_rango(cls, v: date, info) -> date:
        fecha_inicial = info.data.get("fecha_inicial")
        if fecha_inicial and v < fecha_inicial:
            raise ValueError("fecha_final no puede ser anterior a fecha_inicial")
        return v


class SolicitudOut(BaseModel):
    id: int
    tipo: str
    fecha_inicial: date
    fecha_final: date
    tipo_comprobante: str | None
    estado: str
    id_solicitud_sat: str | None
    numero_cfdis: int | None
    mensaje_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CfdiDocumentOut(BaseModel):
    id: int
    uuid: str
    tipo: str
    tipo_comprobante: str | None
    serie: str | None
    folio: str | None
    version: str | None
    lugar_expedicion: str | None
    exportacion: str | None
    condiciones_pago: str | None
    descuento: Decimal | None
    tipo_cambio: Decimal | None
    emisor_rfc: str
    emisor_nombre: str | None
    regimen_fiscal_emisor: str | None
    receptor_rfc: str
    receptor_nombre: str | None
    regimen_fiscal_receptor: str | None
    domicilio_fiscal_receptor: str | None
    fecha_emision: datetime
    total: Decimal
    subtotal: Decimal | None
    moneda: str | None
    metodo_pago: str | None
    forma_pago: str | None
    uso_cfdi: str | None
    total_impuestos_trasladados: Decimal | None
    total_impuestos_retenidos: Decimal | None
    impuestos_desglose: dict | None
    complementos: list[str] | None
    tiene_complemento_combustible: bool
    estado_sat: str

    model_config = {"from_attributes": True}


class CfdiListOut(BaseModel):
    items: list[CfdiDocumentOut]
    total: int
