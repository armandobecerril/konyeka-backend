from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

SAT_PORTAL_URL = "https://portalcfdi.facturaelectronica.sat.gob.mx/"

BASE_DATA_DIR = Path("data")

def create_client_dirs(rfc: str):
    rfc = rfc.upper().strip()
    base = BASE_DATA_DIR / "clientes" / rfc

    dirs = {
        "base": base,
        "downloads": base / "downloads",
        "xml_emitidos": base / "xml_emitidos",
        "xml_recibidos": base / "xml_recibidos",
        "metadata": base / "metadata",
        "logs": base / "logs",
        "tmp": base / "tmp",
    }

    for folder in dirs.values():
        folder.mkdir(parents=True, exist_ok=True)

    return dirs

def open_sat_portal_test(rfc: str):
    dirs = create_client_dirs(rfc)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = dirs["logs"] / f"sat_portal_{timestamp}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=300
        )

        context = browser.new_context(
            accept_downloads=True
        )

        page = context.new_page()
        page.goto(SAT_PORTAL_URL, wait_until="networkidle")

        page.screenshot(path=str(screenshot_path), full_page=True)

        browser.close()

    return {
        "status": "ok",
        "message": "Portal SAT abierto correctamente desde backend.",
        "rfc": rfc.upper().strip(),
        "screenshot": str(screenshot_path)
    }