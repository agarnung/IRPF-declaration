"""Cliente de PayPal desacoplado detrás de una interfaz.

Dos implementaciones:
  - `PayPalSandboxClient`: real, contra la REST API del sandbox de PayPal
    (OAuth2 Client Credentials + Transaction Search API). Necesita
    credenciales vía variables de entorno (ver README).
  - `MockPayPalClient`: datos de ejemplo realistas, sin red ni credenciales,
    para poder desarrollar/probar/hacer la demo sin depender de PayPal.

El resto del pipeline (`process_paypal.py`) solo conoce `PayPalClient` (la
interfaz) y `get_paypal_client()` (la factoría), nunca las clases concretas.
"""

from __future__ import annotations

import abc
import os
import random
from datetime import date, datetime, timedelta

from .models import PayPalTransaction

# Endpoints REST del sandbox de PayPal (Transaction Search API v1).
# https://developer.paypal.com/docs/api/transaction-search/v1/
PAYPAL_SANDBOX_OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
PAYPAL_SANDBOX_TRANSACTIONS_URL = (
    "https://api-m.sandbox.paypal.com/v1/reporting/transactions"
)
PAYPAL_LIVE_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
PAYPAL_LIVE_TRANSACTIONS_URL = "https://api-m.paypal.com/v1/reporting/transactions"

# PayPal exige que cada consulta de Transaction Search cubra como máximo 31
# días naturales; el cliente real pagina en bloques de este tamaño.
MAX_DIAS_POR_CONSULTA = 31


class PayPalClient(abc.ABC):
    """Interfaz mínima que necesita el pipeline de IRPF."""

    @abc.abstractmethod
    def obtener_transacciones(
        self, fecha_inicio: date, fecha_fin: date
    ) -> list[PayPalTransaction]:
        """Devuelve todas las transacciones del rango [fecha_inicio, fecha_fin]."""
        raise NotImplementedError


class PayPalSandboxClient(PayPalClient):
    """Implementación real contra la REST API del sandbox (o live) de PayPal.

    Variables de entorno requeridas:
      - PAYPAL_CLIENT_ID
      - PAYPAL_CLIENT_SECRET
    Opcional:
      - PAYPAL_ENV = "sandbox" (por defecto) | "live"

    No se instancia nunca de forma implícita: `get_paypal_client()` solo la
    usa si `PAYPAL_MODE=real` Y hay credenciales, para no romper la demo si
    a alguien se le olvida configurarlas.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        env: str | None = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET", "")
        self.env = (env or os.environ.get("PAYPAL_ENV", "sandbox")).strip().lower()

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Faltan credenciales de PayPal: define PAYPAL_CLIENT_ID y "
                "PAYPAL_CLIENT_SECRET (sandbox de developer.paypal.com) como "
                "variables de entorno, o usa PAYPAL_MODE=mock para la demo."
            )

        self._oauth_url = (
            PAYPAL_SANDBOX_OAUTH_URL if self.env != "live" else PAYPAL_LIVE_OAUTH_URL
        )
        self._transactions_url = (
            PAYPAL_SANDBOX_TRANSACTIONS_URL
            if self.env != "live"
            else PAYPAL_LIVE_TRANSACTIONS_URL
        )
        self._access_token: str | None = None

    def _obtener_access_token(self) -> str:
        # Import perezoso: `requests` solo hace falta en modo real, para que
        # el modo mock funcione con las dependencias mínimas del proyecto.
        import requests

        if self._access_token:
            return self._access_token

        resp = requests.post(
            self._oauth_url,
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def obtener_transacciones(
        self, fecha_inicio: date, fecha_fin: date
    ) -> list[PayPalTransaction]:
        import requests

        token = self._obtener_access_token()
        transacciones: list[PayPalTransaction] = []

        cursor = fecha_inicio
        while cursor <= fecha_fin:
            bloque_fin = min(cursor + timedelta(days=MAX_DIAS_POR_CONSULTA - 1), fecha_fin)
            page = 1
            total_pages = 1
            while page <= total_pages:
                params = {
                    "start_date": f"{cursor.isoformat()}T00:00:00-0000",
                    "end_date": f"{bloque_fin.isoformat()}T23:59:59-0000",
                    "fields": "all",
                    "page_size": 100,
                    "page": page,
                }
                resp = requests.get(
                    self._transactions_url,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                total_pages = int(data.get("total_pages", 1))
                for item in data.get("transaction_details", []):
                    transacciones.append(_parsear_transaccion_api(item))
                page += 1
            cursor = bloque_fin + timedelta(days=1)

        return transacciones


def _parsear_transaccion_api(item: dict) -> PayPalTransaction:
    """Traduce un elemento de `transaction_details` de la Transaction Search
    API (ver referencia de PayPal) a nuestro `PayPalTransaction`."""
    info = item.get("transaction_info", {})
    payer = item.get("payer_info", {})

    fecha_str = info.get("transaction_initiation_date", "")
    fecha = (
        datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
        if fecha_str
        else date.today()
    )

    gross = info.get("transaction_amount", {})
    fee = info.get("fee_amount", {})
    importe = float(gross.get("value", 0.0))
    divisa = gross.get("currency_code", "EUR")
    comision = float(fee.get("value", 0.0)) if fee else 0.0
    divisa_comision = fee.get("currency_code", divisa) if fee else divisa

    contraparte = payer.get("email_address", "") or payer.get(
        "payer_name", {}
    ).get("alternate_full_name", "")

    tipo = info.get("transaction_event_code", "")
    es_cambio_divisa = tipo in {"T04", "T11"}  # conversión de divisa / transferencia entre monederos

    return PayPalTransaction(
        transaction_id=info.get("transaction_id", ""),
        fecha=fecha,
        tipo=tipo,
        descripcion=info.get("transaction_subject", "") or info.get("transaction_note", ""),
        importe=importe,
        divisa=divisa,
        comision=comision,
        divisa_comision=divisa_comision,
        contraparte=contraparte,
        es_cambio_divisa=es_cambio_divisa,
        nota_origen=info.get("transaction_note", ""),
    )


class MockPayPalClient(PayPalClient):
    """Datos de ejemplo realistas, deterministas (semilla fija) para que la
    demo y los tests sean reproducibles sin credenciales ni red.

    Incluye a propósito un movimiento de cada categoría fiscal relevante:
    venta ocasional (Vinted/Wallapop), ingreso recurrente (cliente
    freelance mensual), reembolso, cambio de divisa y comisión, además de
    algún caso ambiguo para que se note la utilidad del LLM frente a reglas
    fijas.
    """

    def __init__(self, seed: int = 2026) -> None:
        self._seed = seed

    def obtener_transacciones(
        self, fecha_inicio: date, fecha_fin: date
    ) -> list[PayPalTransaction]:
        todas = _transacciones_mock(self._seed)
        return [t for t in todas if fecha_inicio <= t.fecha <= fecha_fin]


def get_paypal_client() -> PayPalClient:
    """Factoría: decide entre cliente real y mock según variables de entorno.

    - `PAYPAL_MODE=real` (y credenciales presentes) → `PayPalSandboxClient`.
    - Cualquier otro caso (por defecto, o `PAYPAL_MODE=mock`, o credenciales
      ausentes) → `MockPayPalClient`, para que la demo nunca se rompa por
      falta de credenciales.
    """
    modo = os.environ.get("PAYPAL_MODE", "mock").strip().lower()
    if modo == "real":
        try:
            return PayPalSandboxClient()
        except ValueError as exc:
            print(
                f"  (aviso) PAYPAL_MODE=real pero {exc} "
                "Usando MockPayPalClient como fallback."
            )
            return MockPayPalClient()
    return MockPayPalClient()


def _transacciones_mock(seed: int) -> list[PayPalTransaction]:
    random.seed(seed)
    base_year = date.today().year

    return [
        PayPalTransaction(
            transaction_id="MOCK-T001",
            fecha=date(base_year, 1, 15),
            tipo="T0000",
            descripcion="Pago recibido de maria.compradora@example.com",
            importe=45.00,
            divisa="EUR",
            comision=-1.52,
            contraparte="maria.compradora@example.com",
            nota_origen="Vinted: chaqueta de segunda mano talla M",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T002",
            fecha=date(base_year, 2, 1),
            tipo="T0006",
            descripcion="Pago recibido de Cliente Freelance SL",
            importe=600.00,
            divisa="EUR",
            comision=-17.70,
            contraparte="facturacion@clientefreelance.example",
            nota_origen="Factura mensual - mantenimiento web (recurrente, mismo importe cada mes)",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T003",
            fecha=date(base_year, 2, 3),
            tipo="T0006",
            descripcion="Pago recibido de Cliente Freelance SL",
            importe=600.00,
            divisa="EUR",
            comision=-17.70,
            contraparte="facturacion@clientefreelance.example",
            nota_origen="Factura mensual - mantenimiento web (recurrente, mismo importe cada mes)",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T004",
            fecha=date(base_year, 3, 10),
            tipo="T1107",
            descripcion="Reembolso de Tienda Online Example",
            importe=-89.99,
            divisa="EUR",
            comision=1.80,
            contraparte="tienda@example.com",
            nota_origen="Reembolso por devolución de producto defectuoso",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T005",
            fecha=date(base_year, 4, 5),
            tipo="T0400",
            descripcion="Conversión de divisa USD a EUR",
            importe=92.30,
            divisa="EUR",
            comision=0.0,
            contraparte="",
            es_cambio_divisa=True,
            nota_origen="General Currency Conversion - 100.00 USD a 92.30 EUR",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T006",
            fecha=date(base_year, 5, 20),
            tipo="T0002",
            descripcion="Tarifa de PayPal",
            importe=-4.20,
            divisa="EUR",
            comision=0.0,
            contraparte="PayPal",
            nota_origen="Comisión mensual de cuenta business",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T007",
            fecha=date(base_year, 6, 2),
            tipo="T0000",
            descripcion="Pago recibido de juan.wallapop@example.com",
            importe=120.00,
            divisa="EUR",
            comision=-4.10,
            contraparte="juan.wallapop@example.com",
            nota_origen="Wallapop: bicicleta estática usada, venta puntual",
        ),
        PayPalTransaction(
            transaction_id="MOCK-T008",
            fecha=date(base_year, 7, 11),
            tipo="T0000",
            descripcion="Pago recibido de desconocido@example.com",
            importe=250.00,
            divisa="USD",
            comision=-8.75,
            divisa_comision="USD",
            contraparte="desconocido@example.com",
            nota_origen="Sin concepto claro; posible venta ocasional en divisa extranjera",
        ),
    ]
