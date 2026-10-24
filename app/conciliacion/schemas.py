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
    serie: str | None = None
    folio: str | None = None
    num_pagos: int = 0
    ultimo_pago: datetime | None = None
    # Hay un cargo/abono bancario conciliado contra esta factura pero el
    # proveedor/cliente todavía no emite el complemento (REP).
    pagada_en_banco_sin_rep: bool = False


class PagoAplicadoOut(BaseModel):
    """Un complemento de pago (REP) aplicado a una factura PPD."""

    pago_relacionado_id: int
    cfdi_pago_id: int
    uuid_pago: str
    origen: str  # "uuid" (cruce exacto) | "sugerencia" (aprobada por el contador)
    num_parcialidad: int
    fecha_pago: datetime | None
    forma_pago: str | None
    imp_saldo_ant: Decimal | None
    imp_pagado: Decimal
    imp_saldo_insoluto: Decimal | None
    fecha_emision_rep: datetime | None
    emisor_rfc: str | None
    emisor_nombre: str | None


class MovimientoAplicadoOut(BaseModel):
    movimiento_id: int
    fecha: datetime | None
    descripcion: str | None
    monto: Decimal
    banco: str | None
    tipo_vinculo: str


class DetallePagosFacturaOut(BaseModel):
    factura_id: int
    uuid: str
    total: Decimal
    monto_pagado: Decimal
    saldo: Decimal
    estado: str
    pagos: list[PagoAplicadoOut]
    movimientos_bancarios: list[MovimientoAplicadoOut] = []


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
