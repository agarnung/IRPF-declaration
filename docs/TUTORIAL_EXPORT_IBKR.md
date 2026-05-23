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

Solo interesan filas con **`AssetClass` = STK** (acciones). No hace falta traer Dividends ni Trades de forex si puedes evitarlos.

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

En `inputs/ejemplos/` hay dos CSV mínimos para entender el flujo:

| Archivo | Qué ilustra |
|---------|-------------|
| `EJEMPLO_2024_compras.csv` | Compras en 2024 (historial para FIFO) |
| `EJEMPLO_2025_operaciones.csv` | Venta con FIFO, cierre de corto, venta en USD |

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

---

## Checklist rápido

- [ ] Flex Query solo **Trades**, columnas de la tabla arriba  
- [ ] CSV en `inputs/`, nombre `OPERACIONES*.csv`  
- [ ] Incluidas **compras de años anteriores** si vendiste en el ejercicio  
- [ ] `python process_data.py --ejercicio AAAA`  
- [ ] Renta Web: **una entidad emisora por fila** del CSV (0327, 0328, 0331)  

Más normativa y límites: [CONFORMIDAD_NORMATIVA.md](CONFORMIDAD_NORMATIVA.md)
