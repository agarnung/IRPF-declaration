#!/usr/bin/env python3
"""
Importa transacciones de PayPal (sandbox real o datos mock) y las clasifica
fiscalmente con un LLM (o, en su defecto, un clasificador heurístico) para
ayudar a decidir qué declarar en el IRPF español: venta ocasional, ingreso
recurrente, reembolso, cambio de divisa o comisión.

Segunda fuente de datos del proyecto, junto a Interactive Brokers
(process_data.py): mismo espíritu (convertir a EUR con tipos oficiales del
BCE, generar una tabla CSV "definitiva" y un informe de validación), pero
aplicado a transacciones heterogéneas de una cuenta personal de PayPal en
vez de a compraventa de valores.

Modo de uso:
  python process_paypal.py --ejercicio 2026
  PAYPAL_MODE=real CLASSIFIER_MODE=llm python process_paypal.py --ejercicio 2026

Variables de entorno (ver README para la lista completa):
  PAYPAL_MODE=mock|real          (por defecto: mock)
  PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_ENV   (solo si PAYPAL_MODE=real)
  CLASSIFIER_MODE=heuristic|llm  (por defecto: heuristic)
  OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL        (solo si CLASSIFIER_MODE=llm)
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import webbrowser
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from paypal.classifier import get_classifier
from paypal.client import get_paypal_client
from paypal.models import ClassifiedTransaction

# Reutiliza la lógica de tipos de cambio BCE ya existente para IBKR: misma
# fuente oficial, mismo criterio (tipo del día o último hábil publicado),
# para no mantener dos implementaciones distintas de la conversión a EUR.
from process_data import cargar_tipos_bce, ultimo_tipo_bce, local_a_eur

EXTRAS_DIR = Path("extras")

EXPLICACION_CATEGORIA = {
    "venta_ocasional": (
        "Venta ocasional de un bien personal (tipo Vinted/Wallapop). Si el "
        "precio de venta no supera el de adquisición, normalmente no genera "
        "ganancia patrimonial (art. 33.1 LIRPF)."
    ),
    "ingreso_recurrente": (
        "Ingreso periódico compatible con actividad económica o rendimiento "
        "habitual: debe declararse como tal, no como ganancia patrimonial "
        "puntual."
    ),
    "reembolso": "Devolución de un pago anterior: no es una renta nueva.",
    "cambio_divisa": (
        "Conversión interna de divisa en PayPal: no es, por sí sola, una "
        "compraventa que genere renta."
    ),
    "comision": "Comisión o tarifa de PayPal: gasto deducible solo si hay actividad económica.",
    "otro": "No se ha podido clasificar con certeza: revisar manualmente.",
}

# Etiquetas y notas en inglés para el informe HTML (el jurado es internacional).
# Las explicaciones por movimiento las genera el clasificador en español y se
# muestran tal cual, etiquetadas como tales.
CATEGORIA_EN = {
    "venta_ocasional": "Occasional sale",
    "ingreso_recurrente": "Recurring income",
    "reembolso": "Refund",
    "cambio_divisa": "Currency conversion",
    "comision": "PayPal fee",
    "otro": "Unclassified",
}

EXPLICACION_CATEGORIA_EN = {
    "venta_ocasional": (
        "One-off sale of a personal item. If the sale price does not exceed the "
        "acquisition cost, it normally does not generate a capital gain "
        "(art. 33.1 LIRPF)."
    ),
    "ingreso_recurrente": (
        "Periodic income compatible with economic activity or habitual income: "
        "declare it as such, not as a one-off capital gain."
    ),
    "reembolso": "Refund of a previous payment: not new income.",
    "cambio_divisa": (
        "Internal PayPal currency conversion: not, by itself, a sale that "
        "generates income."
    ),
    "comision": "PayPal fee: deductible only if there is economic activity.",
    "otro": "Could not be classified with certainty: review manually.",
}

HTML_CSS = """
:root{--bg:#0b1220;--panel:#111c2e;--line:#1e293b;--text:#f8fafc;--muted:#94a3b8;--green:#22c55e;--blue:#38bdf8;--amber:#f59e0b;--red:#f87171}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;line-height:1.45}
a{color:var(--blue)}
.wrap{max-width:1400px;margin:0 auto;padding:32px 24px 48px}
.top{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;flex-wrap:wrap;margin-bottom:24px}
.eyebrow{color:var(--green);font-weight:700;letter-spacing:.12em;font-size:12px}
h1{font-size:42px;margin:6px 0 8px;letter-spacing:-.02em}
h2{font-size:20px;margin:0 0 14px}
.sub{color:var(--muted);margin:0;max-width:760px}
.badges{display:flex;gap:8px;flex-wrap:wrap}
.badge{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font-size:13px;color:var(--text)}
.badge.review{background:rgba(245,158,11,.14);border-color:rgba(245,158,11,.45);color:var(--amber)}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:24px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px}
.kpi .label{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.08em}
.kpi .value{font-size:34px;font-weight:800;margin-top:6px}
.kpi .value.green{color:var(--green)}
.kpi .value.amber{color:var(--amber)}
.card-head{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.card-head h2{margin:0}
.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
input[type=search]{background:#0f172a;border:1px solid var(--line);border-radius:10px;color:var(--text);padding:9px 12px;min-width:320px;font:inherit}
.check{color:var(--muted);font-size:14px;display:flex;gap:8px;align-items:center}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{position:sticky;top:0;background:#0f172a;text-align:left;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:rgba(56,189,248,.04)}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.pos{color:var(--green)}
.neg{color:var(--red)}
.muted{color:var(--muted)}
.small{font-size:12px}
.nowrap{white-space:nowrap}
.chip{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap;border:1px solid transparent}
.cat-venta_ocasional{background:rgba(34,197,94,.14);color:#86efac;border-color:rgba(34,197,94,.35)}
.cat-ingreso_recurrente{background:rgba(56,189,248,.14);color:#7dd3fc;border-color:rgba(56,189,248,.35)}
.cat-reembolso{background:rgba(167,139,250,.14);color:#c4b5fd;border-color:rgba(167,139,250,.35)}
.cat-cambio_divisa{background:rgba(148,163,184,.14);color:#cbd5e1;border-color:rgba(148,163,184,.35)}
.cat-comision{background:rgba(245,158,11,.14);color:#fcd34d;border-color:rgba(245,158,11,.35)}
.cat-otro{background:rgba(248,113,113,.14);color:#fca5a5;border-color:rgba(248,113,113,.35)}
.bar{display:inline-block;width:72px;height:6px;background:#1e293b;border-radius:999px;overflow:hidden;vertical-align:middle;margin-right:8px}
.bar span{display:block;height:100%;background:var(--blue)}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.btn{display:inline-block;background:var(--green);color:#052e16;text-decoration:none;border:0;border-radius:10px;padding:10px 14px;font-weight:800;font-size:14px;cursor:pointer;font-family:inherit}
.btn.secondary{background:#1e293b;color:var(--text);border:1px solid var(--line)}
.disclaimer{border-color:rgba(245,158,11,.45);background:rgba(245,158,11,.06);color:#fde68a}
footer{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:28px}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:32px}input[type=search]{min-width:0;width:100%}}
@media print{body{background:#fff;color:#111}.card,.badge{border-color:#ddd;background:#fff;color:#111}.muted,.small{color:#555}th{background:#f3f4f6;color:#111}td{border-color:#e5e7eb}.btn,.controls{display:none}footer{color:#555}}
"""

HTML_JS = """
const search = document.getElementById('search');
const onlyReview = document.getElementById('onlyReview');
const rows = Array.from(document.querySelectorAll('#detail tbody tr'));
function apply() {
  const q = (search.value || '').toLowerCase().trim();
  const r = onlyReview.checked;
  rows.forEach(row => {
    const text = row.dataset.search || '';
    const review = row.dataset.review === '1';
    row.style.display = (!q || text.includes(q)) && (!r || review) ? '' : 'none';
  });
}
search.addEventListener('input', apply);
onlyReview.addEventListener('change', apply);
"""


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Importa transacciones de PayPal (mock o sandbox real) y las "
            "clasifica fiscalmente con IA para el IRPF."
        )
    )
    parser.add_argument(
        "--ejercicio",
        type=int,
        default=date.today().year,
        help="Año a declarar (por defecto, el año en curso)",
    )
    parser.add_argument(
        "--fecha-inicio",
        type=str,
        default=None,
        help="YYYY-MM-DD; por defecto 1 de enero del ejercicio",
    )
    parser.add_argument(
        "--fecha-fin",
        type=str,
        default=None,
        help="YYYY-MM-DD; por defecto 31 de diciembre del ejercicio",
    )
    parser.add_argument(
        "--actualizar-bce",
        action="store_true",
        help="Vuelve a descargar el histórico de tipos del BCE",
    )
    parser.add_argument(
        "--no-abrir-informe",
        action="store_true",
        help="No abrir el informe HTML en el navegador al terminar",
    )
    return parser.parse_args()


def construir_tabla(
    clasificadas: list[ClassifiedTransaction],
    rates_by_date,
) -> pd.DataFrame:
    filas = []
    for c in clasificadas:
        t = c.transaction
        importe_eur, rate_date, ecb_rate = _a_eur(t.importe, t.divisa, rates_by_date, t.fecha)
        comision_eur, _, _ = _a_eur(
            t.comision, t.divisa_comision or t.divisa, rates_by_date, t.fecha
        )
        filas.append(
            {
                "ID transacción": t.transaction_id,
                "Fecha": t.fecha.isoformat(),
                "Descripción": t.descripcion,
                "Categoría fiscal": c.categoria,
                "Confianza IA": round(c.confianza, 2),
                "Importe original": round(t.importe, 2),
                "Divisa": t.divisa,
                "Importe EUR (BCE)": round(importe_eur, 2),
                "Comisión EUR (BCE)": round(comision_eur, 2),
                "Casilla orientativa": c.casilla_orientativa,
                "Revisar manualmente": "Sí" if c.requiere_revision_manual else "No",
                "Explicación IRPF (IA)": c.explicacion,
                "Motor clasificación": c.motor,
            }
        )
    return pd.DataFrame(filas)


def _a_eur(importe: float, divisa: str, rates_by_date, dia: date) -> tuple[float, date, float]:
    rate_date, ecb_rate = ultimo_tipo_bce(rates_by_date, dia, divisa)
    # Mantiene el signo (a diferencia de IBKR, aquí sí importa si es entrada o salida).
    signo = 1.0 if importe >= 0 else -1.0
    eur = signo * local_a_eur(abs(importe), divisa, ecb_rate)
    return eur, rate_date, ecb_rate


def resumen_por_categoria(tabla: pd.DataFrame) -> pd.DataFrame:
    if tabla.empty:
        return pd.DataFrame()
    resumen = (
        tabla.groupby("Categoría fiscal", as_index=False)
        .agg(
            **{
                "Nº movimientos": ("ID transacción", "count"),
                "Importe EUR total": ("Importe EUR (BCE)", "sum"),
            }
        )
        .round(2)
    )
    resumen["Explicación general"] = resumen["Categoría fiscal"].map(
        lambda c: EXPLICACION_CATEGORIA.get(c, "")
    )
    return resumen


def guardar_csv(path: Path | str, tabla: pd.DataFrame) -> None:
    tabla.to_csv(path, index=False, sep=";", encoding="utf-8-sig")


def escribir_informe_natural(
    ejercicio: int, tabla: pd.DataFrame, resumen: pd.DataFrame, modo_paypal: str, modo_clasificador: str
) -> Path:
    EXTRAS_DIR.mkdir(exist_ok=True)
    path = EXTRAS_DIR / "paypal_resumen_natural.txt"
    lineas = [
        f"Resumen fiscal de PayPal — ejercicio {ejercicio}",
        "=" * 60,
        "",
        f"Fuente de datos: {'PayPal sandbox (real)' if modo_paypal == 'real' else 'datos de ejemplo (mock)'}",
        f"Clasificador: {'LLM' if modo_clasificador == 'llm' else 'heurístico (reglas)'}",
        "",
        "AVISO: esto es una orientación automática, no asesoramiento fiscal",
        "vinculante. Revisa siempre las filas marcadas 'Revisar manualmente'",
        "y, ante dudas o importes relevantes, consulta con un asesor fiscal.",
        "",
    ]

    if tabla.empty:
        lineas.append(f"No hay transacciones de PayPal en {ejercicio}.")
    else:
        lineas.append(f"Total de movimientos analizados: {len(tabla)}")
        lineas.append("")
        lineas.append("Por categoría fiscal:")
        for _, r in resumen.iterrows():
            lineas.append(
                f"  - {r['Categoría fiscal']}: {r['Nº movimientos']} movimiento(s), "
                f"{r['Importe EUR total']:.2f} € (importe convertido, sin descontar comisión). "
                f"{r['Explicación general']}"
            )
        n_revisar = (tabla["Revisar manualmente"] == "Sí").sum()
        if n_revisar:
            lineas.append("")
            lineas.append(
                f"{n_revisar} movimiento(s) marcado(s) para revisión manual "
                "(ambigüedad, importe elevado o posible actividad económica no declarada):"
            )
            for _, r in tabla[tabla["Revisar manualmente"] == "Sí"].iterrows():
                lineas.append(
                    f"  - {r['Fecha']} · {r['Descripción']} ({r['Importe EUR (BCE)']:.2f} €): "
                    f"{r['Explicación IRPF (IA)']}"
                )

    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return path


def generar_informe_html(
    ejercicio: int,
    tabla: pd.DataFrame,
    resumen: pd.DataFrame,
    modo_paypal: str,
    modo_clasificador: str,
) -> Path:
    """Genera un informe HTML autocontenido (sin servidor ni dependencias) para
    abrirlo en el navegador durante la demo o guardarlo como PDF.

    Es la capa "producto" del proyecto: KPIs, resumen por categoría, tabla
    filtrable de movimientos, explicaciones y descargas. No sustituye al CSV ni al
    resumen en texto; los complementa.
    """
    EXTRAS_DIR.mkdir(exist_ok=True)
    path = EXTRAS_DIR / "paypal_informe.html"

    def e(valor: object) -> str:
        return html.escape(str(valor), quote=True)

    total_mov = len(tabla)
    total_eur = float(tabla["Importe EUR (BCE)"].sum()) if not tabla.empty else 0.0
    n_revisar = (
        int((tabla["Revisar manualmente"] == "Sí").sum()) if not tabla.empty else 0
    )
    n_categorias = int(tabla["Categoría fiscal"].nunique()) if not tabla.empty else 0

    filas_resumen: list[str] = []
    if not resumen.empty:
        for _, r in resumen.iterrows():
            cat = str(r["Categoría fiscal"])
            importe = float(r["Importe EUR total"])
            filas_resumen.append(
                "<tr>"
                f'<td><span class="chip cat-{e(cat)}">{e(CATEGORIA_EN.get(cat, cat))}</span></td>'
                f'<td class="num">{int(r["Nº movimientos"])}</td>'
                f'<td class="num {"pos" if importe >= 0 else "neg"}">{importe:,.2f} €</td>'
                f'<td class="muted">{e(EXPLICACION_CATEGORIA_EN.get(cat, ""))}</td>'
                "</tr>"
            )
    if not filas_resumen:
        filas_resumen.append(
            '<tr><td colspan="4" class="muted">No transactions in this period.</td></tr>'
        )

    filas_detalle: list[str] = []
    if not tabla.empty:
        for _, r in tabla.iterrows():
            cat = str(r["Categoría fiscal"])
            revisar = str(r["Revisar manualmente"]) == "Sí"
            importe = float(r["Importe EUR (BCE)"])
            confianza = max(0.0, min(1.0, float(r["Confianza IA"])))
            busqueda = " ".join(
                str(r[col])
                for col in (
                    "Fecha",
                    "Descripción",
                    "Categoría fiscal",
                    "Explicación IRPF (IA)",
                    "Motor clasificación",
                )
            ).lower()
            etiqueta_revision = (
                '<span class="badge review">Review</span>'
                if revisar
                else '<span class="muted">—</span>'
            )
            filas_detalle.append(
                f'<tr data-review="{"1" if revisar else "0"}" data-search="{e(busqueda)}">'
                f'<td class="nowrap">{e(r["Fecha"])}</td>'
                f"<td>{e(r['Descripción'])}</td>"
                f'<td><span class="chip cat-{e(cat)}">{e(CATEGORIA_EN.get(cat, cat))}</span></td>'
                f'<td class="num"><span class="bar"><span style="width:{confianza * 100:.0f}%"></span></span>'
                f'<span class="small">{confianza * 100:.0f}%</span></td>'
                f'<td class="num {"pos" if importe >= 0 else "neg"}">{importe:,.2f} €</td>'
                f"<td>{etiqueta_revision}</td>"
                f'<td class="small">{e(r["Explicación IRPF (IA)"])}</td>'
                f'<td class="small muted">{e(r["Motor clasificación"])}</td>'
                "</tr>"
            )
    if not filas_detalle:
        filas_detalle.append(
            '<tr><td colspan="8" class="muted">No transactions in this period.</td></tr>'
        )

    descargas: list[str] = []
    if not tabla.empty:
        descargas.append(
            f'<a class="btn" href="../tabla_paypal_irpf_{ejercicio}.csv" download>Download CSV</a>'
        )
        descargas.append(
            '<a class="btn secondary" href="paypal_resumen_por_categoria.csv" download>'
            "Category summary</a>"
        )
    descargas.append(
        '<a class="btn secondary" href="paypal_resumen_natural.txt" download>'
        "Summary (TXT)</a>"
    )
    descargas.append(
        '<button class="btn secondary" type="button" onclick="window.print()">'
        "Print / PDF</button>"
    )

    modo_paypal_txt = "PayPal sandbox" if modo_paypal == "real" else "Sample data (mock)"
    modo_clf_txt = "LLM" if modo_clasificador == "llm" else "Heuristic rules"
    nota_datos = (
        "Demo uses 24 deterministic sample transactions; no credentials are required."
        if modo_paypal != "real"
        else "Demo reads the PayPal sandbox; no real money moves."
    )
    generado = datetime.now().strftime("%Y-%m-%d %H:%M")

    documento = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IRPF Tax Assistant — PayPal {ejercicio}</title>
<style>{HTML_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <div class="eyebrow">PAYPAL AI HACKATHON 2026</div>
      <h1>IRPF Tax Assistant</h1>
      <p class="sub">From a messy PayPal history to a reviewable Spanish IRPF summary — {ejercicio}. {e(nota_datos)}</p>
    </div>
    <div class="badges">
      <span class="badge">{e(modo_paypal_txt)}</span>
      <span class="badge">{e(modo_clf_txt)}</span>
    </div>
  </header>

  <section class="kpis">
    <div class="card kpi"><div class="label">Movements</div><div class="value">{total_mov}</div></div>
    <div class="card kpi"><div class="label">Amount (EUR)</div><div class="value {'neg' if total_eur < 0 else 'green'}">{total_eur:,.2f} €</div></div>
    <div class="card kpi"><div class="label">Categories</div><div class="value">{n_categorias}</div></div>
    <div class="card kpi"><div class="label">To review</div><div class="value amber">{n_revisar}</div></div>
  </section>

  <section class="card">
    <h2>What it means</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Category</th><th>Movements</th><th>Amount (EUR)</th><th>Tax note</th></tr></thead>
        <tbody>{''.join(filas_resumen)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>Movements</h2>
      <div class="controls">
        <input id="search" type="search" placeholder="Filter by description, category or explanation…">
        <label class="check"><input id="onlyReview" type="checkbox"> Only movements to review</label>
      </div>
    </div>
    <div class="table-wrap">
      <table id="detail">
        <thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Confidence</th><th>Amount (EUR)</th><th>Review</th><th>Why / explanation</th><th>Engine</th></tr></thead>
        <tbody>{''.join(filas_detalle)}</tbody>
      </table>
    </div>
    <p class="muted small">Per-row explanations are generated by the classifier in Spanish; the interface labels are in English for the demo.</p>
    <div class="btns">{''.join(descargas)}</div>
  </section>

  <section class="card disclaimer">
    <strong>Automatic orientation, not binding tax advice.</strong>
    Always review the flagged movements and consult a tax adviser for relevant amounts.
  </section>

  <footer>
    <span>Generated {e(generado)}</span>
    <span>Code: <a href="https://github.com/agarnung/IRPF-declaration">github.com/agarnung/IRPF-declaration</a> (MIT)</span>
  </footer>
</div>
<script>{HTML_JS}</script>
</body>
</html>
"""

    path.write_text(documento, encoding="utf-8")
    return path


def abrir_informe_html(path: Path) -> bool:
    """Abre el informe HTML en Chromium (o en el navegador por defecto).

    Devuelve False si no hay entorno gráfico o si no se encuentra ningún
    navegador; en ese caso el llamador solo avisa y muestra la ruta.
    """
    if not path.is_file():
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False

    url = path.resolve().as_uri()
    for nombre in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        exe = shutil.which(nombre)
        if not exe:
            continue
        try:
            subprocess.Popen(
                [exe, "--new-window", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue

    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001
        return False


def _cargar_dotenv_si_existe() -> None:
    """Carga variables de entorno desde un .env local si existe python-dotenv
    y el archivo está presente. Totalmente opcional: sin esto, basta con
    exportar las variables a mano o definirlas antes de ejecutar el script."""
    if not Path(".env").is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
    except ImportError:
        print(
            "  (aviso) Existe un .env pero 'python-dotenv' no está instalado; "
            "ignorándolo. Instálalo (pip install python-dotenv) o exporta las "
            "variables de entorno manualmente."
        )


def main() -> int:
    import os

    _cargar_dotenv_si_existe()
    args = parsear_argumentos()
    ejercicio = args.ejercicio
    fecha_inicio = (
        date.fromisoformat(args.fecha_inicio) if args.fecha_inicio else date(ejercicio, 1, 1)
    )
    fecha_fin = (
        date.fromisoformat(args.fecha_fin) if args.fecha_fin else date(ejercicio, 12, 31)
    )

    modo_paypal = os.environ.get("PAYPAL_MODE", "mock").strip().lower()
    modo_clasificador = os.environ.get("CLASSIFIER_MODE", "heuristic").strip().lower()

    print("=" * 70)
    print(f"IRPF — PayPal: importación + clasificación fiscal con IA, ejercicio {ejercicio}")
    print("=" * 70)

    try:
        print(f"\n[1/4] Obteniendo transacciones de PayPal ({fecha_inicio} a {fecha_fin})...")
        print(f"  Modo PayPal: {modo_paypal}")
        cliente = get_paypal_client()
        transacciones = cliente.obtener_transacciones(fecha_inicio, fecha_fin)
        print(f"  Transacciones obtenidas: {len(transacciones)}")

        if not transacciones:
            print("  (Aviso) No hay transacciones de PayPal en el rango indicado.")

        print(f"\n[2/4] Clasificando fiscalmente con IA (modo: {modo_clasificador})...")
        clasificador = get_classifier()
        clasificadas = clasificador.clasificar_lote(transacciones)
        print(f"  Movimientos clasificados: {len(clasificadas)}")

        print("\n[3/4] Cargando tipos de cambio oficiales del BCE y convirtiendo a EUR...")
        rates = cargar_tipos_bce(forzar_descarga=args.actualizar_bce)
        tabla = construir_tabla(clasificadas, rates)
        resumen = resumen_por_categoria(tabla)

        print("\n[4/4] Resultados\n")
        print("=" * 70)
        print(f"TABLA PAYPAL — ejercicio {ejercicio}")
        print("=" * 70)
        if tabla.empty:
            print("  (No hay transacciones de PayPal que declarar en este ejercicio)")
        else:
            print(resumen.to_string(index=False))

        nombre_tabla = f"tabla_paypal_irpf_{ejercicio}.csv"
        if not tabla.empty:
            guardar_csv(nombre_tabla, tabla)
            print(f"\n💾 Tabla detallada: {nombre_tabla}")

            EXTRAS_DIR.mkdir(exist_ok=True)
            guardar_csv(EXTRAS_DIR / "paypal_resumen_por_categoria.csv", resumen)

        informe = escribir_informe_natural(ejercicio, tabla, resumen, modo_paypal, modo_clasificador)
        print(f"💾 Resumen en lenguaje natural: {informe}")

        informe_html = generar_informe_html(ejercicio, tabla, resumen, modo_paypal, modo_clasificador)
        print(f"💾 Informe HTML (demo): {informe_html}")

        if not args.no_abrir_informe:
            if abrir_informe_html(informe_html):
                print("🌐 Informe abierto en el navegador")
            else:
                print(f"  (aviso) No se pudo abrir el navegador; abre manualmente: {informe_html}")

        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"❌ Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
