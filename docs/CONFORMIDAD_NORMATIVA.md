# Conformidad normativa — IRPF 2025/2026 (revisión del script)

Este documento resume cómo `process_data.py` se alinea con la normativa española vigente para **transmisiones de acciones cotizadas** y **opciones financieras (calls/puts)** sobre acciones (Interactive Brokers, valores homogéneos). No sustituye asesoramiento fiscal profesional — especialmente en opciones, donde la casuística de ejercicio/asignación puede ser compleja.

## Base legal principal

| Materia | Norma / fuente |
|---------|----------------|
| Ganancia = transmisión − adquisición | Art. 37.1.a) y 37.2 LIRPF |
| FIFO (valores homogéneos) | Art. 37.2 LIRPF — [Manual AEAT IRPF 2025, régimen general IIC/identificación](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/c11-ganancias-perdidas-patrimoniales/determinacion-importe-ganancias-perdidas-patrimon-especificas/transmisiones-reembolsos-acciones-iic/regimen-general.html) |
| Tipo de cambio oficial | Art. 36 Ley 46/1998 (tipos del BCE) — [Consulta DGT V0528-23](https://www.agenciatributaria.gob.es) |
| Tipos diarios | [Tipos de cambio de referencia del BCE](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.es.html) |
| Opciones/futuros: ganancia patrimonial al liquidar/vencer | Art. 37.1.m) LIRPF — [Manual AEAT IRPF 2025, operaciones en mercados de futuros y opciones](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/c11-ganancias-perdidas-patrimoniales/determinacion-importe-ganancias-perdidas-patrimon-especificas/operaciones-realizadas-mercados-futuros-opciones.html) |
| Opción ejercida/asignada: la prima se integra en la acción | Art. 34 y 35 LIRPF, vía doctrina DGT (p. ej. [consulta V2172-21](https://petete.tributos.hacienda.gob.es/consultas/)) |
| Pérdidas no computables por recompra homogénea (2 meses) | Art. 33.5.f) LIRPF |

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

### 6. Opciones financieras (calls/puts) — encaje y límites

El manual IRPF de la AEAT trata las operaciones en mercados de futuros y opciones en un apartado específico (art. 37.1.m LIRPF): con finalidad especulativa (no cobertura de una actividad económica), generan ganancia o pérdida patrimonial que **se imputa al ejercicio en que se liquida la posición o se extingue el contrato** — no al ejercicio en que se cobra/paga la prima.

El script trata cada contrato (subyacente + strike + vencimiento + call/put) con el **mismo motor FIFO** que las acciones (una opción comprada y vendida más tarde, o una vendida —«escrita»— y recomprada, son espejo largo/corto de una acción), pero separa el resultado en dos grupos según cómo termina el contrato:

| Elemento | Enfoque del script | Grado de certeza |
|----------|--------------------|-------------------|
| Cierre antes de vencimiento (operación de mercado contraria) | Ganancia = prima cobrada − prima pagada, igual que una acción | **Alto** |
| Vencimiento sin ejercer (código IBKR `Ep`) | Prima íntegra como pérdida (compra) o ganancia (venta) en el ejercicio del vencimiento | **Alto** — coincide con el ejemplo del manual AEAT |
| **Se declara aparte de 0327/0328/0331** (no es «transmisión de elementos patrimoniales») | `tabla_opciones_irpf_AAAA.csv`, distinta de la tabla de acciones | **Alto** (art. 37.1.m vs 37.1.a) |
| **Ejercicio propio o asignación** (códigos IBKR `Ex` / `A` en `Notes/Codes`) | La prima **no** se declara como ganancia de la opción: se traslada al valor de transmisión (call vendida asignada, put comprada ejercida) o adquisición (call comprada ejercida, put vendida asignada) de la acción — ver `calcular_ajuste_stk_por_ejercicio` en el código | **Medio-alto** — el criterio (prima pierde independencia) es el que aplican doctrina DGT y asesorías fiscales de trading, pero no hay un precepto único y expreso para todos los supuestos; si Hacienda te requiere, ten a mano la cita de la consulta vinculante que uses |
| Cruce automático opción→acción | Empareja por subyacente + fecha + sentido + cantidad (contratos × multiplicador); si no encuentra una operación de acciones que cuadre, **no** aplica el ajuste — dejaría un número incorrecto — y lo declara en `tabla_opciones_irpf_AAAA.csv` marcado «REVISAR» en vez de perder el importe | **Alto** (salvaguarda), pero requiere revisión manual de esas filas |
| Multiplicador (`Multiplier`) | Si IBKR no lo trae, asume 100 (estándar en opciones sobre acciones) | **Alto** para acciones US/EU habituales; revisa si operas subyacentes con multiplicador distinto |
| Futuros, warrants | **No cubierto** — el script solo procesa `AssetClass = OPT` | — |

### 6bis. Opciones sobre ÍNDICES (liquidación en efectivo)

A diferencia de una opción sobre una acción, una opción sobre un índice (SPX, NDX, VIX, Euro Stoxx 50...) **no entrega acciones**: al ejercerse o ser asignada, se liquida siempre **en efectivo** por la diferencia entre el precio de ejercicio y el valor de liquidación del índice. No hay, por tanto, "acción subyacente" a la que trasladar la prima — el resultado tributa siempre como ganancia/pérdida patrimonial propia (art. 37.1.m LIRPF), estén o no marcadas como ejercicio/asignación por IBKR.

El script identifica estos contratos por el símbolo del subyacente contra una lista editable al principio del código (`SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO`: SPX, NDX, RUT, VIX, DJX, XSP y algunos europeos) y, para ellos, **nunca** intenta el cruce con una operación de acciones — todo va directo a `tabla_opciones_irpf_AAAA.csv`.

| Elemento | Enfoque del script | Grado de certeza |
|----------|--------------------|-------------------|
| Identificación del índice | Lista de símbolos editable, no un campo IBKR dedicado (no existe uno claro en el Flex Query de Trades) | **Medio** — si operas un índice fuera de la lista, amplíala tú mismo o el script lo tratará (incorrectamente) como si fuera una acción |
| Importe de la liquidación en efectivo | Se toma el `Proceeds` de la fila de Trades que cierra el contrato | **Medio** — IBKR a veces reporta la liquidación en efectivo de índices en una sección aparte («Cash Settlement» / «Option Exercises, Assignments and Expirations»), no como `Proceeds` de esa fila; si detecta un cierre marcado ejercicio/asignación con importe 0 €, el script avisa expresamente para que lo compruebes |

**Por qué no es tan directo como en acciones:** a diferencia del FIFO de valores homogéneos (art. 37.2, con jurisprudencia y manuales extensos), el tratamiento de ejercicio/asignación de opciones se apoya en consultas vinculantes de la DGT sobre casos concretos (p. ej. V2172-21), no en un artículo que lo regule de forma exhaustiva para todos los supuestos (opciones sobre índices, ejercicio anticipado americano, etc.). Para importes relevantes, contrasta `extras/opciones_ejercicios_asignaciones.csv` con tu asesor.

### 7. Regla de los 2 meses (art. 33.5.f LIRPF) — no 3 meses

**Aclaración importante:** el plazo legal son **2 meses** (no 3), y aplica tanto a recompras **anteriores** como **posteriores** a la venta con pérdida, no solo a las siguientes.

> No se computan como pérdidas patrimoniales las derivadas de la transmisión de valores o participaciones **admitidos a negociación** en un mercado secundario oficial, cuando el contribuyente hubiera adquirido valores homogéneos dentro de los **2 meses anteriores o posteriores** a la transmisión.

El script comprueba, para cada venta con pérdida (posición larga) del ejercicio, si hay alguna compra del mismo símbolo en la ventana `[venta − 2 meses, venta + 2 meses]` (usando **todo** el historial cargado en `inputs/`, no solo hasta el 31/12 del ejercicio, para detectar recompras de enero/febrero del año siguiente si ya tienes ese CSV). Si hay recompra, la pérdida se marca «No computable este ejercicio».

| Elemento | Enfoque del script | Grado de certeza |
|----------|--------------------|-------------------|
| Ventana ±2 meses (no 3) | Suma de meses de calendario, recortando al último día válido del mes | **Alto** (texto literal del art. 33.5.f) |
| Solo posiciones **largas** (ventas de acciones) | Los cortos no encajan igual en «recompra tras transmisión con pérdida»; no se analizan | **Medio** — si abres/cierras cortos con pérdida de forma repetitiva, revisa el caso con tu asesor |
| Recompra **parcial** (menos unidades que las vendidas) | Heurística conservadora: bloquea **toda** la pérdida de esa venta, no solo la parte proporcional | **Medio** — es conservador (no te hace reclamar de más), pero si el importe es relevante puede que una parte sí fuera computable; consulta a tu asesor para prorratear |
| Recompra fuera del histórico cargado (p. ej. compraste en enero del año siguiente y aún no tienes ese CSV) | No se detecta — el aviso de `informe_validacion.txt` no puede avisar de lo que no ve | **Bajo** para ventas de noviembre/diciembre si no cargas también enero-febrero siguientes |
| Fila de la tabla definitiva mezcla lotes bloqueados y computables de la misma empresa | Se avisa en consola y en `informe_validacion.txt`; Renta Web puede exigir **dos entradas separadas** para esa empresa | **Alto** (aviso), pero la separación manual queda en el usuario |

Solo las **pérdidas** están sujetas a esta regla; las **ganancias** siempre son computables y no se tocan.

### 8. Un bloque por empresa / símbolo

- Agrupa todas las operaciones tributables del ejercicio del mismo ticker en una fila (0327, 0328, 0331), como exige cumplimentar varias transmisiones del mismo valor en Renta Web.

## Lo que el script NO calcula (límites)

Debes revisar o completar manualmente:

| Supuesto | Motivo |
|----------|--------|
| **Dividendos** | Rendimientos del capital mobiliario (retenciones / otras casillas). No son compraventa; no entran en 0328/0331. |
| **Intereses** de cuenta o préstamo de valores | Otro apartado de la declaración. |
| **Operaciones de cambio de divisa** (`EUR.USD`, etc.) | Tributan aparte (art. 33 / 37.1.l LIRPF; DGT V2324-10). No están en la tabla de acciones. |
| **Acciones adquiridas antes del 31-12-1994** | Pueden aplicar coeficientes de abatimiento / régimen transitorio. |
| **Regla de los 2 meses con recompra parcial** | El script bloquea toda la pérdida de forma conservadora; no prorratea (ver sección 7). |
| **Regla de los 2 meses con recompra fuera del histórico cargado** | Solo detecta recompras presentes en los CSV de `inputs/` (ver sección 7). |
| **Splits, spin-offs, ADR, suscripciones** | Pueden alterar cantidades y coste; el CSV debe reflejarlos o hay que ajustar. |
| **Fondos / IIC (37.1.c)** | Valor liquidativo, reglas distintas a acciones cotizadas. |
| **Futuros, warrants** | No cubiertos (solo `AssetClass = OPT`). |
| **Cash settlement de opciones sobre índices no reflejado en Trades** | Si IBKR reporta la liquidación en efectivo fuera de la fila de Trades, el script avisa pero no la recupera automáticamente (ver sección 6bis). |
| **Símbolo de índice no incluido en `SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO`** | Se trataría (mal) como una opción sobre acción e intentaría cruzarla con una acción inexistente. Amplía la lista si hace falta. |
| **Retenciones / integración en base del ahorro** | Solo se calculan importes de transmisión y adquisición. |
| **Comisiones en divisa distinta a la operación** | El script asume comisión en la misma divisa que `Proceeds`. |

## Criterio alternativo DGT (inmuebles en divisa)

En algunos supuestos (p. ej. inmuebles en dólares) la DGT ha indicado calcular la ganancia **en divisa** y convertir **una sola vez** a euros al tipo de la fecha de transmisión. Para **acciones con FIFO y operaciones en distintas fechas**, la práctica habitual y la de software fiscal es convertir **cada operación** con el BCE de su fecha (como hace este script). Si tu asesor indica otro criterio, habría que adaptar el código.

## Comprobaciones automáticas

Al ejecutar el script se valida que, en cada fila de la tabla definitiva de acciones, `Ganancia ≈ 0328 − 0331` (tolerancia 0,02 €); que los ejercicios/asignaciones de opciones se hayan podido cruzar con una operación de acciones (si no, aviso + fila «REVISAR»); y se lista qué empresas tienen pérdidas mezcladas (computable + bloqueada por la regla de los 2 meses). Ver `extras/informe_validacion.txt`.
