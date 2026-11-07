"""Caltrans highway traffic counts and freeway ramps.

Adapted from max/avg_daily_traffic.py and max/dc_centers_and_freeway_ramp.py.
"""

import json
import subprocess
import urllib.parse

import geopandas as gpd

from findit import CACHE_DIR
from findit.osm import elements_to_df, overpass
from findit.geo import to_gdf

CALTRANS_AADT_QUERY = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/"
    "Traffic_AADT/FeatureServer/0/query"
)
AADT_CACHE = CACHE_DIR / "caltrans_aadt.geojson"


def caltrans_aadt(page_size: int = 2000) -> gpd.GeoDataFrame:
    """Caltrans AADT count stations with a normalised `AADT` column (cached)."""
    if not AADT_CACHE.exists():
        offset, features = 0, []
        while True:
            qs = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "geojson",
                                         "resultOffset": offset, "resultRecordCount": page_size})
            # curl so TLS uses the macOS trust store (python.org builds often lack certs)
            proc = subprocess.run(["curl", "-sS", "-L", "--max-time", "120", f"{CALTRANS_AADT_QUERY}?{qs}"],
                                  check=True, capture_output=True, text=True)
            batch = json.loads(proc.stdout).get("features") or []
            features.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        AADT_CACHE.write_text(json.dumps({"type": "FeatureCollection", "features": features}))

    gdf = gpd.read_file(AADT_CACHE)
    if "AADT" not in gdf.columns:
        # Caltrans publishes BACK_AADT / AHEAD_AADT per station; take the larger
        gdf["AADT"] = gdf[[c for c in ("BACK_AADT", "AHEAD_AADT") if c in gdf.columns]].max(axis=1)
    return gdf


def freeway_ramps(bbox) -> gpd.GeoDataFrame:
    """Freeway on/off ramps (OSM motorway_link centres) inside bbox = (south, west, north, east)."""
    s, w, n, e = bbox
    query = f"""
    [out:json][timeout:180];
    way[highway=motorway_link]({s},{w},{n},{e});
    out center;
    """
    return to_gdf(elements_to_df(overpass(query)))
