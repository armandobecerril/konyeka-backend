from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class FacturaConciliacionOut(BaseModel):
    id: int
    uuid: str
    tipo: str
    emisor_rfc: str
    emisor_nombre: str | None
    receptor_rfc: str
    receptor_nombre: str | None
    fecha_emision: datetime
    total: Decimal
    monto_pagado: Decimal
    saldo: Decimal
    estado: str  # conciliada | parcial | pendiente
    dias_desde_emision: int


class PagoHuerfanoOut(BaseModel):
    id: int  # id de PagoRelacionado
    cfdi_pago_id: int
    uuid_pago: str
    uuid_factura_relacionada: str
    num_parcialidad: int
    imp_pagado: Decimal
    fecha_pago: datetime | None
    forma_pago: str | None
    emisor_rfc: str
    emisor_nombre: str | None
    receptor_rfc: str
    receptor_nombre: str | None
    tipo: str
    tiene_sugerencia: bool


class ResumenConciliacionOut(BaseModel):
    total_facturas_ppd: int
    monto_total_ppd: Decimal
    conciliadas: int
    monto_conciliado: Decimal
    parciales: int
    monto_parcial: Decimal
    pendientes: int
    monto_pendiente: Decimal
    pagos_huerfanos: int
    monto_pagos_huerfanos: Decimal
    facturas_pue: int
    monto_pue: Decimal
    sugerencias_pendientes: int


class SugerenciaOut(BaseModel):
    id: int
    score: int
    motivo: str
    estado: str
    created_at: datetime

    pago_relacionado_id: int
    uuid_pago: str
    imp_pagado: Decimal
    fecha_pago: datetime | None
    pago_emisor_nombre: str | None
    pago_emisor_rfc: str
    pago_receptor_nombre: str | None
    pago_receptor_rfc: str

    factura_cfdi_id: int
    factura_uuid: str
    factura_total: Decimal
    factura_fecha_emision: datetime
    factura_emisor_nombre: str | None
    factura_receptor_nombre: str | None


class GenerarSugerenciasOut(BaseModel):
    nuevas: int
    total_pendientes: int
