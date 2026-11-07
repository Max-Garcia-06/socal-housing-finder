"""Distance, nearest-neighbour and grid helpers.

Haversine and nearest-competitor logic adapted from justin/osm.py and
max/dc_centers_and_freeway_ramp.py; BallTree replaces the O(n*m) loops.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _tree(targets: pd.DataFrame) -> BallTree:
    return BallTree(np.radians(targets[["lat", "lon"]].to_numpy()), metric="haversine")


def nearest_k(points: pd.DataFrame, targets: pd.DataFrame, k: int = 1):
    """Distances (km) and target indices of the k nearest targets for each point.

    Both frames need `lat` and `lon` columns. Returns (dist_km, idx), each of
    shape (len(points), k).
    """
    k = min(k, len(targets))
    dist, idx = _tree(targets).query(np.radians(points[["lat", "lon"]].to_numpy()), k=k)
    return dist * EARTH_RADIUS_KM, idx


def count_within(points: pd.DataFrame, targets: pd.DataFrame, radius_km: float) -> np.ndarray:
    """Number of targets within radius_km of each point."""
    return _tree(targets).query_radius(
        np.radians(points[["lat", "lon"]].to_numpy()), r=radius_km / EARTH_RADIUS_KM, count_only=True
    )


def add_proximity_features(points: pd.DataFrame, targets: pd.DataFrame, name: str,
                           k: int = 5, radius_km: float = 1.0) -> pd.DataFrame:
    """Add nearest / avg-of-k distance and count-within-radius columns for one target layer.

    e.g. add_proximity_features(grid, bus_stops, "bus_stop") adds
    `bus_stop_nearest_km`, `bus_stop_avg5_km`, `bus_stop_within_1km`.
    """
    out = points.copy()
    dist, _ = nearest_k(out, targets, k)
    out[f"{name}_nearest_km"] = dist[:, 0]
    out[f"{name}_avg{dist.shape[1]}_km"] = dist.mean(axis=1)
    out[f"{name}_within_{radius_km:g}km"] = count_within(out, targets, radius_km)
    return out


def grid(bbox, n: int = 40) -> pd.DataFrame:
    """n x n grid of cell-centre points over bbox = (south, west, north, east)."""
    south, west, north, east = bbox
    lats = np.linspace(south, north, n)
    lons = np.linspace(west, east, n)
    lat, lon = np.meshgrid(lats, lons, indexing="ij")
    return pd.DataFrame({"lat": lat.ravel(), "lon": lon.ravel()})


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """lat/lon DataFrame to a WGS84 GeoDataFrame."""
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")


def snap_nearest(points: pd.DataFrame, layer: gpd.GeoDataFrame, cols: list[str], dist_col: str) -> pd.DataFrame:
    """Attach `cols` from the nearest feature in `layer`, plus distance in metres.

    Works for points, lines or polygons in `layer` (e.g. Caltrans AADT stations).
    """
    pts = to_gdf(points.reset_index(drop=True)).to_crs(epsg=3310)  # CA Albers, metres
    joined = gpd.sjoin_nearest(pts, layer[cols + ["geometry"]].to_crs(epsg=3310),
                               how="left", distance_col=dist_col)
    # Equidistant ties duplicate rows; keep the first match per point
    joined = joined[~joined.index.duplicated(keep="first")]
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"]))
