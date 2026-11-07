"""Test de integración del pipeline completo de PayPal (process_paypal.py)
en modo mock + heurístico, sin red ni credenciales (salvo la descarga de
tipos BCE, que ya usa el proyecto para IBKR y cachea en cache/).
"""

from __future__ import annotations

from datetime import date

from paypal.classifier import CATEGORIAS_FISCALES, HeuristicTransactionClassifier
from paypal.client import MockPayPalClient
from process_data import cargar_tipos_bce
import process_paypal
from process_paypal import (
    construir_tabla,
    generar_informe_html,
    resumen_por_categoria,
)


def test_pipeline_mock_heuristico_produce_tabla_coherente():
    cliente = MockPayPalClient()
    hoy = date.today()
    transacciones = cliente.obtener_transacciones(date(hoy.year, 1, 1), date(hoy.year, 12, 31))
    assert transacciones

    clasificador = HeuristicTransactionClassifier()
    clasificadas = clasificador.clasificar_lote(transacciones)

    rates = cargar_tipos_bce()
    tabla = construir_tabla(clasificadas, rates)

    assert len(tabla) == len(transacciones)
    assert set(tabla["Categoría fiscal"]).issubset(set(CATEGORIAS_FISCALES))
    assert "Importe EUR (BCE)" in tabla.columns
    assert "Explicación IRPF (IA)" in tabla.columns
    assert (tabla["Explicación IRPF (IA)"].str.len() > 0).all()

    resumen = resumen_por_categoria(tabla)
    assert not resumen.empty
    # La suma de los totales por categoría debe cuadrar con la suma general.
    assert abs(resumen["Importe EUR total"].sum() - tabla["Importe EUR (BCE)"].sum()) < 0.05


def test_informe_html_se_genera_y_escapa_contenido(tmp_path, monkeypatch):
    monkeypatch.setattr(process_paypal, "EXTRAS_DIR", tmp_path)
    cliente = MockPayPalClient()
    hoy = date.today()
    transacciones = cliente.obtener_transacciones(date(hoy.year, 1, 1), date(hoy.year, 12, 31))
    clasificadas = HeuristicTransactionClassifier().clasificar_lote(transacciones)
    tabla = construir_tabla(clasificadas, cargar_tipos_bce())
    resumen = resumen_por_categoria(tabla)

    tabla.loc[0, "Descripción"] = "<script>alert(1)</script>"
    ruta = generar_informe_html(hoy.year, tabla, resumen, "mock", "heuristic")
    contenido = ruta.read_text(encoding="utf-8")

    assert ruta.name == "paypal_informe.html"
    assert "IRPF Tax Assistant" in contenido
    assert "Occasional sale" in contenido
    assert "Recurring income" in contenido
    assert "Only movements to review" in contenido
    assert "Download CSV" in contenido
    assert "<script>alert(1)</script>" not in contenido
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in contenido
