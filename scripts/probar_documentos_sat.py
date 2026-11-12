"""Certifica, contra el SAT real, la descarga de la Opinión de Cumplimiento y
la Constancia de Situación Fiscal con la e.firma ya guardada de un cliente.

No crea registros ni toca la UI: usa la e.firma de la base (la misma que usa
la plataforma), descarga cada documento con el método directo (HTTP + e.firma,
sin navegador ni CAPTCHA) y, con --rpa, también con el navegador de respaldo.
Guarda los PDF en data/certificacion/ e imprime el resultado de cada paso.

Uso (desde konyeka-backend, con el .venv activado):
    python -m scripts.probar_documentos_sat ISC091120KE1
    python -m scripts.probar_documentos_sat ISC091120KE1 --solo opinion
    python -m scripts.probar_documentos_sat ISC091120KE1 --rpa
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(os.getcwd())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import app.main  # noqa: E402,F401  (registra todos los modelos)

from app.core.db import SessionLocal  # noqa: E402
from app.efirma.service import obtener_signer  # noqa: E402
from app.rfc_clients.models import RfcClient  # noqa: E402
from app.sat_documentos import portal_http  # noqa: E402

SALIDA = Path("data") / "certificacion"


def _guardar(rfc: str, nombre: str, contenido: bytes) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"{rfc}_{nombre}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    ruta.write_bytes(contenido)
    return ruta


def _paginas(contenido: bytes) -> int:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        return len(pdf.pages)


def _paso(nombre: str, fn):
    inicio = time.monotonic()
    try:
        resultado = fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ {nombre}: FALLÓ en {time.monotonic() - inicio:.1f}s -> {type(exc).__name__}: {exc}")
        return None
    print(f"  ✓ {nombre}: OK en {time.monotonic() - inicio:.1f}s")
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rfc")
    parser.add_argument("--solo", choices=["opinion", "constancia"])
    parser.add_argument("--rpa", action="store_true", help="probar también el respaldo con navegador")
    args = parser.parse_args()
    rfc = args.rfc.upper().strip()

    db = SessionLocal()
    try:
        cliente = db.query(RfcClient).filter(RfcClient.rfc == rfc).first()
        if cliente is None:
            print(f"No existe el cliente {rfc} en esta base.")
            return 1
        signer = obtener_signer(db, cliente.id)
        print(f"e.firma de {rfc}: certificado {signer.certificate_number}, vence {signer.certificate.get_notAfter().decode()}")

        fallas = 0
        if args.solo in (None, "opinion"):
            print("\nOpinión de Cumplimiento (HTTP + e.firma):")
            r = _paso("descarga", lambda: portal_http.descargar_opinion(signer, rfc=rfc))
            if r:
                pdf, sentido = r
                print(f"    sentido: {sentido}  ·  {len(pdf):,} bytes  ·  {_paginas(pdf)} pág.  ·  {_guardar(rfc, 'opinion', pdf)}")
            else:
                fallas += 1

        if args.solo in (None, "constancia"):
            print("\nConstancia de Situación Fiscal (HTTP + e.firma):")
            pasos: list = []
            pdf = _paso("descarga", lambda: portal_http.descargar_constancia(signer, rfc=rfc, pasos=pasos))
            for nombre, url, status, ctype, _ in pasos:
                print(f"    · {nombre}: {status} {ctype[:30]:<30} {url[:110]}")
            if pdf:
                print(f"    {len(pdf):,} bytes  ·  {_paginas(pdf)} pág.  ·  {_guardar(rfc, 'constancia', pdf)}")
            else:
                fallas += 1
                carpeta = SALIDA / f"constancia_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                carpeta.mkdir(parents=True, exist_ok=True)
                for n, (nombre, url, status, ctype, cuerpo) in enumerate(pasos):
                    ext = "pdf" if cuerpo[:4] == b"%PDF" else "html"
                    (carpeta / f"{n:02d}_{nombre}.{ext}").write_bytes(cuerpo)
                print(f"    respuestas del SAT guardadas en {carpeta}/")

        if args.rpa:
            from app.sat_documentos.rpa import descargar_constancia_situacion_fiscal, descargar_opinion_cumplimiento
            from app.sat_documentos.service import _credenciales_efirma

            cer, key, pwd = _credenciales_efirma(db, cliente.id)
            if args.solo in (None, "opinion"):
                print("\nOpinión de Cumplimiento (navegador de respaldo):")
                r = _paso("descarga", lambda: descargar_opinion_cumplimiento(rfc=rfc, cer_bytes=cer, key_bytes=key, password=pwd))
                if r:
                    print(f"    sentido: {r[1]}  ·  {_guardar(rfc, 'opinion_rpa', r[0])}")
                else:
                    fallas += 1
            if args.solo in (None, "constancia"):
                print("\nConstancia de Situación Fiscal (navegador de respaldo):")
                pdf = _paso("descarga", lambda: descargar_constancia_situacion_fiscal(rfc=rfc, cer_bytes=cer, key_bytes=key, password=pwd))
                if pdf:
                    portal_http.validar_pdf(pdf, rfc=rfc, tipo="constancia")
                    print(f"    {_guardar(rfc, 'constancia_rpa', pdf)}")
                else:
                    fallas += 1

        print("\nRESULTADO:", "CERTIFICADO ✓" if fallas == 0 else f"{fallas} paso(s) fallaron ✗")
        return 0 if fallas == 0 else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
