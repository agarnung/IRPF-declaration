"""Tests del clasificador fiscal de PayPal.

Se centran en HeuristicTransactionClassifier (no requiere red ni API key) y
en el comportamiento de fallback de LLMTransactionClassifier cuando la API
no está disponible — justo el escenario sin credenciales en el que se
desarrolló este módulo.
"""

from __future__ import annotations

from datetime import date

import pytest

from paypal.classifier import (
    CATEGORIAS_FISCALES,
    HeuristicTransactionClassifier,
    LLMTransactionClassifier,
    TransactionClassifier,
    get_classifier,
)
from paypal.client import MockPayPalClient
from paypal.models import PayPalTransaction


def _tx(**kwargs) -> PayPalTransaction:
    base = dict(
        transaction_id="T1",
        fecha=date(2026, 5, 1),
        tipo="T0000",
        descripcion="",
        importe=100.0,
        divisa="EUR",
        comision=0.0,
        contraparte="alguien@example.com",
        nota_origen="",
    )
    base.update(kwargs)
    return PayPalTransaction(**base)


def test_heuristico_implementa_interfaz():
    clf = HeuristicTransactionClassifier()
    assert isinstance(clf, TransactionClassifier)


def test_heuristico_detecta_cambio_divisa():
    clf = HeuristicTransactionClassifier()
    tx = _tx(es_cambio_divisa=True, importe=92.3)
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "cambio_divisa"
    assert resultado.motor == "heuristico"


def test_heuristico_detecta_reembolso():
    clf = HeuristicTransactionClassifier()
    tx = _tx(importe=-50.0, comision=1.5, descripcion="Reembolso de tienda")
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "reembolso"


def test_heuristico_detecta_comision():
    clf = HeuristicTransactionClassifier()
    tx = _tx(importe=-4.2, contraparte="PayPal", descripcion="Tarifa de PayPal")
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "comision"


def test_heuristico_detecta_venta_ocasional():
    clf = HeuristicTransactionClassifier()
    tx = _tx(importe=45.0, nota_origen="Vinted: chaqueta de segunda mano")
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "venta_ocasional"


def test_heuristico_venta_ocasional_importe_alto_marca_revision():
    clf = HeuristicTransactionClassifier()
    tx = _tx(importe=1500.0, nota_origen="Wallapop: venta de varios artículos")
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "venta_ocasional"
    assert resultado.requiere_revision_manual is True


def test_heuristico_detecta_recurrencia_en_lote():
    """Dos pagos idénticos de la misma contraparte en el lote deben
    detectarse como ingreso recurrente, aunque el texto no lo diga."""
    clf = HeuristicTransactionClassifier()
    txs = [
        _tx(transaction_id="A", contraparte="cliente@example.com", importe=600.0, nota_origen=""),
        _tx(transaction_id="B", contraparte="cliente@example.com", importe=600.0, nota_origen=""),
    ]
    resultados = clf.clasificar_lote(txs)
    assert all(r.categoria == "ingreso_recurrente" for r in resultados)


def test_heuristico_suscripcion_negativa_no_es_ingreso_recurrente():
    clf = HeuristicTransactionClassifier()
    tx = _tx(
        importe=-12.99,
        contraparte="soporte@serviciomusica.example",
        descripcion="Pago a Servicio de Música",
        nota_origen="Suscripción personal de música; gasto de uso privado",
    )
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "otro"
    assert resultado.requiere_revision_manual is True


def test_heuristico_sin_pistas_devuelve_otro_y_marca_revision():
    clf = HeuristicTransactionClassifier()
    tx = _tx(importe=250.0, contraparte="desconocido@example.com", descripcion="", nota_origen="")
    resultado = clf.clasificar(tx)
    assert resultado.categoria == "otro"
    assert resultado.requiere_revision_manual is True


def test_heuristico_categoria_siempre_valida():
    clf = HeuristicTransactionClassifier()
    cliente = MockPayPalClient()
    txs = cliente.obtener_transacciones(date(2000, 1, 1), date(2100, 1, 1))
    resultados = clf.clasificar_lote(txs)
    assert len(resultados) == len(txs)
    for r in resultados:
        assert r.categoria in CATEGORIAS_FISCALES
        assert 0.0 <= r.confianza <= 1.0
        assert r.explicacion  # siempre debe haber una explicación en lenguaje natural


def test_llm_classifier_requiere_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        LLMTransactionClassifier()


def test_get_classifier_por_defecto_es_heuristico(monkeypatch):
    monkeypatch.delenv("CLASSIFIER_MODE", raising=False)
    clf = get_classifier()
    assert isinstance(clf, HeuristicTransactionClassifier)


def test_get_classifier_llm_sin_api_key_cae_a_heuristico(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_MODE", "llm")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clf = get_classifier()
    assert isinstance(clf, HeuristicTransactionClassifier)


def test_llm_classifier_cae_a_heuristico_si_la_llamada_falla(monkeypatch):
    """Simula una API key presente pero una llamada que falla (red, cuota...):
    el resultado debe venir del fallback heurístico, no una excepción."""
    pytest.importorskip("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")

    clf = LLMTransactionClassifier()

    class _ClienteRoto:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("fallo de red simulado")

    clf._client = _ClienteRoto()

    tx = _tx(importe=45.0, nota_origen="Vinted: chaqueta de segunda mano")
    resultado = clf.clasificar(tx)
    assert resultado.categoria in CATEGORIAS_FISCALES
    assert resultado.requiere_revision_manual is True
    assert "LLM no disponible" in resultado.explicacion

def test_parse_json_llm_tolera_code_fences():
    from paypal.classifier import _parse_json_llm

    datos = _parse_json_llm('```json\n{"categoria": "otro", "confianza": 0.2}\n```')
    assert datos["categoria"] == "otro"
    assert datos["confianza"] == 0.2


def test_parse_json_llm_tolera_texto_alrededor():
    from paypal.classifier import _parse_json_llm

    datos = _parse_json_llm('Clasificación: {"categoria": "reembolso"} fin')
    assert datos["categoria"] == "reembolso"

def test_llm_classifier_acepta_litellm_env(monkeypatch):
    from paypal.classifier import LLMTransactionClassifier

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("LITELLM_API_KEY", "sk-local")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    c = LLMTransactionClassifier()
    assert c.api_key == "sk-local"
    assert c.base_url == "http://127.0.0.1:4000/v1"
    assert c.model == "local-model"

    # Si ya incluye /v1, no debe duplicarlo.
    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1")
    c2 = LLMTransactionClassifier()
    assert c2.base_url == "http://127.0.0.1:4000/v1"
