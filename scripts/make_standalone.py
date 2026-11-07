"""Bundle web/index.html + data into one self-contained HTML (dist/common-ground.html).

Used for the hosted claude.ai artifact, which blocks remote map tiles and stylesheets:
the build inlines Leaflet's CSS and the data, and draws OC city outlines instead of tiles.
    uv run python scripts/make_standalone.py
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "web" / "index.html").read_text()
data = (ROOT / "web" / "data" / "sites.js").read_text()
bounds = json.loads((ROOT / "data" / "raw" / "city_bounds.geojson").read_text())
css = requests.get("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", timeout=60).text

html = html.replace('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">', f"<style>{css}</style>")
html = html.replace('<script src="data/sites.js"></script>',
                    "<script>" + data + "\nwindow.NO_TILES=true;\nwindow.CITY_BOUNDS=" + json.dumps(bounds) + ";</script>")
html = html.replace(".leaflet-container{background:#EDE9E0", ".leaflet-container{background:#DCE5E7")
out = ROOT / "dist" / "common-ground.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html)
print(out, f"{len(html) / 1e6:.1f} MB")
