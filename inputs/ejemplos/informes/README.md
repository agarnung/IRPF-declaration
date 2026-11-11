> **English version:** [README_EN.md](README_EN.md)

# Informes de ejemplo (IBKR) — datos 100 % inventados

Estos CSV/TXT son la salida de `process_data.py` usando **solo** los CSV de
`inputs/ejemplos/`. No contienen datos reales de ninguna cuenta.

- `2025/`: compras 2024 + operaciones 2025 (`EJEMPLO_2024_compras.csv`,
  `EJEMPLO_2025_operaciones.csv`). Genera tabla de acciones, minusvalías
  computables, FIFO, tipos BCE, posiciones abiertas y análisis de la regla de
  los 2 meses.
- `2026/`: opciones 2026 (`EJEMPLO_2026_opciones.csv`). Genera tabla de
  acciones, tabla de opciones, FIFO de opciones, ejercicios/asignaciones y
  resumen de divisas.

## Regenerarlos

Desde la raíz del repo, sin mezclar con datos reales:

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

Copia después `tabla_*.csv` y `extras/` a `inputs/ejemplos/informes/2025/` o
`2026/`. Los nombres de los informes de opciones/minusvalías se han mantenido
tal cual; en 2026 los detalles de acciones y opciones se han separado con sufijo
`_acciones`/`_opciones` para no colisionar.
