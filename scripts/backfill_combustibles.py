"""Re-marca `tiene_complemento_combustible` en los CFDIs ya descargados,
ahora que también cuentan las facturas directas de gasolinera (conceptos
ClaveProdServ 151015xx) además de los complementos EstadoDeCuentaCombustible
y ConsumoDeCombustibles. Relee el XML guardado; no descarga nada del SAT.

Uso:
    python -m scripts.backfill_combustibles --dry-run
    python -m scripts.backfill_combustibles
"""
import argparse
import logging
import os
import sys

sys.path.append(os.getcwd())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.core.db import SessionLocal  # noqa: E402
from app.core.storage import get_xml_storage  # noqa: E402
from app.sat_downloads.models import CfdiDocument  # noqa: E402
from app.sat_downloads.service import _tiene_complemento_combustible  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("backfill_combustibles")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    storage = get_xml_storage()
    marcados = 0
    try:
        docs = (
            db.query(CfdiDocument)
            .filter(
                CfdiDocument.tiene_complemento_combustible.is_(False),
                CfdiDocument.tipo_comprobante.in_(("I", "E")),
            )
            .all()
        )
        for doc in docs:
            try:
                xml_bytes = storage.read(doc.storage_path)
            except Exception:  # noqa: BLE001
                logger.warning("No se pudo leer %s", doc.storage_path)
                continue
            if _tiene_complemento_combustible(xml_bytes, list(doc.complementos or [])):
                marcados += 1
                if not args.dry_run:
                    doc.tiene_complemento_combustible = True
        if not args.dry_run:
            db.commit()
        logger.info("Revisados %s CFDIs, %s marcados como combustible%s", len(docs), marcados,
                    " (dry-run)" if args.dry_run else "")
    finally:
        db.close()


if __name__ == "__main__":
    main()
