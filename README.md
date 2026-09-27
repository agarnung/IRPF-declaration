# IRPF — Interactive Brokers

Genera **tablas CSV definitivas** para la Renta a partir de tus operaciones de Interactive Brokers: **compraventa de acciones/ETFs** (FIFO, tipos BCE, casillas **0327/0328/0331**) y **opciones (calls/puts)**, incluidas sobre índices.

![](https://s3.elespanol.com/2026/05/22/actualidad/1003744256765_263093308_1024x576.jpg)

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

# Exports Flex Query en inputs/OPERACIONES*.csv
python process_data.py --ejercicio 2026
```

`--actualizar-bce` vuelve a descargar los tipos del BCE.

Si declaras opciones, mira antes [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md): la Flex Query necesita columnas extra (`UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`) que no hacen falta si solo declaras acciones.

## Entrada y salida

| | |
|---|---|
| **Entrada** | `inputs/OPERACIONES*.csv` (uno o varios años: hace falta historial de compras para FIFO; las tablas solo declaran cierres del ejercicio) |
| **Acciones** | `tabla_definitiva_irpf_AAAA.csv` — una fila por empresa; TOTAL = suma de «Ganancia / pérdida neta €» |
| **Opciones** | `tabla_opciones_irpf_AAAA.csv` — una fila por contrato cerrado/vencido (las ejercidas/asignadas sobre acciones van dentro de la tabla de acciones, no aquí) |
| **Minusvalías computables** | `tabla_minusvalias_computables_irpf_AAAA.csv` — solo pérdidas de acciones deducibles ya este ejercicio (regla de los 2 meses) |
| **Extras** | `extras/` — FIFO, tipos BCE, posiciones abiertas, cruces opción↔acción y análisis completo de la regla de los 2 meses (incluidas las bloqueadas) |
| **Caché** | `cache/` — tipos BCE descargados |

## Renta Web

**Acciones (0327/0328/0331):** una entidad emisora por fila de la tabla; 0328/0331 ya incluyen el ajuste por primas de opciones ejercidas/asignadas cuando aplica. Si el aviso de validación señala una empresa con pérdidas mezcladas (computable + bloqueada por la regla de los 2 meses), consulta `extras/perdidas_analisis_regla_2_meses.csv` y declárala en dos entradas separadas si Renta Web lo exige.

**Opciones:** no son 0327/0328/0331 — son ganancia/pérdida que no deriva de transmisión de elementos patrimoniales (art. 37.1.m LIRPF); van en el apartado de futuros/opciones u otros elementos patrimoniales de la base del ahorro (la casilla exacta varía cada año). Una fila marcada «EJERCIDA/ASIGNADA sin cruce automático» no debería declararse ahí, sino sumarse a mano a 0328/0331 de la acción — revisa `extras/opciones_ejercicios_asignaciones.csv`.

## Excel

CSV con `;` y decimales con **punto**. Si `SUMA()` da 0, importa con *Datos → Desde texto/CSV* (UTF-8) o usa la fila TOTAL.

## Más información

- [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md) — Flex Query (acciones y opciones) + ejemplos en `inputs/ejemplos/`
- [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md) — normativa, cortos, opciones (acciones e índices), regla de los 2 meses

## TODO

- Futuros (distintos de opciones).
