"""Webhook de Stripe: firma, modo (prueba vs. real) y filtro por producto
(la cuenta de Stripe de ISC es compartida con VEMI y otros productos)."""
import json
import time

import pytest
import stripe
from fastapi import HTTPException

from app.billing import service as billing
from app.core.config import settings

SECRETO = "whsec_prueba"


def _firmado(evento: dict) -> tuple[bytes, str]:
    cuerpo = json.dumps(evento).encode()
    ts = int(time.time())
    firma = stripe.WebhookSignature._compute_signature(f"{ts}.{cuerpo.decode()}", SECRETO)
    return cuerpo, f"t={ts},v1={firma}"


def _evento(product="konyeka", livemode=False, tipo="checkout.session.completed"):
    return {
        "id": "evt_1", "object": "event", "type": tipo, "livemode": livemode, "api_version": "2024-06-20",
        "data": {"object": {"id": "cs_test_1", "object": "checkout.session", "payment_status": "paid", "amount_total": 520840,
                            "currency": "mxn", "payment_intent": "pi_1", "metadata": {"product": product, "orden_id": "o-1"}}},
    }


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENTS_MODE", "stripe_test")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRETO)


def test_evento_konyeka():
    e = billing.parsear_webhook_stripe(*_firmado(_evento()))
    assert e.orden_id == "o-1" and e.pagado and e.monto_centavos == 520840 and e.pago_id == "pi_1"


def test_ignora_otros_productos_de_isc():
    assert billing.parsear_webhook_stripe(*_firmado(_evento(product="vemi"))) is None


def test_rechaza_firma_invalida_y_modo_equivocado():
    cuerpo, _ = _firmado(_evento())
    with pytest.raises(HTTPException):
        billing.parsear_webhook_stripe(cuerpo, "t=1,v1=bad")
    with pytest.raises(HTTPException):
        billing.parsear_webhook_stripe(*_firmado(_evento(livemode=True)))


def test_llave_de_modo_equivocado(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_x")
    with pytest.raises(HTTPException):
        billing.parsear_webhook_stripe(*_firmado(_evento()))


def test_sesion_de_checkout_con_cupon(monkeypatch):
    """Checkout de una sola línea con IVA incluido, etiqueta de producto y
    cupón espejo por el descuento exacto (sin red: se captura la llamada)."""
    from types import SimpleNamespace

    capturado = {}
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: capturado.update(kw) or SimpleNamespace(id="cs_1", url="https://checkout"))
    monkeypatch.setattr(stripe.Coupon, "retrieve", lambda cid: (_ for _ in ()).throw(Exception("no existe")))
    creados = []
    monkeypatch.setattr(stripe.Coupon, "create", lambda **kw: creados.append(kw))
    subtotal, descuento = 449000, 89800
    base = subtotal - descuento
    orden = SimpleNamespace(id="12345678-aaaa", subtotal_centavos=subtotal, descuento_centavos=descuento,
                            total_centavos=base + round(base * 0.16), cupon_id=1)
    r = billing.ProveedorStripe().crear_sesion(orden, SimpleNamespace(id=7, nombre="Despacho B"), SimpleNamespace(email="b@x.mx"),
                                               SimpleNamespace(nombre="Despacho 10"))
    assert r == {"id": "cs_1", "url": "https://checkout"}
    linea = capturado["line_items"][0]["price_data"]
    assert linea["unit_amount"] == subtotal + round(subtotal * 0.16)
    assert linea["unit_amount"] - creados[0]["amount_off"] == orden.total_centavos
    assert capturado["metadata"]["product"] == "konyeka" and capturado["payment_intent_data"]["metadata"]["product"] == "konyeka"
    assert capturado["payment_intent_data"]["statement_descriptor_suffix"] == "KONYEKA"
    assert capturado["mode"] == "payment" and capturado["idempotency_key"] == "konyeka-orden:12345678-aaaa"
