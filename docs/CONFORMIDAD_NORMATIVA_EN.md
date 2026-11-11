# Regulatory conformity — Spanish IRPF 2025/2026 (script review)

This document summarises how `process_data.py` aligns with current Spanish tax rules for **transfers of listed shares** and **financial options (calls/puts)** on shares (Interactive Brokers, homogeneous securities). It is not professional tax advice — especially for options, where exercise/assignment cases can be complex.

## Main legal basis

| Matter | Rule / source |
|---|---|
| Gain = transfer − acquisition | Art. 37.1.a) and 37.2 LIRPF |
| FIFO (homogeneous securities) | Art. 37.2 LIRPF — AEAT IRPF 2025 manual |
| Official exchange rate | Art. 36 Law 46/1998 (ECB rates) — DGT consultation V0528-23 |
| Daily rates | [ECB euro reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) |
| Options/futures: capital gain on settlement/expiry | Art. 37.1.m) LIRPF — AEAT IRPF 2025 manual, futures and options |
| Option exercised/assigned: premium is integrated into the stock | Art. 34 and 35 LIRPF, via DGT doctrine (e.g. consultation V2172-21) |
| Losses not deductible due to a homogeneous repurchase (2 months) | Art. 33.5.f) LIRPF |

## What the script does (and how it matches the rules)

### 1. Transfer value (box 0328)

- Uses the **net amount** of each sale in foreign currency: `Proceeds + IBCommission` (in IBKR the commission is negative).
- Equivalent to *price × quantity − sale commissions*, consistent with the amount actually received.
- Converts to **euros** using the ECB rate on the **sale date** (or the last published business day if it is a holiday/weekend).
- Only includes sales in the year passed to `--ejercicio`.

**Basis:** Art. 37.1.a) LIRPF (agreed price / market quotation on the transfer date). IBKR cash reflects the market agreed price.

### 2. Acquisition value (box 0331)

- Each **purchase** creates a FIFO lot with its euro cost: net amount paid (`Proceeds + commission`) × ECB rate on the **purchase date**.
- On each **sale**, the oldest lots are consumed (FIFO, art. 37.2).
- The year’s 0331 is the **sum of the FIFO cost** of the shares sold that year (even if they were bought in previous years).

### 3. Gain / loss

- Per symbol: **0328 − 0331** (savings tax base, transfer of securities).

### 4. ECB exchange rate

- Source: official ECB `eurofxref` XML.
- ECB format: 1 EUR = X units of foreign currency → EUR = foreign amount / X.
- If there is no rate on the trade date, the **last published** rate is used, going back up to 14 days (weekend / TARGET holiday).

### 5. Short positions (IBKR) — fit and limits

The AEAT IRPF 2025 manual **does not devote a specific section** to short sales; the script applies the art. 37 scheme (transfer − acquisition) and the art. 37.2 FIFO by analogy, as most Spanish tax software does.

| Element | Script approach | Degree of certainty |
|---|---|---|
| Short close: 0328 = **opening** amount (short sale), 0331 = **buyback** | Consistent with gain = amount received on sale − amount paid on buyback | **High** (common practice) |
| FIFO on shorts (oldest short positions are closed first) | Mirror of long FIFO | **High** |
| ECB rate on each trade date | Art. 36 Law 46/1998 | **High** |
| **Attribution to the year of the buyback** (not the opening) | The whole trade is taxed when the short is closed | **Medium** — reasonable if the gain is deemed realised on closing; if an adviser required declaring the opening in the short-sale year, an adjustment would be needed |
| Short open at 31/12 without closing | Not in the definitive table (correct: no closed gain yet) | **High** |
| Same trade mixes long + short | Position is netted first (closes the opposite side, then opens) | **High** (portfolio convention) |

**It is not “absolutely” guaranteed at 100%** without a binding DGT consultation on specific shorts. For **longs**, the fit with art. 37.1.a, 37.2 and ECB is much more direct. For **shorts**, contrast `extras/detalle_fifo.csv` (type `corto`) with your adviser if the amounts are large or the short was opened in one year and closed in another.

### 6. Financial options (calls/puts) — fit and limits

The AEAT IRPF manual treats futures and options trades in a specific section (art. 37.1.m LIRPF): with a speculative purpose (not hedging an economic activity), they generate a capital gain or loss that is **attributed to the year in which the position is settled or the contract expires** — not the year in which the premium is received/paid.

The script treats each contract (underlying + strike + expiry + call/put) with the **same FIFO engine** as stocks (an option bought and sold later, or sold — “written” — and bought back, are the long/short mirror of a stock), but separates the result into two groups according to how the contract ends:

| Element | Script approach | Degree of certainty |
|---|---|---|
| Close before expiry (opposite market trade) | Gain = premium received − premium paid, like a stock | **High** |
| Expiry without exercise (IBKR code `Ep`) | Full premium as a loss (purchase) or gain (sale) in the expiry year | **High** — matches the AEAT manual example |
| **Declared separately from 0327/0328/0331** (not a “transfer of assets”) | `tabla_opciones_irpf_AAAA.csv`, separate from the stock table | **High** (art. 37.1.m vs 37.1.a) |
| **Own exercise or assignment** (IBKR codes `Ex` / `A` in `Notes/Codes`) | The premium is **not** declared as an option gain: it is transferred to the stock’s transfer value (written call assigned, purchased put exercised) or acquisition value (purchased call exercised, written put assigned) — see `calcular_ajuste_stk_por_ejercicio` in the code | **Medium-high** — the criterion (the premium loses independence) is what DGT doctrine and trading tax advisers apply, but there is no single express provision for every case; if the tax authority asks, keep the binding consultation citation you use at hand |
| Automatic option→stock crossing | Matches by underlying + date + direction + quantity (contracts × multiplier); if it cannot find a matching stock trade, it **does not** apply the adjustment — it would leave an incorrect number — and reports it in `tabla_opciones_irpf_AAAA.csv` marked “REVIEW” instead of losing the amount | **High** (safeguard), but those rows require manual review |
| Multiplier (`Multiplier`) | If IBKR does not provide it, assumes 100 (standard for stock options) | **High** for usual US/EU stocks; check if you trade underlyings with a different multiplier |
| Futures, warrants | **Not covered** — the script only processes `AssetClass = OPT` | — |

### 6bis. Index options (cash settlement)

Unlike an option on a stock, an option on an index (SPX, NDX, VIX, Euro Stoxx 50…) **does not deliver shares**: on exercise or assignment it always settles **in cash** for the difference between the strike price and the index settlement value. There is therefore no “underlying stock” to which the premium can be transferred — the result is always taxed as its own capital gain/loss (art. 37.1.m LIRPF), whether or not IBKR marks it as exercise/assignment.

The script identifies these contracts by the underlying symbol against an editable list at the top of the code (`SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO`: SPX, NDX, RUT, VIX, DJX, XSP and some European ones) and, for them, **never** attempts the stock crossing — everything goes directly to `tabla_opciones_irpf_AAAA.csv`.

| Element | Script approach | Degree of certainty |
|---|---|---|
| Index identification | Editable symbol list, not a dedicated IBKR field (there is no clear one in the Flex Query Trades) | **Medium** — if you trade an index outside the list, extend it yourself or the script will treat it (incorrectly) as a stock |
| Cash settlement amount | Uses the `Proceeds` of the Trades row that closes the contract | **Medium** — IBKR sometimes reports index cash settlement in a separate section (“Cash Settlement” / “Option Exercises, Assignments and Expirations”), not as `Proceeds` of that row; if it detects a close marked exercise/assignment with amount €0, the script explicitly warns you to check it |

**Why it is not as direct as stocks:** unlike FIFO on homogeneous securities (art. 37.2, with extensive case law and manuals), the treatment of option exercise/assignment relies on binding DGT consultations on specific cases (e.g. V2172-21), not on an article that regulates every case exhaustively (index options, early American exercise, etc.). For significant amounts, contrast `extras/opciones_ejercicios_asignaciones.csv` with your adviser.

### 7. Two-month rule (art. 33.5.f LIRPF) — not three months

**Important clarification:** the legal period is **2 months** (not 3), and it applies to repurchases **before** as well as **after** the loss sale, not only to later ones.

> Losses from the transfer of securities admitted to trading on an official secondary market are not counted when the taxpayer has acquired homogeneous securities within the **2 months before or after** the transfer.

For each loss sale (long position) in the year, the script checks whether there is any purchase of the same symbol in the window `[sale − 2 months, sale + 2 months]` (using the **whole** history loaded in `inputs/`, not only up to 31/12 of the year, to detect January/February repurchases of the following year if you already have that CSV). If there is a repurchase, the loss is marked “Not deductible this year”.

| Element | Script approach | Degree of certainty |
|---|---|---|
| ±2-month window (not 3) | Sum of calendar months, clipped to the last valid day of the month | **High** (literal text of art. 33.5.f) |
| **Long positions only** (stock sales) | Shorts do not fit the “repurchase after a loss transfer” rule in the same way; they are not analysed | **Medium** — if you open/close losing shorts repeatedly, review the case with your adviser |
| **Partial** repurchase (fewer units than sold) | Conservative heuristic: blocks **the whole** loss of that sale, not only the proportional part | **Medium** — it is conservative (it does not let you claim too much), but if the amount is significant part of it might be deductible; consult your adviser to prorate |
| Repurchase outside the loaded history (e.g. you bought in January of the following year and do not have that CSV yet) | Not detected — the `informe_validacion.txt` warning cannot warn about what it does not see | **Low** for November/December sales if you do not also load the following January-February |
| Definitive table row mixes blocked and deductible lots from the same company | Warns in the console and in `informe_validacion.txt`; Renta Web may require **two separate entries** for that company | **High** (warning), but the manual split remains with the user |

Only **losses** are subject to this rule; **gains** are always deductible and are not touched.

### 8. One row per company / symbol

- Groups all taxable trades of the year for the same ticker into one row (0327, 0328, 0331), as Renta Web requires when filing several transfers of the same security.

## What the script does NOT calculate (limits)

You must review or complete these manually:

| Case | Reason |
|---|---|
| **Dividends** | Income from movable capital (withholding / other boxes). They are not a trade; they do not go in 0328/0331. |
| **Interest** on cash or securities lending | Another section of the return. |
| **Currency conversion trades** (`EUR.USD`, etc.) | Taxed separately (art. 33 / 37.1.l LIRPF; DGT V2324-10). They are not in the stock table. |
| **Shares acquired before 31-12-1994** | Abatement coefficients / transitional regime may apply. |
| **Two-month rule with partial repurchase** | The script blocks the whole loss conservatively; it does not prorate (see section 7). |
| **Two-month rule with repurchase outside the loaded history** | Only detects repurchases present in the `inputs/` CSVs (see section 7). |
| **Splits, spin-offs, ADRs, subscriptions** | They may change quantities and cost; the CSV must reflect them or you must adjust. |
| **Funds / UCITS (37.1.c)** | Net asset value, different rules from listed shares. |
| **Futures, warrants** | Not covered (only `AssetClass = OPT`). |
| **Index option cash settlement not reflected in Trades** | If IBKR reports cash settlement outside the Trades row, the script warns but does not recover it automatically (see section 6bis). |
| **Index symbol not included in `SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO`** | It would be treated (incorrectly) as a stock option and the script would try to match it with a non-existent stock. Extend the list if needed. |
| **Withholdings / savings tax base integration** | Only transfer and acquisition amounts are calculated. |
| **Commissions in a currency different from the trade** | The script assumes the commission is in the same currency as `Proceeds`. |

## Alternative DGT criterion (real estate in foreign currency)

In some cases (e.g. real estate in dollars) the DGT has indicated calculating the gain **in foreign currency** and converting **once** to euros at the transfer-date rate. For **shares with FIFO and trades on different dates**, common practice and tax software convert **each trade** at the ECB rate of its date (as this script does). If your adviser indicates another criterion, the code would need to be adapted.

## Automatic checks

When the script runs, it validates that in each row of the definitive stock table `Gain ≈ 0328 − 0331` (tolerance €0.02); that option exercises/assignments could be matched with a stock trade (otherwise warning + “REVIEW” row); and it lists which companies have mixed losses (deductible + blocked by the two-month rule). See `extras/informe_validacion.txt`.
