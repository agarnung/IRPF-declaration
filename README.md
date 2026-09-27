# IRPF — Interactive Brokers

Genera **tablas CSV definitivas** para la declaración de la Renta a partir de tus operaciones de Interactive Brokers: **compraventa de acciones y ETFs** (tipos **BCE**, FIFO, casillas **0327 / 0328 / 0331**) y **opciones financieras (calls/puts)** (casilla de «Otros elementos patrimoniales», art. 37.1.m LIRPF), con conversión a euros a tipos **BCE**.

![](https://s3.elespanol.com/2026/05/22/actualidad/1003744256765_263093308_1024x576.jpg)

## Qué hace y qué no (por ahora)

**Sí:**
- Ganancia o pérdida por **compraventa de acciones** cerrada en el ejercicio (ventas en largo y cierres en corto), FIFO.
- Ganancia o pérdida por **opciones (calls/puts)** cerradas antes de vencimiento o que expiran sin ejercer.
- **Ejercicio y asignación de opciones sobre ACCIONES**: detecta automáticamente (columna `Notes/Codes` de IBKR) cuándo una opción se ha ejercido/asignado y traslada la prima al **valor de transmisión o adquisición de la acción** (0328/0331), en vez de tratarla como ganancia de la opción — así lo exige la DGT (p. ej. consulta V2172-21).
- **Opciones sobre ÍNDICES** (SPX, NDX, VIX, Euro Stoxx 50...): al liquidarse siempre en efectivo (sin entrega de acciones), tributan siempre como ganancia/pérdida propia — el script las identifica por una lista de símbolos editable y nunca intenta cruzarlas con una acción.
- **Regla de los 2 meses** (art. 33.5.f LIRPF): tabla con las **pérdidas de acciones que SÍ puedes computar** este ejercicio (sin recompra de valores homogéneos en los 2 meses anteriores/posteriores); el resto no aparece en esa tabla porque de momento no es deducible.

**No:** dividendos, intereses, cambio de divisa (`CASH` / `EUR.USD`), posiciones abiertas a 31/12, futuros, fondos/IIC.

> Nota: Por eso las filas **TOTAL** de los CSV **no tienen por qué coincidir** con «P&G realizadas» de IBKR (suelen incluir dividendos, otros tipos de cambio, etc.). Para Hacienda se recomienda usar estas tablas; IBKR solo para comprobar que no falten operaciones.

Más detalle y grado de certeza normativa por caso: [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md)

## Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Exports Flex Query en inputs/OPERACIONES*.csv (acciones y, si operas con ellas, opciones)
python process_data.py --ejercicio 2026
```

Opciones: `--actualizar-bce` (vuelve a descargar tipos del BCE).

Si vas a declarar opciones, revisa primero [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md) — la Flex Query necesita columnas adicionales (`UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`) que no hacen falta si solo declaras acciones.

## Entrada y salida

| | |
|---|---|
| **Entrada** | `inputs/OPERACIONES*.csv` (uno o varios años: hace falta historial de **compras** para FIFO; las tablas solo declaran **cierres del ejercicio**) |
| **Salida — acciones** | `tabla_definitiva_irpf_AAAA.csv` — una fila por empresa; **TOTAL** = suma solo de «Ganancia / pérdida neta €» |
| **Salida — opciones** | `tabla_opciones_irpf_AAAA.csv` — una fila por contrato cerrado/vencido (no incluye las ejercidas/asignadas, que van dentro de la tabla de acciones) |
| **Salida — minusvalías computables** | `tabla_minusvalias_computables_irpf_AAAA.csv` — solo las pérdidas de acciones que la regla de los 2 meses permite computar este ejercicio; **si una venta con pérdida no aparece en esta tabla, es porque no es computable todavía** |
| **Extras** | `extras/` — FIFO, tipos BCE, posiciones abiertas, ejercicios/asignaciones de opciones y análisis completo (bloqueadas incluidas) de la regla de los 2 meses |
| **Caché** | `cache/` — tipos BCE descargados (no son tus operaciones) |

## Renta Web

**Acciones (0327/0328/0331):**
- **Una entidad emisora por fila** de `tabla_definitiva_irpf_AAAA.csv` (cada símbolo).
- Rellenar **0327** (símbolo), **0328** (transmisión), **0331** (adquisición FIFO). Estos importes ya incluyen, cuando aplica, el ajuste por primas de opciones ejercidas/asignadas (columna de auditoría aparte en la propia tabla).
- Si el informe de validación avisa de una empresa con pérdidas mezcladas (computable + bloqueada por la regla de los 2 meses), consulta `extras/perdidas_analisis_regla_2_meses.csv` y, si Renta Web lo exige, declara esa empresa en **dos entradas separadas**.

**Opciones (no son 0327/0328/0331):**
- Las filas de `tabla_opciones_irpf_AAAA.csv` son ganancia/pérdida patrimonial que **no deriva de transmisión de elementos patrimoniales** (art. 37.1.m LIRPF) — van en el apartado de la base del ahorro correspondiente a operaciones en mercados de futuros y opciones/otros elementos patrimoniales (la casilla concreta varía cada año; búscala en Renta Web por ese título, o mira el manual práctico de Renta del ejercicio en la sede de la AEAT).
- Cualquier fila marcada «EJERCIDA/ASIGNADA sin cruce automático» en esa tabla **no** debería declararse ahí, sino sumarse manualmente a 0328/0331 de la acción correspondiente — revisa `extras/opciones_ejercicios_asignaciones.csv`.

## Excel

CSV con `;` y decimales con **punto**. Si `SUMA()` da 0, importa con *Datos → Desde texto/CSV* (UTF-8) o usa la fila **TOTAL**.

## Más información

- [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md) — cómo crear la Flex Query en IBKR (acciones y opciones) + CSV de ejemplo en `inputs/ejemplos/`
- [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md) — normativa, cortos, opciones, regla de los 2 meses, discrepancias con IBKR

## TODO

- Futuros (distintos de opciones).

Dividendos: fuera de alcance a propósito — se llevan aparte.
