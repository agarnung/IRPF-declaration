"""Clasificación fiscal de transacciones de PayPal.

Dos implementaciones de `TransactionClassifier`:
  - `LLMTransactionClassifier`: usa un modelo de lenguaje a través de una API
    compatible con OpenAI (OpenAI, Azure OpenAI, Groq, OpenRouter, vLLM o un
    proxy LiteLLM local) para clasificar cada movimiento y redactar una
    explicación en lenguaje natural del tratamiento fiscal aproximado en el IRPF.
  - `HeuristicTransactionClassifier`: reglas simples (palabras clave, tipo de
    transacción, divisa, recurrencia) que no necesitan API key — fallback
    para la demo y base de los tests.

El proyecto no usaba hasta ahora ninguna librería de LLM, así que aquí se
opta por el patrón más estándar e interoperable: el cliente HTTP oficial
`openai`, apuntando por defecto a la API de OpenAI pero configurable a
cualquier endpoint compatible vía OPENAI_BASE_URL.
"""

from __future__ import annotations

import abc
import json
import os
import re

from .models import CATEGORIAS_FISCALES, ClassifiedTransaction, PayPalTransaction

# Modelo por defecto si no se especifica OPENAI_MODEL. "Mini"/económico
# porque clasificar una transacción es una tarea sencilla y barata.
MODELO_LLM_DEFECTO = "gpt-4o-mini"

PROMPT_SISTEMA = """Eres un asistente fiscal especializado en IRPF español (Impuesto \
sobre la Renta de las Personas Físicas). Tu tarea es clasificar movimientos \
de una cuenta de PayPal perteneciente a un particular (no una empresa) y \
explicar en lenguaje natural, de forma breve y clara, su tratamiento fiscal \
aproximado.

Categorías posibles (usa EXACTAMENTE una de estas, en minúsculas):
- venta_ocasional: venta puntual de un bien personal usado (p.ej. Vinted, \
Wallapop, un objeto de segunda mano). Normalmente NO genera ganancia \
patrimonial si se vende por menos de lo que costó (art. 33.1 LIRPF / regla \
de "menaje" para bienes de uso personal), salvo que el importe o la \
frecuencia sugieran una actividad económica.
- ingreso_recurrente: pago repetido/periódico de un cliente o plataforma, \
compatible con actividad económica (autónomo/freelance) o rendimiento del \
trabajo/capital — debe declararse como tal.
- reembolso: devolución de un pago anterior (no es renta, no tributa).
- cambio_divisa: conversión de divisa dentro de PayPal (no es una \
transacción de compraventa real; revisar si genera ganancia/pérdida \
patrimonial solo si hay tenencia de divisa como activo).
- comision: comisión o tarifa cobrada por PayPal (gasto deducible si la \
actividad relacionada es una actividad económica; irrelevante si es \
consumo personal).
- otro: cualquier cosa que no encaje claramente en las anteriores.

INSTRUCCIONES:
1. Clasifica basándote en descripción, importe, frecuencia y contraparte.
2. Da una confianza entre 0.0 y 1.0.
3. Escribe una explicación breve (máximo 3 frases) en español, en lenguaje \
natural, sobre el tratamiento fiscal aproximado en el IRPF.
4. Indica si hace falta revisión manual (p.ej. importes altos, posible \
actividad económica no declarada, ambigüedad real).
5. Si lo sabes, indica una casilla o apartado orientativo de Renta Web \
(si no aplica ninguna casilla clara, deja el campo vacío).

IMPORTANTE: esto es una ORIENTACIÓN automática, no asesoramiento fiscal \
vinculante. Debes dejarlo claro de forma implícita (tono prudente), sin \
necesidad de repetirlo literalmente en cada explicación.

Responde ÚNICAMENTE con un JSON con este esquema exacto, sin texto adicional:
{"categoria": "...", "confianza": 0.0, "explicacion": "...", \
"casilla_orientativa": "...", "requiere_revision_manual": true}
"""


class TransactionClassifier(abc.ABC):
    """Interfaz mínima que necesita el pipeline de IRPF."""

    @abc.abstractmethod
    def clasificar(self, transaccion: PayPalTransaction) -> ClassifiedTransaction:
        raise NotImplementedError

    def clasificar_lote(
        self, transacciones: list[PayPalTransaction]
    ) -> list[ClassifiedTransaction]:
        """Por defecto, una llamada por transacción. Las implementaciones que
        puedan abaratar costes agrupando llamadas pueden sobrescribir esto."""
        return [self.clasificar(t) for t in transacciones]


class LLMTransactionClassifier(TransactionClassifier):
    """Clasificador real contra una API compatible con OpenAI.

    Variables de entorno:
      - OPENAI_API_KEY o LITELLM_API_KEY (una de las dos, obligatoria)
      - OPENAI_BASE_URL o LITELLM_BASE_URL (opcional; por defecto la API
        pública de OpenAI — cualquier proveedor OpenAI-compatible vale, p.ej.
        Azure OpenAI, OpenRouter, Groq, vLLM, o un proxy LiteLLM local)
      - OPENAI_MODEL o LLM_MODEL (opcional; por defecto MODELO_LLM_DEFECTO)

    Si la llamada a la API falla (red, cuota, modelo no disponible...), cada
    transacción individual cae en `HeuristicTransactionClassifier` en vez de
    abortar todo el lote: mejor una orientación heurística que ninguna.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        fallback: TransactionClassifier | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("LITELLM_API_KEY")
            or ""
        )
        env_base = os.environ.get("OPENAI_BASE_URL")
        if not env_base and os.environ.get("LITELLM_BASE_URL"):
            env_base = os.environ["LITELLM_BASE_URL"].rstrip("/")
            if not env_base.endswith("/v1"):
                env_base += "/v1"
        self.base_url = base_url or env_base or None
        self.model = (
            model
            or os.environ.get("OPENAI_MODEL")
            or os.environ.get("LLM_MODEL")
            or MODELO_LLM_DEFECTO
        )
        self.fallback = fallback or HeuristicTransactionClassifier()

        if not self.api_key:
            raise ValueError(
                "Missing OPENAI_API_KEY (or LITELLM_API_KEY). Set it as an "
                "environment variable or use CLASSIFIER_MODE=heuristic for the "
                "demo without an LLM."
            )

        # Import perezoso: el paquete `openai` solo hace falta en modo real.
        from openai import OpenAI

        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def clasificar(self, transaccion: PayPalTransaction) -> ClassifiedTransaction:
        try:
            return self._clasificar_con_llm(transaccion)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de red/API cae a heurística
            resultado = self.fallback.clasificar(transaccion)
            resultado.explicacion = (
                f"(LLM no disponible: {exc}; clasificación heurística de respaldo) "
                + resultado.explicacion
            )
            resultado.requiere_revision_manual = True
            return resultado

    def _clasificar_con_llm(self, transaccion: PayPalTransaction) -> ClassifiedTransaction:
        contenido_usuario = _describir_transaccion_para_prompt(transaccion)

        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": contenido_usuario},
            ],
            temperature=0.1,
        )
        try:
            respuesta = self._client.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
        except Exception:
            # Algunos modelos locales (Ollama/vLLM vía LiteLLM) no soportan
            # response_format=json_object; reintentamos sin ese parámetro.
            respuesta = self._client.chat.completions.create(**kwargs)
        bruto = respuesta.choices[0].message.content
        datos = _parse_json_llm(bruto)

        categoria = str(datos.get("categoria", "otro")).strip().lower()
        if categoria not in CATEGORIAS_FISCALES:
            categoria = "otro"

        return ClassifiedTransaction(
            transaction=transaccion,
            categoria=categoria,
            confianza=float(datos.get("confianza", 0.5)),
            explicacion=str(datos.get("explicacion", "")).strip(),
            casilla_orientativa=str(datos.get("casilla_orientativa", "")).strip(),
            requiere_revision_manual=bool(datos.get("requiere_revision_manual", False)),
            motor="llm",
        )


def _parse_json_llm(bruto: str) -> dict:
    """Extrae JSON de la respuesta de un LLM, tolerando ```json ... ```.

    Los modelos locales a veces envuelven el JSON en markdown o añaden texto;
    aquí se limpia lo razonable y, si aun así no es JSON, se lanza la excepción
    para que `clasificar()` caiga a la heurística por transacción.
    """
    texto = (bruto or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto).strip()
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fin = texto.find("{"), texto.rfind("}")
        if inicio != -1 and fin > inicio:
            return json.loads(texto[inicio : fin + 1])
        raise


def _describir_transaccion_para_prompt(t: PayPalTransaction) -> str:
    return (
        f"Fecha: {t.fecha.isoformat()}\n"
        f"Tipo PayPal: {t.tipo}\n"
        f"Descripción: {t.descripcion}\n"
        f"Nota/memo: {t.nota_origen}\n"
        f"Importe: {t.importe:.2f} {t.divisa}\n"
        f"Comisión: {t.comision:.2f} {t.divisa_comision or t.divisa}\n"
        f"Contraparte: {t.contraparte}\n"
        f"Marcado como cambio de divisa por PayPal: {t.es_cambio_divisa}\n"
    )


# ---------------------------------------------------------------------------
# Clasificador heurístico (fallback / demo sin API key)
# ---------------------------------------------------------------------------

PALABRAS_REEMBOLSO = ("reembolso", "refund", "devoluc")
PALABRAS_VENTA_OCASIONAL = (
    "vinted", "wallapop", "milanuncios", "segunda mano", "usado", "usada",
)
PALABRAS_RECURRENTE = (
    "factura mensual", "mensualidad", "suscripcion", "suscripción",
    "recurrente", "nómina", "nomina",
)
PALABRAS_COMISION = ("comisión", "comision", "tarifa", "fee")

UMBRAL_VENTA_OCASIONAL_SOSPECHOSA_EUR = 1000.0


class HeuristicTransactionClassifier(TransactionClassifier):
    """Reglas simples basadas en tipo de transacción, texto e importe.

    No sustituye al LLM en matices (p.ej. distinguir una venta ocasional
    genuina de una actividad económica no declarada), pero cubre los casos
    de ejemplo típicos y sirve de fallback robusto cuando no hay API key o
    la llamada al LLM falla. Además, detecta ingresos recurrentes agrupando
    por contraparte + importe idéntico repetido, algo que una regla por
    transacción aislada no podría ver.
    """

    def __init__(self) -> None:
        # Cache interna para detectar recurrencia: (contraparte, importe) -> nº de veces visto.
        self._historial: dict[tuple[str, float], int] = {}

    def clasificar_lote(
        self, transacciones: list[PayPalTransaction]
    ) -> list[ClassifiedTransaction]:
        # Se calcula la recurrencia sobre TODO el lote antes de clasificar,
        # para no depender del orden en que se llame a `clasificar()`.
        conteo: dict[tuple[str, float], int] = {}
        for t in transacciones:
            clave = (t.contraparte.lower(), round(t.importe, 2))
            conteo[clave] = conteo.get(clave, 0) + 1
        self._historial = conteo
        return [self.clasificar(t) for t in transacciones]

    def clasificar(self, transaccion: PayPalTransaction) -> ClassifiedTransaction:
        t = transaccion
        texto = f"{t.descripcion} {t.nota_origen}".lower()

        if t.es_cambio_divisa or _contiene(texto, ("conversión de divisa", "currency conversion")):
            return self._resultado(
                t, "cambio_divisa", 0.9,
                "Es una conversión interna de divisa dentro de PayPal, no una "
                "compraventa de un bien o servicio: no genera, por sí misma, "
                "una renta a declarar en el IRPF (salvo que gestiones divisa "
                "extranjera como activo patrimonial, caso infrecuente para un "
                "particular).",
                casilla="",
            )

        if t.importe < 0 and (_contiene(texto, PALABRAS_COMISION) or "paypal" in t.contraparte.lower()):
            return self._resultado(
                t, "comision", 0.85,
                "Es una comisión o tarifa cobrada por PayPal. Si está ligada a "
                "una actividad económica (autónomo), es gasto deducible; si es "
                "de uso personal, no tiene efecto en el IRPF.",
                casilla="Gasto deducible (solo si hay actividad económica, modelo 130/IRPF)",
            )

        if _contiene(texto, PALABRAS_REEMBOLSO) or (t.importe < 0 and t.comision > 0):
            return self._resultado(
                t, "reembolso", 0.85,
                "Es la devolución de un pago anterior: no es una renta nueva y, "
                "en general, no tiene que declararse en el IRPF (simplemente "
                "revierte un gasto o cobro previo).",
                casilla="",
            )

        clave = (t.contraparte.lower(), round(t.importe, 2))
        es_recurrente = self._historial.get(clave, 0) >= 2

        if t.importe > 0 and (es_recurrente or _contiene(texto, PALABRAS_RECURRENTE)):
            return self._resultado(
                t, "ingreso_recurrente", 0.75 if es_recurrente else 0.6,
                "El mismo importe se repite con la misma contraparte (o el "
                "texto sugiere un cobro periódico): es compatible con "
                "actividad económica (autónomo) o rendimiento habitual, que "
                "debe declararse como rendimiento de actividades económicas "
                "o del trabajo, no como ganancia patrimonial puntual. "
                "Revisa si estás dado de alta como autónomo si esto es "
                "recurrente y significativo.",
                casilla="Rendimientos de actividades económicas (estimación directa)",
                revisar=True,
            )

        if t.importe > 0 and _contiene(texto, PALABRAS_VENTA_OCASIONAL):
            sospechoso = t.importe >= UMBRAL_VENTA_OCASIONAL_SOSPECHOSA_EUR
            return self._resultado(
                t, "venta_ocasional", 0.8 if not sospechoso else 0.5,
                "Parece una venta puntual de un bien personal de segunda mano "
                "(tipo Vinted/Wallapop). Si se vende por un importe igual o "
                "inferior al precio de adquisición, normalmente no genera "
                "ganancia patrimonial (art. 33.1 LIRPF); si se vende por más "
                "de lo pagado, sí habría ganancia patrimonial a declarar. Si "
                "estas ventas son frecuentes o de importe elevado, Hacienda "
                "puede considerarlo actividad económica."
                + (" Importe elevado: revisa si es realmente ocasional." if sospechoso else ""),
                casilla="Ganancia patrimonial (solo si precio venta > adquisición)",
                revisar=sospechoso,
            )

        if t.importe > 0:
            return self._resultado(
                t, "otro", 0.3,
                "No se ha podido determinar con reglas simples el origen de "
                "este ingreso. Revisa manualmente si corresponde a una venta "
                "ocasional, un ingreso recurrente u otro concepto antes de "
                "incluirlo (o no) en la declaración.",
                casilla="",
                revisar=True,
            )

        return self._resultado(
            t, "otro", 0.3,
            "No se ha podido determinar con reglas simples el motivo de este "
            "cargo. Revisa manualmente el movimiento.",
            casilla="",
            revisar=True,
        )

    @staticmethod
    def _resultado(
        t: PayPalTransaction,
        categoria: str,
        confianza: float,
        explicacion: str,
        casilla: str = "",
        revisar: bool = False,
    ) -> ClassifiedTransaction:
        return ClassifiedTransaction(
            transaction=t,
            categoria=categoria,
            confianza=confianza,
            explicacion=explicacion,
            casilla_orientativa=casilla,
            requiere_revision_manual=revisar,
            motor="heuristico",
        )


def _contiene(texto: str, palabras: tuple[str, ...]) -> bool:
    return any(re.search(re.escape(p), texto) for p in palabras)


def get_classifier() -> TransactionClassifier:
    """Factoría: decide entre LLM real y heurística según variables de entorno.

    - `CLASSIFIER_MODE=llm` (y OPENAI_API_KEY presente) → LLMTransactionClassifier.
    - Cualquier otro caso (por defecto, o `CLASSIFIER_MODE=heuristic`, o sin
      API key) → HeuristicTransactionClassifier, para que la demo nunca se
      rompa por falta de API key.
    """
    modo = os.environ.get("CLASSIFIER_MODE", "heuristic").strip().lower()
    if modo == "llm":
        try:
            return LLMTransactionClassifier()
        except ValueError as exc:
            print(
                f"  (warning) CLASSIFIER_MODE=llm but {exc} "
                "Using HeuristicTransactionClassifier as fallback."
            )
            return HeuristicTransactionClassifier()
    return HeuristicTransactionClassifier()
