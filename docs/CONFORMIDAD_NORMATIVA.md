# Conformidad normativa — IRPF 2025 (revisión del script)

Este documento resume cómo `process_data.py` se alinea con la normativa española vigente para **transmisiones de acciones cotizadas** (Interactive Brokers, valores homogéneos). No sustituye asesoramiento fiscal profesional.

## Base legal principal

| Materia | Norma / fuente |
|---------|----------------|
| Ganancia = transmisión − adquisición | Art. 37.1.a) y 37.2 LIRPF |
| FIFO (valores homogéneos) | Art. 37.2 LIRPF — [Manual AEAT IRPF 2025, régimen general IIC/identificación](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/c11-ganancias-perdidas-patrimoniales/determinacion-importe-ganancias-perdidas-patrimon-especificas/transmisiones-reembolsos-acciones-iic/regimen-general.html) |
| Tipo de cambio oficial | Art. 36 Ley 46/1998 (tipos del BCE) — [Consulta DGT V0528-23](https://www.agenciatributaria.gob.es) |
| Tipos diarios | [Tipos de cambio de referencia del BCE](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.es.html) |

## Qué hace el script (y encaja con la norma)

### 1. Valor de transmisión (casilla 0328)

- Usa el **importe neto** de cada venta en divisa: `Proceeds + IBCommission` (en IBKR la comisión va con signo negativo).
- Equivale a *precio × cantidad − comisiones de la venta*, coherente con la práctica de importe efectivamente recibido.
- Convierte a **euros** con el tipo BCE del **día de la venta** (o último día hábil publicado si es festivo/fin de semana).
- Solo suma ventas del **ejercicio** indicado con `--ejercicio`.

**Fundamento:** Art. 37.1.a) LIRPF (precio pactado / cotización en la fecha de la transmisión). El cash de IBKR refleja el precio pactado en mercado.

### 2. Valor de adquisición (casilla 0331)

- Cada **compra** genera un lote FIFO con coste en euros: importe neto pagado (`Proceeds + comisión`) × tipo BCE del **día de la compra**.
- En cada **venta**, se consumen los lotes más antiguos (FIFO, art. 37.2).
- La 0331 del ejercicio es la **suma del coste FIFO** de las acciones vendidas ese año (aunque se compraran en años anteriores).

### 3. Ganancia / pérdida

- Por símbolo: **0328 − 0331** (base del ahorro, transmisión de valores).

### 4. Tipo de cambio BCE

- Fuente: XML oficial `eurofxref` del BCE.
- Formato BCE: 1 EUR = X unidades de divisa → EUR = importe_divisa / X.
- Si no hay tipo el día de la operación, se usa el **último publicado** retrocediendo hasta 14 días (fin de semana / festivo TARGET).

### 5. Posiciones en corto (IBKR) — encaje y límites

El manual IRPF 2025 de la AEAT **no dedica un apartado específico** a las ventas en corto; el script aplica por analogía el esquema del art. 37 (transmisión − adquisición) y el FIFO del art. 37.2, como hace la mayoría del software fiscal español.

| Elemento | Enfoque del script | Grado de certeza |
|----------|-------------------|------------------|
| Cierre de corto: 0328 = importe de la **apertura** (venta en corto), 0331 = **recompra** | Coherente con ganancia = lo recibido al vender − lo pagado al recomprar | **Alto** (práctica habitual) |
| FIFO en cortos (se cierran las posiciones cortas más antiguas) | Espejo del FIFO de largos | **Alto** |
| BCE en fecha de cada operación | Art. 36 Ley 46/1998 | **Alto** |
| **Imputación al ejercicio de la recompra** (no de la apertura) | Toda la operación tributa cuando cierras el corto | **Medio** — razonable si la ganancia se entiende realizada al cierre; si un asesor exigiera declarar la apertura en el año de la venta en corto, habría que ajustar |
| Corto abierto a 31/12 sin cerrar | No sale en tabla definitiva (correcto: aún no hay ganancia cerrada) | **Alto** |
| Misma operación mezcla largo + corto | Primero se netea posición (cierra contrario, luego abre) | **Alto** (convención de cartera) |

**No es “absolutamente” garantizado al 100 %** sin consulta vinculante DGT sobre cortos concretos. Para **largos**, el encaje con art. 37.1.a, 37.2 y BCE es mucho más directo. Para **cortos**, conviene contrastar el `extras/detalle_fifo.csv` (tipo `corto`) con tu gestor si las cantidades son elevadas o el corto se abrió en un año y se cerró en otro.

### 6. Un bloque por empresa / símbolo

- Agrupa todas las operaciones tributables del ejercicio del mismo ticker en una fila (0327, 0328, 0331), como exige cumplimentar varias transmisiones del mismo valor en Renta Web.

## Lo que el script NO calcula (límites)

Debes revisar o completar manualmente:

| Supuesto | Motivo |
|----------|--------|
| **Dividendos** | Rendimientos del capital mobiliario (retenciones / otras casillas). No son compraventa; no entran en 0328/0331. |
| **Intereses** de cuenta o préstamo de valores | Otro apartado de la declaración. |
| **Operaciones de cambio de divisa** (`EUR.USD`, etc.) | Tributan aparte (art. 33 / 37.1.l LIRPF; DGT V2324-10). No están en la tabla de acciones. |
| **Acciones adquiridas antes del 31-12-1994** | Pueden aplicar coeficientes de abatimiento / régimen transitorio. |
| **Recompra en 2 meses tras venta con pérdida** | Casilla «No imputación…» — no se detecta automáticamente. |
| **Splits, spin-offs, ADR, suscripciones** | Pueden alterar cantidades y coste; el CSV debe reflejarlos o hay que ajustar. |
| **Fondos / IIC (37.1.c)** | Valor liquidativo, reglas distintas a acciones cotizadas. |
| **Retenciones / integración en base del ahorro** | Solo se calculan importes de transmisión y adquisición. |
| **Comisiones en divisa distinta a la operación** | El script asume comisión en la misma divisa que `Proceeds`. |

## Criterio alternativo DGT (inmuebles en divisa)

En algunos supuestos (p. ej. inmuebles en dólares) la DGT ha indicado calcular la ganancia **en divisa** y convertir **una sola vez** a euros al tipo de la fecha de transmisión. Para **acciones con FIFO y operaciones en distintas fechas**, la práctica habitual y la de software fiscal es convertir **cada operación** con el BCE de su fecha (como hace este script). Si tu asesor indica otro criterio, habría que adaptar el código.

## Comprobaciones automáticas

Al ejecutar el script se valida que, en cada fila de la tabla definitiva, `Ganancia ≈ 0328 − 0331` (tolerancia 0,02 €). Ver `extras/informe_validacion.txt`.
