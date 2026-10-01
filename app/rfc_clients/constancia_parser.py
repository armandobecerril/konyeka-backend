"""Lector determinístico de la Constancia de Situación Fiscal del SAT (PDF).

El formato de este PDF lo genera el propio SAT y no cambia entre
contribuyentes, así que no hace falta IA para leerlo: basta con ubicar
etiquetas y tablas fijas. Dos particularidades del PDF que genera el SAT:

1. El RFC vive en la tabla "Datos de Identificación del Contribuyente:",
   en la fila cuya primera celda es "RFC:".
2. La Denominación/Razón Social, en esa misma tabla, pierde los espacios
   entre palabras (un detalle de cómo el SAT codifica ese campo libre en
   el PDF) — así que se reconstruye en cambio a partir del recuadro de la
   Cédula de Identificación Fiscal (la caja con el código QR), donde el
   nombre sí viene con espacios reales, ubicándolo entre las etiquetas
   "Registro Federal de Contribuyentes" y "Nombre, denominación o razón
   social".
3. El régimen fiscal vive en la tabla "Regímenes:", con el nombre oficial
   tal cual aparece en el catálogo del SAT (con espacios correctos) — se
   homologa contra REGIMENES_FISCALES_VALIDOS para devolverlo en el mismo
   formato "código - descripción" que usa el resto de la app.
"""
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO

import pdfplumber

from app.rfc_clients.schemas import REGIMENES_FISCALES_VALIDOS

RFC_PATTERN = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


@dataclass
class ConstanciaExtraida:
    rfc: str | None
    razon_social: str | None
    regimen_fiscal: str | None  # "código - descripción", ya homologado


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def _rfc_desde_tablas(tablas: list) -> str | None:
    for tabla in tablas:
        for fila in tabla:
            if not fila or not fila[0]:
                continue
            etiqueta = fila[0].strip()
            if etiqueta.upper().startswith("RFC") and len(fila) > 1 and fila[1]:
                candidato = fila[1].strip().upper()
                if RFC_PATTERN.match(candidato):
                    return candidato
    return None


def _regimen_desde_tablas(tablas: list) -> str | None:
    mapa_normalizado = {_normalizar(desc.split(" - ", 1)[1]): desc for desc in REGIMENES_FISCALES_VALIDOS}
    candidatos: list[tuple[str, str]] = []  # (valor homologado, fecha_fin)
    dentro = False
    for tabla in tablas:
        for fila in tabla:
            if not fila or not fila[0]:
                continue
            primera = fila[0].strip()
            normalizado = _normalizar(primera)
            if normalizado.startswith("regimenes"):
                dentro = True
                continue
            if normalizado == "regimen":
                continue  # encabezado de columna, no es un dato
            if not dentro or not primera:
                continue
            sin_prefijo = re.sub(r"^regimen\s+", "", normalizado)
            match = mapa_normalizado.get(normalizado) or mapa_normalizado.get(sin_prefijo)
            if match:
                fecha_fin = fila[2].strip() if len(fila) > 2 and fila[2] else ""
                candidatos.append((match, fecha_fin))
    if not candidatos:
        return None
    # Si hay historial de regímenes, el vigente es el que no tiene fecha fin.
    for valor, fecha_fin in candidatos:
        if not fecha_fin:
            return valor
    return candidatos[-1][0]


def _razon_social_desde_cedula(pagina) -> str | None:
    palabras = pagina.extract_words()
    if not palabras:
        return None

    tops_contribuyentes = [p["top"] for p in palabras if _normalizar(p["text"]) == "contribuyentes"]
    tops_nombre = [p["top"] for p in palabras if _normalizar(p["text"]) == "nombre,"]
    if not tops_contribuyentes or not tops_nombre:
        return None

    inicio = min(tops_contribuyentes)
    candidatos_fin = [t for t in tops_nombre if t > inicio]
    if not candidatos_fin:
        return None
    fin = min(candidatos_fin)

    columna_izquierda = [p for p in palabras if inicio < p["top"] < fin and p["x0"] < 300]
    if not columna_izquierda:
        return None
    columna_izquierda.sort(key=lambda p: (round(p["top"], 1), p["x0"]))
    texto = " ".join(p["text"] for p in columna_izquierda).strip()
    return texto or None


def extraer_constancia(contenido_pdf: bytes) -> ConstanciaExtraida:
    rfc = razon_social = regimen_fiscal = None

    with pdfplumber.open(BytesIO(contenido_pdf)) as pdf:
        for pagina in pdf.pages:
            tablas = pagina.extract_tables()
            if rfc is None:
                rfc = _rfc_desde_tablas(tablas)
            if regimen_fiscal is None:
                regimen_fiscal = _regimen_desde_tablas(tablas)
            if razon_social is None:
                razon_social = _razon_social_desde_cedula(pagina)

    return ConstanciaExtraida(rfc=rfc, razon_social=razon_social, regimen_fiscal=regimen_fiscal)
