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
from datetime import date
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
                f"{r['Importe EUR total']:.2f} € netos. {r['Explicación general']}"
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

        return 0

    except Exception as exc:  # noqa: BLE001
        print(f"❌ Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
