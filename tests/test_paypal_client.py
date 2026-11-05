"""Tests del cliente de PayPal: interfaz, mock y factoría.

No requieren red ni credenciales (usan MockPayPalClient). El cliente real
(PayPalSandboxClient) se prueba solo en sus aspectos sin red: validación de
credenciales ausentes y parseo de la respuesta de la API.
"""

from __future__ import annotations

from datetime import date

import pytest

from paypal.client import (
    MockPayPalClient,
    PayPalClient,
    PayPalSandboxClient,
    _parsear_transaccion_api,
    get_paypal_client,
)
from paypal.models import PayPalTransaction


def test_mock_client_implementa_interfaz():
    cliente = MockPayPalClient()
    assert isinstance(cliente, PayPalClient)


def test_mock_client_devuelve_transacciones_de_ejemplo():
    cliente = MockPayPalClient()
    hoy = date.today()
    txs = cliente.obtener_transacciones(date(hoy.year, 1, 1), date(hoy.year, 12, 31))
    assert len(txs) == 8
    assert all(isinstance(t, PayPalTransaction) for t in txs)


def test_mock_client_filtra_por_rango_de_fechas():
    cliente = MockPayPalClient()
    hoy = date.today()
    # Rango de un solo día que no coincide con ninguna transacción mock.
    txs = cliente.obtener_transacciones(date(hoy.year, 12, 30), date(hoy.year, 12, 31))
    assert txs == []


def test_mock_client_incluye_categorias_relevantes():
    """Las transacciones mock deben cubrir, al menos, los casos de uso que
    justifican el módulo: venta ocasional, ingreso recurrente, reembolso,
    cambio de divisa y comisión (ver README del hackathon)."""
    cliente = MockPayPalClient()
    hoy = date.today()
    txs = cliente.obtener_transacciones(date(hoy.year, 1, 1), date(hoy.year, 12, 31))

    hay_cambio_divisa = any(t.es_cambio_divisa for t in txs)
    hay_comision = any(
        t.importe < 0 and ("tarifa" in t.descripcion.lower() or "comisión" in t.nota_origen.lower())
        for t in txs
    )
    hay_reembolso = any("reembolso" in t.descripcion.lower() for t in txs)
    hay_venta_ocasional = any("vinted" in t.nota_origen.lower() or "wallapop" in t.nota_origen.lower() for t in txs)
    hay_recurrente = any("mensual" in t.nota_origen.lower() for t in txs)

    assert hay_cambio_divisa
    assert hay_comision
    assert hay_reembolso
    assert hay_venta_ocasional
    assert hay_recurrente


def test_sandbox_client_requiere_credenciales(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError):
        PayPalSandboxClient()


def test_sandbox_client_acepta_credenciales_explicitas():
    cliente = PayPalSandboxClient(client_id="id-test", client_secret="secret-test")
    assert cliente.client_id == "id-test"
    assert cliente.env == "sandbox"


def test_parsear_transaccion_api():
    item = {
        "transaction_info": {
            "transaction_id": "ABC123",
            "transaction_initiation_date": "2026-03-05T10:00:00-0700",
            "transaction_amount": {"currency_code": "EUR", "value": "50.00"},
            "fee_amount": {"currency_code": "EUR", "value": "-1.75"},
            "transaction_event_code": "T0000",
            "transaction_subject": "Pago de prueba",
            "transaction_note": "Nota de prueba",
        },
        "payer_info": {"email_address": "pagador@example.com"},
    }
    t = _parsear_transaccion_api(item)
    assert t.transaction_id == "ABC123"
    assert t.fecha == date(2026, 3, 5)
    assert t.importe == 50.0
    assert t.comision == -1.75
    assert t.contraparte == "pagador@example.com"
    assert t.divisa == "EUR"


def test_get_paypal_client_por_defecto_es_mock(monkeypatch):
    monkeypatch.delenv("PAYPAL_MODE", raising=False)
    cliente = get_paypal_client()
    assert isinstance(cliente, MockPayPalClient)


def test_get_paypal_client_mock_explicito(monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    cliente = get_paypal_client()
    assert isinstance(cliente, MockPayPalClient)


def test_get_paypal_client_real_sin_credenciales_cae_a_mock(monkeypatch):
    """Si se pide modo real pero faltan credenciales, no debe romper la
    demo: cae a mock con un aviso, nunca lanza excepción."""
    monkeypatch.setenv("PAYPAL_MODE", "real")
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    cliente = get_paypal_client()
    assert isinstance(cliente, MockPayPalClient)


def test_get_paypal_client_real_con_credenciales(monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "real")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id-test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret-test")
    cliente = get_paypal_client()
    assert isinstance(cliente, PayPalSandboxClient)

def test_sandbox_client_no_consulta_fechas_futuras(monkeypatch):
    """PayPal rechaza end_date en el futuro: el cliente debe capar a hoy."""
    cliente = PayPalSandboxClient(client_id="id-test", client_secret="secret-test")
    monkeypatch.setattr(cliente, "_obtener_access_token", lambda: "token-test")
    calls = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"total_pages": 0, "transaction_details": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(dict(params))
        return _Resp()

    monkeypatch.setattr("requests.get", fake_get)
    hoy = date.today()
    cliente.obtener_transacciones(date(hoy.year, 1, 1), date(hoy.year, 12, 31))
    assert calls
    assert all(c["end_date"][:10] <= hoy.isoformat() for c in calls)
