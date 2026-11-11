# Sample reports (IBKR) — 100% invented data

These CSV/TXT files are the output of `process_data.py` using **only** the CSVs in
`inputs/ejemplos/`. They do not contain real data from any account.

- `2025/`: 2024 purchases + 2025 trades (`EJEMPLO_2024_compras.csv`,
  `EJEMPLO_2025_operaciones.csv`). Generates the stock table, deductible losses,
  FIFO, ECB rates, open positions and the two-month rule analysis.
- `2026/`: 2026 options (`EJEMPLO_2026_opciones.csv`). Generates the stock table,
  options table, options FIFO, exercises/assignments and currency summary.

## Regenerate them

From the repository root, without mixing with real data:

```bash
python - <<'PY'
import sys, process_data
process_data.ARCHIVOS_CSV = [
    "ejemplos/EJEMPLO_2024_compras.csv",
    "ejemplos/EJEMPLO_2025_operaciones.csv",
]
sys.argv = ["process_data.py", "--ejercicio", "2025"]
process_data.main()
PY

python - <<'PY'
import sys, process_data
process_data.ARCHIVOS_CSV = ["ejemplos/EJEMPLO_2026_opciones.csv"]
sys.argv = ["process_data.py", "--ejercicio", "2026"]
process_data.main()
PY
```

Then copy `tabla_*.csv` and `extras/` to `inputs/ejemplos/informes/2025/` or
`2026/`. The options/loss report names have been kept as they were; in 2026 the
stock and option details were separated with the `_acciones`/`_opciones` suffix to
avoid collisions.
