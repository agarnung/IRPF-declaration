# Sample CSVs

**NVIDIA** (`NVDA`) in **USD** (FIFO + ECB); fictitious `CORTO` (EUR) and `USDX` (USD). They are not read on their own: the script uses `inputs/OPERACIONES*.csv`.

`EJEMPLO_2026_opciones.csv` adds **options (calls/puts)**: a normal close with a gain, an expiry without exercise with a loss, and a written call + a written put that are assigned, whose premium is automatically transferred to the transfer/acquisition value of the corresponding `AAPL` shares.

To try them, see [docs/TUTORIAL_EXPORT_IBKR_EN.md](../../docs/TUTORIAL_EXPORT_IBKR_EN.md).
