# CSV de ejemplo

**NVIDIA** (`NVDA`) en **USD** (FIFO + BCE); ficticios `CORTO` (EUR) y `USDX` (USD). No se leen solos: el script usa `inputs/OPERACIONES*.csv`.

`EJEMPLO_2026_opciones.csv` añade **opciones (calls/puts)**: un cierre normal con ganancia, un vencimiento sin ejercer con pérdida, y una call + una put escritas y asignadas cuya prima se traslada automáticamente al valor de transmisión/adquisición de las acciones `AAPL` correspondientes.

Para probarlos, ver [docs/TUTORIAL_EXPORT_IBKR.md](../../docs/TUTORIAL_EXPORT_IBKR.md).
