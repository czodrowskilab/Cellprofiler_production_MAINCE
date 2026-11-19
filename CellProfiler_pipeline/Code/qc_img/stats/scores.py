"""
qc/stats/scores.py — Puntajes y escalas de color a nivel cohorte (Polars).

Porta _mad_scores, _median_cs y _red_wells (hoy en III_QC_collage/report) a
Polars vectorizado. Firma de entrada/salida en dict (compatible con
test_baseline.py y con report.js) mientras el pipeline no sea DataFrame
end-to-end; el puente dict→DataFrame→dict se elimina en Fase 3.

Cada función construye UN DataFrame long [plate, well, value] y computa
median/MAD por grupo con expresiones, en vez de los dobles/triples loops numpy.
"""
from __future__ import annotations

import polars as pl

from qc.stats import expressions as X
from qc.stats import thresholds as T

_MAD_TO_SIGMA = 1.4826


def _long_frame(plates_data: list, col: str) -> pl.DataFrame:
    """Aplana plates_data a [plate, well, cmpd, value] para una columna, sin nulls/NaN.

    cmpd es la perturbación del pozo según plate_map ('' si no hay platemap o el
    pozo no está mapeado).
    """
    rows = []
    for pd_ in plates_data:
        pname = pd_["name"]
        pmap = pd_.get("plate_map") or {}
        for well, m in pd_["plate_qc"].items():
            v = m.get(col)
            if v is not None and not (isinstance(v, float) and v != v):
                rows.append((pname, well, pmap.get(well, "") or "", float(v)))
    if not rows:
        return pl.DataFrame(schema={"plate": pl.Utf8, "well": pl.Utf8,
                                    "cmpd": pl.Utf8, "value": pl.Float64})
    return pl.DataFrame(rows, schema=["plate", "well", "cmpd", "value"], orient="row")


def mad_scores(col: str, plates_data: list) -> tuple[dict, float | None, float | None]:
    """Por well: valor + z contra placa (z_pl) y contra cohorte (z_co).

    El z se calcula SOLO contra pozos que comparten la misma perturbación:
      · z_pl → misma placa + misma perturbación
      · z_co → todas las placas + misma perturbación
    Un grupo con menos de MIN_GROUP_WELLS pozos no produce z (se devuelve None
    para esa mitad). Sin platemap todos los pozos comparten la perturbación ''
    y el cálculo equivale al anterior (placa completa / cohorte completa).

    Cada entrada incluye n_pl / n_co (tamaño del grupo de referencia). Los dos
    últimos valores devueltos son mediana/MAD de TODOS los pozos (informativos).
    """
    df = _long_frame(plates_data, col)
    if df.height < 4:
        return {}, None, None

    g_med = float(df["value"].median())
    g_mad = float((df["value"] - g_med).abs().median()) or 1.0

    min_n = T.MIN_GROUP_WELLS

    # Referencia de cohorte: por perturbación (todas las placas).
    per_cohort = df.group_by("cmpd").agg(
        pl.col("value").median().alias("_cmed"),
        (pl.col("value") - pl.col("value").median()).abs().median().alias("_cmad"),
        pl.len().alias("n_co"),
    )
    # Referencia de placa: por (placa, perturbación).
    per_plate = df.group_by(["plate", "cmpd"]).agg(
        pl.col("value").median().alias("_pmed"),
        (pl.col("value") - pl.col("value").median()).abs().median().alias("_pmad"),
        pl.len().alias("n_pl"),
    )
    df = df.join(per_cohort, on="cmpd").join(per_plate, on=["plate", "cmpd"])
    df = df.with_columns(
        pl.when(pl.col("_pmad") == 0).then(1.0).otherwise(pl.col("_pmad")).alias("_pmad"),
        pl.when(pl.col("_cmad") == 0).then(1.0).otherwise(pl.col("_cmad")).alias("_cmad"),
    ).with_columns(
        pl.when(pl.col("n_pl") >= min_n)
          .then(((pl.col("value") - pl.col("_pmed")) / pl.col("_pmad")).round(2))
          .otherwise(None).alias("z_pl"),
        pl.when(pl.col("n_co") >= min_n)
          .then(((pl.col("value") - pl.col("_cmed")) / pl.col("_cmad")).round(2))
          .otherwise(None).alias("z_co"),
        pl.col("value").round(5).alias("v"),
    )

    scores: dict = {}
    for row in df.iter_rows(named=True):
        scores.setdefault(row["plate"], {})[row["well"]] = {
            "v": row["v"], "z_pl": row["z_pl"], "z_co": row["z_co"],
            "n_pl": row["n_pl"], "n_co": row["n_co"],
        }
    return scores, round(g_med, 5), round(g_mad, 5)


def median_colorscale(col: str, plates_data: list) -> tuple:
    """Colorscale semáforo centrada en mediana cohorte ± 2/3·MAD. Porta _median_cs.

    Devuelve (cs, cmin, cmax) — cs es la lista Plotly de [pos, color], o
    ("RdBu_r", None, None) si hay <4 valores.
    """
    df = _long_frame(plates_data, col)
    if df.height < 4:
        return "RdBu_r", None, None

    arr = df["value"]
    med = float(arr.median())
    mad = float((arr - med).abs().median())
    lo2, lo3 = med - 2 * mad, med - 3 * mad
    hi2, hi3 = med + 2 * mad, med + 3 * mad
    cmin = max(float(arr.min()), lo3 * 0.95)
    cmax = min(float(arr.max()), hi3 * 1.05)
    rng = cmax - cmin
    if rng == 0:
        return "RdBu_r", cmin, cmax

    def pos(v):
        return round(max(0.0, min(1.0, (v - cmin) / rng)), 4)

    cs = [
        [0.0, "#ff4444"], [pos(lo3), "#ff4444"], [pos(lo3), "#ffbe00"],
        [pos(lo2), "#ffbe00"], [pos(lo2), "#4bd760"], [pos(hi2), "#4bd760"],
        [pos(hi2), "#ffbe00"], [pos(hi3), "#ffbe00"], [pos(hi3), "#ff4444"],
        [1.0, "#ff4444"],
    ]
    return cs, round(cmin, 5), round(cmax, 5)


def red_wells_absolute(plates_data: list, cols: list, thresholds: dict) -> dict:
    """Conteo de wells rojos por bordes ABSOLUTOS. Porta report._red_wells.

    Un well es rojo si CUALQUIER col rompe su (lo, hi). Usado por PctMax/PctMin.
    """
    result = {}
    for pd_ in plates_data:
        count = 0
        for well, m in pd_["plate_qc"].items():
            for col in cols:
                v = m.get(col)
                if v is None:
                    continue
                lo, hi = thresholds.get(col, (None, None))
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    count += 1
                    break
        result[pd_["name"]] = count
    return result


def red_wells_zscore(metric: str, channels: list, mad_score_cache: dict,
                     plates_data: list, z_thresh: float = 3.0) -> dict:
    """Conteo de wells rojos por z-score de cohorte. Porta report._zscore_red.

    Un well es rojo si |z_co| > z_thresh en cualquier canal. Usado por las
    métricas robustas (Slope, MedInt). mad_score_cache: {col: mad_scores(col)[0]}.
    """
    result = {}
    for pd_ in plates_data:
        pname = pd_["name"]
        count = 0
        for well in pd_["plate_qc"]:
            for ch in channels:
                col = f"ImageQuality_{metric}_{ch}"
                sc = mad_score_cache.get(col, {}).get(pname, {}).get(well)
                if sc and sc.get("z_co") is not None and abs(sc["z_co"]) > z_thresh:
                    count += 1
                    break
        result[pname] = count
    return result