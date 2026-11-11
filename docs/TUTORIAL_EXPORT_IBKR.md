> **English version:** [TUTORIAL_EXPORT_IBKR_EN.md](TUTORIAL_EXPORT_IBKR_EN.md)

# Cómo exportar el CSV desde Interactive Brokers

El script necesita **operaciones en bruto** (CSV), no un PDF ni un informe de «P&G realizadas» de IBKR. Esos informes usan **tipos de cambio del bróker**; Hacienda exige el **BCE del día de cada operación** y el método **FIFO**.

## Por qué no basta con el informe de IBKR

| Problema | Qué hace IBKR | Qué exige Hacienda / este script |
|----------|---------------|----------------------------------|
| **Tipo de cambio** | Su cotización interna al ejecutar | [Tipo oficial BCE](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.es.html) del **día de la operación** |
| **FIFO** | Puede mostrar otro criterio en pantalla | Primera compra = primera venta (**art. 37.2 LIRPF**) |

**Metáfora (tipo de cambio):** Hacienda te da una «regla oficial» (BCE). No vale la «regla del bróker» ni la «media del año».

**Metáfora (FIFO):** Fila del supermercado: la primera acción que compraste es la primera que se considera vendida.

Este proyecto calcula **solo compraventa de acciones** (casillas 0327 / 0328 / 0331). **No** incluye dividendos, intereses ni filas de cambio de divisa (`EUR.USD`).

---

## Pasos en IBKR

### 1. Abrir Flex Queries

1. Inicia sesión en [Interactive Brokers](https://www.interactivebrokers.com).
2. **Rendimiento e informes** → **Consultas Flex**.
3. **+** (nueva consulta de actividad comercial).

### 2. Secciones y columnas

En **Secciones**, activa solo **Trades** (Operaciones).

Dentro de Trades, marca estas columnas (los nombres en pantalla pueden variar; en el CSV suelen salir como abajo):

| En IBKR (aprox.) | Columna en el CSV del script |
|------------------|------------------------------|
| Symbol | `Symbol` |
| Date/Time | `DateTime` |
| Quantity | `Quantity` |
| Trade Price | `TradePrice` |
| Proceeds | `Proceeds` |
| Commission | `IBCommission` |
| Currency | `CurrencyPrimary` |
| Asset Class | `AssetClass` |
| Buy/Sell | `Buy/Sell` |

El script procesa filas con **`AssetClass` = STK** (acciones) y, si las incluyes, también **`AssetClass` = OPT** (opciones). No hace falta traer Dividends ni Trades de forex si puedes evitarlos.

#### Columnas adicionales si operas con opciones (calls/puts)

Si tu cuenta tiene operaciones de opciones y quieres que también salgan en la tabla, añade **además** estas columnas a la misma Flex Query (están en la misma sección Trades, agrupadas normalmente bajo «Opciones» o al final del listado de campos):

| En IBKR (aprox.) | Columna en el CSV del script | Para qué sirve |
|------------------|------------------------------|-----------------|
| Underlying Symbol | `UnderlyingSymbol` | Acción subyacente (p. ej. `AAPL` para una opción sobre Apple) |
| Strike | `Strike` | Precio de ejercicio |
| Expiry | `Expiry` | Fecha de vencimiento del contrato |
| Put/Call | `Put/Call` | `C` (call) o `P` (put) |
| Multiplier | `Multiplier` | Acciones por contrato (normalmente 100; si falta, el script asume 100) |
| Notes/Codes | `Notes/Codes` | **Imprescindible** para detectar ejercicio (`Ex`) y asignación (`A`) — sin esta columna el script no puede distinguir un ejercicio/asignación de un cierre normal, y **la prima acabaría tributando aparte en vez de sumarse/restarse a la acción**, que es un resultado incorrecto según Hacienda |

Si ejerces una opción o te asignan una, IBKR genera automáticamente en Trades una fila de la acción subyacente (compra o venta al precio de ejercicio) **el mismo día**. Asegúrate de que esa fila de acciones también está en tu export (mismo rango de fechas) — si no, el script no podrá cruzar el ejercicio con la acción y lo marcará como «REVISAR» en vez de aplicarlo automáticamente (ver `extras/opciones_ejercicios_asignaciones.csv` tras ejecutar el script).

#### Si operas opciones sobre ÍNDICES (SPX, NDX, VIX, Euro Stoxx 50...)

Estas **no** entregan acciones: se liquidan siempre en efectivo, así que el script las trata siempre como ganancia/pérdida propia (nunca intenta cruzarlas con una acción, aunque IBKR las marque como ejercicio/asignación). Las reconoce por el símbolo del subyacente contra la lista `SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO` al principio de `process_data.py` — amplíala si operas un índice que no esté ahí (si no, el script intentará —mal— cruzarla con una acción inexistente).

IBKR a veces reporta la liquidación en efectivo de una opción sobre índice fuera de la sección Trades (en «Cash Settlement» / «Option Exercises, Assignments and Expirations»). Si al ejecutar el script ves un aviso de «posible liquidación en efectivo no reflejada», comprueba el importe en IBKR y ajústalo a mano en `tabla_opciones_irpf_AAAA.csv`.

### 3. Formato de salida

- **Formato:** CSV  
- **Separador:** coma (`,`)  
- **Fecha:** `dd/MM/yyyy` (europeo)  
- El script espera fecha/hora como en IBKR: `31/12/2025,153045`

Guarda la consulta (p. ej. «IRPF Operaciones»).

### 4. Ejecutar por periodo

1. **Ejecutar** la consulta.
2. Periodo: **todo el historial que necesites para FIFO**  
   - Ejemplo declaración 2025: desde la **primera compra** que aún no habías vendido hasta **31/12/2025**.  
   - Si vendiste en 2025 acciones compradas en 2024, incluye **2024 y 2025** (dos exports o un solo CSV largo).
3. Descarga el CSV y guárdalo en **`inputs/`** con un nombre tipo `OPERACIONES_FLEX_2025.csv`.

Puedes tener varios archivos (`OPERACIONES_FLEX_2024.csv`, `OPERACIONES_FLEX_2025.csv`, …). El script los une y aplica FIFO.

### 5. Generar la tabla IRPF

```bash
python process_data.py --ejercicio 2025
```

Salida: `tabla_definitiva_irpf_2025.csv` (y detalles en `extras/`).

---

## Archivos de ejemplo

En `inputs/ejemplos/` hay CSV mínimos para entender el flujo:

| Archivo | Qué ilustra |
|---------|-------------|
| `EJEMPLO_2024_compras.csv` | Compras en 2024 (historial para FIFO) |
| `EJEMPLO_2025_operaciones.csv` | Venta con FIFO, cierre de corto, venta en USD |
| `EJEMPLO_2026_opciones.csv` | Opciones: cierre normal, vencimiento sin ejercer, y ejercicio/asignación con traslado automático de la prima a la acción |

**NVIDIA** (`NVDA`) en **USD** + conversión **BCE** y FIFO; ficticios `CORTO` (euros) y `USDX` (otro ticker en USD).

Para probarlos sin mezclar con tus datos reales, en `process_data.py` define temporalmente:

```python
ARCHIVOS_CSV = [
    "ejemplos/EJEMPLO_2024_compras.csv",
    "ejemplos/EJEMPLO_2025_operaciones.csv",
]
```

y ejecuta `python process_data.py --ejercicio 2025`.

En uso normal deja `ARCHIVOS_CSV = []` y pon tus exports como `inputs/OPERACIONES_FLEX_*.csv`.

### Qué deberías ver al probar el ejemplo

**NVIDIA (NVDA)** — en dólares, como en IBKR (`CurrencyPrimary` = USD). Precios ficticios redondos solo para seguir el cálculo:

| Operación | Fecha | Importe USD* | Tipo BCE (USD/EUR) | Importe EUR (BCE) |
|-----------|-------|--------------|--------------------|-------------------|
| Compra 100 | 15/03/2024 | 10.002 | 1,0892 | **9.182,89** |
| Compra 100 | 20/09/2024 | 12.002 | 1,1166 | 11.019,10 |
| Venta 100 | 10/06/2025 | 14.998 | 1,1429 | **13.122,76** |

\* Incluye comisión de ejemplo (`Proceeds` + `IBCommission`).

Al vender en 2025, la casilla **0331** debe ser **≈ 9.182,89 €** (primer lote, marzo 2024), **no** ≈ 11.019 € (lote de septiembre) → **FIFO**. Ganancia neta de esa fila **≈ 3.939,87 €**. Los tipos BCE pueden variar ligeramente si actualizas `cache/` con `--actualizar-bce`.

En `extras/posiciones_abiertas.csv` debería quedar otro lote de **100 NVDA** sin vender (compra de septiembre 2024).

- **CORTO:** venta en corto en febrero y recompra en marzo (en euros) → ganancia al cerrar en 2025.
- **USDX:** otro ticker en USD; compra 2024 y venta 2025 con BCE de cada fecha.

### Qué deberías ver con el ejemplo de opciones (`EJEMPLO_2026_opciones.csv`)

Pruébalo igual que el anterior, cambiando la lista temporal de `ARCHIVOS_CSV` y ejecutando `python process_data.py --ejercicio 2026`. En euros para seguir el cálculo sin tipos BCE de por medio:

| Contrato | Qué pasa | Dónde sale |
|----------|----------|------------|
| `AAPL 180 C` vencimiento 19/06/2026 | Se compra por 502 € (prima + comisión) y se **vende para cerrar** por 798 € antes de vencer | `tabla_opciones_irpf_2026.csv`: ganancia **296 €** |
| `TSLA 150 P` vencimiento 20/02/2026 | Se compra por 301 € y **vence sin ejercer** (código `Ep`) | `tabla_opciones_irpf_2026.csv`: pérdida **-301 €** |
| `AAPL 200 C` vendida (escrita) el 20/01, **asignada** el 15/03 (código `A`) | Prima cobrada (399 €) se **suma** al valor de transmisión de la venta de 100 AAPL a 200 el 15/03 | Dentro de `tabla_definitiva_irpf_2026.csv` (AAPL), **no** en la tabla de opciones |
| `AAPL 170 P` vendida (escrita) el 25/01, **asignada** el 20/03 (código `A`) | Prima cobrada (499 €) se **resta** del valor de adquisición de la compra de 100 AAPL a 170 el 20/03 | Dentro de `tabla_definitiva_irpf_2026.csv` (AAPL) |

Como las dos asignaciones de AAPL casan entre sí (vendes 100 el 15/03, recompras 100 el 20/03), el motor FIFO de acciones las empareja igual que una compraventa normal: transmisión ajustada 20.398 € (20.000 + 399 − 1 de comisión), adquisición ajustada 16.502 € (17.000 − 499 + 1 de comisión) → ganancia **3.896 €** en la fila `AAPL` de `tabla_definitiva_irpf_2026.csv`. Revisa `extras/opciones_ejercicios_asignaciones.csv` para ver el cruce prima↔acción detallado, y `extras/detalle_operaciones_bce.csv` para ver el ajuste aplicado a cada operación de acciones.

---

## Checklist rápido

- [ ] Flex Query **Trades**, columnas de la tabla de acciones arriba
- [ ] Si operas opciones: añadidas también `UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`
- [ ] CSV en `inputs/`, nombre `OPERACIONES*.csv`
- [ ] Incluidas **compras de años anteriores** si vendiste en el ejercicio
- [ ] Si ejerces/te asignan opciones: la operación de acciones resultante está en el **mismo CSV** (mismo rango de fechas)
- [ ] `python process_data.py --ejercicio AAAA`
- [ ] Sin filas «REVISAR» en `extras/opciones_ejercicios_asignaciones.csv` (o revisadas manualmente si las hay)
- [ ] Renta Web acciones: **una entidad emisora por fila** de `tabla_definitiva_irpf_AAAA.csv` (0327, 0328, 0331)
- [ ] Renta Web opciones: filas de `tabla_opciones_irpf_AAAA.csv` en el apartado de futuros/opciones (no en 0327/0328/0331)
- [ ] Pérdidas de acciones: solo las de `tabla_minusvalias_computables_irpf_AAAA.csv` son computables este ejercicio

Más normativa y límites: [CONFORMIDAD_NORMATIVA.md](CONFORMIDAD_NORMATIVA.md)
