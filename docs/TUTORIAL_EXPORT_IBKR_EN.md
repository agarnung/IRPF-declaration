# How to export the CSV from Interactive Brokers

The script needs **raw trades** (CSV), not a PDF and not an IBKR “Realized P&L” report. Those reports use the **broker’s exchange rates**; the Spanish tax authority requires the **ECB rate for the day of each trade** and the **FIFO** method.

## Why the IBKR report is not enough

| Problem | What IBKR does | What the Spanish tax authority / this script requires |
|---|---|---|
| **Exchange rate** | Its internal rate at execution | [Official ECB rate](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) for the **trade date** |
| **FIFO** | May show another criterion on screen | First purchase = first sale (**art. 37.2 LIRPF**) |

**Metaphor (exchange rate):** the tax authority gives you an “official rule” (ECB). The “broker rule” or the “yearly average” does not count.

**Metaphor (FIFO):** supermarket queue: the first stock you bought is the first one considered sold.

This project calculates **stock trades only** (boxes 0327 / 0328 / 0331). It does **not** include dividends, interest or currency-conversion rows (`EUR.USD`).

---

## Steps in IBKR

### 1. Open Flex Queries

1. Log in to [Interactive Brokers](https://www.interactivebrokers.com).
2. **Performance & Reports** → **Flex Queries**.
3. **+** (new trade activity query).

### 2. Sections and columns

In **Sections**, enable only **Trades**.

Inside Trades, select these columns (the on-screen names may vary; in the CSV they usually appear as below):

| In IBKR (approx.) | Column in the script CSV |
|---|---|
| Symbol | `Symbol` |
| Date/Time | `DateTime` |
| Quantity | `Quantity` |
| Trade Price | `TradePrice` |
| Proceeds | `Proceeds` |
| Commission | `IBCommission` |
| Currency | `CurrencyPrimary` |
| Asset Class | `AssetClass` |
| Buy/Sell | `Buy/Sell` |

The script processes rows with **`AssetClass` = STK** (stocks) and, if you include them, also **`AssetClass` = OPT** (options). You do not need Dividends or forex Trades if you can avoid them.

#### Additional columns if you trade options (calls/puts)

If your account has options and you want them in the table, add **these columns** to the same Flex Query (they are in the same Trades section, usually grouped under “Options” or at the end of the field list):

| In IBKR (approx.) | Column in the script CSV | What it is for |
|---|---|---|
| Underlying Symbol | `UnderlyingSymbol` | Underlying stock (e.g. `AAPL` for an Apple option) |
| Strike | `Strike` | Strike price |
| Expiry | `Expiry` | Contract expiration date |
| Put/Call | `Put/Call` | `C` (call) or `P` (put) |
| Multiplier | `Multiplier` | Shares per contract (usually 100; if missing, the script assumes 100) |
| Notes/Codes | `Notes/Codes` | **Essential** to detect exercise (`Ex`) and assignment (`A`) — without this column the script cannot distinguish an exercise/assignment from a normal close, and **the premium would be taxed separately instead of being added to/subtracted from the stock**, which is incorrect under the tax authority’s criteria |

If you exercise an option or get assigned, IBKR automatically generates a row for the underlying stock (buy or sell at the strike price) **on the same day**. Make sure that stock row is also in your export (same date range) — otherwise the script cannot match the exercise with the stock and will mark it as “REVIEW” instead of applying it automatically (see `extras/opciones_ejercicios_asignaciones.csv` after running the script).

#### If you trade options on INDICES (SPX, NDX, VIX, Euro Stoxx 50…)

These **do not** deliver shares: they always settle in cash, so the script always treats them as their own gain/loss (it never tries to match them with a stock, even if IBKR marks them as exercise/assignment). It recognizes them by the underlying symbol against the `SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO` list at the top of `process_data.py` — extend it if you trade an index that is not there (otherwise the script will try — incorrectly — to match it with a non-existent stock).

IBKR sometimes reports the cash settlement of an index option outside the Trades section (under “Cash Settlement” / “Option Exercises, Assignments and Expirations”). If you see a “possible cash settlement not reflected” warning after running the script, check the amount in IBKR and adjust it manually in `tabla_opciones_irpf_AAAA.csv`.

### 3. Output format

- **Format:** CSV
- **Separator:** comma (`,`)
- **Date:** `dd/MM/yyyy` (European)
- The script expects date/time as in IBKR: `31/12/2025,153045`

Save the query (e.g. “IRPF Trades”).

### 4. Run by period

1. **Run** the query.
2. Period: **the whole history you need for FIFO**
   - Example for the 2025 return: from the **first purchase** you had not yet sold through **31/12/2025**.
   - If you sold in 2025 shares bought in 2024, include **2024 and 2025** (two exports or one long CSV).
3. Download the CSV and save it in **`inputs/`** with a name such as `OPERACIONES_FLEX_2025.csv`.

You can have several files (`OPERACIONES_FLEX_2024.csv`, `OPERACIONES_FLEX_2025.csv`, …). The script merges them and applies FIFO.

### 5. Generate the IRPF table

```bash
python process_data.py --year 2025
```

Output: `tabla_definitiva_irpf_2025.csv` (and details in `extras/`).

---

## Example files

`inputs/ejemplos/` contains minimal CSVs to understand the flow:

| File | What it illustrates |
|---|---|
| `EJEMPLO_2024_compras.csv` | Purchases in 2024 (history for FIFO) |
| `EJEMPLO_2025_operaciones.csv` | Sale with FIFO, short close, sale in USD |
| `EJEMPLO_2026_opciones.csv` | Options: normal close, expiry without exercise, and exercise/assignment with automatic transfer of the premium to the stock |

**NVIDIA** (`NVDA`) in **USD** + **ECB** conversion and FIFO; fictitious `CORTO` (euros) and `USDX` (another ticker in USD).

To try them without mixing your real data, temporarily set in `process_data.py`:

```python
ARCHIVOS_CSV = [
    "ejemplos/EJEMPLO_2024_compras.csv",
    "ejemplos/EJEMPLO_2025_operaciones.csv",
]
```

and run `python process_data.py --year 2025`.

In normal use leave `ARCHIVOS_CSV = []` and put your exports as `inputs/OPERACIONES_FLEX_*.csv`.

### What you should see when testing the example

**NVIDIA (NVDA)** — in dollars, as in IBKR (`CurrencyPrimary` = USD). Fictitious round prices only to follow the calculation:

| Trade | Date | USD amount* | ECB rate (USD/EUR) | EUR amount (ECB) |
|---|---|---|---|---|
| Buy 100 | 15/03/2024 | 10.002 | 1.0892 | **9,182.89** |
| Buy 100 | 20/09/2024 | 12.002 | 1.1166 | 11,019.10 |
| Sell 100 | 10/06/2025 | 14.998 | 1.1429 | **13,122.76** |

\* Includes the example commission (`Proceeds` + `IBCommission`).

When selling in 2025, box **0331** must be **≈ €9,182.89** (first lot, March 2024), **not** ≈ €11,019 (the September lot) → **FIFO**. Net gain for that row **≈ €3,939.87**. ECB rates may vary slightly if you refresh `cache/` with `--update-ecb`.

`extras/posiciones_abiertas.csv` should still contain another lot of **100 NVDA** unsold (September 2024 purchase).

- **CORTO:** short sale in February and buyback in March (in euros) → gain when closing in 2025.
- **USDX:** another ticker in USD; buy in 2024 and sell in 2025 with the ECB rate of each date.

### What you should see with the options example (`EJEMPLO_2026_opciones.csv`)

Test it like the previous one, changing the temporary `ARCHIVOS_CSV` list and running `python process_data.py --year 2026`. In euros to follow the calculation without ECB rates:

| Contract | What happens | Where it appears |
|---|---|---|
| `AAPL 180 C` expiry 19/06/2026 | Bought for €502 (premium + commission) and **sold to close** for €798 before expiry | `tabla_opciones_irpf_2026.csv`: gain **€296** |
| `TSLA 150 P` expiry 20/02/2026 | Bought for €301 and **expires without exercise** (code `Ep`) | `tabla_opciones_irpf_2026.csv`: loss **-€301** |
| `AAPL 200 C` sold (written) on 20/01, **assigned** on 15/03 (code `A`) | Premium received (€399) is **added** to the transfer value of the sale of 100 AAPL at 200 on 15/03 | Inside `tabla_definitiva_irpf_2026.csv` (AAPL), **not** in the options table |
| `AAPL 170 P` sold (written) on 25/01, **assigned** on 20/03 (code `A`) | Premium received (€499) is **subtracted** from the acquisition value of the purchase of 100 AAPL at 170 on 20/03 | Inside `tabla_definitiva_irpf_2026.csv` (AAPL) |

Because the two AAPL assignments match each other (you sell 100 on 15/03, buy 100 on 20/03), the stock FIFO engine matches them like a normal trade: adjusted transfer €20,398 (20,000 + 399 − 1 commission), adjusted acquisition €16,502 (17,000 − 499 + 1 commission) → gain **€3,896** in the `AAPL` row of `tabla_definitiva_irpf_2026.csv`. Check `extras/opciones_ejercicios_asignaciones.csv` for the detailed premium↔stock crossing and `extras/detalle_operaciones_bce.csv` for the adjustment applied to each stock trade.

---

## Quick checklist

- [ ] Flex Query **Trades**, columns from the stock table above
- [ ] If you trade options: also add `UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`
- [ ] CSV in `inputs/`, name `OPERACIONES*.csv`
- [ ] **Purchases from previous years** included if you sold during the tax year
- [ ] If you exercise/get assigned options: the resulting stock trade is in the **same CSV** (same date range)
- [ ] `python process_data.py --year AAAA`
- [ ] No “REVIEW” rows in `extras/opciones_ejercicios_asignaciones.csv` (or reviewed manually if there are any)
- [ ] Renta Web stocks: **one issuer per row** of `tabla_definitiva_irpf_AAAA.csv` (0327, 0328, 0331)
- [ ] Renta Web options: rows from `tabla_opciones_irpf_AAAA.csv` in the futures/options section (not in 0327/0328/0331)
- [ ] Stock losses: only those in `tabla_minusvalias_computables_irpf_AAAA.csv` are deductible this year

More rules and limits: [CONFORMIDAD_NORMATIVA_EN.md](CONFORMIDAD_NORMATIVA_EN.md)
