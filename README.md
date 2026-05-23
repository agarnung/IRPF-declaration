# IRPF — Interactive Brokers

Genera **`tabla_definitiva_irpf_AAAA.csv`** para la Renta: acciones en euros (tipos **BCE**), **FIFO**, casillas **0327 / 0328 / 0331**.

## Qué hace y qué no

**Sí:** ganancia o pérdida por **compraventa de acciones** cerrada en el ejercicio (ventas en largo y cierres en corto).

**No:** dividendos, intereses, cambio de divisa (`CASH` / `EUR.USD`), posiciones abiertas a 31/12.

Por eso la fila **TOTAL** del CSV **no tiene por qué coincidir** con «P&G realizadas» de IBKR (suelen incluir dividendos, otros tipos de cambio, etc.). Para Hacienda usa esta tabla; IBKR solo para comprobar que no falten operaciones en `inputs/`.

Más detalle: [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md)

## Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Exports Flex Query en inputs/OPERACIONES*.csv
python process_data.py --ejercicio 2026
```

Opciones: `--actualizar-bce` (vuelve a descargar tipos del BCE).

## Entrada y salida

| | |
|---|---|
| **Entrada** | `inputs/OPERACIONES*.csv` (uno o varios años: hace falta historial de **compras** para FIFO, la tabla solo declara **ventas/cierres del ejercicio**) |
| **Salida principal** | `tabla_definitiva_irpf_2026.csv` — una fila por empresa; **TOTAL** = suma solo de «Ganancia / pérdida neta €» |
| **Extras** | `extras/` — FIFO, tipos BCE, posiciones abiertas (opcional) |
| **Caché** | `cache/` — tipos BCE descargados (no son tus operaciones) |

## Renta Web

- **Una entidad emisora por fila** de la tabla (cada símbolo).
- Rellenar **0327** (símbolo), **0328** (transmisión), **0331** (adquisición FIFO).
- Casilla «No imputación… por recompra homogénea»: solo si hubo **pérdida** y **recompra en los 2 meses siguientes** (el script no lo detecta).

## Excel

CSV con `;` y decimales con **punto**. Si `SUMA()` da 0, importa con *Datos → Desde texto/CSV* (UTF-8) o usa la fila **TOTAL**.

## Más información

- [docs/TUTORIAL_EXPORT_IBKR.md](docs/TUTORIAL_EXPORT_IBKR.md) — cómo crear la Flex Query en IBKR + CSV de ejemplo en `inputs/ejemplos/`
- [docs/CONFORMIDAD_NORMATIVA.md](docs/CONFORMIDAD_NORMATIVA.md) — normativa, cortos, discrepancias con IBKR

## TODO

Ampliar el proyecto para incluir también **dividendos** (importes brutos y netos, **retenciones en el país de origen**, deducciones y el resto de casillas y trámites que exija la Renta por rendimientos del capital mobiliario), además de la compraventa de acciones que ya cubre el script.
