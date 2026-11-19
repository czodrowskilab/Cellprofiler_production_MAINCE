import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

html = Path("/mnt/czolab-storage-01/acruzcar/MAINCE_output/Cohort1/V01/QC/Collages/Cohort1_QC_report.html")
out_dir = Path("secciones")
out_dir.mkdir(exist_ok=True)

path_csv = Path("/mnt/czolab-storage-01/acruzcar/MAINCE_output/Cohort1/V01/CellProfiler_files/CSVs/III_QC.csv")
path_image = Path("/mnt/czolab-storage-01/acruzcar/MAINCE_Natural_products/Cohort1/JGU_Czodrowski_CW_20241126_Cohort1_P01_/untreated_data/001001-1-001002006.tif")

def slug(texto):
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(
        viewport={"width": 1800, "height": 900},
        device_scale_factor=3,
    )
    page.goto(html.as_uri(), wait_until="load", timeout=120_000)
    page.wait_for_selector(".section")
    
    well_sel = page.locator("rect#sv-A01")
    well_sel.click()

    site_sel = page.locator("rect#site-1")
    site_sel.click()

    page.set_input_files("#iv-csv-input", path_csv)

    expect(page.locator("#iv-files-label")).not_to_have_class(re.compile(r"iv-btn-disabled"))
    page.set_input_files("#iv-files-input", path_image)

    secciones = page.locator(".section")
    for i in range(secciones.count()):
        sec = secciones.nth(i)
        h2 = sec.locator("h2")
        titulo = h2.first.inner_text() if h2.count() else f"seccion_{i+1}"
        ruta = out_dir / f"{i+1:02d}_{slug(titulo)}.png"
        sec.screenshot(path=str(ruta))
        print("guardado:", ruta)

    browser.close()