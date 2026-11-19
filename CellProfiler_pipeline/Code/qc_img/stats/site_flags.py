"""
qc/stats/site_flags.py — Clasificación de sitios/pozos que fallan criterios de calidad.

Produce el parquet de SOLO los sitios fallidos (con los thresholds por defecto) y
expone las referencias de z robusto que el reporte HTML usa para reclasificar en
el navegador cuando el usuario cambia thresholds o criterios.

Criterios (ver qc.stats.thresholds para valores y reglas completas):
  Robustos (z robusto, Blur y MedianIntensity):
    · slope:<ch>   |z| > blur_z_fail    sobre ImageQuality_PowerLogLogSlope_<ch>
    · medint:<ch>  |z| > medint_z_fail  sobre ImageQuality_MedianIntensity_<ch>
  Absolutos (sitio):
    · pctmax:<ch>          ImageQuality_PercentMaximal_<ch> > pctmax_fail
    · Ratio_P10/P90        Ratio_P10/P90                    > p1090_fail
    · cells_nuclei_ratio   Count_Cells / Count_Nuclei       < ratio_min
    · low_cell_count       Count_Cells (sitio)              < cells_site_min
  Absoluto (pozo):
    · low_cell_count_well  suma de Count_Cells del pozo     < cells_well_min
                           (se marcan TODOS los sitios del pozo)

z robusto: z = (x - mediana) / MAD, MAD = mediana(|x - mediana|) (MAD crudo, sin
factor 1.4826, igual que scores.mad_scores). Se calcula SOLO contra sitios de
pozos que comparten la misma perturbación (compuesto del platemap):
    · z de placa   → misma placa + misma perturbación
    · z de cohorte → todas las placas + misma perturbación
Doble guardia: el sitio falla si |z_placa| > k O |z_cohorte| > k.
Un grupo con < MIN_GROUP_SITES sitios (un pozo completo = 9) no produce z (no se evalúa esa mitad de la
guardia). Sin platemap, todos los pozos comparten la perturbación "" y el cálculo
equivale a placa completa / cohorte completa.
"""
from __future__ import annotations

from statistics import median

from qc import profiles
from qc.stats import thresholds as T

ROBUST_METRICS = profiles.ROBUST_METRICS          # ("PowerLogLogSlope", "MedianIntensity")
_CRIT_NAME = {"PowerLogLogSlope": "slope", "MedianIntensity": "medint"}
# Familia (checkbox) a la que pertenece cada criterio emitido.
_FAMILY = {"slope": "slope", "medint": "medint", "pctmax": "debris",
           "Ratio_P10/P90": "ldens", "cells_nuclei_ratio": "ratio",
           "low_cell_count": "cells", "low_cell_count_well": "cells"}

CELLS_COL = profiles.COUNT_COLS["Cells"]          # Count_Cells
NUCLEI_COL = profiles.COUNT_COLS["Filtered"]      # Count_Nuclei


# ── Utilidades ───────────────────────────────────────────────────────────────
def _robust_col(metric: str, ch: str) -> str:
    return f"ImageQuality_{metric}_{ch}"


def _pctmax_col(ch: str) -> str:
    return f"ImageQuality_PercentMaximal_{ch}"


def _valid(v) -> bool:
    return v is not None and not (isinstance(v, float) and v != v)


def _th(th: dict | None) -> dict:
    """Thresholds efectivos: defaults + overrides."""
    out = dict(T.DEFAULTS)
    if th:
        out.update(th)
    return out


def _all_active() -> set:
    return {c["key"] for c in T.CRITERIA}


def compound_of(compound_map: dict | None, plate: str, well: str) -> str:
    """Perturbación de un pozo ('' si no hay platemap o el pozo no está mapeado)."""
    if not compound_map:
        return ""
    return (compound_map.get(plate) or {}).get(well, "") or ""


def _mad_floor(med: float) -> float:
    return max(T.MAD_FLOOR_FRAC * abs(med), T.MAD_FLOOR_ABS)


def _group_ref(vals: list):
    """(mediana, MAD_efectivo, n) de un grupo, o None si es demasiado pequeño.

    Se exigen al menos MIN_GROUP_SITES valores. MAD_efectivo = max(MAD, piso): el
    piso solo actúa cuando el MAD real es casi nulo en un grupo que SÍ tiene
    tamaño suficiente.
    """
    n = len(vals)
    if n < T.MIN_GROUP_SITES:
        return None
    med = median(vals)
    mad = median([abs(v - med) for v in vals])
    return med, max(mad, _mad_floor(med)), n


# ── Referencias de z (por perturbación) ──────────────────────────────────────
def robust_site_refs(site_qc: dict, profile: profiles.DatasetProfile,
                     compound_map: dict | None = None) -> dict:
    """Referencias (mediana, MAD, n) por grupo de perturbación.

    Returns {
      "cohort": {cmpd: {col: (med, mad, n)}},
      "plate":  {plate: {cmpd: {col: (med, mad, n)}}},
    }
    Solo contiene grupos que alcanzan el tamaño mínimo.
    """
    cols = [_robust_col(m, ch) for m in ROBUST_METRICS for ch in profile.channels]
    cohort_vals: dict = {}      # {cmpd: {col: [v]}}
    plate_vals: dict = {}       # {plate: {cmpd: {col: [v]}}}
    for plate, wells in site_qc.items():
        for well, fields in wells.items():
            cmpd = compound_of(compound_map, plate, well)
            c_bucket = cohort_vals.setdefault(cmpd, {})
            p_bucket = plate_vals.setdefault(plate, {}).setdefault(cmpd, {})
            for sv in fields.values():
                for col in cols:
                    v = sv.get(col)
                    if _valid(v):
                        c_bucket.setdefault(col, []).append(float(v))
                        p_bucket.setdefault(col, []).append(float(v))

    cohort: dict = {}
    for cmpd, by_col in cohort_vals.items():
        refs = {col: r for col, vals in by_col.items()
                if (r := _group_ref(vals)) is not None}
        if refs:
            cohort[cmpd] = refs
    plate: dict = {}
    for pname, by_cmpd in plate_vals.items():
        for cmpd, by_col in by_cmpd.items():
            refs = {col: r for col, vals in by_col.items()
                    if (r := _group_ref(vals)) is not None}
            if refs:
                plate.setdefault(pname, {})[cmpd] = refs
    return {"cohort": cohort, "plate": plate}


def z_coverage(site_qc: dict, compound_map: dict | None, refs: dict) -> dict:
    """Cuántos pozos tienen (o no) grupo de referencia suficiente. Para el reporte."""
    total = no_plate = no_cohort = no_any = unmapped = 0
    matched = any(bool(v) for v in (compound_map or {}).values())
    for plate, wells in site_qc.items():
        for well in wells:
            cmpd = compound_of(compound_map, plate, well)
            has_c = bool(refs["cohort"].get(cmpd))
            has_p = bool((refs["plate"].get(plate) or {}).get(cmpd))
            total += 1
            no_plate += not has_p
            no_cohort += not has_c
            no_any += (not has_p and not has_c)
            unmapped += (cmpd == "" and matched)
    return {"wells": total, "noPlateRef": no_plate, "noCohortRef": no_cohort,
            "noRef": no_any, "unmapped": unmapped,
            "matched": matched}


def z_scope_note(cov: dict) -> str:
    """Texto (EN) para el reporte: alcance del z y cobertura de referencias."""
    scope = ("same-perturbation wells only" if cov["matched"]
             else "no platemap found: all wells treated as one group")
    msg = (f"Blur / MedianIntensity z-scores use {scope}; a group needs at least "
           f"{T.MIN_GROUP_SITES} sites to produce a z-score.")
    if cov["wells"]:
        msg += (f" Wells without a plate-level reference: {cov['noPlateRef']}/{cov['wells']}; "
                f"without a cohort-level reference: {cov['noCohortRef']}/{cov['wells']}; "
                f"without either (Blur/MedianIntensity not evaluated): {cov['noRef']}/{cov['wells']}.")
    if cov["matched"] and cov["unmapped"]:
        msg += f" {cov['unmapped']} well(s) have no platemap entry and are compared among themselves."
    return msg


def refs_payload(refs: dict, plate_alias: dict | None = None) -> dict:
    """Serializa las refs para el frontend: valores [med, mad, n] redondeados.

    plate_alias: {nombre_en_qc_sites: nombre_en_payload} para que las claves de
    placa casen con DATA del reporte.
    """
    def _r(t):
        return [float(f"{t[0]:.6g}"), float(f"{t[1]:.6g}"), int(t[2])]

    alias = plate_alias or {}
    return {
        "cohort": {c: {col: _r(t) for col, t in by.items()}
                   for c, by in refs["cohort"].items()},
        "plate": {alias.get(p, p): {c: {col: _r(t) for col, t in by.items()}
                                    for c, by in by_c.items()}
                  for p, by_c in refs["plate"].items()},
    }


# ── Reglas de fallo ──────────────────────────────────────────────────────────
def robust_site_fail(metric: str, ch: str, value, plate: str, cmpd: str,
                     refs: dict, th: dict | None = None) -> bool:
    """True si el sitio falla la métrica robusta por z de PLACA o de COHORTE.

    Ambas referencias son de la misma perturbación (cmpd). Si falta una, esa
    mitad de la guardia no dispara.
    """
    if not _valid(value):
        return False
    col = _robust_col(metric, ch)
    k = _th(th)[T.z_fail_key(metric)]
    v = float(value)
    for ref in ((refs.get("cohort", {}).get(cmpd) or {}).get(col),
                ((refs.get("plate", {}).get(plate) or {}).get(cmpd) or {}).get(col)):
        if ref is not None:
            med, mad, _ = ref
            if abs((v - med) / mad) > k:
                return True
    return False


def well_cells_total(fields: dict):
    """Suma de Count_Cells de los sitios válidos del pozo (None si no hay)."""
    vals = [float(sv[CELLS_COL]) for sv in fields.values()
            if _valid(sv.get(CELLS_COL))]
    return sum(vals) if vals else None


def site_failed_criteria(site_vals: dict, profile: profiles.DatasetProfile,
                         robust_refs: dict, plate: str, cmpd: str = "",
                         th: dict | None = None, active: set | None = None) -> list:
    """Criterios a nivel de SITIO que rompe este sitio (vacía si pasa todo)."""
    t = _th(th)
    act = _all_active() if active is None else active
    failed = []

    for metric in ROBUST_METRICS:
        crit = _CRIT_NAME[metric]
        if crit not in act:
            continue
        for ch in profile.channels:
            if robust_site_fail(metric, ch, site_vals.get(_robust_col(metric, ch)),
                                plate, cmpd, robust_refs, t):
                failed.append(f"{crit}:{ch}")

    if "debris" in act:
        for ch in profile.channels:
            v = site_vals.get(_pctmax_col(ch))
            if _valid(v) and float(v) > t["pctmax_fail"]:
                failed.append(f"pctmax:{ch}")

    if "ldens" in act:
        v = site_vals.get("Ratio_P10/P90")
        if _valid(v) and float(v) > t["p1090_fail"]:
            failed.append("Ratio_P10/P90")

    cells, nuclei = site_vals.get(CELLS_COL), site_vals.get(NUCLEI_COL)
    if "ratio" in act and _valid(cells) and _valid(nuclei) and float(nuclei) > 0:
        if float(cells) / float(nuclei) < t["ratio_min"]:
            failed.append("cells_nuclei_ratio")

    if "cells" in act and _valid(cells) and float(cells) < t["cells_site_min"]:
        failed.append("low_cell_count")

    return failed


def well_failed_criteria(fields: dict, th: dict | None = None,
                         active: set | None = None) -> list:
    """Criterios a nivel de POZO (aplican a todos sus sitios)."""
    t = _th(th)
    act = _all_active() if active is None else active
    if "cells" in act:
        total = well_cells_total(fields)
        if total is not None and total < t["cells_well_min"]:
            return ["low_cell_count_well"]
    return []


def well_family_failures(fields: dict, plate: str, cmpd: str,
                         profile: profiles.DatasetProfile, robust_refs: dict,
                         th: dict | None = None, active: set | None = None) -> set:
    """Familias de criterio (slope, medint, debris, ldens, ratio, cells) que
    falla un pozo: cualquier sitio que falle, o un criterio de pozo."""
    fams = set()
    for sv in fields.values():
        for crit in site_failed_criteria(sv, profile, robust_refs, plate, cmpd, th, active):
            fams.add(_FAMILY[crit.split(":")[0]])
    for crit in well_failed_criteria(fields, th, active):
        fams.add(_FAMILY[crit])
    return fams


def well_robust_verdict(site_qc: dict, profile: profiles.DatasetProfile,
                        robust_refs: dict | None = None,
                        compound_map: dict | None = None,
                        th: dict | None = None) -> dict:
    """Veredicto por pozo del PEOR sitio para las métricas robustas.

    Un pozo falla una métrica si CUALQUIER sitio suyo la falla (doble guardia
    z placa/cohorte, misma perturbación). Es la regla que el collage y el
    overview PNG usan para el borde del tile.

    Returns {plate: {well: {metric: bool}}}, metric ∈ ROBUST_METRICS.
    """
    if robust_refs is None:
        robust_refs = robust_site_refs(site_qc, profile, compound_map)
    out: dict = {}
    for plate, wells in site_qc.items():
        out[plate] = {}
        for well, fields in wells.items():
            cmpd = compound_of(compound_map, plate, well)
            wv = {}
            for metric in ROBUST_METRICS:
                wv[metric] = any(
                    robust_site_fail(metric, ch, sv.get(_robust_col(metric, ch)),
                                     plate, cmpd, robust_refs, th)
                    for ch in profile.channels for sv in fields.values())
            out[plate][well] = wv
    return out


# ── Parquet de sitios fallidos ───────────────────────────────────────────────
def flagged_sites_rows(site_qc: dict, profile: profiles.DatasetProfile,
                       compound_map: dict | None = None,
                       th: dict | None = None, active: set | None = None,
                       robust_refs: dict | None = None) -> list:
    """Filas (dict) de SOLO los sitios fallidos: metadatos + mediciones + fail +
    failed_criteria. Incluye los sitios de pozos que fallan un criterio de pozo.
    """
    if robust_refs is None:
        robust_refs = robust_site_refs(site_qc, profile, compound_map)
    rows = []
    for plate, wells in site_qc.items():
        for well, fields in wells.items():
            cmpd = compound_of(compound_map, plate, well)
            well_fail = well_failed_criteria(fields, th, active)
            for field, sv in fields.items():
                failed = site_failed_criteria(sv, profile, robust_refs, plate,
                                              cmpd, th, active) + well_fail
                if not failed:
                    continue
                row = {"Metadata_Plate": plate, "Metadata_Well": well,
                       "Metadata_Field": field, "Perturbation": cmpd}
                row.update(sv)
                row["fail"] = True
                row["failed_criteria"] = failed
                rows.append(row)
    return rows


def write_flagged_sites_parquet(site_qc: dict, profile: profiles.DatasetProfile,
                                out_path, compound_map: dict | None = None,
                                th: dict | None = None,
                                robust_refs: dict | None = None) -> int:
    """Escribe el parquet de sitios fallidos. Devuelve el nº de filas escritas.

    Columnas: Metadata_Plate/Well/Field, Perturbation, mediciones (ordenadas),
    fail (bool), failed_criteria (list[str]).
    """
    import polars as pl

    rows = flagged_sites_rows(site_qc, profile, compound_map, th,
                              robust_refs=robust_refs)
    fixed = ("Metadata_Plate", "Metadata_Well", "Metadata_Field", "Perturbation",
             "fail", "failed_criteria")
    if not rows:
        pl.DataFrame(schema={
            "Metadata_Plate": pl.Utf8, "Metadata_Well": pl.Utf8,
            "Metadata_Field": pl.Int64, "Perturbation": pl.Utf8,
            "fail": pl.Boolean, "failed_criteria": pl.List(pl.Utf8)}
        ).write_parquet(out_path)
        return 0

    meas_cols = sorted({k for r in rows for k in r if k not in fixed})
    ordered = ["Metadata_Plate", "Metadata_Well", "Metadata_Field", "Perturbation",
               *meas_cols, "fail", "failed_criteria"]
    df = pl.DataFrame([{c: r.get(c) for c in ordered} for r in rows], schema=ordered)
    df.write_parquet(out_path)
    return df.height