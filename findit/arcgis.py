"""Bulk download from ArcGIS REST layers (FeatureServer / MapServer), cached.

One bbox query per layer, paged, saved as parquet under data/cache/, then all
spatial work happens locally. Retries because FEMA's NFHL often times out on
the first call.
"""

import hashlib
import time

import geopandas as gpd
import requests

from findit import CACHE_DIR

HEADERS = {"User-Agent": "socal-housing-finder (hackathon project)"}


def query(layer_url: str, bbox, where: str = "1=1", out_fields: str = "*",
          page_size: int | None = None, retries: int = 3, timeout: int = 120) -> gpd.GeoDataFrame:
    """Features of one layer intersecting bbox = (south, west, north, east), in EPSG:4326.

    page_size defaults to the layer's maxRecordCount. Empty layers return an
    empty GeoDataFrame with a geometry column.
    """
    s, w, n, e = bbox
    key = hashlib.md5(f"{layer_url}|{bbox}|{where}|{out_fields}".encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"arcgis_{key}.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)

    if page_size is None:
        meta = _get(f"{layer_url}", {"f": "json"}, retries, timeout)
        page_size = int(meta.get("maxRecordCount") or 1000)

    params = {
        "where": where,
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "outSR": 4326,
        "f": "geojson",
        "resultRecordCount": page_size,
    }
    features, offset = [], 0
    while True:
        batch = _get(f"{layer_url}/query", {**params, "resultOffset": offset}, retries, timeout)
        if "error" in batch:
            raise RuntimeError(f"{layer_url}: {batch['error']}")
        got = batch.get("features") or []
        features.extend(got)
        exceeded = batch.get("exceededTransferLimit") or batch.get("properties", {}).get("exceededTransferLimit")
        if len(got) < page_size and not exceeded:
            break
        if not got:
            break
        offset += len(got)

    if features:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    else:
        gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    gdf = gdf[gdf.geometry.notna()].reset_index(drop=True)
    gdf.to_parquet(cache)
    return gdf


def _get(url: str, params: dict, retries: int, timeout: int) -> dict:
    last = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} failed after {retries} tries: {last}")
