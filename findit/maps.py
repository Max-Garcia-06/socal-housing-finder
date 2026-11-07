"""Folium maps: scored grid heatmaps, tract choropleths, point markers.

Adapted from model/heatmap.py. Colours by percentile rank instead of raw score:
the old ranker scores all sat between 0.47 and 0.54, so a fixed 0-1 scale
painted nearly every cell the same colour.
"""

import folium
import geopandas as gpd
import pandas as pd
from branca.colormap import LinearColormap

COLORS = ["#d73027", "#fee08b", "#1a9850"]  # low (red) -> high (green)


def base_map(bbox, zoom: int = 12) -> folium.Map:
    s, w, n, e = bbox
    return folium.Map(location=[(s + n) / 2, (w + e) / 2], zoom_start=zoom, tiles="OpenStreetMap")


def grid_heatmap(scored: pd.DataFrame, bbox, n: int, score_col: str = "score",
                 m: folium.Map | None = None, opacity: float = 0.55) -> folium.Map:
    """Draw a findit.geo.grid(bbox, n) frame with a score column as coloured cells."""
    m = m or base_map(bbox)
    s, w, nth, e = bbox
    dlat, dlon = (nth - s) / (n - 1), (e - w) / (n - 1)
    pct = scored[score_col].rank(pct=True)
    cmap = LinearColormap(COLORS, vmin=0, vmax=1, caption=f"{score_col} (percentile)")

    for (_, row), p in zip(scored.iterrows(), pct):
        if pd.isna(p):
            continue
        folium.Rectangle(
            bounds=[[row.lat - dlat / 2, row.lon - dlon / 2], [row.lat + dlat / 2, row.lon + dlon / 2]],
            stroke=False, fill=True, fill_color=cmap(p), fill_opacity=opacity,
            tooltip=f"{score_col}: {row[score_col]:.3g} (p{p * 100:.0f})",
        ).add_to(m)
    cmap.add_to(m)
    return m


def choropleth(tracts: gpd.GeoDataFrame, value_col: str, bbox, label: str | None = None,
               m: folium.Map | None = None, opacity: float = 0.6) -> folium.Map:
    """Colour tract polygons by value_col (percentile scale), with hover tooltips."""
    m = m or base_map(bbox)
    gdf = tracts[["GEOID", value_col, "geometry"]].copy()
    gdf["_pct"] = gdf[value_col].rank(pct=True)
    cmap = LinearColormap(COLORS, vmin=0, vmax=1, caption=f"{label or value_col} (percentile)")
    folium.GeoJson(
        gdf,
        style_function=lambda f: {
            "fillColor": cmap(f["properties"]["_pct"]) if f["properties"]["_pct"] is not None else "#999999",
            "fillOpacity": opacity, "weight": 0.3, "color": "#555555",
        },
        tooltip=folium.GeoJsonTooltip(fields=["GEOID", value_col], aliases=["Tract", label or value_col]),
    ).add_to(m)
    cmap.add_to(m)
    return m


def add_markers(m: folium.Map, points: pd.DataFrame, label_col: str = "name",
                color: str = "blue", icon: str = "home") -> folium.Map:
    """Add a marker per row of a lat/lon frame."""
    for _, row in points.iterrows():
        folium.Marker(
            [row.lat, row.lon],
            tooltip=str(row.get(label_col, "")),
            icon=folium.Icon(color=color, icon=icon, prefix="fa"),
        ).add_to(m)
    return m
