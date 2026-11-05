"""Modelos de datos para transacciones de PayPal.

Deliberadamente independientes del formato exacto que devuelva la API de
PayPal (real o mock) o el clasificador (real o heurístico): son el contrato
interno que usa el resto del pipeline (`process_paypal.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# Categorías fiscales que puede asignar el clasificador. Se usan tanto en el
# modo real (el LLM debe elegir una de estas, se valida tras la respuesta)
# como en el modo heurístico (reglas si/entonces).
CATEGORIAS_FISCALES = (
    "venta_ocasional",
    "ingreso_recurrente",
    "reembolso",
    "cambio_divisa",
    "comision",
    "otro",
)


@dataclass
class PayPalTransaction:
    """Transacción tal como la devuelve el cliente de PayPal (real o mock).

    Campos alineados, en lo posible, con los de la API de PayPal
    (Transaction Search / Reporting API v1): ver
    https://developer.paypal.com/docs/api/transaction-search/v1/
    """

    transaction_id: str
    fecha: date
    tipo: str  # tal cual lo reporta PayPal: "T00" pago recibido, "T03" reembolso, etc. (aprox.)
    descripcion: str  # texto libre: nombre de la contraparte / concepto
    importe: float  # con signo: positivo = entrada, negativo = salida
    divisa: str
    comision: float = 0.0  # con signo negativo si PayPal cobró comisión
    divisa_comision: str = ""
    contraparte: str = ""  # email o nombre de quien paga/cobra
    es_cambio_divisa: bool = False
    nota_origen: str = ""  # texto crudo adicional útil para el LLM (memo, item, etc.)

    @property
    def importe_neto(self) -> float:
        """Importe + comisión, ambos con su signo ya puesto por PayPal."""
        return self.importe + self.comision


@dataclass
class ClassifiedTransaction:
    """Transacción de PayPal ya clasificada fiscalmente."""

    transaction: PayPalTransaction
    categoria: str  # una de CATEGORIAS_FISCALES
    confianza: float  # 0.0-1.0, heurística o reportada por el LLM
    explicacion: str  # explicación en lenguaje natural del tratamiento IRPF aproximado
    casilla_orientativa: str = ""  # casilla/apartado aproximado de Renta Web, si aplica
    requiere_revision_manual: bool = False
    motor: str = ""  # "llm" | "heuristico" — qué clasificó esta fila (trazabilidad)

    @property
    def fecha(self) -> date:
        return self.transaction.fecha

    @property
    def importe_eur_estimado(self) -> float:
        """Importe bruto en la divisa original; la conversión a EUR con tipos
        BCE se hace en `process_paypal.py`, igual que para IBKR, para no
        duplicar esa lógica aquí."""
        return self.transaction.importe
