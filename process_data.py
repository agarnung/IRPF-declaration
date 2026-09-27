#!/usr/bin/env python3
"""
Genera la tabla definitiva para la Renta (IRPF): conversión a EUR con tipos BCE,
emparejamiento FIFO y bloque único por empresa/acción — y, adicionalmente,
opciones financieras (calls/puts) y el análisis de la regla de los 2 meses.

Fuentes oficiales BCE (sí, se pueden obtener por programa):
  - Histórico XML: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml
  - Últimos 90 días: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml
  - API SDMX: https://data-api.ecb.europa.eu/service/data/EXR/D.{DIVISA}.EUR.SP00.A
"""

from __future__ import annotations

import argparse
import calendar
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

# Opciones: multiplicador por defecto si IBKR no trae la columna 'Multiplier'
# (estándar en opciones sobre acciones US/EU: 1 contrato = 100 acciones).
MULTIPLICADOR_OPCION_DEFECTO = 100.0

# Códigos IBKR (columna 'Notes/Codes') que indican que el contrato de opción
# se cerró por ejercicio propio o por asignación de la contraparte, y no por
# una operación de mercado normal. Fuente: guides.interactivebrokers.com,
# Flex Statement "Codes" (A=Assignment, Ex=Exercise).
CODIGOS_EJERCICIO_ASIGNACION = {"A", "Ex"}

# Art. 33.5.f LIRPF: NO se computan las pérdidas por transmisión de valores
# admitidos a negociación si se recompran valores homogéneos dentro de los
# 2 MESES anteriores o posteriores a la venta (no 3, ni "solo posteriores").
VENTANA_RECOMPRA_MESES = 2
TOLERANCIA_QTY = 1e-6

# Opciones sobre ÍNDICES bursátiles: liquidan siempre EN EFECTIVO (no hay
# entrega de acciones), a diferencia de las opciones sobre acciones. Por eso
# el ejercicio/asignación de una opción sobre índice NUNCA debe intentar
# cruzarse con una operación de acciones ni trasladar la prima a 0328/0331:
# tributa siempre como ganancia/pérdida patrimonial propia (art. 37.1.m
# LIRPF), igual que un cierre normal, esté o no "ejercida". Símbolos raíz
# habituales de opciones sobre índices en US (CBOE) — amplía esta lista si
# operas otros índices (p. ej. opciones MEFF sobre IBEX 35, si tu bróker las
# reporta con un símbolo propio):
SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO = {
    # CBOE (US)
    "SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "VIX", "DJX", "XSP", "MRUT",
    "OEX", "XEO",
    # Eurex / Europa (símbolo IBKR puede variar según el listado exacto)
    "SX5E", "ODAX", "DAX", "SMI", "UKX", "AEX",
}


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------


@dataclass
class Lot:
    """Lote en posición larga (compra)."""
    fecha: date
    qty: float
    cost_eur: float
    ajuste_eur: float = 0.0  # parte de cost_eur que viene de una opción ejercida/asignada

    @property
    def cost_per_share(self) -> float:
        return self.cost_eur / self.qty if self.qty else 0.0


@dataclass
class ShortLot:
    """Lote en posición corta (venta en corto / opción vendida = apertura)."""
    fecha: date
    qty: float
    transmission_eur: float
    ajuste_eur: float = 0.0  # parte de transmission_eur que viene de una opción ejercida/asignada

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
    closing_notes_codes: str = ""
    ajuste_venta_eur: float = 0.0  # parte de venta_eur que viene de una opción ejercida/asignada
    ajuste_adquisicion_eur: float = 0.0  # ídem para adquisicion_eur

    @property
    def ganancia_eur(self) -> float:
        return self.venta_eur - self.adquisicion_eur

    @property
    def ajuste_neto_eur(self) -> float:
        """Efecto neto de opciones ejercidas/asignadas sobre la ganancia de ESTE lote."""
        return self.ajuste_venta_eur - self.ajuste_adquisicion_eur

    @property
    def imputation_date(self) -> date:
        """Ejercicio: fecha de venta (largo) o de recompra/cierre (corto)."""
        if self.position_type == "corto":
            return self.acquisition_date
        return self.transmission_date

    @property
    def es_ejercicio_o_asignacion(self) -> bool:
        codigos = {c.strip() for c in self.closing_notes_codes.split(";") if c.strip()}
        codigos |= {c.strip() for c in self.closing_notes_codes.split(",") if c.strip()}
        return bool(codigos & CODIGOS_EJERCICIO_ASIGNACION)


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
    importe_eur_base: float
    ajuste_opciones_eur: float
    tipo: str  # "compra" | "venta" | ...
    notes_codes: str = ""

    @property
    def importe_eur(self) -> float:
        return self.importe_eur_base + self.ajuste_opciones_eur


@dataclass
class AjusteEjercicioOpcion:
    """Ajuste pendiente de aplicar a una operación de ACCIONES por el
    ejercicio o asignación de una opción (la prima pierde independencia
    y pasa a formar parte del valor de transmisión o adquisición)."""
    contrato: str
    underlying: str
    put_call: str
    fecha: date
    side_esperado: str  # "BUY" | "SELL" en la acción subyacente
    qty_esperada: float
    importe_eur: float  # con signo: se SUMA al importe de la operación de acciones
    casilla: str  # "0328 (transmisión)" | "0331 (adquisición)"
    fallback_ganancia_eur: float  # ganancia/pérdida de la prima si NO se puede cruzar con la acción
    fallback_divisas: str = ""


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


def sumar_meses(d: date, meses: int) -> date:
    """Suma (o resta, si meses<0) meses de calendario a una fecha, recortando
    el día al último válido del mes de destino (evita errores tipo 31 de febrero)."""
    mes_total = d.month - 1 + meses
    anio = d.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(d.day, ultimo_dia))


def _filtrar_filas_validas(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["Symbol"].notna() & (df["Symbol"] != "Symbol")].copy()


def _col_opcional(df: pd.DataFrame, *alternativas: str) -> str | None:
    for nombre in alternativas:
        if nombre in df.columns:
            return nombre
    return None


# ---------------------------------------------------------------------------
# Preparación: acciones (STK)
# ---------------------------------------------------------------------------


def preparar_operaciones_stk(df: pd.DataFrame) -> pd.DataFrame:
    df = _filtrar_filas_validas(df)
    df = df[df["AssetClass"] == "STK"].copy()
    if df.empty:
        return df

    for col in ("Quantity", "Proceeds", "IBCommission", "TradePrice"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["DateTime_parsed"] = df["DateTime"].map(parse_datetime_ibkr)
    df["Fecha"] = df["DateTime_parsed"].dt.date
    df["Side"] = df["Buy/Sell"].str.strip().str.upper()
    df["AjusteOpcionesEUR"] = 0.0
    df = df.sort_values(["Symbol", "DateTime_parsed"]).reset_index(drop=True)
    return df


def filtrar_hasta_ejercicio(df: pd.DataFrame, ejercicio: int) -> pd.DataFrame:
    """FIFO solo con operaciones hasta 31/12 del ejercicio (ignora años posteriores)."""
    if df.empty:
        return df
    fin = date(ejercicio, 12, 31)
    filtrado = df[df["Fecha"] <= fin].copy()
    omitidas = len(df) - len(filtrado)
    if omitidas:
        print(
            f"  Ignoradas {omitidas} operaciones posteriores a {fin} "
            f"(no afectan a la declaración de {ejercicio})"
        )
    return filtrado


# ---------------------------------------------------------------------------
# Preparación: opciones (OPT)
# ---------------------------------------------------------------------------


def preparar_operaciones_opt(df: pd.DataFrame) -> pd.DataFrame:
    df = _filtrar_filas_validas(df)
    df = df[df["AssetClass"] == "OPT"].copy()
    if df.empty:
        return df

    col_underlying = _col_opcional(df, "UnderlyingSymbol", "Underlying Symbol")
    col_putcall = _col_opcional(df, "Put/Call", "PutCall")
    col_strike = _col_opcional(df, "Strike")
    col_expiry = _col_opcional(df, "Expiry", "Expiration")
    col_multiplier = _col_opcional(df, "Multiplier")
    col_notes = _col_opcional(df, "Notes/Codes", "Code", "Codes")

    faltan = [
        nombre
        for nombre, col in [
            ("UnderlyingSymbol", col_underlying),
            ("Put/Call", col_putcall),
            ("Strike", col_strike),
            ("Expiry", col_expiry),
        ]
        if col is None
    ]
    if faltan:
        raise KeyError(
            "Hay operaciones de opciones (AssetClass=OPT) pero faltan columnas "
            f"en el CSV: {', '.join(faltan)}. Añádelas a la Flex Query de IBKR "
            "(ver docs/TUTORIAL_EXPORT_IBKR.md) y vuelve a exportar."
        )
    if col_notes is None:
        print(
            "  (aviso) El CSV de opciones no trae 'Notes/Codes': no se podrá "
            "detectar automáticamente ejercicio/asignación (se tratarán todas "
            "las opciones como cierres normales). Añade esa columna a la Flex "
            "Query para un cálculo correcto si ejerces o te asignan opciones."
        )

    for col in ("Quantity", "Proceeds", "IBCommission", "TradePrice", col_strike):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if col_multiplier is not None:
        df["Multiplier"] = pd.to_numeric(df[col_multiplier], errors="coerce")
        sin_multiplicador = df["Multiplier"].isna().sum()
        if sin_multiplicador:
            print(
                f"  (aviso) {sin_multiplicador} opciones sin 'Multiplier' válido: "
                f"se asume {MULTIPLICADOR_OPCION_DEFECTO:.0f} (estándar)."
            )
        df["Multiplier"] = df["Multiplier"].fillna(MULTIPLICADOR_OPCION_DEFECTO)
    else:
        print(
            "  (aviso) El CSV de opciones no trae 'Multiplier': se asume "
            f"{MULTIPLICADOR_OPCION_DEFECTO:.0f} acciones/contrato para todas."
        )
        df["Multiplier"] = MULTIPLICADOR_OPCION_DEFECTO

    df["UnderlyingSymbol"] = df[col_underlying].astype(str).str.strip().str.upper()

    putcall_norm = df[col_putcall].astype(str).str.strip().str.upper()
    df["PutCall"] = putcall_norm.str[0]
    invalido = ~df["PutCall"].isin(["C", "P"])
    if invalido.any():
        contratos_malos = ", ".join(
            sorted(df.loc[invalido, "Symbol"].astype(str).unique())[:10]
        )
        raise KeyError(
            f"{invalido.sum()} operación(es) de opciones con 'Put/Call' vacío o no "
            f"reconocible (ni C ni P): {contratos_malos}. Revisa esa columna en el "
            "CSV de IBKR."
        )

    df["EsIndice"] = df["UnderlyingSymbol"].isin(SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO)

    df["Strike"] = df[col_strike]
    df["NotesCodes"] = df[col_notes].astype(str).fillna("") if col_notes else ""
    df.loc[df["NotesCodes"] == "nan", "NotesCodes"] = ""

    expiry_raw = df[col_expiry]
    # pandas suele inferir la columna como float si en el CSV combinado hay
    # huecos (filas STK sin Expiry) -> "20260619.0"; se limpia antes de parsear.
    expiry_str = expiry_raw.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["ExpiryDate"] = pd.to_datetime(expiry_str, format="%Y%m%d", errors="coerce")
    faltan_fecha = df["ExpiryDate"].isna()
    if faltan_fecha.any():
        df.loc[faltan_fecha, "ExpiryDate"] = pd.to_datetime(
            expiry_str[faltan_fecha], errors="coerce", dayfirst=True
        )
    df["ExpiryISO"] = df["ExpiryDate"].dt.strftime("%Y-%m-%d").fillna("desconocido")

    df["OptionContractKey"] = (
        df["UnderlyingSymbol"] + "_" + df["ExpiryISO"] + "_" + df["PutCall"] + "_"
        + df["Strike"].map(lambda x: f"{x:g}")
    )
    df["OptionLabel"] = (
        df["UnderlyingSymbol"] + " " + df["PutCall"] + " " + df["Strike"].map(lambda x: f"{x:g}")
        + " " + df["ExpiryISO"]
    )

    df["DateTime_parsed"] = df["DateTime"].map(parse_datetime_ibkr)
    df["Fecha"] = df["DateTime_parsed"].dt.date
    df["Side"] = df["Buy/Sell"].str.strip().str.upper()
    df = df.sort_values(["OptionContractKey", "DateTime_parsed"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Motor FIFO genérico (reutilizado por acciones y por cada contrato de opción)
# ---------------------------------------------------------------------------


def fifo_generico(
    clave: str,
    ops: pd.DataFrame,
    rates_by_date: dict[date, dict[str, float]],
) -> tuple[list[FifoMatch], list[Lot], list[ShortLot], list[OperationRow]]:
    """
    IBKR: Quantity > 0 en BUY (compra/recompra), Quantity < 0 en SELL (venta/apertura corto).
    Primero se cierra posición contraria (FIFO), el resto abre largo o corto.
    Válido tanto para acciones (clave=símbolo) como para un contrato de opción
    concreto (clave=OptionContractKey): la mecánica larga/corta es idéntica.
    """
    long_lots: list[Lot] = []
    short_lots: list[ShortLot] = []
    matches: list[FifoMatch] = []
    detalle: list[OperationRow] = []

    tiene_ajuste = "AjusteOpcionesEUR" in ops.columns
    tiene_notes = "NotesCodes" in ops.columns

    for _, row in ops.iterrows():
        currency = row["CurrencyPrimary"]
        side = row["Side"]
        qty_signed = float(row["Quantity"])
        proceeds = float(row["Proceeds"])
        commission = float(row["IBCommission"])
        op_day: date = row["Fecha"]
        dt: datetime = row["DateTime_parsed"]
        notes_codes = str(row["NotesCodes"]) if tiene_notes else ""

        if abs(qty_signed) <= 1e-9:
            continue

        importe_eur_base, rate_date, ecb_rate = importe_operacion_eur(
            proceeds, commission, currency, rates_by_date, op_day
        )
        ajuste = float(row["AjusteOpcionesEUR"]) if tiene_ajuste else 0.0
        importe_eur = importe_eur_base + ajuste

        if qty_signed > 0:
            remaining = qty_signed
            had_short = bool(short_lots)

            while remaining > 1e-9 and short_lots:
                lot = short_lots[0]
                take = min(remaining, lot.qty)
                proporcion_lot = take / lot.qty if lot.qty else 0.0
                transmission_slice = lot.transmission_per_share * take
                ajuste_venta_slice = lot.ajuste_eur * proporcion_lot
                acquisition_slice = importe_eur * (take / qty_signed)
                ajuste_adquisicion_slice = ajuste * (take / qty_signed)
                matches.append(
                    FifoMatch(
                        symbol=clave,
                        position_type="corto",
                        transmission_date=lot.fecha,
                        acquisition_date=op_day,
                        qty=take,
                        venta_eur=transmission_slice,
                        adquisicion_eur=acquisition_slice,
                        matched_dates=[lot.fecha, op_day],
                        closing_notes_codes=notes_codes,
                        ajuste_venta_eur=ajuste_venta_slice,
                        ajuste_adquisicion_eur=ajuste_adquisicion_slice,
                    )
                )
                lot.qty -= take
                lot.transmission_eur -= transmission_slice
                lot.ajuste_eur -= ajuste_venta_slice
                remaining -= take
                if lot.qty <= 1e-9:
                    short_lots.pop(0)

            if remaining > 1e-9:
                cost_rest = importe_eur * (remaining / qty_signed)
                ajuste_rest = ajuste * (remaining / qty_signed)
                long_lots.append(
                    Lot(fecha=op_day, qty=remaining, cost_eur=cost_rest, ajuste_eur=ajuste_rest)
                )

            if had_short and remaining > 1e-9:
                op_tipo = "recompra_corto+compra"
            elif had_short:
                op_tipo = "recompra_corto"
            else:
                op_tipo = "compra"

            detalle.append(
                OperationRow(
                    symbol=clave,
                    currency=currency,
                    side=side,
                    dt=dt,
                    qty=qty_signed,
                    proceeds_local=proceeds,
                    commission_local=commission,
                    rate_date=rate_date,
                    ecb_rate=ecb_rate,
                    importe_eur_base=importe_eur_base,
                    ajuste_opciones_eur=ajuste,
                    tipo=op_tipo,
                    notes_codes=notes_codes,
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
            proporcion_lot = take / lot.qty if lot.qty else 0.0
            cost_slice = lot.cost_per_share * take
            ajuste_adquisicion_slice = lot.ajuste_eur * proporcion_lot
            adquisicion_long += cost_slice
            matched_long_dates.append(lot.fecha)
            venta_slice = venta_total_eur * (take / abs(qty_signed))
            ajuste_venta_slice = ajuste * (take / abs(qty_signed))
            matches.append(
                FifoMatch(
                    symbol=clave,
                    position_type="largo",
                    transmission_date=op_day,
                    acquisition_date=lot.fecha,
                    qty=take,
                    venta_eur=venta_slice,
                    adquisicion_eur=cost_slice,
                    matched_dates=[op_day, lot.fecha],
                    closing_notes_codes=notes_codes,
                    ajuste_venta_eur=ajuste_venta_slice,
                    ajuste_adquisicion_eur=ajuste_adquisicion_slice,
                )
            )
            lot.qty -= take
            lot.cost_eur -= cost_slice
            lot.ajuste_eur -= ajuste_adquisicion_slice
            remaining -= take
            if lot.qty <= 1e-9:
                long_lots.pop(0)

        if remaining > 1e-9:
            if long_lots:
                raise RuntimeError(
                    f"{clave}: venta el {op_day} no pudo cerrar largos pendientes."
                )
            transmission_rest = venta_total_eur * (remaining / abs(qty_signed))
            ajuste_rest = ajuste * (remaining / abs(qty_signed))
            short_lots.append(
                ShortLot(
                    fecha=op_day, qty=remaining, transmission_eur=transmission_rest,
                    ajuste_eur=ajuste_rest,
                )
            )
            op_tipo = "apertura_corto"
        else:
            op_tipo = "venta"

        detalle.append(
            OperationRow(
                symbol=clave,
                currency=currency,
                side=side,
                dt=dt,
                qty=qty_signed,
                proceeds_local=proceeds,
                commission_local=commission,
                rate_date=rate_date,
                ecb_rate=ecb_rate,
                importe_eur_base=importe_eur_base,
                ajuste_opciones_eur=ajuste,
                tipo=op_tipo,
                notes_codes=notes_codes,
            )
        )

    return matches, long_lots, short_lots, detalle


# ---------------------------------------------------------------------------
# Opciones: cálculo del ajuste que hay que trasladar a la acción subyacente
# ---------------------------------------------------------------------------


def calcular_ajuste_stk_por_ejercicio(
    m: FifoMatch, put_call: str
) -> tuple[str, float, str]:
    """
    Devuelve (side_esperado_en_la_accion, importe_ajuste_eur, casilla) para una
    opción ejercida/asignada, según el criterio DGT (p.ej. V2172-21): la prima
    pierde independencia como ganancia/pérdida patrimonial y pasa a integrarse
    en la operación de compraventa de la acción subyacente:

      - CALL comprada y ejercida (titular compra la acción): la prima PAGADA
        se SUMA al valor de ADQUISICIÓN (0331) de la compra.
      - CALL vendida y asignada (emisor vende la acción): la prima COBRADA
        se SUMA al valor de TRANSMISIÓN (0328) de la venta.
      - PUT comprada y ejercida (titular vende la acción): la prima PAGADA
        se RESTA del valor de TRANSMISIÓN (0328) de la venta.
      - PUT vendida y asignada (emisor compra la acción): la prima COBRADA
        se RESTA del valor de ADQUISICIÓN (0331) de la compra.
    """
    if put_call == "C" and m.position_type == "largo":
        return "BUY", m.adquisicion_eur, "0331 (adquisición)"
    if put_call == "C" and m.position_type == "corto":
        return "SELL", m.venta_eur, "0328 (transmisión)"
    if put_call == "P" and m.position_type == "largo":
        return "SELL", -m.adquisicion_eur, "0328 (transmisión)"
    if put_call == "P" and m.position_type == "corto":
        return "BUY", -m.venta_eur, "0331 (adquisición)"
    raise ValueError(f"Put/Call desconocido: {put_call!r}")


def calcular_ajustes_stk_desde_opciones(
    df_stk_all: pd.DataFrame, pendientes: list[AjusteEjercicioOpcion]
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Reparte el importe de cada ejercicio/asignación de opción entre las filas
    de ACCIONES (mismo símbolo, fecha y sentido) que lo materializan, a
    prorrata de unidades si ese día hay varias filas. Si no encuentra una
    operación de acciones que cuadre en cantidad, NO aplica el ajuste (para
    no dar una cifra incorrecta) y lo deja marcado para revisión manual.
    """
    ajuste = pd.Series(0.0, index=df_stk_all.index)
    auditoria: list[dict] = []
    if not pendientes:
        return ajuste, pd.DataFrame(auditoria)

    por_clave: dict[tuple[str, date, str], list[AjusteEjercicioOpcion]] = {}
    for a in pendientes:
        por_clave.setdefault((a.underlying, a.fecha, a.side_esperado), []).append(a)

    if df_stk_all.empty:
        for items in por_clave.values():
            for a in items:
                auditoria.append(_fila_auditoria_ajuste(a, "REVISAR: no hay operaciones de acciones cargadas"))
        return ajuste, pd.DataFrame(auditoria)

    for (symbol, fecha, side), items in por_clave.items():
        qty_esperada = sum(i.qty_esperada for i in items)
        importe_total = sum(i.importe_eur for i in items)
        filas = df_stk_all[
            (df_stk_all["Symbol"] == symbol)
            & (df_stk_all["Fecha"] == fecha)
            & (df_stk_all["Side"] == side)
        ]
        qty_disponible = filas["Quantity"].abs().sum()

        if filas.empty or abs(qty_disponible - qty_esperada) > TOLERANCIA_QTY:
            estado = (
                "REVISAR: no se encontró operación de acciones que cuadre "
                f"(esperadas {qty_esperada:g} uds. el {fecha}, encontradas {qty_disponible:g})"
            )
            for a in items:
                auditoria.append(_fila_auditoria_ajuste(a, estado))
            continue

        for idx, fila in filas.iterrows():
            proporcion = (abs(fila["Quantity"]) / qty_disponible) if qty_disponible else 0.0
            ajuste.loc[idx] += importe_total * proporcion

        estado = f"Aplicado a {symbol} {side} {fecha} ({len(filas)} fila(s) de acciones)"
        for a in items:
            auditoria.append(_fila_auditoria_ajuste(a, estado))

    return ajuste, pd.DataFrame(auditoria)


def construir_filas_opciones_sin_cruce(
    pendientes: list[AjusteEjercicioOpcion], auditoria: pd.DataFrame, ejercicio: int
) -> pd.DataFrame:
    """
    Opciones marcadas como ejercicio/asignación pero cuyo ajuste NO se pudo
    cruzar con una operación de acciones (símbolo no cargado, fecha/cantidad
    no coincide, etc.). No podemos dejarlas fuera de toda tabla —eso
    ocultaría dinero real—, así que se declaran aquí con el resultado de la
    prima tal cual (como si fuera un cierre normal), marcadas para revisión:
    lo correcto según la DGT es que ese importe entre en 0328/0331 de la
    acción, no aquí, pero mientras no localices esa operación esta es la
    única cifra que refleja el dinero movido por el contrato.

    Se identifica cada evento por (contrato, fecha) — no solo por contrato —
    porque el mismo contrato puede tener varios ejercicios/asignaciones
    parciales sin cruzar en fechas distintas, y todos cuentan. Y solo se
    incluyen los del `ejercicio` que se está declarando ahora: el ajuste en
    sí se calcula usando TODO el histórico (ver `ejercidas` en
    construir_tablas_opciones), pero como ganancia patrimonial "de reserva"
    debe imputarse al año real del ejercicio/asignación, no repetirse en
    todas las declaraciones futuras que carguen ese mismo CSV histórico.
    """
    if not pendientes or auditoria.empty:
        return pd.DataFrame()
    claves_revisar = set(
        zip(
            auditoria.loc[auditoria["Estado"].str.startswith("REVISAR"), "Contrato"],
            auditoria.loc[auditoria["Estado"].str.startswith("REVISAR"), "Fecha ejercicio/asignación"],
        )
    )
    filas = []
    for a in pendientes:
        if a.fecha.year != ejercicio:
            continue
        if (a.contrato, a.fecha.isoformat()) not in claves_revisar:
            continue
        filas.append(
            {
                "Contrato": a.contrato,
                "Subyacente": a.underlying,
                "Put/Call": a.put_call,
                "Índice (liq. efectivo)": "No",
                "Nº operaciones": 1,
                "Contratos": "",
                "Prima cobrada (cierres/vencimientos) €": "",
                "Prima pagada (aperturas consumidas) €": "",
                "Ganancia / pérdida neta €": round(a.fallback_ganancia_eur, 2),
                "Divisas": a.fallback_divisas,
                "Nota": (
                    "EJERCIDA/ASIGNADA sin cruce automático con una operación de "
                    f"acciones de {a.underlying}: por normativa este importe debería "
                    f"ir en la casilla {a.casilla} de la acción, no aquí. Revisa "
                    "extras/opciones_ejercicios_asignaciones.csv y ajusta manualmente."
                ),
            }
        )
    return pd.DataFrame(filas)


def _fila_auditoria_ajuste(a: AjusteEjercicioOpcion, estado: str) -> dict:
    return {
        "Contrato": a.contrato,
        "Subyacente": a.underlying,
        "Fecha ejercicio/asignación": a.fecha.isoformat(),
        "Operación esperada en la acción": a.side_esperado,
        "Unidades esperadas": a.qty_esperada,
        "Importe prima EUR (con signo del ajuste)": round(a.importe_eur, 2),
        "Casilla afectada": a.casilla,
        "Estado": estado,
    }


# ---------------------------------------------------------------------------
# Tablas: acciones
# ---------------------------------------------------------------------------


def construir_tablas_acciones(
    df: pd.DataFrame,
    rates_by_date: dict[date, dict[str, float]],
    ejercicio: int,
    df_stk_todo_historico: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    filas_declaracion = []
    filas_detalle_fifo = []
    filas_detalle_ops = []
    filas_abiertas = []
    filas_minusvalias_computables = []
    filas_minusvalias_auditoria = []

    if df.empty:
        vacio = pd.DataFrame()
        return vacio, vacio, vacio, vacio, vacio, vacio

    for symbol, ops in df.groupby("Symbol", sort=True):
        matches, long_restantes, short_restantes, detalle = fifo_generico(
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
                    "Importe EUR (BCE)": round(op.importe_eur_base, 2),
                    "Ajuste por opción ejercida/asignada €": round(op.ajuste_opciones_eur, 2),
                    "Importe EUR final": round(op.importe_eur, 2),
                }
            )

        tributables = [m for m in matches if m.imputation_date.year == ejercicio]
        if tributables:
            venta_sum = sum(m.venta_eur for m in tributables)
            adq_sum = sum(m.adquisicion_eur for m in tributables)
            ajuste_sum = sum(m.ajuste_neto_eur for m in tributables)
            filas_declaracion.append(
                {
                    "Símbolo (casilla 0327)": symbol,
                    "Nº operaciones": len(tributables),
                    "Unidades": round(sum(m.qty for m in tributables), 6),
                    "Valor transmisión (casilla 0328) €": round(venta_sum, 2),
                    "Valor adquisición FIFO (casilla 0331) €": round(adq_sum, 2),
                    "Ganancia / pérdida neta €": round(venta_sum - adq_sum, 2),
                    "Incluye ajuste por opciones ejercidas/asignadas €": round(ajuste_sum, 2),
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

            for m in tributables:
                if m.position_type != "largo" or m.ganancia_eur >= -1e-9:
                    continue
                fila, auditoria = _analizar_regla_dos_meses(
                    m, df_stk_todo_historico
                )
                filas_minusvalias_auditoria.append(auditoria)
                if fila is not None:
                    filas_minusvalias_computables.append(fila)

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
    tabla_minusvalias = pd.DataFrame(filas_minusvalias_computables)
    tabla_minusvalias_auditoria = pd.DataFrame(filas_minusvalias_auditoria)
    return (
        tabla_decl,
        tabla_fifo,
        tabla_ops,
        tabla_abiertas,
        tabla_minusvalias,
        tabla_minusvalias_auditoria,
    )


def _analizar_regla_dos_meses(
    m: FifoMatch, df_stk_todo_historico: pd.DataFrame
) -> tuple[dict | None, dict]:
    """
    Art. 33.5.f LIRPF: una pérdida por venta de acciones cotizadas NO se puede
    computar en el ejercicio si se han comprado valores homogéneos dentro de
    los 2 meses ANTERIORES o POSTERIORES a la venta. Se compara contra TODO
    el histórico cargado (no solo hasta 31/12 del ejercicio), para poder
    detectar recompras de enero/febrero del año siguiente si esos ficheros
    también se han cargado.

    Heurística conservadora: si hay CUALQUIER recompra dentro de la ventana,
    aunque sea parcial, se marca toda la pérdida de esa venta como NO
    computable este ejercicio (evita reclamar de más; una recompra parcial
    podría permitir computar la parte no recomprada — coméntalo con tu
    asesor si el importe es relevante).
    """
    venta_date = m.transmission_date
    inicio = sumar_meses(venta_date, -VENTANA_RECOMPRA_MESES)
    fin = sumar_meses(venta_date, VENTANA_RECOMPRA_MESES)

    compras_ventana = df_stk_todo_historico[
        (df_stk_todo_historico["Symbol"] == m.symbol)
        & (df_stk_todo_historico["Side"] == "BUY")
        & (df_stk_todo_historico["Fecha"] >= inicio)
        & (df_stk_todo_historico["Fecha"] <= fin)
    ]
    qty_recompra = compras_ventana["Quantity"].sum()
    bloqueada = qty_recompra > 1e-9
    fechas_recompra = sorted({d.isoformat() for d in compras_ventana["Fecha"]})

    base = {
        "Empresa": m.symbol,
        "Fecha transmisión": venta_date.isoformat(),
        "Fecha adquisición (FIFO)": m.acquisition_date.isoformat(),
        "Unidades": round(m.qty, 6),
        "Pérdida €": round(m.ganancia_eur, 2),
        "Ventana analizada": f"{inicio.isoformat()} a {fin.isoformat()}",
        "Unidades recompradas en la ventana": round(qty_recompra, 6),
        "Fechas de recompra detectadas": ", ".join(fechas_recompra) if fechas_recompra else "-",
        "Computable este ejercicio": "No" if bloqueada else "Sí",
        "Motivo": (
            "Recompra de valores homogéneos dentro de ±2 meses (art. 33.5.f LIRPF): "
            "la pérdida se difiere hasta que transmitas esos valores sin recomprar de nuevo en el plazo."
            if bloqueada
            else "Sin recompra detectada en ±2 meses: pérdida computable en este ejercicio."
        ),
    }

    if bloqueada:
        return None, base
    fila_computable = {k: v for k, v in base.items() if k not in ("Computable este ejercicio", "Motivo")}
    return fila_computable, base


# ---------------------------------------------------------------------------
# Tablas: opciones
# ---------------------------------------------------------------------------


def construir_tablas_opciones(
    df: pd.DataFrame,
    rates_by_date: dict[date, dict[str, float]],
    ejercicio: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[AjusteEjercicioOpcion], list[str]]:
    filas_declaracion = []
    filas_detalle_fifo = []
    filas_detalle_ops = []
    filas_abiertas = []
    ajustes_pendientes: list[AjusteEjercicioOpcion] = []
    avisos_indice: list[str] = []

    if df.empty:
        vacio = pd.DataFrame()
        return vacio, vacio, vacio, vacio, ajustes_pendientes, avisos_indice

    for contrato_key, ops in df.groupby("OptionContractKey", sort=True):
        label = ops["OptionLabel"].iloc[0]
        underlying = ops["UnderlyingSymbol"].iloc[0]
        put_call = ops["PutCall"].iloc[0]
        multiplier = float(ops["Multiplier"].iloc[0])
        es_indice = bool(ops["EsIndice"].iloc[0])

        matches, long_restantes, short_restantes, detalle = fifo_generico(
            label, ops, rates_by_date
        )

        for op in detalle:
            filas_detalle_ops.append(
                {
                    "Contrato": label,
                    "Subyacente": underlying,
                    "Fecha operación": op.dt.strftime("%d/%m/%Y %H:%M"),
                    "Año": op.dt.year,
                    "Operación": op.tipo.upper(),
                    "Divisa": op.currency,
                    "Contratos": round(op.qty, 6),
                    "Proceeds divisa": round(op.proceeds_local, 4),
                    "Comisión divisa": round(op.commission_local, 4),
                    "Fecha tipo BCE": op.rate_date.isoformat(),
                    "Tipo BCE (divisa/EUR)": round(op.ecb_rate, 6) if op.currency != "EUR" else 1.0,
                    "Importe EUR (prima)": round(op.importe_eur_base, 2),
                    "Notes/Codes IBKR": op.notes_codes,
                }
            )

        tributables = [m for m in matches if m.imputation_date.year == ejercicio]

        if es_indice:
            # Opciones sobre índices: liquidan SIEMPRE en efectivo (no hay
            # acción que entregar), así que ejercicio/asignación tributa
            # igual que un cierre normal — nunca se traslada la prima a 0328/0331.
            normales = tributables
            ejercidas = []
            for m in tributables:
                if not m.es_ejercicio_o_asignacion:
                    continue
                cierre = m.venta_eur if m.position_type == "largo" else m.adquisicion_eur
                if abs(cierre) < 1e-9:
                    avisos_indice.append(
                        f"{label}: código '{m.closing_notes_codes}' el "
                        f"{m.imputation_date} con importe de liquidación 0 €. "
                        "IBKR a veces reporta la liquidación en efectivo de "
                        "opciones sobre índices fuera de Trades (sección 'Cash "
                        "Settlement' / 'Option Exercises, Assignments and "
                        "Expirations'); revisa si falta ese importe y ajústalo "
                        "a mano en la tabla de opciones."
                    )
        else:
            normales = [m for m in tributables if not m.es_ejercicio_o_asignacion]
            # OJO: el ajuste al coste/valor de la acción por ejercicio/asignación
            # se calcula sobre TODOS los matches (no solo los de este ejercicio):
            # la opción pudo ejercerse en un año anterior y la acción resultante
            # venderse este año o en uno futuro — el ajuste tiene que aplicarse
            # siempre que la operación de la acción esté cargada, con independencia
            # de qué --ejercicio se esté declarando ahora mismo.
            ejercidas = [m for m in matches if m.es_ejercicio_o_asignacion]

        for m in ejercidas:
            side_esperado, importe_ajuste, casilla = calcular_ajuste_stk_por_ejercicio(
                m, put_call
            )
            ajustes_pendientes.append(
                AjusteEjercicioOpcion(
                    contrato=label,
                    underlying=underlying,
                    put_call=put_call,
                    fecha=m.imputation_date,
                    side_esperado=side_esperado,
                    qty_esperada=m.qty * multiplier,
                    importe_eur=importe_ajuste,
                    casilla=casilla,
                    fallback_ganancia_eur=m.ganancia_eur,
                    fallback_divisas=", ".join(sorted(ops["CurrencyPrimary"].unique())),
                )
            )

        if normales:
            venta_sum = sum(m.venta_eur for m in normales)
            adq_sum = sum(m.adquisicion_eur for m in normales)
            filas_declaracion.append(
                {
                    "Contrato": label,
                    "Subyacente": underlying,
                    "Put/Call": put_call,
                    "Índice (liq. efectivo)": "Sí" if es_indice else "No",
                    "Nº operaciones": len(normales),
                    "Contratos": round(sum(m.qty for m in normales), 6),
                    "Prima cobrada (cierres/vencimientos) €": round(venta_sum, 2),
                    "Prima pagada (aperturas consumidas) €": round(adq_sum, 2),
                    "Ganancia / pérdida neta €": round(venta_sum - adq_sum, 2),
                    "Divisas": ", ".join(sorted(ops["CurrencyPrimary"].unique())),
                    "Nota": "",
                }
            )
            for m in normales:
                fechas_match = ", ".join(sorted({d.isoformat() for d in m.matched_dates}))
                filas_detalle_fifo.append(
                    {
                        "Contrato": label,
                        "Subyacente": underlying,
                        "Tipo posición": m.position_type,
                        "Fecha cierre/vencimiento": m.imputation_date.isoformat(),
                        "Contratos": m.qty,
                        "Prima cobrada EUR": round(m.venta_eur, 2),
                        "Prima pagada EUR": round(m.adquisicion_eur, 2),
                        "Ganancia EUR": round(m.ganancia_eur, 2),
                        "Fechas emparejadas": fechas_match,
                    }
                )

        if long_restantes:
            qty_l = sum(l.qty for l in long_restantes)
            filas_abiertas.append(
                {
                    "Contrato": label,
                    "Subyacente": underlying,
                    "Tipo posición": "larga (comprada)",
                    "Contratos abiertos": round(qty_l, 6),
                    "Prima pendiente €": round(sum(l.cost_eur for l in long_restantes), 2),
                    "Nota": "Opción comprada sin cerrar/vencer; no declarar hasta liquidar",
                }
            )
        if short_restantes:
            qty_s = sum(l.qty for l in short_restantes)
            filas_abiertas.append(
                {
                    "Contrato": label,
                    "Subyacente": underlying,
                    "Tipo posición": "corta (vendida/emitida)",
                    "Contratos abiertos": round(-qty_s, 6),
                    "Prima pendiente €": round(
                        sum(l.transmission_eur for l in short_restantes), 2
                    ),
                    "Nota": "Opción vendida sin cerrar/vencer; no declarar hasta liquidar",
                }
            )

    tabla_decl = pd.DataFrame(filas_declaracion)
    tabla_fifo = pd.DataFrame(filas_detalle_fifo)
    tabla_ops = pd.DataFrame(filas_detalle_ops)
    tabla_abiertas = pd.DataFrame(filas_abiertas)
    return tabla_decl, tabla_fifo, tabla_ops, tabla_abiertas, ajustes_pendientes, avisos_indice


def resumen_divisa_operacion(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
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
COL_AJUSTE_OPC = "Incluye ajuste por opciones ejercidas/asignadas €"
COL_DIVISAS = "Divisas"
FILA_TOTAL = "TOTAL"

COL_OPC_GANANCIA = "Ganancia / pérdida neta €"


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
        COL_AJUSTE_OPC: "",
        COL_DIVISAS: "",
    }
    return pd.concat([tabla, pd.DataFrame([fila])], ignore_index=True)


def agregar_fila_total_opciones(tabla: pd.DataFrame) -> pd.DataFrame:
    if tabla.empty:
        return tabla
    suma_ganancia = round(tabla[COL_OPC_GANANCIA].sum(), 2)
    fila = {col: "" for col in tabla.columns}
    fila["Contrato"] = FILA_TOTAL
    fila[COL_OPC_GANANCIA] = suma_ganancia
    return pd.concat([tabla, pd.DataFrame([fila])], ignore_index=True)


def validar_tabla_declaracion(tabla: pd.DataFrame, ejercicio: int) -> list[str]:
    """Comprueba coherencia interna y avisos normativos."""
    avisos: list[str] = []
    if tabla.empty:
        avisos.append(f"No hay ventas de acciones en el ejercicio {ejercicio}.")
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


def avisos_symbolos_mixtos_regla_2_meses(
    tabla_fifo: pd.DataFrame, tabla_minusvalias_auditoria: pd.DataFrame
) -> list[str]:
    """
    Si una empresa tiene, en el mismo ejercicio, algún lote de pérdida BLOQUEADO
    por la regla de los 2 meses junto con otros lotes computables (ganancias u
    otras pérdidas sí computables), la fila agregada de la tabla definitiva
    mezcla ambos efectos. Hacienda exige diferenciar el importe no imputable;
    avisamos para que el usuario separe manualmente esa empresa en dos entradas
    de Renta Web si es su caso.
    """
    if tabla_minusvalias_auditoria.empty or tabla_fifo.empty:
        return []
    bloqueadas = tabla_minusvalias_auditoria[
        tabla_minusvalias_auditoria["Computable este ejercicio"] == "No"
    ]
    total_lotes_por_empresa = tabla_fifo.groupby("Empresa").size()
    avisos = []
    for empresa, n_bloqueadas in bloqueadas.groupby("Empresa").size().items():
        n_total = total_lotes_por_empresa.get(empresa, 0)
        if n_total > n_bloqueadas:
            avisos.append(
                f"{empresa}: tiene pérdida(s) NO computable(s) este ejercicio por la "
                "regla de los 2 meses (recompra homogénea) MEZCLADAS en la misma fila "
                "de la tabla definitiva con otras operaciones del año sí computables. "
                "Revisa extras/perdidas_analisis_regla_2_meses.csv y, si Renta Web lo "
                "exige, declara esa empresa en dos entradas separadas (parte computable "
                "/ parte diferida)."
            )
        else:
            avisos.append(
                f"{empresa}: TODA su pérdida del ejercicio está bloqueada por la regla "
                "de los 2 meses (recompra homogénea). La fila de esa empresa en la "
                "tabla definitiva NO debe integrarse como pérdida este año (0328/0331 "
                "siguen siendo correctos como valores de la operación, pero el resultado "
                "se difiere) — revisa extras/perdidas_analisis_regla_2_meses.csv."
            )
    return avisos


def escribir_informe_validacion(
    ejercicio: int,
    archivos: list[str],
    avisos: list[str],
    tabla: pd.DataFrame,
    tabla_opciones: pd.DataFrame,
    avisos_opciones: list[str],
    avisos_regla_2_meses: list[str],
    avisos_indice: list[str],
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
        "Criterios aplicados (IRPF 2025/2026):",
        "  - Art. 37.2 LIRPF: FIFO en acciones",
        "  - Art. 36 Ley 46/1998: tipos BCE por fecha de operación",
        "  - 0328: importe neto de ventas en EUR (tipo BCE día venta)",
        "  - 0331: coste FIFO de compras en EUR (tipo BCE día compra)",
        "  - Art. 37.1.m LIRPF: opciones/futuros, ganancia patrimonial al liquidar/vencer",
        "  - Art. 34/35 LIRPF (DGT, p.ej. V2172-21): prima de opción sobre ACCIÓN",
        "    ejercida/asignada se integra en el valor de transmisión o adquisición",
        "    de la acción, no tributa aparte",
        "  - Opciones sobre ÍNDICES (liquidación en efectivo, sin entrega de",
        "    acciones): tributan SIEMPRE como ganancia/pérdida propia (37.1.m),",
        "    también si están marcadas ejercicio/asignación — no hay acción que",
        "    ajustar (ver SIMBOLOS_INDICE_LIQUIDACION_EFECTIVO en el script)",
        "  - Art. 33.5.f LIRPF: pérdidas de ACCIONES NO computables si hay recompra",
        "    homogénea en los 2 MESES anteriores o posteriores a la venta",
        "",
        "Comprobación 0328 - 0331 = ganancia (acciones):",
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
            f"Filas en tabla definitiva (acciones): {n_filas}",
            f"Suma ganancia / pérdida neta acciones (fila TOTAL del CSV): {suma_g} €",
            "  (Unidades, 0328 y 0331 no se suman: son importes por empresa en Renta Web)",
        ]
    )

    lineas.append("")
    lineas.append("Opciones (calls/puts) cerradas o vencidas sin ejercer:")
    if tabla_opciones.empty:
        lineas.append("  No hay opciones cerradas/vencidas en el ejercicio.")
    else:
        suma_opc = round(tabla_opciones[COL_OPC_GANANCIA].sum(), 2)
        lineas.append(f"  Filas: {len(tabla_opciones)}  ·  Ganancia/pérdida neta: {suma_opc} €")
        lineas.append(
            "  Estas NO van en 0327/0328/0331: son ganancia/pérdida patrimonial que no"
        )
        lineas.append(
            "  deriva de transmisión de elementos patrimoniales (ver README/CONFORMIDAD)."
        )

    if avisos_opciones:
        lineas.append("")
        lineas.append("Avisos de opciones ejercidas/asignadas:")
        lineas.extend(f"  AVISO: {a}" for a in avisos_opciones)

    if avisos_regla_2_meses:
        lineas.append("")
        lineas.append("Avisos de la regla de los 2 meses (art. 33.5.f LIRPF):")
        lineas.extend(f"  AVISO: {a}" for a in avisos_regla_2_meses)

    if avisos_indice:
        lineas.append("")
        lineas.append("Avisos de opciones sobre índices (posible liquidación en efectivo no reflejada):")
        lineas.extend(f"  AVISO: {a}" for a in avisos_indice)

    lineas.extend(["", "Límites: ver docs/CONFORMIDAD_NORMATIVA.md"])
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def guardar_csv(path: Path | str, tabla: pd.DataFrame) -> None:
    """UTF-8 con BOM para que Excel en Windows muestre bien ñ, €, etc."""
    tabla.to_csv(path, index=False, sep=";", encoding="utf-8-sig")


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tabla IRPF: BCE + FIFO, acciones y opciones (un ejercicio fiscal en la salida)."
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
    if tabla is None or tabla.empty:
        return None
    EXTRAS_DIR.mkdir(exist_ok=True)
    path = EXTRAS_DIR / nombre
    guardar_csv(path, tabla)
    return path


def main() -> int:
    args = parsear_argumentos()
    ejercicio = args.ejercicio
    tabla_salida = f"tabla_definitiva_irpf_{ejercicio}.csv"
    tabla_salida_opciones = f"tabla_opciones_irpf_{ejercicio}.csv"
    tabla_salida_minusvalias = f"tabla_minusvalias_computables_irpf_{ejercicio}.csv"

    print("=" * 70)
    print(f"IRPF — Tabla definitiva ejercicio {ejercicio} (BCE + FIFO, acciones y opciones)")
    print("=" * 70)

    try:
        print("\n[1/5] Leyendo operaciones...")
        archivos = resolver_archivos_entrada()
        print(f"  Ejercicio fiscal: {ejercicio}")
        print(f"  Archivos ({len(archivos)}):")
        for nombre in archivos:
            print(f"    • {nombre}")
        df_raw = leer_operaciones(archivos)

        df_stk_all = preparar_operaciones_stk(df_raw)
        df_opt_all = preparar_operaciones_opt(df_raw)

        if df_stk_all.empty and df_opt_all.empty:
            print("  (Aviso) No hay operaciones STK ni OPT en los archivos cargados")
        else:
            años = sorted(
                {
                    d.year
                    for serie in (df_stk_all, df_opt_all)
                    if not serie.empty
                    for d in serie["Fecha"]
                }
            )
            print(
                f"  Operaciones cargadas: {len(df_stk_all)} acciones, "
                f"{len(df_opt_all)} opciones ({años[0]}–{años[-1]})"
            )

        df_stk_cutoff = filtrar_hasta_ejercicio(df_stk_all, ejercicio)
        df_opt_cutoff = filtrar_hasta_ejercicio(df_opt_all, ejercicio)

        print("\n[2/5] Cargando tipos de cambio oficiales del BCE...")
        rates = cargar_tipos_bce(forzar_descarga=args.actualizar_bce)
        min_d, max_d = min(rates), max(rates)
        print(f"  Histórico BCE: {min_d} → {max_d} ({len(rates)} días)")

        print("\n[3/5] Calculando FIFO de opciones y detectando ejercicios/asignaciones...")
        (
            tabla_opciones,
            tabla_fifo_opciones,
            tabla_ops_opciones,
            tabla_abiertas_opciones,
            ajustes_pendientes,
            avisos_indice,
        ) = construir_tablas_opciones(df_opt_cutoff, rates, ejercicio)
        print(
            f"  Contratos con ejercicio/asignación detectado: {len(ajustes_pendientes)}"
        )
        if avisos_indice:
            print(
                f"  (Aviso) {len(avisos_indice)} opción(es) sobre índice con posible "
                "liquidación en efectivo no reflejada; revisa el informe de validación"
            )

        ajuste_serie, auditoria_ajustes = calcular_ajustes_stk_desde_opciones(
            df_stk_all, ajustes_pendientes
        )
        if not df_stk_all.empty:
            df_stk_all["AjusteOpcionesEUR"] = ajuste_serie
            df_stk_cutoff = filtrar_hasta_ejercicio(df_stk_all, ejercicio)
        no_aplicados = (
            auditoria_ajustes[auditoria_ajustes["Estado"].str.startswith("REVISAR")]
            if not auditoria_ajustes.empty
            else auditoria_ajustes
        )
        if not no_aplicados.empty:
            print(
                f"  (Aviso) {len(no_aplicados)} ejercicio(s)/asignación(es) de opción "
                "no se pudieron cruzar automáticamente con una operación de acciones; "
                "revisa extras/opciones_ejercicios_asignaciones.csv"
            )
            filas_sin_cruce = construir_filas_opciones_sin_cruce(
                ajustes_pendientes, auditoria_ajustes, ejercicio
            )
            if not filas_sin_cruce.empty:
                tabla_opciones = (
                    pd.concat([tabla_opciones, filas_sin_cruce], ignore_index=True)
                    if not tabla_opciones.empty
                    else filas_sin_cruce
                )

        print("\n[4/5] Calculando FIFO de acciones y conversión a euros...")
        (
            tabla_decl,
            tabla_fifo,
            tabla_ops,
            tabla_abiertas,
            tabla_minusvalias,
            tabla_minusvalias_auditoria,
        ) = construir_tablas_acciones(df_stk_cutoff, rates, ejercicio, df_stk_all)

        df_ejercicio = df_stk_cutoff[
            df_stk_cutoff["Fecha"].map(lambda d: d.year) == ejercicio
        ] if not df_stk_cutoff.empty else df_stk_cutoff
        resumen = resumen_divisa_operacion(df_ejercicio)

        if not df_stk_cutoff.empty:
            ventas_ej = df_stk_cutoff[
                (df_stk_cutoff["Side"] == "SELL")
                & (df_stk_cutoff["Fecha"].map(lambda d: d.year) == ejercicio)
            ]
            if ventas_ej.empty:
                print(f"  (Aviso) No hay ventas de acciones en {ejercicio} en los archivos cargados")

        print("\n[5/5] Resultados\n")

        print("=" * 70)
        print(f"TABLA DEFINITIVA — Acciones, ejercicio {ejercicio} (solo ventas de ese año)")
        print("Casillas: 0327 símbolo | 0328 transmisión | 0331 adquisición")
        print("=" * 70)
        avisos = validar_tabla_declaracion(tabla_decl, ejercicio)
        tabla_export = agregar_fila_total(tabla_decl)

        if tabla_decl.empty:
            print(
                f"  (No hay ventas de acciones en {ejercicio}; nada que declarar en 0327/0328/0331)"
            )
        else:
            print(tabla_export.to_string(index=False))

        guardar_csv(tabla_salida, tabla_export)

        if not tabla_decl.empty:
            print(
                f"\n  Suma ganancia/pérdida neta acciones {ejercicio}: "
                f"{tabla_decl[COL_GANANCIA].sum():.2f} €  "
                f"(fila TOTAL en el CSV; 0328/0331 no se suman entre empresas)"
            )

        print("\n" + "=" * 70)
        print(f"TABLA OPCIONES (calls/puts) — ejercicio {ejercicio}")
        print("NO son casillas 0327/0328/0331: ganancia patrimonial que no deriva de")
        print("transmisión de elementos patrimoniales (ver README / CONFORMIDAD_NORMATIVA)")
        print("=" * 70)
        tabla_opciones_export = agregar_fila_total_opciones(tabla_opciones)
        if tabla_opciones.empty:
            print("  (No hay opciones cerradas/vencidas sin ejercer en el ejercicio)")
        else:
            print(tabla_opciones_export.to_string(index=False))
        if not tabla_opciones.empty:
            guardar_csv(tabla_salida_opciones, tabla_opciones_export)
            print(f"\n  Suma ganancia/pérdida neta opciones {ejercicio}: "
                  f"{tabla_opciones[COL_OPC_GANANCIA].sum():.2f} €")

        print("\n" + "=" * 70)
        print("PÉRDIDAS COMPUTABLES — regla de los 2 meses (art. 33.5.f LIRPF)")
        print("Solo aparecen aquí las ventas con pérdida que SÍ puedes computar este")
        print("ejercicio. El resto (recompra homogénea en ±2 meses) NO es computable")
        print("todavía: ver extras/perdidas_analisis_regla_2_meses.csv")
        print("=" * 70)
        if tabla_minusvalias.empty:
            if tabla_minusvalias_auditoria.empty:
                print("  (No hay ventas con pérdida de acciones en el ejercicio)")
            else:
                print("  (Todas las pérdidas del ejercicio están bloqueadas por recompra; ver extras/)")
        else:
            print(tabla_minusvalias.to_string(index=False))
            guardar_csv(tabla_salida_minusvalias, tabla_minusvalias)

        avisos_opciones = []
        if not no_aplicados.empty:
            for _, r in no_aplicados.iterrows():
                avisos_opciones.append(
                    f"{r['Contrato']} ({r['Fecha ejercicio/asignación']}): {r['Estado']}"
                )
        avisos_regla_2_meses = avisos_symbolos_mixtos_regla_2_meses(
            tabla_fifo, tabla_minusvalias_auditoria
        )

        escribir_informe_validacion(
            ejercicio, archivos, avisos, tabla_decl, tabla_opciones,
            avisos_opciones, avisos_regla_2_meses, avisos_indice,
        )
        if avisos:
            print("\n  Avisos de validación (acciones):")
            for aviso in avisos:
                print(f"    • {aviso}")
        if avisos_opciones:
            print("\n  Avisos de opciones ejercidas/asignadas:")
            for aviso in avisos_opciones:
                print(f"    • {aviso}")
        if avisos_regla_2_meses:
            print("\n  Avisos de la regla de los 2 meses:")
            for aviso in avisos_regla_2_meses:
                print(f"    • {aviso}")
        if avisos_indice:
            print("\n  Avisos de opciones sobre índices:")
            for aviso in avisos_indice:
                print(f"    • {aviso}")

        extras_guardados: list[Path] = []
        for nombre, tabla in (
            ("detalle_fifo.csv", tabla_fifo),
            ("detalle_operaciones_bce.csv", tabla_ops),
            ("posiciones_abiertas.csv", tabla_abiertas),
            ("resumen_operaciones_divisa.csv", resumen),
            ("detalle_fifo_opciones.csv", tabla_fifo_opciones),
            ("detalle_operaciones_opciones_bce.csv", tabla_ops_opciones),
            ("posiciones_abiertas_opciones.csv", tabla_abiertas_opciones),
            ("opciones_ejercicios_asignaciones.csv", auditoria_ajustes),
            ("perdidas_analisis_regla_2_meses.csv", tabla_minusvalias_auditoria),
        ):
            path = guardar_extras(nombre, tabla)
            if path is not None:
                extras_guardados.append(path)
        informe = EXTRAS_DIR / "informe_validacion.txt"
        if informe.exists():
            extras_guardados.append(informe)

        print(f"\n💾 Tabla definitiva (acciones): {tabla_salida}")
        if not tabla_opciones.empty:
            print(f"💾 Tabla opciones: {tabla_salida_opciones}")
        if not tabla_minusvalias.empty:
            print(f"💾 Tabla minusvalías computables: {tabla_salida_minusvalias}")
        if extras_guardados:
            print(f"   Detalles (auditoría): {EXTRAS_DIR}/")
            for path in extras_guardados:
                print(f"     • {path.name}")

        return 0

    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 1
    except KeyError as exc:
        print(f"❌ {exc}")
        return 1
    except Exception as exc:
        print(f"❌ Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
