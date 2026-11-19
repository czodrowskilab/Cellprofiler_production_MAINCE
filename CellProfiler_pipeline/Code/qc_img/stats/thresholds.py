"""
qc/stats/thresholds.py — Fuente ÚNICA de thresholds y criterios de calidad.

Antes los cortes estaban repartidos en cuatro sitios que podían desincronizarse
(site_flags.py, colors.py, report.js y valores literales en renderCounts). Ahora:

  · DEFAULTS      valores por defecto. Python los usa para el parquet de sitios
                  fallidos y los conteos del PDF; el reporte HTML los recibe en el
                  payload como valores iniciales de los controles editables.
  · UI_SPEC       metadatos para dibujar los controles (grupo, etiqueta, límites).
  · CRITERIA      familias de criterio que se pueden activar/desactivar.
  · MIN_GROUP_*   tamaño mínimo de grupo para calcular un z robusto.

Reglas de clasificación (a nivel de SITIO; un pozo falla si falla cualquiera de
sus sitios, salvo los criterios declarados "de pozo"):

  blur      |z| > blur_z_fail      sobre ImageQuality_PowerLogLogSlope_<ch>
  medint    |z| > medint_z_fail    sobre ImageQuality_MedianIntensity_<ch>
            z = (x - mediana) / MAD del grupo de referencia (ver abajo). Doble
            guardia: falla si |z_placa| > k  O  |z_cohorte| > k.
  debris    ImageQuality_PercentMaximal_<ch> > pctmax_fail
  ldens     Ratio_P10/P90 > p1090_fail
  ratio     Count_Cells / Count_Nuclei < ratio_min
  cells     Count_Cells (sitio) < cells_site_min
            Count_Cells (suma del pozo) < cells_well_min   [criterio de pozo]

Los umbrales *_warn solo afectan al color (ámbar); no marcan fallo.

Grupo de referencia del z (Blur y MedianIntensity)
--------------------------------------------------
El z se calcula SOLO contra pozos que comparten la misma perturbación (compuesto
del platemap):
  · z de placa   → sitios de los pozos de ESA placa con la misma perturbación.
  · z de cohorte → sitios de los pozos de TODAS las placas con esa perturbación.
Si no hay platemap, todos los pozos comparten una única perturbación "" y el
cálculo equivale al anterior (placa completa / cohorte completa).
Un grupo con < MIN_GROUP_SITES sitios (o < MIN_GROUP_WELLS pozos a nivel de
pozo) NO produce z: esa mitad de la doble guardia no se evalúa y el reporte lo
cuantifica. No se rellena con el piso de MAD, porque con grupos tan pequeños el
piso convertiría variaciones triviales en fallos.
"""
from __future__ import annotations

# ── Valores por defecto ──────────────────────────────────────────────────────
DEFAULTS: dict = {
    "blur_z_warn": 2.5,
    "blur_z_fail": 3.0,
    "medint_z_warn": 3.0,
    "medint_z_fail": 5.0,
    "pctmax_warn": 0.1,
    "pctmax_fail": 1.0,
    "p1090_warn": 0.5,
    "p1090_fail": 0.55,
    "ratio_min": 0.95,
    "cells_site_min": 70,
    "cells_well_min": 1000,
}

# ── Tamaño mínimo de grupo de referencia ─────────────────────────────────────
MIN_GROUP_SITES = 9      # z por sitio: basta un pozo completo (9 sitios); con n réplicas
                         # técnicas el grupo tiene 9*n sitios
MIN_GROUP_WELLS = 4      # z por pozo (heatmaps/tooltips): se exigen >= 4 pozos

# Piso de MAD (solo para grupos que SÍ alcanzan el tamaño mínimo): evita que un
# MAD casi nulo convierta variaciones triviales en z gigantes.
MAD_FLOOR_FRAC = 0.02    # 2% de |mediana|
MAD_FLOOR_ABS = 1e-9     # mínimo absoluto para mediana ~ 0

# ── Criterios activables (clave = value de los checkboxes del frontend) ──────
CRITERIA: list = [
    {"key": "slope",  "label": "Blur"},
    {"key": "debris", "label": "Debris"},
    {"key": "ldens",  "label": "Low cell density"},
    {"key": "medint", "label": "MedianIntensity"},
    {"key": "ratio",  "label": "Cells/Nuclei ratio"},
    {"key": "cells",  "label": "Cell count"},
]

# ── Metadatos de los controles (orden = orden en pantalla) ───────────────────
UI_SPEC: list = [
    {"group": "Blur (PowerLogLogSlope)", "criterion": "slope", "fields": [
        {"key": "blur_z_warn", "label": "Warning |z| >", "step": 0.5, "min": 0, "max": 50,
         "help": "Amber above this robust z-score."},
        {"key": "blur_z_fail", "label": "Fail |z| >", "step": 0.5, "min": 0, "max": 50,
         "help": "A site fails when |z| exceeds this vs the plate OR the cohort "
                 "(same perturbation only)."},
    ]},
    {"group": "MedianIntensity", "criterion": "medint", "fields": [
        {"key": "medint_z_warn", "label": "Warning |z| >", "step": 0.5, "min": 0, "max": 50,
         "help": "Amber above this robust z-score."},
        {"key": "medint_z_fail", "label": "Fail |z| >", "step": 0.5, "min": 0, "max": 50,
         "help": "A site fails when |z| exceeds this vs the plate OR the cohort "
                 "(same perturbation only)."},
    ]},
    {"group": "Debris (PercentMaximal)", "criterion": "debris", "fields": [
        {"key": "pctmax_warn", "label": "Warning >", "step": 0.05, "min": 0, "max": 100,
         "help": "Amber above this % of saturated pixels."},
        {"key": "pctmax_fail", "label": "Fail >", "step": 0.1, "min": 0, "max": 100,
         "help": "A site fails when any channel exceeds this % of saturated pixels."},
    ]},
    {"group": "Low cell density (Ratio P10/P90)", "criterion": "ldens", "fields": [
        {"key": "p1090_warn", "label": "Warning >", "step": 0.05, "min": 0, "max": 1,
         "help": "Amber above this Syto P10/P90 ratio."},
        {"key": "p1090_fail", "label": "Fail >", "step": 0.05, "min": 0, "max": 1,
         "help": "A site fails when the Syto P10/P90 ratio exceeds this value."},
    ]},
    {"group": "Cells / Nuclei ratio", "criterion": "ratio", "fields": [
        {"key": "ratio_min", "label": "Fail <", "step": 0.01, "min": 0, "max": 1,
         "help": "A site fails when Count_Cells / Count_Nuclei is below this value."},
    ]},
    {"group": "Cell count", "criterion": "cells", "fields": [
        {"key": "cells_site_min", "label": "Min cells per site", "step": 5, "min": 0, "max": 100000,
         "help": "A site fails when Count_Cells is below this value."},
        {"key": "cells_well_min", "label": "Min cells per well", "step": 50, "min": 0, "max": 1000000,
         "help": "A well fails (all its sites are listed) when its summed Count_Cells "
                 "is below this value."},
    ]},
]


def payload_spec() -> dict:
    """Estructura JSON-ready para el frontend (defaults + metadatos + reglas)."""
    return {
        "defaults": dict(DEFAULTS),
        "ui": UI_SPEC,
        "criteria": CRITERIA,
        "minGroupSites": MIN_GROUP_SITES,
        "minGroupWells": MIN_GROUP_WELLS,
        "madFloorFrac": MAD_FLOOR_FRAC,
        "madFloorAbs": MAD_FLOOR_ABS,
    }


def z_fail_key(metric: str) -> str:
    """Clave del corte de fallo de una métrica robusta."""
    return "blur_z_fail" if metric == "PowerLogLogSlope" else "medint_z_fail"


def z_warn_key(metric: str) -> str:
    return "blur_z_warn" if metric == "PowerLogLogSlope" else "medint_z_warn"