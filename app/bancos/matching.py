"""Motor de cruce estado de cuenta ↔ CFDI (lógica pura, sin BD).

Para cada movimiento bancario busca el CFDI que lo respalda:
- cargo (sale dinero)  -> CFDIs RECIBIDOS: factura PUE, complemento de pago
  (REP) recibido, factura PPD todavía sin REP, y nómina EMITIDA.
- abono (entra dinero) -> CFDIs EMITIDOS: factura PUE, REP emitido, factura
  PPD emitida sin REP.

Puntaje 0-99 = monto (hasta 50) + cercanía de fecha (hasta 30) + RFC o
nombre de la contraparte en el concepto bancario (hasta 20), con ajustes
por tipo. La asignación es 1 a 1 (un movimiento con un CFDI) y voraz por
puntaje: primero se fijan las parejas más seguras.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

UMBRAL_SUGERENCIA = 55
UMBRAL_AUTOMATICO = 80
MARGEN_AUTOMATICO = 10

PALABRAS_VACIAS = {
    "SA", "DE", "CV", "RL", "S", "A", "C", "V", "SAB", "SAPI", "SC", "AC", "LA", "EL", "LOS", "LAS", "DEL",
    "Y", "E", "EN", "MEXICO", "SERVICIOS", "GRUPO", "COMERCIAL", "CIA", "COMPANIA", "SOCIEDAD", "ANONIMA",
    "VARIABLE", "CAPITAL", "RESPONSABILIDAD", "LIMITADA", "SPEI", "PAGO", "TRANSFERENCIA", "ENVIADO",
}


@dataclass
class Movimiento:
    id: int
    fecha: date
    tipo: str  # cargo | abono
    monto: Decimal
    descripcion: str


@dataclass
class Candidato:
    cfdi_id: int
    tipo_vinculo: str  # pue | rep | ppd_sin_rep | nomina
    sentido: str  # cargo | abono: qué tipo de movimiento puede respaldar
    fecha: date
    monto: Decimal
    rfc: str | None
    nombre: str | None
    etiqueta: str = ""


@dataclass
class Pareja:
    movimiento_id: int
    cfdi_id: int
    tipo_vinculo: str
    monto: Decimal
    score: int
    motivo: str
    automatico: bool


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto.upper())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9& ]", " ", texto)


def tokens_nombre(nombre: str | None) -> set[str]:
    return {t for t in _normalizar(nombre).split() if len(t) >= 4 and t not in PALABRAS_VACIAS}


# Cómo aparecen en el banco contrapartes frecuentes cuya razón social no
# se parece al concepto bancario.
ALIAS_CONOCIDOS = {
    "INSTITUTO DEL FONDO NACIONAL DE LA VIVIENDA": ("INFONAVIT",),
    "INSTITUTO MEXICANO DEL SEGURO SOCIAL": ("IMSS", "SUA", "SIPARE"),
    "SERVICIO DE ADMINISTRACION TRIBUTARIA": ("SAT", "DECLARACION", "LINEA DE CAPTURA"),
    "COMISION FEDERAL DE ELECTRICIDAD": ("CFE",),
    "TELEFONOS DE MEXICO": ("TELMEX",),
    "TOTAL PLAY": ("TOTALPLAY",),
    "RADIOMOVIL DIPSA": ("TELCEL",),
    "NUEVA WAL MART": ("WALMART", "WAL MART"),
    "HOME DEPOT": ("HOMEDEPOT", "HOME DEPOT"),
    "AMAZON": ("AMAZON", "AMZN"),
    "GOOGLE": ("GOOGLE",),
    "EFECTIVALE": ("EFECTIVALE",),
    "EDENRED": ("EDENRED",),
}
PALABRAS_SIGLA_VACIAS = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "Y", "E", "PARA", "SA", "CV", "RL", "SAB", "SAPI"}


def alias_de(nombre: str | None) -> set[str]:
    """Formas en que la contraparte puede aparecer en el concepto bancario:
    siglas (Instituto Mexicano del Seguro Social -> IMSS), nombre pegado
    (Total Play -> TOTALPLAY) y alias conocidos (INFONAVIT, CFE, TELMEX...)."""
    n = _normalizar(nombre)
    if not n:
        return set()
    palabras = [p for p in n.split() if p not in PALABRAS_SIGLA_VACIAS]
    alias: set[str] = set()
    if len(palabras) >= 3:
        alias.add("".join(p[0] for p in palabras))
    if len(palabras) >= 2:
        alias.add(palabras[0] + palabras[1])
    for clave, formas in ALIAS_CONOCIDOS.items():
        if clave in n:
            alias.update(f.replace(" ", "") for f in formas)
    return {a for a in alias if len(a) >= 3}


def puntuar(mov: Movimiento, cand: Candidato, *, tolerancia_relativa: Decimal = Decimal("0.005")) -> tuple[int, str]:
    """(score, motivo). score 0 = no es candidato."""
    if mov.tipo != cand.sentido or cand.monto is None or cand.monto <= 0:
        return 0, ""

    razones: list[str] = []
    diferencia = abs(mov.monto - cand.monto)
    if diferencia <= Decimal("0.01"):
        score = 50
        razones.append("el importe es idéntico")
    elif diferencia <= Decimal("1.00"):
        score = 42
        razones.append(f"el importe difiere solo ${diferencia:,.2f}")
    elif diferencia <= cand.monto * tolerancia_relativa:
        score = 30
        razones.append("el importe es casi igual")
    else:
        return 0, ""

    dias = (mov.fecha - cand.fecha).days
    puntos_fecha = 0
    if cand.tipo_vinculo in ("rep", "nomina"):
        d = abs(dias)
        if d <= 1:
            puntos_fecha, texto = 30, "la fecha de pago del complemento coincide con el banco"
        elif d <= 3:
            puntos_fecha, texto = 22, f"la fecha de pago está a {d} días del movimiento"
        elif d <= 7:
            puntos_fecha, texto = 12, f"la fecha de pago está a {d} días del movimiento"
        else:
            texto = ""
    elif cand.tipo_vinculo == "ppd_sin_rep":
        if 0 <= dias <= 30:
            puntos_fecha, texto = 18, "el pago salió en el mes siguiente a la factura PPD"
        elif 0 <= dias <= 120:
            puntos_fecha, texto = 8, f"el pago salió {dias} días después de la factura PPD"
        else:
            texto = ""
    else:  # pue
        if 0 <= dias <= 3:
            puntos_fecha, texto = 30, "se pagó el mismo día o a los pocos días de la factura"
        elif -3 <= dias < 0:
            puntos_fecha, texto = 25, "la factura se emitió justo después del pago"
        elif 0 <= dias <= 15:
            puntos_fecha, texto = 22, f"se pagó {dias} días después de la factura"
        elif 0 <= dias <= 45:
            puntos_fecha, texto = 12, f"se pagó {dias} días después de la factura"
        else:
            texto = ""
    if puntos_fecha:
        razones.append(texto)
    score += puntos_fecha

    descripcion = _normalizar(mov.descripcion)
    descripcion_compacta = descripcion.replace(" ", "")
    rfc = (cand.rfc or "").upper().strip()
    if rfc and len(rfc) >= 12 and rfc in descripcion_compacta:
        score += 20
        razones.append("el RFC aparece en el concepto bancario")
    else:
        palabras_desc = set(descripcion.split())
        comunes = tokens_nombre(cand.nombre) & palabras_desc
        alias = {a for a in alias_de(cand.nombre) if a in palabras_desc or (len(a) >= 6 and a in descripcion_compacta)}
        if comunes or alias:
            score += 16 if (len(comunes) >= 2 or (comunes and alias)) else 14
            razones.append(f"el concepto bancario menciona {', '.join(sorted(comunes | alias)[:2])}")

    if cand.tipo_vinculo == "rep":
        score += 5
    elif cand.tipo_vinculo == "ppd_sin_rep":
        score -= 10
        razones.append("la factura es PPD y aún no tiene complemento de pago")

    return max(0, min(score, 99)), "Coincide porque " + ", ".join(razones) + "."


def asignar(
    movimientos: list[Movimiento],
    candidatos: list[Candidato],
    *,
    excluir: set[tuple[int, int]] | None = None,
) -> list[Pareja]:
    """Asignación voraz 1 a 1 por puntaje. `excluir` = parejas
    (movimiento_id, cfdi_id) que el contador ya rechazó."""
    excluir = excluir or set()
    por_sentido: dict[str, list[Candidato]] = {"cargo": [], "abono": []}
    for c in candidatos:
        por_sentido.setdefault(c.sentido, []).append(c)

    todas: list[tuple[int, Movimiento, Candidato, str]] = []
    mejores_por_mov: dict[int, list[int]] = {}
    for mov in movimientos:
        for cand in por_sentido.get(mov.tipo, []):
            if (mov.id, cand.cfdi_id) in excluir:
                continue
            score, motivo = puntuar(mov, cand)
            if score >= UMBRAL_SUGERENCIA:
                todas.append((score, mov, cand, motivo))
                mejores_por_mov.setdefault(mov.id, []).append(score)

    todas.sort(key=lambda x: (-x[0], abs((x[1].fecha - x[2].fecha).days), x[1].id))
    usados_mov: set[int] = set()
    usados_cfdi: set[int] = set()
    parejas: list[Pareja] = []
    for score, mov, cand, motivo in todas:
        if mov.id in usados_mov or cand.cfdi_id in usados_cfdi:
            continue
        puntajes = sorted(mejores_por_mov.get(mov.id, []), reverse=True)
        segundo = puntajes[1] if len(puntajes) > 1 else 0
        automatico = score >= UMBRAL_AUTOMATICO and (score - segundo) >= MARGEN_AUTOMATICO
        parejas.append(
            Pareja(
                movimiento_id=mov.id,
                cfdi_id=cand.cfdi_id,
                tipo_vinculo=cand.tipo_vinculo,
                monto=cand.monto,
                score=score,
                motivo=motivo,
                automatico=automatico,
            )
        )
        usados_mov.add(mov.id)
        usados_cfdi.add(cand.cfdi_id)
    return parejas


def rankear(mov: Movimiento, candidatos: list[Candidato], limite: int = 15) -> list[tuple[int, str, Candidato]]:
    """Candidatos para vincular a mano: mismo sentido, importe hasta ±5%,
    ordenados por puntaje (sin umbral)."""
    salida = []
    for cand in candidatos:
        score, motivo = puntuar(mov, cand, tolerancia_relativa=Decimal("0.05"))
        if score > 0:
            salida.append((score, motivo, cand))
    salida.sort(key=lambda x: -x[0])
    return salida[:limite]
