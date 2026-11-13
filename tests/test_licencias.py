"""Pruebas de integración de despachos, separación de datos, licencias, pagos
(pasarela simulada), cupones, solicitudes de acceso y portal de superadmin.

Requieren PostgreSQL: TEST_DATABASE_URL (p. ej.
postgresql+psycopg://konyeka@localhost:5433/konyeka_test). La base se
recrea con las migraciones. Sin esa variable, se omiten.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Define TEST_DATABASE_URL para correr las pruebas de licencias")

if URL:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import app.core.db as core_db
    from app.core.config import settings
    from app.core.db import get_db
    from app.main import app


@pytest.fixture(scope="module")
def cliente():
    base, nombre = URL.rsplit("/", 1)
    admin_engine = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{nombre}"'))
    admin_engine.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env={**os.environ, "DATABASE_URL": URL},
                   capture_output=True)
    engine = create_engine(URL)
    Sesion = sessionmaker(bind=engine, autoflush=False)

    def _db():
        db = Sesion()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    original = core_db.SessionLocal
    core_db.SessionLocal = Sesion
    previo = (settings.PAYMENTS_MODE, settings.ADMIN_DEV_LOGIN, settings.APP_ENV, settings.ACS_CONNECTION_STRING)
    settings.PAYMENTS_MODE, settings.ADMIN_DEV_LOGIN, settings.APP_ENV, settings.ACS_CONNECTION_STRING = "mock", True, "test", ""
    with TestClient(app) as c:
        c.Sesion = Sesion
        yield c
    settings.PAYMENTS_MODE, settings.ADMIN_DEV_LOGIN, settings.APP_ENV, settings.ACS_CONNECTION_STRING = previo
    core_db.SessionLocal = original
    app.dependency_overrides.clear()
    engine.dispose()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _registrar(c, email, **kw) -> str:
    datos = {"nombre": kw.pop("nombre", "Contador Prueba"), "email": email, "password": "secreta123", **kw}
    r = c.post("/registro", json=datos)
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _rfc(n: int) -> str:
    return f"AAA0101{n:02d}AA{n % 10}"


def _alta(c, tok, rfc, nombre="Cliente SA de CV"):
    return c.post("/clientes", json={"rfc": rfc, "razon_social": nombre}, headers=_h(tok))


def _pagar(c, tok, **compra):
    r = c.post("/licencia/checkout", json=compra, headers=_h(tok))
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    if r.json()["gratis"]:
        return r.json()["orden_id"], None
    sesion = url.split("/checkout/mock/")[1].split("?")[0]
    r2 = c.post(f"/pagos/mock/{sesion}/pagar", headers=_h(tok))
    assert r2.status_code == 200, r2.text
    assert r2.json()["estado"] == "pagada"
    return r.json()["orden_id"], sesion


@pytest.fixture(scope="module")
def despachos(cliente):
    a = _registrar(cliente, "duena@despacho-a.mx", despacho_nombre="Despacho A", anonimo_id="anon-a", utm_source="google")
    b = _registrar(cliente, "dueno@despacho-b.mx", despacho_nombre="Despacho B")
    return {"a": a, "b": b}


def test_registro_crea_prueba(cliente, despachos):
    r = cliente.get("/despacho", headers=_h(despachos["a"]))
    assert r.status_code == 200
    j = r.json()
    assert j["rol_despacho"] == "dueno"
    assert j["despacho"]["codigo"].startswith("KON-")
    assert j["licencia"]["en_prueba"] and j["licencia"]["rfcs_max"] == 3 and j["licencia"]["usuarios_max"] == 1
    with cliente.Sesion() as db:
        n = db.execute(text("select count(*) from notificaciones where plantilla='bienvenida'")).scalar()
        assert n == 2


def test_separacion_entre_despachos(cliente, despachos):
    a, b = despachos["a"], despachos["b"]
    r = _alta(cliente, a, _rfc(1))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert cliente.get(f"/clientes/{cid}", headers=_h(a)).status_code == 200
    # El otro despacho no lo ve, ni su bóveda, ni puede escribir en él.
    assert cliente.get(f"/clientes/{cid}", headers=_h(b)).status_code == 404
    assert cliente.get(f"/clientes/{cid}/efirma", headers=_h(b)).status_code == 404
    assert cliente.put(f"/clientes/{cid}", json={"razon_social": "Hackeado"}, headers=_h(b)).status_code == 404
    assert all(x["id"] != cid for x in cliente.get("/clientes", headers=_h(b)).json())
    # El mismo RFC puede existir en otro despacho.
    assert _alta(cliente, b, _rfc(1)).status_code == 201
    assert _alta(cliente, a, _rfc(1)).status_code == 409
    assert cliente.get("/clientes/resumen", headers=_h(b)).json()["total_activos"] == 1


def test_limite_de_rfc_en_prueba(cliente, despachos):
    a = despachos["a"]
    assert _alta(cliente, a, _rfc(2)).status_code == 201
    assert _alta(cliente, a, _rfc(3)).status_code == 201
    r = _alta(cliente, a, _rfc(4))
    assert r.status_code == 402 and "3 RFC" in r.json()["detail"]


def test_solicitud_de_acceso_y_lugares(cliente, despachos):
    a = despachos["a"]
    codigo = cliente.get("/despacho", headers=_h(a)).json()["despacho"]["codigo"]
    c_tok = _registrar(cliente, "auxiliar@despacho-a.mx", modo="unirse", destino=codigo, mensaje="Soy Carla, auxiliar")
    yo = cliente.get("/despacho", headers=_h(c_tok)).json()
    assert yo["despacho"] is None and yo["solicitud"]["estado"] == "pendiente"
    # Sin despacho no ve clientes.
    assert cliente.get("/clientes", headers=_h(c_tok)).status_code == 403
    pend = cliente.get("/despacho/solicitudes", headers=_h(a)).json()
    assert len(pend) == 1 and pend[0]["solicitante"]["email"] == "auxiliar@despacho-a.mx"
    # Un miembro sin despacho no puede aprobar; el otro despacho tampoco.
    assert cliente.post(f"/despacho/solicitudes/{pend[0]['id']}/aprobar", headers=_h(despachos["b"])).status_code == 404
    # La prueba incluye 1 usuario: no hay lugar.
    r = cliente.post(f"/despacho/solicitudes/{pend[0]['id']}/aprobar", headers=_h(a))
    assert r.status_code == 402, r.text
    # El correo al dueño trae el enlace con token.
    with cliente.Sesion() as db:
        texto = db.execute(text("select texto from notificaciones where plantilla='solicitud_acceso' order by id desc limit 1")).scalar()
    token = texto.split("token=")[1].split("&")[0]
    vista = cliente.get("/despacho/acceso", params={"token": token}, headers=_h(a)).json()
    assert vista["puede_resolver"] and vista["solicitante"]["nombre"] == "Contador Prueba"

    # Compra Despacho 20 (2 usuarios) con la pasarela simulada y aprueba.
    orden_id, sesion = _pagar(cliente, a, plan_codigo="d20")
    est = cliente.get("/licencia", headers=_h(a)).json()["estado"]
    assert est["plan_codigo"] == "d20" and est["usuarios_max"] == 2 and not est["en_prueba"]
    r = cliente.post("/despacho/acceso", json={"token": token, "accion": "aprobar"}, headers=_h(a))
    assert r.status_code == 200, r.text
    # Ahora la auxiliar ve los mismos clientes.
    assert len(cliente.get("/clientes", headers=_h(c_tok)).json()) == 3
    assert {m["email"] for m in cliente.get("/despacho/miembros", headers=_h(a)).json()} == {"duena@despacho-a.mx", "auxiliar@despacho-a.mx"}
    # Una miembro no puede aprobar ni comprar.
    assert cliente.post("/licencia/checkout", json={"plan_codigo": "d50"}, headers=_h(c_tok)).status_code == 403

    # Webhook idempotente: el mismo evento otra vez no duplica la licencia.
    r = cliente.post(f"/pagos/mock/{sesion}/pagar", headers=_h(a))
    assert r.json()["estado"] == "duplicado"
    with cliente.Sesion() as db:
        n = db.execute(text("select count(*) from licencias l join ordenes o on o.id=l.orden_id where o.id=:o"), {"o": orden_id}).scalar()
        assert n == 1
        pasos = {p for (p,) in db.execute(text("select paso from embudo_eventos")).all()}
    assert {"signup_completed", "client_added", "checkout_started", "purchase_completed", "join_requested", "member_joined"} <= pasos


def test_prorrateo_de_mejora(cliente, despachos):
    a = despachos["a"]
    r = cliente.post("/licencia/cotizar", json={"plan_codigo": "d50"}, headers=_h(a))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tipo"] == "mejora"
    dias = j["lineas"][0]["concepto"]
    esperado = round((1399000 - 749000) * 364 / 365)
    assert abs(j["subtotal_centavos"] - esperado) <= round(650000 / 365) + 1, (j, dias)
    assert j["total_centavos"] == j["subtotal_centavos"] + round(j["subtotal_centavos"] * 0.16)
    # Bajar de paquete a mitad del periodo no se permite.
    assert cliente.post("/licencia/cotizar", json={"plan_codigo": "d10"}, headers=_h(a)).status_code == 422


def test_cupones(cliente, despachos):
    b = despachos["b"]
    adm = cliente.post("/admin/auth/dev", json={"email": "armando@isc.mx"}).json()["token"]
    r = cliente.post("/admin/cupones", json={"nombre": "Lanzamiento", "tipo": "porcentaje", "codigo": "LANZA20", "porcentaje": 20},
                     headers=_h(adm))
    assert r.status_code == 201, r.text
    r = cliente.post("/admin/cupones", json={"nombre": "Prueba extendida", "tipo": "dias_gratis", "codigo": "MES30", "dias": 30,
                                             "plan_codigo": "d10"}, headers=_h(adm))
    assert r.status_code == 201, r.text
    # Lote de 5 con CSV.
    r = cliente.post("/admin/cupones", json={"nombre": "Expo", "tipo": "monto", "monto_centavos": 50000, "cantidad": 5,
                                             "prefijo": "EXPO"}, headers=_h(adm))
    lote = r.json()["lote"]
    csv = cliente.get(f"/admin/cupones/lote/{lote}.csv", headers=_h(adm)).text
    assert csv.count("EXPO-") == 5

    q = cliente.post("/licencia/cotizar", json={"plan_codigo": "d10", "cupon": "lanza20"}, headers=_h(b)).json()
    assert q["cupon"]["ok"] and q["descuento_centavos"] == 89800
    assert q["total_centavos"] == (449000 - 89800) + round((449000 - 89800) * 0.16)
    # Los de días gratis no aplican al pagar.
    q2 = cliente.post("/licencia/cotizar", json={"plan_codigo": "d10", "cupon": "MES30"}, headers=_h(b)).json()
    assert not q2["cupon"]["ok"]

    # Canje de 30 días: se suman al final de la prueba.
    vence_antes = datetime.fromisoformat(cliente.get("/licencia", headers=_h(b)).json()["estado"]["vence"])
    r = cliente.post("/licencia/canjear", json={"codigo": "MES30"}, headers=_h(b))
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["vence"]) - vence_antes >= timedelta(days=29)
    assert cliente.post("/licencia/canjear", json={"codigo": "MES30"}, headers=_h(b)).status_code == 422  # ya lo usó

    # Pago con 20%: el cupón queda canjeado.
    _pagar(cliente, b, plan_codigo="d10", cupon="LANZA20", requiere_factura=True,
           datos_fiscales={"rfc": "BBB010101BB1", "razon_social": "Despacho B SC", "regimen": "612", "cp": "06600", "uso_cfdi": "G03"})
    lista = {x["codigo"]: x for x in cliente.get("/admin/cupones", headers=_h(adm)).json()}
    assert lista["LANZA20"]["canjeados"] == 1 and lista["LANZA20"]["descuento_centavos"] == 89800


def test_contador_individual_se_une_con_sus_clientes(cliente, despachos):
    a = despachos["a"]
    d = _registrar(cliente, "independiente@correo.mx", despacho_nombre="Contadora Independiente")
    _pagar(cliente, d, plan_codigo="d10")
    assert _alta(cliente, d, _rfc(20), "Cliente propio").status_code == 201
    dueno_a = "duena@despacho-a.mx"
    # Sin marcar «llevar clientes» no se puede.
    assert cliente.post("/despacho/unirme", json={"destino": dueno_a}, headers=_h(d)).status_code == 409
    r = cliente.post("/despacho/unirme", json={"destino": dueno_a, "traer_clientes": True}, headers=_h(d))
    assert r.status_code == 201, r.text
    # A tiene 2 usuarios ocupados: compra 1 usuario extra (complemento prorrateado) y aprueba.
    q = cliente.post("/licencia/cotizar", json={"plan_codigo": "d20", "usuarios_extra": 1}, headers=_h(a)).json()
    assert q["tipo"] == "complemento" and 0 < q["subtotal_centavos"] <= 129000
    _pagar(cliente, a, plan_codigo="d20", usuarios_extra=1)
    sid = cliente.get("/despacho/solicitudes", headers=_h(a)).json()[0]["id"]
    r = cliente.post(f"/despacho/solicitudes/{sid}/aprobar", headers=_h(a))
    assert r.status_code == 200, r.text
    rfcs = {x["rfc"] for x in cliente.get("/clientes", headers=_h(a)).json()}
    assert _rfc(20) in rfcs
    yo = cliente.get("/despacho", headers=_h(d)).json()
    assert yo["rol_despacho"] == "miembro"


def test_licencia_vencida_es_solo_lectura(cliente, despachos):
    b = despachos["b"]
    with cliente.Sesion() as db:
        did = db.execute(text("select despacho_id from users where email='dueno@despacho-b.mx'")).scalar()
        db.execute(text("update licencias set vence = now() - interval '1 day' where despacho_id=:d"), {"d": did})
        db.commit()
    assert cliente.get("/clientes", headers=_h(b)).status_code == 200
    r = _alta(cliente, b, _rfc(30))
    assert r.status_code == 402
    cid = cliente.get("/clientes", headers=_h(b)).json()[0]["id"]
    assert cliente.post(f"/clientes/{cid}/sat/solicitudes", json={}, headers=_h(b)).status_code == 402
    assert cliente.delete(f"/clientes/{cid}", headers=_h(b)).status_code == 200  # dar de baja sí


def test_admin(cliente, despachos, monkeypatch):
    from fastapi import HTTPException

    from app.admin import auth

    # Un token de usuario no entra al portal y uno de admin no sirve como usuario.
    assert cliente.get("/admin/finanzas", headers=_h(despachos["a"])).status_code == 403
    adm = cliente.post("/admin/auth/dev", json={"email": "armando@isc.mx"}).json()["token"]
    assert cliente.get("/clientes", headers=_h(adm)).status_code == 401
    f = cliente.get("/admin/finanzas", headers=_h(adm)).json()
    assert f["kpis"]["ordenes"] >= 4 and f["kpis"]["despachos_pagando"] == 1  # B venció y D se fusionó en A
    assert f["kpis"]["sin_iva_centavos"] + f["kpis"]["iva_centavos"] == f["kpis"]["bruto_centavos"]
    e = cliente.get("/admin/embudo", headers=_h(adm)).json()
    assert e["pasos"][0]["key"] == "visit"
    ds = cliente.get("/admin/despachos", params={"q": "despacho-a"}, headers=_h(adm)).json()
    assert len(ds) == 1 and ds[0]["usuarios"] == 3
    det = cliente.get(f"/admin/despachos/{ds[0]['id']}", headers=_h(adm)).json()
    assert det["estado"]["usuarios_max"] == 3
    r = cliente.post(f"/admin/despachos/{ds[0]['id']}/licencia", json={"plan_codigo": "d50", "dias": 30, "nota": "cortesía"},
                     headers=_h(adm))
    assert r.status_code == 201
    assert any(a["accion"] == "licencia.manual" for a in cliente.get("/admin/auditoria", headers=_h(adm)).json())
    assert cliente.get("/admin/notificaciones", headers=_h(adm)).json()

    # Reglas de Entra: tenant, MFA, rol y lista permitida.
    monkeypatch.setattr(settings, "ENTRA_TENANT_ID", "tid-isc")
    monkeypatch.setattr(settings, "ADMIN_ALLOWLIST", "armando@isc.mx")
    base = {"tid": "tid-isc", "oid": "o1", "amr": ["pwd", "mfa"], "roles": ["KONYEKA.SuperAdmin"], "preferred_username": "armando@isc.mx"}
    assert auth.autorizar_claims(base)["email"] == "armando@isc.mx"
    for cambio in ({"amr": ["pwd"]}, {"roles": []}, {"tid": "otro"}, {"preferred_username": "otro@isc.mx"}):
        with pytest.raises(HTTPException):
            auth.autorizar_claims({**base, **cambio})
    # El acceso de desarrollo no existe en producción.
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert cliente.post("/admin/auth/dev", json={"email": "x@isc.mx"}).status_code == 404
    assert cliente.get("/admin/finanzas", headers=_h(adm)).status_code == 403


def test_embudo_publico(cliente):
    assert cliente.post("/embudo/evento", json={"paso": "visit", "anonimo_id": "anon-z", "utm_source": "facebook"}).json()["ok"]
    # Pasos de producto no se aceptan desde el navegador.
    assert not cliente.post("/embudo/evento", json={"paso": "purchase_completed"}).json()["ok"]
