> **English README (required by the hackathon).** Canonical version: [README.md](README.md). Spanish version: [README_ES.md](README_ES.md).
> The project overview, setup, run and test instructions below are in English. The demo video has English narration and English subtitles.

# IRPF — Interactive Brokers + PayPal

Generates **definitive CSV tables** for the Spanish income tax return from two data sources:

- **Interactive Brokers** (`process_data.py`): stock/ETF trades (FIFO, ECB rates, boxes **0327/0328/0331**) and options (calls/puts), including index options.
- **PayPal** (`process_paypal.py`, **new**): imports your PayPal movements and uses **AI** to classify each one (occasional sale, recurring income, refund, currency conversion, fee) and explain in plain language its approximate tax treatment in the Spanish IRPF.

![](https://s3.elespanol.com/2026/05/22/actualidad/1003744256765_263093308_1024x576.jpg)

## New: PayPal + AI module (PayPal AI Hackathon 2026)

Many people with occasional selling activity (Vinted, Wallapop, a one-off freelance job, purchase refunds) have PayPal movements that **nobody classifies properly** when filing their tax return. This module, added during the **PayPal AI Hackathon 2026**, covers exactly that:

1. **Imports** the PayPal transaction history — just like the existing IBKR exports — through a decoupled client behind an interface (`paypal/client.py`), with two interchangeable implementations:
   - `PayPalSandboxClient`: real, against the **PayPal sandbox REST API** (OAuth2 *client credentials* + Transaction Search API).
   - `MockPayPalClient`: realistic, deterministic sample data, so the project can be used and tested **without PayPal credentials**.
2. **Classifies** each movement with AI (`paypal/classifier.py`), also behind an interface:
   - `LLMTransactionClassifier`: real, against any **OpenAI-compatible** API (OpenAI, Azure OpenAI, OpenRouter, Groq, a self-hosted server...). Returns category, confidence, an indicative Renta Web box and a **plain-language explanation** of the approximate tax treatment.
   - `HeuristicTransactionClassifier`: simple rules (keywords, transaction type, recurrence detection by repeated amount + counterparty) that need no API key — it is the **automatic fallback** if the LLM fails and the base for the tests.
3. Generates `tabla_paypal_irpf_<year>.csv` (per-transaction detail), `extras/paypal_resumen_natural.txt` (prose summary with the disclaimer that this is automatic guidance, not binding tax advice) and `extras/paypal_informe.html` (self-contained report to open in the browser: KPIs, filterable table, explanations and downloads; no server or extra dependencies).

Both the PayPal mode and the classifier choose real vs. mock/heuristic automatically from environment variables (see below), with a **safe fallback to mock/heuristic** if credentials are missing — so the demo never breaks.

What is new compared with the previous state of the repo (IBKR only): the whole `paypal/` package, `process_paypal.py`, its tests (`tests/test_paypal_*.py`, `tests/test_process_paypal.py`) and this README section.

## What it does and does not do (for now)

**Yes:**
- Gain/loss from **stock trades** closed in the tax year (long and short), FIFO.
- **Options**: closing before expiry, expiry without exercise, and **exercise/assignment** — in the latter case the premium is transferred to the **stock**'s transfer/acquisition value (DGT criterion), except for **index** options, which settle in cash and are always taxed separately.
- **Two-month rule** (art. 33.5.f LIRPF): table with the stock losses you **can** offset this year (no homogeneous repurchase within ±2 months); if a loss sale does not appear in that table, it is because it is not yet deductible.

**No:** dividends (handled separately, on purpose), interest, currency conversion (`CASH`/`EUR.USD`), open positions at 31/12, futures, funds/UCITS.

> The **TOTAL** row of each CSV **does not have to match** IBKR's "realized P&L" (that usually includes dividends, other currency conversions, etc.). Use these tables for the tax return; use IBKR only to check that no trades are missing.

Normative detail and degree of certainty per case: [docs/CONFORMIDAD_NORMATIVA_EN.md](docs/CONFORMIDAD_NORMATIVA_EN.md)

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Interactive Brokers: Flex Query exports in inputs/OPERACIONES*.csv
python process_data.py --year 2026

# PayPal: works without any configuration (mock mode + heuristic classifier)
python process_paypal.py --year 2026
```

`--update-ecb` re-downloads the ECB rates (both scripts).

When it finishes, `process_paypal.py` opens `extras/paypal_informe.html` in Chromium (or the default browser). If Chromium is installed as a snap (common on Ubuntu), the report and its downloads are copied to `~/irpf-paypal/` so the browser can read them. Use `--no-open-report` if you do not want it to open.

If you file IBKR options, read [docs/TUTORIAL_EXPORT_IBKR_EN.md](docs/TUTORIAL_EXPORT_IBKR_EN.md) first: the Flex Query needs extra columns (`UnderlyingSymbol`, `Strike`, `Expiry`, `Put/Call`, `Multiplier`, `Notes/Codes`) that are not needed if you only file stocks.

### PayPal: mock vs. real mode

By default, `process_paypal.py` works **without any credentials**: it uses sample data (`MockPayPalClient`) and a heuristic classifier (`HeuristicTransactionClassifier`). To enable real modes, copy `.env.example` to `.env` (loaded automatically if you have `python-dotenv` installed) or export the variables manually:

| Variable | What it is for | Default |
|---|---|---|
| `PAYPAL_MODE` | `mock` (sample data) or `real` (PayPal sandbox) | `mock` |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | Credentials of your app at [developer.paypal.com](https://developer.paypal.com/dashboard/) (only if `PAYPAL_MODE=real`) | — |
| `PAYPAL_ENV` | `sandbox` or `live` (only if `PAYPAL_MODE=real`) | `sandbox` |
| `CLASSIFIER_MODE` | `heuristic` (rules, no external AI) or `llm` (real AI classification) | `heuristic` |
| `OPENAI_API_KEY` / `LITELLM_API_KEY` | LLM provider API key (one of the two is enough; only if `CLASSIFIER_MODE=llm`) | — |
| `OPENAI_BASE_URL` / `LITELLM_BASE_URL` | OpenAI-compatible endpoint. `LITELLM_BASE_URL` adds `/v1` if missing | OpenAI public API |
| `OPENAI_MODEL` / `LLM_MODEL` | Model to use | `gpt-4o-mini` |

```bash
# Example with everything in real mode (needs PayPal sandbox credentials and an LLM API key)
PAYPAL_MODE=real PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... \
CLASSIFIER_MODE=llm OPENAI_API_KEY=sk-... \
python process_paypal.py --year 2026
```

If you request real mode but credentials are missing, the script **does not fail**: it warns on the console and continues in mock/heuristic mode.

> **If the real sandbox fails due to network/DNS**, you do not need to fix it for the demo: use `PAYPAL_MODE=mock` (24 sample movements) and, optionally, `CLASSIFIER_MODE=llm`. The automatic mock/heuristic fallback only covers missing credentials; a network failure in real mode stops the run, so record with the mock.

The classifier also accepts a **local LiteLLM proxy** (Ollama/vLLM/llama.cpp) without a paid API key: set `LITELLM_API_KEY` and `LITELLM_BASE_URL` (the code appends `/v1` if missing) together with `LLM_MODEL`. If the local model does not support `response_format=json_object`, it retries without that parameter and tolerates ```` ```json ```` blocks; if it still does not return valid JSON, each transaction falls back to the heuristic classifier.

## Input and output

### Interactive Brokers

| | |
|---|---|
| **Input** | `inputs/OPERACIONES*.csv` (one or more years: purchase history is needed for FIFO; the tables only declare sales closed in the tax year) |
| **Stocks** | `tabla_definitiva_irpf_AAAA.csv` — one row per company; TOTAL = sum of "Net gain / loss €" |
| **Options** | `tabla_opciones_irpf_AAAA.csv` — one row per closed/expired contract (exercised/assigned stock options go inside the stock table, not here) |
| **Deductible losses** | `tabla_minusvalias_computables_irpf_AAAA.csv` — only stock losses deductible this year (two-month rule) |
| **Extras** | `extras/` — FIFO, ECB rates, open positions, option↔stock crossings and the full two-month rule analysis (including blocked losses) |
| **Cache** | `cache/` — downloaded ECB rates |
| **Synthetic examples** | [inputs/ejemplos/](inputs/ejemplos/README_EN.md) (sample CSVs) and [inputs/ejemplos/informes/](inputs/ejemplos/informes/README_EN.md) (already generated outputs with invented data, 2025 and 2026) |

### PayPal

| | |
|---|---|
| **Input** | Transactions in the given date range (`--start-date`/`--end-date`, defaulting to the calendar year of `--year`), obtained from the real or mock client — no CSV needed |
| **Output** | `tabla_paypal_irpf_AAAA.csv` — one row per transaction: tax category, AI confidence, amount converted to EUR with ECB rates, indicative box and plain-language explanation |
| **Extras** | `extras/paypal_resumen_por_categoria.csv` (aggregated totals), `extras/paypal_resumen_natural.txt` (prose summary + list of movements to review manually) and `extras/paypal_informe.html` (self-contained visual report, ready for the browser or to save as PDF) |

## Renta Web

**Stocks (0327/0328/0331):** one issuer per table row; 0328/0331 already include the adjustment for exercised/assigned option premiums where applicable. If the validation warning points to a company with mixed losses (deductible + blocked by the two-month rule), check `extras/perdidas_analisis_regla_2_meses.csv` and declare it in two separate entries if Renta Web requires it.

**Options:** they are not 0327/0328/0331 — they are gain/loss that does not derive from the transfer of assets (art. 37.1.m LIRPF); they go in the futures/options or other assets section of the savings tax base (the exact box changes every year). A row marked "EXERCISED/ASSIGNED without automatic crossing" should not be declared there, but added manually to the stock's 0328/0331 — check `extras/opciones_ejercicios_asignaciones.csv`.

**PayPal:** the classification (occasional sale / recurring income / refund / currency conversion / fee) and the indicative box are **automatic guidance, not binding tax advice** — whether it comes from the LLM or the heuristic rules. Always review the rows marked "Review manually" (high amount, recurrence suggesting undeclared economic activity, or movements that do not fit any category) before moving them to Renta Web, and for real doubts consult a tax adviser.

## Excel

CSV with `;` and **dot** decimals. If `SUMA()` returns 0, import with *Data → From text/CSV* (UTF-8) or use the TOTAL row.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

All tests (31) cover the PayPal module — client, classifier and pipeline — and run **without network or credentials** (mock + heuristic mode); the only real network access during tests is the pre-existing ECB rate cache download.

## More information

- [docs/TUTORIAL_EXPORT_IBKR_EN.md](docs/TUTORIAL_EXPORT_IBKR_EN.md) — Flex Query (stocks and options) + examples in `inputs/ejemplos/`
- [docs/CONFORMIDAD_NORMATIVA_EN.md](docs/CONFORMIDAD_NORMATIVA_EN.md) — regulations, shorts, options (stocks and indices), two-month rule
- [.env.example](.env.example) — documented PayPal module environment variables
- [LICENSE](LICENSE) — MIT

## TODO

- Futures (other than options) in Interactive Brokers.
- PayPal: local pagination/cache for real transactions in high-volume accounts; support for more PayPal event types (currently inferred from text/code; there is no exhaustive mapping of every `transaction_event_code`).
