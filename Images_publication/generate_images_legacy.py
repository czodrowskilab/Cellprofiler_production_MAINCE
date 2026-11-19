import re
from pathlib import Path
from playwright.sync_api import sync_playwright

html = Path("/mnt/czolab-storage-01/acruzcar/MAINCE_output/Cohort1/V01/QC/Collages/Cohort1_QC_report.html")
out_dir = Path("secciones")
out_dir.mkdir(exist_ok=True)

def slug(texto):
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")

def seleccionar_primero(page, plate_idx=0):
    """Selecciona el primer pozo (con montaje embebido) y su primer sitio."""
    page.wait_for_function("typeof currentPlate !== 'undefined' && currentPlate !== null")
    if plate_idx:
        page.evaluate("i => renderPlate(i)", plate_idx)
    sel = page.evaluate("""() => {
        const plate = currentPlate, pd = DATA[plate];
        // El montaje solo existe para pozos en flagged_b64 (marcados con los thresholds por defecto)
        const well = Object.keys(pd.flagged_b64).sort(_wellSort)[0];
        if (!well) return null;
        const site = Object.keys(pd.site_data[well]).sort((a, b) => a - b)[0];
        selectWell(plate, well);
        selectSite(plate, well, site);
        return {plate, well, site};
    }""")
    if sel is None:
        print("Aviso: ninguna pozo de esta placa tiene montaje; solo se selecciona el pozo.")
        sel = page.evaluate("""() => {
            const plate = currentPlate;
            const well = Object.keys(DATA[plate].wells).sort(_wellSort)[0];
            selectWell(plate, well);
            return {plate, well, site: null};
        }""")
    page.wait_for_selector(f"#sv-{sel['well']}.selected")
    if sel["site"] is not None:
        page.wait_for_selector(f"#site-{sel['site']}.selected")
    # Plotly y renderMetrics/renderCounts se dibujan en setTimeout
    page.wait_for_selector("#metrics-contents .js-plotly-plot")
    page.wait_for_timeout(1000)
    return sel

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1900, "height": 900},
        device_scale_factor=3,
    )
    page.goto(html.as_uri(), wait_until="load", timeout=120_000)
    page.wait_for_selector(".section")

    # Ocultar lo interactivo que no quieres en las imágenes
    page.add_style_tag(content=".h2-actions, .iv-mini-btn { display: none !important; }")

    sel = seleccionar_primero(page)
    print("seleccionado:", sel)

    secciones = page.locator(".section")
    for i in range(secciones.count()):
        sec = secciones.nth(i)
        h2 = sec.locator("h2")
        titulo = h2.first.inner_text() if h2.count() else f"seccion_{i+1}"
        ruta = out_dir / f"{i+1:02d}_{slug(titulo)}.png"
        sec.screenshot(path=str(ruta))
        print("guardado:", ruta)

    browser.close()