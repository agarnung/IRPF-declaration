> **Versión en español.** English README (required by the hackathon): [README.md](README.md).

# IRPF — Interactive Brokers + PayPal

> **Versión en español.** English README (required by the hackathon): [README.md](README.md)

Genera **tablas CSV definitivas** para la Renta a partir de dos fuentes de datos:

- **Interactive Brokers** (`process_data.py`): compraventa de acciones/ETFs (FIFO, tipos BCE, casillas **0327/0328/0331**) y opciones (calls/puts), incluidas sobre índices.
- **PayPal** (`process_paypal.py`, **nuevo**): importa tus movimientos de PayPal y usa **IA** para clasificar cada uno (venta ocasional, ingreso recurrente, reembolso, cambio de divisa, comisión) y explicarte en lenguaje natural su tratamiento fiscal aproximado en el IRPF.

![](https://s3.elespanol.com/2026/05/22/actualidad/1003744256765_263093308_1024x576.jpg)

## Novedad: módulo PayPal + IA (PayPal AI Hackathon 2026)

Mucha gente con actividad de venta ocasional (Vinted, Wallapop, algún encargo de freelance puntual, reembolsos de compras) tiene movimientos en PayPal que **nadie categoriza bien** a la hora de declarar la Renta. Este módulo, añadido durante el **PayPal AI Hackathon 2026**, cubre justo eso:

1. **Importa** el histórico de transacciones de PayPal — igual que ya se hace con los exports de IBKR — a través de un cliente desacoplado detrás de una interfaz (`paypal/client.py`), con dos implementaciones intercambiables:
   - `PayPalSandboxClient`: real, contra la **REST API del sandbox de PayPal** (OAuth2 *client credentials* + Transaction Search API).
   - `MockPayPalClient`: datos de ejemplo realistas y deterministas, para poder usar y probar el proyecto **sin credenciales de PayPal**.
2. **Clasifica** cada movimiento con IA (`paypal/classifier.py`), también detrás de una interfaz:
   - `LLMTransactionClassifier`: real, contra cualquier API **compatible con OpenAI** (OpenAI, Azure OpenAI, OpenRouter, Groq, un servidor propio...). Devuelve categoría, confianza, casilla orientativa de Renta Web y una **explicación en lenguaje natural** del tratamiento fiscal aproximado.
   - `HeuristicTransactionClassifier`: reglas simples (palabras clave, tipo de transacción, detección de recurrencia por importe+contraparte repetidos) que no necesitan API key — sirve de **fallback automático** si el LLM falla y de base para los tests.
3. Genera `tabla_paypal_irpf_<año>.csv` (detalle por transacción), `extras/paypal_resumen_natural.txt` (resumen en prosa, con el aviso de que es una orientación automática, no asesoramiento fiscal vinculante) y `extras/paypal_informe.html` (informe autocontenido para abrir en el navegador: KPIs, tabla filtrable, explicaciones y descargas; sin servidor ni dependencias extra).

Tanto el modo PayPal como el clasificador eligen automáticamente real vs. mock/heurístico según variables de entorno (ver más abajo), con **fallback seguro a mock/heurístico** si faltan credenciales — así la demo nunca se rompe.

Qué es nuevo frente al estado previo del repo (solo IBKR): todo el paquete `paypal/`, el script `process_paypal.py`, sus tests (`tests/test_paypal_*.py`, `tests/test_process_paypal.py`) y esta sección del README.

## Qué hace y qué no (por ahora)

**Sí:**
- Ganancia/pérdida por **compraventa de acciones** cerrada en el ejercicio (largo y corto), FIFO.
- **Opciones**: cierre antes de vencimiento, vencimiento sin ejercer, y **ejercicio/asignación** — en este último caso traslada la prima al valor de transmisión/adquisición de la **acción** (criterio DGT), salvo en opciones sobre **índices**, que liquidan en efectivo y tributan siempre aparte.
- **Regla de los 2 meses** (art. 33.5.f LIRPF): tabla con las pérdidas de acciones que **sí** puedes computar este ejercicio (sin recompra homogénea en ±2 meses); si una venta con pérdida no sale en esa tabla es porque todavía no es deducible.

**No:** dividendos (se llevan aparte, a propósito), intereses, cambio de divisa (`CASH`/`EUR.USD`), posiciones abiertas a 31/12, futuros, fondos/IIC.

> La fila **TOTAL** de cada CSV **no tiene por qué coincidir** con «P&G realizadas» de IBKR (esa suele incluir dividendos, otros cambios de divisa, etc.). Para Hacienda usa estas tablas; IBKR solo para comprobar que no falten operaciones.

Detalle y grado de certeza normativa por caso: [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md)

## Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Interactive Brokers: exports Flex Query en inputs/OPERACIONES*.csv
python process_data.py --ejercicio 2026

# PayPal: funciona sin configurar nada (modo mock + clasificador heurístico)
python process_paypal.py --ejercicio 2026
```

`--actualizar-bce` vuelve a descargar los tipos del BCE (ambos scripts).

Al terminar, `process_paypal.py` abre `extras/paypal_informe.html` en Chromium (o en el navegador por defecto). Si Chromium está instalado como snap (habitual en Ubuntu), el informe y sus descargas se copian a `~/irpf-paypal/` para que el navegador pueda leerlos. Usa `--no-abrir-informe` si no quieres que se abra.

Si declaras opciones de IBKR, mira antes [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md): la Flex Query necesita columnas extra (`UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`) que no hacen falta si solo declaras acciones.

### PayPal: modo mock vs. modo real

Por defecto, `process_paypal.py` funciona **sin ninguna credencial**: usa datos de ejemplo (`MockPayPalClient`) y un clasificador heurístico (`HeuristicTransactionClassifier`). Para activar los modos reales, copia `.env.example` a `.env` (se carga automáticamente si tienes `python-dotenv` instalado) o exporta las variables a mano:

| Variable | Para qué sirve | Por defecto |
|---|---|---|
| `PAYPAL_MODE` | `mock` (datos de ejemplo) o `real` (sandbox de PayPal) | `mock` |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | Credenciales de tu app en [developer.paypal.com](https://developer.paypal.com/dashboard/) (solo si `PAYPAL_MODE=real`) | — |
| `PAYPAL_ENV` | `sandbox` o `live` (solo si `PAYPAL_MODE=real`) | `sandbox` |
| `CLASSIFIER_MODE` | `heuristic` (reglas, sin IA externa) o `llm` (clasificación real con IA) | `heuristic` |
| `OPENAI_API_KEY` / `LITELLM_API_KEY` | API key del proveedor LLM (basta una de las dos; solo si `CLASSIFIER_MODE=llm`) | — |
| `OPENAI_BASE_URL` / `LITELLM_BASE_URL` | Endpoint compatible con OpenAI. `LITELLM_BASE_URL` añade `/v1` si falta | API pública de OpenAI |
| `OPENAI_MODEL` / `LLM_MODEL` | Modelo a usar | `gpt-4o-mini` |

```bash
# Ejemplo con todo en real (necesita credenciales de PayPal sandbox y una API key de LLM)
PAYPAL_MODE=real PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... \
CLASSIFIER_MODE=llm OPENAI_API_KEY=sk-... \
python process_paypal.py --ejercicio 2026
```

Si pides modo real pero faltan credenciales, el script **no falla**: avisa por consola y sigue en modo mock/heurístico.

> **Si el sandbox real falla por red/DNS**, no hace falta arreglarlo para la demo: usa `PAYPAL_MODE=mock` (24 movimientos de ejemplo) y, si quieres, `CLASSIFIER_MODE=llm`. El fallback automático a mock/heurístico solo cubre credenciales ausentes; un fallo de red en modo real sí corta la ejecución, así que para grabar usa el mock.

El clasificador acepta también un **proxy LiteLLM local** (Ollama/vLLM/llama.cpp) sin API key de pago: define `LITELLM_API_KEY` y `LITELLM_BASE_URL` (el código añade `/v1` si falta) junto con `LLM_MODEL`. Si el modelo local no soporta `response_format=json_object`, se reintenta sin ese parámetro y se toleran bloques ```` ```json ````; si aun así no devuelve JSON válido, cada transacción cae a la heurística.

## Entrada y salida

### Interactive Brokers

| | |
|---|---|
| **Entrada** | `inputs/OPERACIONES*.csv` (uno o varios años: hace falta historial de compras para FIFO; las tablas solo declaran cierres del ejercicio) |
| **Acciones** | `tabla_definitiva_irpf_AAAA.csv` — una fila por empresa; TOTAL = suma de «Ganancia / pérdida neta €» |
| **Opciones** | `tabla_opciones_irpf_AAAA.csv` — una fila por contrato cerrado/vencido (las ejercidas/asignadas sobre acciones van dentro de la tabla de acciones, no aquí) |
| **Minusvalías computables** | `tabla_minusvalias_computables_irpf_AAAA.csv` — solo pérdidas de acciones deducibles ya este ejercicio (regla de los 2 meses) |
| **Extras** | `extras/` — FIFO, tipos BCE, posiciones abiertas, cruces opción↔acción y análisis completo de la regla de los 2 meses (incluidas las bloqueadas) |
| **Caché** | `cache/` — tipos BCE descargados |
| **Ejemplos sintéticos** | `inputs/ejemplos/` (CSV de ejemplo) e `inputs/ejemplos/informes/` (salidas ya generadas con datos inventados, 2025 y 2026) |

### PayPal

| | |
|---|---|
| **Entrada** | Transacciones del rango de fechas indicado (`--fecha-inicio`/`--fecha-fin`, por defecto el año natural de `--ejercicio`), obtenidas del cliente real o mock — no hace falta ningún CSV |
| **Salida** | `tabla_paypal_irpf_AAAA.csv` — una fila por transacción: categoría fiscal, confianza de la IA, importe convertido a EUR con tipos BCE, casilla orientativa y explicación en lenguaje natural |
| **Extras** | `extras/paypal_resumen_por_categoria.csv` (totales agregados), `extras/paypal_resumen_natural.txt` (resumen en prosa + listado de movimientos a revisar manualmente) y `extras/paypal_informe.html` (informe visual autocontenido, listo para el navegador o para guardar como PDF) |

## Renta Web

**Acciones (0327/0328/0331):** una entidad emisora por fila de la tabla; 0328/0331 ya incluyen el ajuste por primas de opciones ejercidas/asignadas cuando aplica. Si el aviso de validación señala una empresa con pérdidas mezcladas (computable + bloqueada por la regla de los 2 meses), consulta `extras/perdidas_analisis_regla_2_meses.csv` y declárala en dos entradas separadas si Renta Web lo exige.

**Opciones:** no son 0327/0328/0331 — son ganancia/pérdida que no deriva de transmisión de elementos patrimoniales (art. 37.1.m LIRPF); van en el apartado de futuros/opciones u otros elementos patrimoniales de la base del ahorro (la casilla exacta varía cada año). Una fila marcada «EJERCIDA/ASIGNADA sin cruce automático» no debería declararse ahí, sino sumarse a mano a 0328/0331 de la acción — revisa `extras/opciones_ejercicios_asignaciones.csv`.

**PayPal:** la clasificación (venta ocasional / ingreso recurrente / reembolso / cambio de divisa / comisión) y la casilla orientativa son una **ayuda automática, no asesoramiento fiscal vinculante** — tanto si viene del LLM como de las reglas heurísticas. Revisa siempre las filas marcadas «Revisar manualmente» (importe elevado, recurrencia sospechosa de actividad económica no declarada, o movimientos que no encajan en ninguna categoría) antes de trasladarlas a Renta Web, y ante dudas reales consulta con un asesor fiscal.

## Excel

CSV con `;` y decimales con **punto**. Si `SUMA()` da 0, importa con *Datos → Desde texto/CSV* (UTF-8) o usa la fila TOTAL.


## Vídeo demo

El vídeo (2:55) tiene narración y subtítulos en inglés. Vídeo: https://www.youtube.com/watch?v=VdFLO-PU66Q — subtítulos: [video/subtitles.srt](video/subtitles.srt); capítulos: [video/chapters.txt](video/chapters.txt).

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Todos los tests (31) cubren el módulo PayPal — cliente, clasificador y pipeline — y se ejecutan **sin red ni credenciales** (modo mock + heurístico); el único acceso de red real durante los tests es la descarga/caché de tipos del BCE, ya existente para IBKR.

## Más información

- [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md) — Flex Query (acciones y opciones) + ejemplos en `inputs/ejemplos/`
- [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md) — normativa, cortos, opciones (acciones e índices), regla de los 2 meses
- [.env.example](.env.example) — variables de entorno del módulo PayPal, documentadas
- [LICENSE](LICENSE) — MIT

## TODO

- Futuros (distintos de opciones) en Interactive Brokers.
- PayPal: paginación/caché local de transacciones reales para cuentas con mucho volumen; soporte de más tipos de evento de PayPal (actualmente se infieren por texto/código, no hay un mapeo exhaustivo de todos los `transaction_event_code`).
