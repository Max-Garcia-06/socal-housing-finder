"""OpenStreetMap points of interest via Overpass.

Adapted from justin/in_n_out_locations.py and justin/rejected_locations.py.
"""

import hashlib
import json

import pandas as pd
import requests

from findit import CACHE_DIR

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "socal-housing-finder (hackathon project)"}

# Overpass tag filters for amenities that matter when choosing where to live
AMENITIES = {
    "bus_stop": '["highway"="bus_stop"]',
    "rail_station": '["railway"="station"]',
    "grocery": '["shop"~"supermarket|grocery"]',
    "school": '["amenity"="school"]',
    "clinic": '["amenity"~"clinic|doctors|hospital"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "park": '["leisure"="park"]',
    "library": '["amenity"="library"]',
    "childcare": '["amenity"="childcare"]',
    "social_facility": '["amenity"="social_facility"]',
}


def overpass(query: str, timeout: int = 180) -> list[dict]:
    """Run a raw Overpass QL query and return its elements (cached by query text)."""
    cache = CACHE_DIR / f"overpass_{hashlib.md5(query.encode()).hexdigest()[:12]}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    resp = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    cache.write_text(json.dumps(elements))
    return elements


def elements_to_df(elements: list[dict]) -> pd.DataFrame:
    """Flatten Overpass elements to lat/lon rows; ways/relations use their centre."""
    rows = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_type": el["type"],
            "osm_id": el["id"],
            "name": tags.get("name", tags.get("brand", "")),
            "lat": lat,
            "lon": lon,
            "street": tags.get("addr:street", ""),
            "housenumber": tags.get("addr:housenumber", ""),
            "city": tags.get("addr:city", ""),
            "postcode": tags.get("addr:postcode", ""),
            "tags": tags,
        })
    return pd.DataFrame(rows)


def pois(tag_filter: str, bbox) -> pd.DataFrame:
    """All nodes/ways/relations matching an Overpass tag filter inside bbox = (south, west, north, east).

    e.g. pois('["amenity"="school"]', IRVINE_BBOX) or pois(AMENITIES["grocery"], bbox)
    """
    s, w, n, e = bbox
    query = f"""
    [out:json][timeout:180];
    (
      node{tag_filter}({s},{w},{n},{e});
      way{tag_filter}({s},{w},{n},{e});
      relation{tag_filter}({s},{w},{n},{e});
    );
    out center tags;
    """
    return elements_to_df(overpass(query))


def polygons(tag_filter: str, bbox):
    """Closed ways matching a tag filter inside bbox, as a WGS84 GeoDataFrame with `area_acres`.

    Multipolygon relations are skipped; most church lots and campuses are plain ways.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    s, w, n, e = bbox
    query = f"""
    [out:json][timeout:180];
    way{tag_filter}({s},{w},{n},{e});
    out geom tags;
    """
    rows = []
    for el in overpass(query):
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
        if len(pts) < 4 or pts[0] != pts[-1]:
            continue
        tags = el.get("tags", {})
        rows.append({"osm_id": el["id"], "name": tags.get("name", ""), "tags": tags, "geometry": Polygon(pts)})
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf["area_acres"] = gdf.to_crs(epsg=3310).area / 4046.86
    return gdf


def first_seen_date(osm_type: str, osm_id: int) -> str:
    """Date an OSM element was first mapped (from its edit history), YYYY-MM-DD."""
    resp = requests.get(f"https://api.openstreetmap.org/api/0.6/{osm_type}/{osm_id}/history.json",
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    versions = resp.json().get("elements", [])
    return versions[0].get("timestamp", "")[:10] if versions else ""
