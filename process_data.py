#!/usr/bin/env python3
"""
Genera la tabla definitiva para la Renta (IRPF): conversión a EUR con tipos BCE,
emparejamiento FIFO y bloque único por empresa/acción.

Fuentes oficiales BCE (sí, se pueden obtener por programa):
  - Histórico XML: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml
  - Últimos 90 días: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml
  - API SDMX: https://data-api.ecb.europa.eu/service/data/EXR/D.{DIVISA}.EUR.SP00.A
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd

# Año de la declaración (solo ventas de este año en la tabla definitiva).
EJERCICIO_FISCAL = 2026

# CSV de entrada: todos los que existan se combinan (orden cronológico).
# Incluye compras de años anteriores + operaciones del ejercicio.
# Ejemplo: ["OPERACIONES_FLEX_2024.csv", "OPERACIONES_FLEX_2025.csv"]
# Si está vacío, se usan todos los inputs/OPERACIONES*.csv
ARCHIVOS_CSV: list[str] = []

INPUTS_DIR = Path("inputs")
EXTRAS_DIR = Path("extras")
CACHE_DIR = Path("cache")
CACHE_ECB_XML = CACHE_DIR / "ecb_eurofxref_hist.xml"
ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
ECB_HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
MAX_BUSINESS_DAY_LOOKBACK = 14


@dataclass
class Lot:
    """Lote en posición larga (compra)."""
    fecha: date
    qty: float
    cost_eur: float

    @property
    def cost_per_share(self) -> float:
        return self.cost_eur / self.qty if self.qty else 0.0


@dataclass
class ShortLot:
    """Lote en posición corta (venta en corto = apertura)."""
    fecha: date
    qty: float
    transmission_eur: float

    @property
    def transmission_per_share(self) -> float:
        return self.transmission_eur / self.qty if self.qty else 0.0


@dataclass
class FifoMatch:
    symbol: str
    position_type: str  # "largo" | "corto"
    transmission_date: date
    acquisition_date: date
    qty: float
    venta_eur: float
    adquisicion_eur: float
    matched_dates: list[date] = field(default_factory=list)

    @property
    def ganancia_eur(self) -> float:
        return self.venta_eur - self.adquisicion_eur

    @property
    def imputation_date(self) -> date:
        """Ejercicio: fecha de venta (largo) o de recompra/cierre (corto)."""
        if self.position_type == "corto":
            return self.acquisition_date
        return self.transmission_date


@dataclass
class OperationRow:
    symbol: str
    currency: str
    side: str
    dt: datetime
    qty: float
    proceeds_local: float
    commission_local: float
    rate_date: date
    ecb_rate: float
    importe_eur: float
    tipo: str  # "compra" | "venta"


def leer_csv_ibkr(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"No se encuentra '{path}' en {os.getcwd()}")

    try:
        df = pd.read_csv(path, encoding="utf-8")
    except pd.errors.ParserError:
        cleaned_lines: list[str] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1]
                line = line.replace('""', '"')
                cleaned_lines.append(line)
        df = pd.read_csv(io.StringIO("\n".join(cleaned_lines)))

    df.columns = df.columns.str.strip()
    return df


def _resolver_ruta_entrada(nombre: str) -> Path | None:
    candidatos = [Path(nombre), INPUTS_DIR / nombre, INPUTS_DIR / Path(nombre).name]
    for path in candidatos:
        if path.is_file():
            return path
    return None


def resolver_archivos_entrada() -> list[str]:
    INPUTS_DIR.mkdir(exist_ok=True)

    if ARCHIVOS_CSV:
        archivos: list[str] = []
        for nombre in ARCHIVOS_CSV:
            path = _resolver_ruta_entrada(nombre)
            if path is not None:
                archivos.append(str(path))
            else:
                print(f"  (aviso) No se encuentra {nombre}, se omite")
        if not archivos:
            raise FileNotFoundError(
                "Ningún archivo de ARCHIVOS_CSV existe en inputs/. "
                "Revisa la lista al inicio del script."
            )
        return archivos

    archivos = sorted(glob.glob(str(INPUTS_DIR / "OPERACIONES*.csv")))
    if not archivos:
        raise FileNotFoundError(
            f"No hay ficheros OPERACIONES*.csv en {INPUTS_DIR}/. "
            "Coloca ahí los exports de IBKR o define ARCHIVOS_CSV en process_data.py."
        )
    return archivos


def leer_operaciones(archivos: list[str]) -> pd.DataFrame:
    partes = [leer_csv_ibkr(path) for path in archivos]
    df = pd.concat(partes, ignore_index=True)
    clave = ["Symbol", "DateTime", "Quantity", "Proceeds", "Buy/Sell"]
    antes = len(df)
    df = df.drop_duplicates(subset=clave, keep="first")
    if len(df) < antes:
        print(f"  Eliminadas {antes - len(df)} filas duplicadas entre archivos")
    return df


def parse_datetime_ibkr(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d/%m/%Y,%H%M%S")


def descargar_ecb_xml(url: str = ECB_HIST_URL) -> bytes:
    print(f"  Descargando tipos BCE desde {url} ...")
    with urlopen(url, timeout=120) as resp:
        return resp.read()


def cargar_tipos_bce(forzar_descarga: bool = False) -> dict[date, dict[str, float]]:
    """
    Devuelve {fecha: {USD: 1.08, GBP: 0.85, ...}} donde cada tipo es
    'unidades de divisa por 1 EUR' (formato estándar eurofxref del BCE).
    """
    CACHE_DIR.mkdir(exist_ok=True)
    xml_bytes: bytes | None = None

    if CACHE_ECB_XML.exists() and not forzar_descarga:
        xml_bytes = CACHE_ECB_XML.read_bytes()
        print(f"  Usando caché local: {CACHE_ECB_XML}")

    if xml_bytes is None:
        try:
            xml_bytes = descargar_ecb_xml(ECB_HIST_URL)
        except URLError:
            try:
                print("  Histórico completo no disponible; probando últimos 90 días...")
                xml_bytes = descargar_ecb_xml(ECB_HIST_90D_URL)
            except URLError as exc:
                if CACHE_ECB_XML.exists():
                    print(f"  Sin red; usando caché antigua ({exc})")
                    xml_bytes = CACHE_ECB_XML.read_bytes()
                else:
                    raise RuntimeError(
                        "No hay conexión al BCE y no existe cache/ecb_eurofxref_hist.xml. "
                        "Conéctate a Internet y vuelve a ejecutar el script."
                    ) from exc

        CACHE_ECB_XML.write_bytes(xml_bytes)

    root = ET.fromstring(xml_bytes)
    rates_by_date: dict[date, dict[str, float]] = {}

    for elem in root.iter():
        if not elem.tag.endswith("Cube") or "time" not in elem.attrib:
            continue
        day = datetime.strptime(elem.attrib["time"], "%Y-%m-%d").date()
        day_rates: dict[str, float] = {}
        for child in elem:
            if child.tag.endswith("Cube") and "currency" in child.attrib:
                day_rates[child.attrib["currency"]] = float(child.attrib["rate"])
        if day_rates:
            rates_by_date[day] = day_rates

    return rates_by_date


def ultimo_tipo_bce(
    rates_by_date: dict[date, dict[str, float]],
    operation_day: date,
    currency: str,
) -> tuple[date, float]:
    """Tipo del día o del último día hábil publicado por el BCE (hacia atrás)."""
    if currency == "EUR":
        return operation_day, 1.0

    for offset in range(MAX_BUSINESS_DAY_LOOKBACK + 1):
        probe = operation_day - timedelta(days=offset)
        day_rates = rates_by_date.get(probe)
        if day_rates and currency in day_rates:
            return probe, day_rates[currency]

    raise KeyError(
        f"No hay tipo BCE para {currency} en o antes de {operation_day}. "
        f"Amplía el histórico o revisa la divisa."
    )


def local_a_eur(amount_local: float, currency: str, ecb_rate: float) -> float:
    """ecb_rate = unidades de divisa por 1 EUR → EUR = local / rate."""
    if currency == "EUR":
        return amount_local
    return amount_local / ecb_rate


def importe_operacion_eur(
    proceeds: float,
    commission: float,
    currency: str,
    rates_by_date: dict[date, dict[str, float]],
    operation_day: date,
) -> tuple[float, date, float]:
    """
    Importe en EUR de la operación (valor bruto de flujo + comisión en divisa).
    proceeds y commission vienen con signo IBKR (compras negativas, comisiones negativas).
    """
    net_local = proceeds + commission
    rate_date, ecb_rate = ultimo_tipo_bce(rates_by_date, operation_day, currency)
    eur = local_a_eur(abs(net_local), currency, ecb_rate)
    return eur, rate_date, ecb_rate


def preparar_operaciones(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["Symbol"].notna() & (df["Symbol"] != "Symbol")].copy()
    df = df[df["AssetClass"] == "STK"].copy()

    for col in ("Quantity", "Proceeds", "IBCommission", "TradePrice"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["DateTime_parsed"] = df["DateTime"].map(parse_datetime_ibkr)
    df["Fecha"] = df["DateTime_parsed"].dt.date
    df["Side"] = df["Buy/Sell"].str.strip().str.upper()
    df = df.sort_values(["Symbol", "DateTime_parsed"]).reset_index(drop=True)
    return df


def filtrar_hasta_ejercicio(df: pd.DataFrame, ejercicio: int) -> pd.DataFrame:
    """FIFO solo con operaciones hasta 31/12 del ejercicio (ignora años posteriores)."""
    fin = date(ejercicio, 12, 31)
    filtrado = df[df["Fecha"] <= fin].copy()
    omitidas = len(df) - len(filtrado)
    if omitidas:
        print(
            f"  Ignoradas {omitidas} operaciones posteriores a {fin} "
            f"(no afectan a la declaración de {ejercicio})"
        )
    return filtrado


def fifo_por_simbolo(
    symbol: str,
    ops: pd.DataFrame,
    rates_by_date: dict[date, dict[str, float]],
) -> tuple[list[FifoMatch], list[Lot], list[ShortLot], list[OperationRow]]:
    """
    IBKR: Quantity > 0 en BUY (compra/recompra), Quantity < 0 en SELL (venta/apertura corto).
    Primero se cierra posición contraria (FIFO), el resto abre largo o corto.
    """
    long_lots: list[Lot] = []
    short_lots: list[ShortLot] = []
    matches: list[FifoMatch] = []
    detalle: list[OperationRow] = []

    for _, row in ops.iterrows():
        currency = row["CurrencyPrimary"]
        side = row["Side"]
        qty_signed = float(row["Quantity"])
        proceeds = float(row["Proceeds"])
        commission = float(row["IBCommission"])
        op_day: date = row["Fecha"]
        dt: datetime = row["DateTime_parsed"]

        if abs(qty_signed) <= 1e-9:
            continue

        importe_eur, rate_date, ecb_rate = importe_operacion_eur(
            proceeds, commission, currency, rates_by_date, op_day
        )

        if qty_signed > 0:
            remaining = qty_signed
            had_short = bool(short_lots)

            while remaining > 1e-9 and short_lots:
                lot = short_lots[0]
                take = min(remaining, lot.qty)
                transmission_slice = lot.transmission_per_share * take
                acquisition_slice = importe_eur * (take / qty_signed)
                matches.append(
                    FifoMatch(
                        symbol=symbol,
                        position_type="corto",
                        transmission_date=lot.fecha,
                        acquisition_date=op_day,
                        qty=take,
                        venta_eur=transmission_slice,
                        adquisicion_eur=acquisition_slice,
                        matched_dates=[lot.fecha, op_day],
                    )
                )
                lot.qty -= take
                lot.transmission_eur -= transmission_slice
                remaining -= take
                if lot.qty <= 1e-9:
                    short_lots.pop(0)

            if remaining > 1e-9:
                cost_rest = importe_eur * (remaining / qty_signed)
                long_lots.append(Lot(fecha=op_day, qty=remaining, cost_eur=cost_rest))

            if had_short and remaining > 1e-9:
                op_tipo = "recompra_corto+compra"
            elif had_short:
                op_tipo = "recompra_corto"
            else:
                op_tipo = "compra"

            detalle.append(
                OperationRow(
                    symbol=symbol,
                    currency=currency,
                    side=side,
                    dt=dt,
                    qty=qty_signed,
                    proceeds_local=proceeds,
                    commission_local=commission,
                    rate_date=rate_date,
                    ecb_rate=ecb_rate,
                    importe_eur=importe_eur,
                    tipo=op_tipo,
                )
            )
            continue

        # qty_signed < 0 → venta (cierre largo y/o apertura corto)
        remaining = abs(qty_signed)
        venta_total_eur = importe_eur
        adquisicion_long = 0.0
        matched_long_dates: list[date] = []

        while remaining > 1e-9 and long_lots:
            lot = long_lots[0]
            take = min(remaining, lot.qty)
            cost_slice = lot.cost_per_share * take
            adquisicion_long += cost_slice
            matched_long_dates.append(lot.fecha)
            venta_slice = venta_total_eur * (take / abs(qty_signed))
            matches.append(
                FifoMatch(
                    symbol=symbol,
                    position_type="largo",
                    transmission_date=op_day,
                    acquisition_date=lot.fecha,
                    qty=take,
                    venta_eur=venta_slice,
                    adquisicion_eur=cost_slice,
                    matched_dates=[op_day, lot.fecha],
                )
            )
            lot.qty -= take
            lot.cost_eur -= cost_slice
            remaining -= take
            if lot.qty <= 1e-9:
                long_lots.pop(0)

        if remaining > 1e-9:
            if long_lots:
                raise RuntimeError(
                    f"{symbol}: venta el {op_day} no pudo cerrar largos pendientes."
                )
            transmission_rest = venta_total_eur * (remaining / abs(qty_signed))
            short_lots.append(
                ShortLot(fecha=op_day, qty=remaining, transmission_eur=transmission_rest)
            )
            op_tipo = "apertura_corto"
        else:
            op_tipo = "venta"

        detalle.append(
            OperationRow(
                symbol=symbol,
                currency=currency,
                side=side,
                dt=dt,
                qty=qty_signed,
                proceeds_local=proceeds,
                commission_local=commission,
                rate_date=rate_date,
                ecb_rate=ecb_rate,
                importe_eur=importe_eur,
                tipo=op_tipo,
            )
        )

    return matches, long_lots, short_lots, detalle


def construir_tablas(
    df: pd.DataFrame,
    rates_by_date: dict[date, dict[str, float]],
    ejercicio: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    filas_declaracion = []
    filas_detalle_fifo = []
    filas_detalle_ops = []
    filas_abiertas = []

    for symbol, ops in df.groupby("Symbol", sort=True):
        matches, long_restantes, short_restantes, detalle = fifo_por_simbolo(
            symbol, ops, rates_by_date
        )

        for op in detalle:
            filas_detalle_ops.append(
                {
                    "Empresa": op.symbol,
                    "Fecha operación": op.dt.strftime("%d/%m/%Y %H:%M"),
                    "Año": op.dt.year,
                    "Operación": op.tipo.upper(),
                    "Divisa": op.currency,
                    "Unidades": round(op.qty, 6),
                    "Proceeds divisa": round(op.proceeds_local, 4),
                    "Comisión divisa": round(op.commission_local, 4),
                    "Fecha tipo BCE": op.rate_date.isoformat(),
                    "Tipo BCE (divisa/EUR)": round(op.ecb_rate, 6) if op.currency != "EUR" else 1.0,
                    "Importe EUR (BCE)": round(op.importe_eur, 2),
                }
            )

        tributables = [m for m in matches if m.imputation_date.year == ejercicio]
        if tributables:
            venta_sum = sum(m.venta_eur for m in tributables)
            adq_sum = sum(m.adquisicion_eur for m in tributables)
            filas_declaracion.append(
                {
                    "Símbolo (casilla 0327)": symbol,
                    "Nº operaciones": len(tributables),
                    "Unidades": round(sum(m.qty for m in tributables), 6),
                    "Valor transmisión (casilla 0328) €": round(venta_sum, 2),
                    "Valor adquisición FIFO (casilla 0331) €": round(adq_sum, 2),
                    "Ganancia / pérdida neta €": round(venta_sum - adq_sum, 2),
                    "Divisas": ", ".join(sorted(ops["CurrencyPrimary"].unique())),
                }
            )
            for m in tributables:
                fechas_match = ", ".join(sorted({d.isoformat() for d in m.matched_dates}))
                filas_detalle_fifo.append(
                    {
                        "Empresa": m.symbol,
                        "Tipo": m.position_type,
                        "Fecha transmisión (0328)": m.transmission_date.isoformat(),
                        "Fecha adquisición (0331)": m.acquisition_date.isoformat(),
                        "Unidades": m.qty,
                        "Transmisión EUR": round(m.venta_eur, 2),
                        "Adquisición FIFO EUR": round(m.adquisicion_eur, 2),
                        "Ganancia EUR": round(m.ganancia_eur, 2),
                        "Fechas emparejadas": fechas_match,
                    }
                )

        if long_restantes:
            qty_l = sum(l.qty for l in long_restantes)
            filas_abiertas.append(
                {
                    "Empresa (Símbolo)": symbol,
                    "Tipo posición": "largo",
                    "Posición abierta (uds.)": round(qty_l, 6),
                    "Importe pendiente €": round(sum(l.cost_eur for l in long_restantes), 2),
                    "Nota": "Compras sin venta; no declarar hasta transmitir",
                }
            )
        if short_restantes:
            qty_s = sum(l.qty for l in short_restantes)
            filas_abiertas.append(
                {
                    "Empresa (Símbolo)": symbol,
                    "Tipo posición": "corto",
                    "Posición abierta (uds.)": round(-qty_s, 6),
                    "Importe pendiente €": round(
                        sum(l.transmission_eur for l in short_restantes), 2
                    ),
                    "Nota": "Corto abierto; declarar al recomprar (cierre)",
                }
            )

    tabla_decl = pd.DataFrame(filas_declaracion)
    tabla_fifo = pd.DataFrame(filas_detalle_fifo)
    tabla_ops = pd.DataFrame(filas_detalle_ops)
    tabla_abiertas = pd.DataFrame(filas_abiertas)
    return tabla_decl, tabla_fifo, tabla_ops, tabla_abiertas


def resumen_divisa_operacion(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["Quantity_Abs"] = tmp["Quantity"].abs()
    tmp["Proceeds_Abs"] = tmp["Proceeds"].abs()
    tmp["IBCommission_Abs"] = tmp["IBCommission"].abs()
    resumen = (
        tmp.groupby(["Symbol", "CurrencyPrimary", "Buy/Sell"], as_index=False)
        .agg(
            Acciones_Totales=("Quantity_Abs", "sum"),
            Importe_Divisa=("Proceeds_Abs", "sum"),
            Comisiones_Divisa=("IBCommission_Abs", "sum"),
        )
        .round(2)
    )
    resumen.columns = [
        "Empresa / Par",
        "Divisa",
        "Operación",
        "Nº acciones",
        "Importe (divisa)",
        "Comisiones (divisa)",
    ]
    return resumen


COL_SIMBOLO = "Símbolo (casilla 0327)"
COL_N_OPS = "Nº operaciones"
COL_UNIDADES = "Unidades"
COL_0328 = "Valor transmisión (casilla 0328) €"
COL_0331 = "Valor adquisición FIFO (casilla 0331) €"
COL_GANANCIA = "Ganancia / pérdida neta €"
COL_DIVISAS = "Divisas"
FILA_TOTAL = "TOTAL"


def agregar_fila_total(tabla: pd.DataFrame) -> pd.DataFrame:
    """
    Fila TOTAL: solo suma «Ganancia / pérdida neta €».
    Unidades y casillas 0328/0331 son por empresa (Renta Web); no tienen sentido sumarlas.
    """
    suma_ganancia = 0.0 if tabla.empty else round(tabla[COL_GANANCIA].sum(), 2)
    fila = {
        COL_SIMBOLO: FILA_TOTAL,
        COL_N_OPS: "",
        COL_UNIDADES: "",
        COL_0328: "",
        COL_0331: "",
        COL_GANANCIA: suma_ganancia,
        COL_DIVISAS: "",
    }
    return pd.concat([tabla, pd.DataFrame([fila])], ignore_index=True)


def validar_tabla_declaracion(tabla: pd.DataFrame, ejercicio: int) -> list[str]:
    """Comprueba coherencia interna y avisos normativos."""
    avisos: list[str] = []
    if tabla.empty:
        avisos.append(f"No hay ventas en el ejercicio {ejercicio}.")
        return avisos

    for _, row in tabla.iterrows():
        if row[COL_SIMBOLO] == FILA_TOTAL:
            continue
        t, a, g = row[COL_0328], row[COL_0331], row[COL_GANANCIA]
        diff = round(t - a, 2)
        if abs(diff - g) > 0.02:
            avisos.append(
                f"{row[COL_SIMBOLO]}: ganancia {g} ≠ 0328−0331 ({diff})"
            )
    return avisos


def escribir_informe_validacion(
    ejercicio: int,
    archivos: list[str],
    avisos: list[str],
    tabla: pd.DataFrame,
) -> None:
    EXTRAS_DIR.mkdir(exist_ok=True)
    path = EXTRAS_DIR / "informe_validacion.txt"
    lineas = [
        f"Informe de validación — ejercicio {ejercicio}",
        "=" * 50,
        "",
        "Archivos de entrada:",
        *[f"  - {a}" for a in archivos],
        "",
        "Criterios aplicados (IRPF 2025):",
        "  - Art. 37.2 LIRPF: FIFO",
        "  - Art. 36 Ley 46/1998: tipos BCE por fecha de operación",
        "  - 0328: importe neto de ventas en EUR (tipo BCE día venta)",
        "  - 0331: coste FIFO de compras en EUR (tipo BCE día compra)",
        "",
        "Comprobación 0328 - 0331 = ganancia:",
    ]
    if not avisos:
        lineas.append("  OK — todas las filas cuadran.")
    else:
        lineas.extend(f"  AVISO: {a}" for a in avisos)

    n_filas = len(tabla)
    suma_g = round(tabla[COL_GANANCIA].sum(), 2) if not tabla.empty else 0.0

    lineas.extend(
        [
            "",
            f"Filas en tabla definitiva: {n_filas}",
            f"Suma ganancia / pérdida neta (fila TOTAL del CSV): {suma_g} €",
            "  (Unidades, 0328 y 0331 no se suman: son importes por empresa en Renta Web)",
            "",
            "Límites: ver docs/CONFORMIDAD_NORMATIVA.md",
        ]
    )
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def guardar_csv(path: Path | str, tabla: pd.DataFrame) -> None:
    """UTF-8 con BOM para que Excel en Windows muestre bien ñ, €, etc."""
    tabla.to_csv(path, index=False, sep=";", encoding="utf-8-sig")


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tabla IRPF: BCE + FIFO (un ejercicio fiscal en la salida)."
    )
    parser.add_argument(
        "--ejercicio",
        type=int,
        default=EJERCICIO_FISCAL,
        help=f"Año a declarar (por defecto {EJERCICIO_FISCAL})",
    )
    parser.add_argument(
        "--actualizar-bce",
        action="store_true",
        help="Vuelve a descargar el histórico de tipos del BCE",
    )
    return parser.parse_args()


def guardar_extras(nombre: str, tabla: pd.DataFrame) -> Path | None:
    if tabla.empty:
        return None
    EXTRAS_DIR.mkdir(exist_ok=True)
    path = EXTRAS_DIR / nombre
    guardar_csv(path, tabla)
    return path


def main() -> int:
    args = parsear_argumentos()
    ejercicio = args.ejercicio
    tabla_salida = f"tabla_definitiva_irpf_{ejercicio}.csv"

    print("=" * 70)
    print(f"IRPF — Tabla definitiva ejercicio {ejercicio} (BCE + FIFO)")
    print("=" * 70)

    try:
        print("\n[1/4] Leyendo operaciones...")
        archivos = resolver_archivos_entrada()
        print(f"  Ejercicio fiscal: {ejercicio}")
        print(f"  Archivos ({len(archivos)}):")
        for nombre in archivos:
            print(f"    • {nombre}")
        df_raw = leer_operaciones(archivos)
        df_stk = preparar_operaciones(df_raw)
        años = sorted(df_stk["Fecha"].map(lambda d: d.year).unique())
        print(f"  Operaciones cargadas: {len(df_stk)} filas ({años[0]}–{años[-1]})")
        df_stk = filtrar_hasta_ejercicio(df_stk, ejercicio)

        print("\n[2/4] Cargando tipos de cambio oficiales del BCE...")
        rates = cargar_tipos_bce(forzar_descarga=args.actualizar_bce)
        min_d, max_d = min(rates), max(rates)
        print(f"  Histórico BCE: {min_d} → {max_d} ({len(rates)} días)")

        print("\n[3/4] Calculando FIFO y conversión a euros...")
        tabla_decl, tabla_fifo, tabla_ops, tabla_abiertas = construir_tablas(
            df_stk, rates, ejercicio
        )
        df_ejercicio = df_stk[df_stk["Fecha"].map(lambda d: d.year) == ejercicio]
        resumen = resumen_divisa_operacion(df_ejercicio)

        ventas_ej = df_stk[
            (df_stk["Side"] == "SELL")
            & (df_stk["Fecha"].map(lambda d: d.year) == ejercicio)
        ]
        if ventas_ej.empty:
            print(f"  (Aviso) No hay ventas en {ejercicio} en los archivos cargados")

        print("\n[4/4] Resultados\n")

        print("=" * 70)
        print(f"TABLA DEFINITIVA — Ejercicio {ejercicio} (solo ventas de ese año)")
        print("Casillas: 0327 símbolo | 0328 transmisión | 0331 adquisición")
        print("=" * 70)
        avisos = validar_tabla_declaracion(tabla_decl, ejercicio)
        tabla_export = agregar_fila_total(tabla_decl)

        if tabla_decl.empty:
            print(
                f"  (No hay ventas en {ejercicio}; no hay nada que declarar en 0327/0328/0331)"
            )
        else:
            print(tabla_export.to_string(index=False))

        guardar_csv(tabla_salida, tabla_export)

        if not tabla_decl.empty:
            print(
                f"\n  Suma ganancia/pérdida neta ejercicio {ejercicio}: "
                f"{tabla_decl[COL_GANANCIA].sum():.2f} €  "
                f"(fila TOTAL en el CSV; 0328/0331 no se suman entre empresas)"
            )

        escribir_informe_validacion(ejercicio, archivos, avisos, tabla_decl)
        if avisos:
            print("\n  Avisos de validación:")
            for aviso in avisos:
                print(f"    • {aviso}")

        extras_guardados: list[Path] = []
        for nombre, tabla in (
            ("detalle_fifo.csv", tabla_fifo),
            ("detalle_operaciones_bce.csv", tabla_ops),
            ("posiciones_abiertas.csv", tabla_abiertas),
            ("resumen_operaciones_divisa.csv", resumen),
        ):
            path = guardar_extras(nombre, tabla)
            if path is not None:
                extras_guardados.append(path)
        informe = EXTRAS_DIR / "informe_validacion.txt"
        if informe.exists():
            extras_guardados.append(informe)

        print(f"\n💾 Tabla definitiva: {tabla_salida}")
        if extras_guardados:
            print(f"   Detalles (auditoría): {EXTRAS_DIR}/")
            for path in extras_guardados:
                print(f"     • {path.name}")

        return 0

    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 1
    except Exception as exc:
        print(f"❌ Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
