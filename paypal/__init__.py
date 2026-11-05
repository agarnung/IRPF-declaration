"""Módulo de importación y clasificación fiscal de transacciones de PayPal.

Añade PayPal como segunda fuente de datos (junto a Interactive Brokers) para
la declaración de la Renta española. A diferencia del módulo de IBKR (solo
compraventa de valores), las transacciones de PayPal son heterogéneas: ventas
ocasionales (Vinted/Wallapop/freelance puntual), ingresos recurrentes,
reembolsos, cambios de divisa y comisiones. Por eso aquí se usa un LLM (o,
en su defecto, un clasificador heurístico) para etiquetar cada movimiento y
explicar en lenguaje natural su tratamiento fiscal aproximado.

Submódulos:
  - models: dataclasses de transacción (en bruto y clasificada)
  - client: interfaz `PayPalClient` + implementación real (REST sandbox) y
    mock (datos de ejemplo realistas, sin credenciales)
  - classifier: interfaz `TransactionClassifier` + implementación real
    (API tipo OpenAI) y heurística (reglas simples, sin API key)
"""

from .models import PayPalTransaction, ClassifiedTransaction

__all__ = ["PayPalTransaction", "ClassifiedTransaction"]
