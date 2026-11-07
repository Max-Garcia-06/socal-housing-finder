"""Folium map of ranked SB 4 sites: colour by rank, click for the site checklist."""

import html

import folium
import pandas as pd
from branca.colormap import LinearColormap

from findit import screens
from findit.maps import COLORS, base_map

STATUS_COLOR = {"pass": "#1a9850", "fail": "#d73027", "flag": "#f39c12", "unknown": "#8c8c8c"}
CHECKED_TESTS = ["flood", "fire", "transit", "freeway", "zoning"]


def site_map(ranked, bbox, zoom: int = 12) -> folium.Map:
    m = base_map(bbox, zoom=zoom)
    n = max(int(ranked["rank"].max()) if ranked["rank"].notna().any() else 1, 1)
    cmap = LinearColormap(COLORS, vmin=0, vmax=1, caption="Rank (green = build here first)")

    ranked_layer = folium.FeatureGroup(name="Ranked sites (pass every checked test)")
    failed_layer = folium.FeatureGroup(name="Fails flood or fire", show=True)
    for _, r in ranked.iterrows():
        failed = pd.isna(r["rank"])
        color = "#555555" if failed else cmap(1 - (r["rank"] - 1) / max(n - 1, 1))
        label = "not ranked" if failed else f"#{int(r['rank'])}"
        tip = f"{label} {r.get('name') or 'Unnamed site'}"
        folium.GeoJson(
            r.geometry.__geo_interface__,
            style_function=lambda _, c=color, f=failed: {
                "color": c, "fillColor": c, "weight": 2, "fillOpacity": 0.25 if f else 0.6,
                "dashArray": "4" if f else None},
            tooltip=tip,
            popup=folium.Popup(popup_html(r), max_width=460),
        ).add_to(failed_layer if failed else ranked_layer)
        if not failed and r["rank"] <= 10:
            pt = r.geometry.representative_point()
            folium.Marker([pt.y, pt.x], tooltip=tip, icon=folium.DivIcon(html=(
                f'<div style="font:bold 12px sans-serif;color:#fff;background:{color};border-radius:10px;'
                f'width:22px;height:20px;text-align:center;line-height:20px;border:1px solid #333">{int(r["rank"])}</div>'
            ))).add_to(ranked_layer)
    ranked_layer.add_to(m)
    failed_layer.add_to(m)
    cmap.add_to(m)
    folium.LayerControl().add_to(m)
    m.get_root().html.add_child(folium.Element(_legend()))
    return m


def popup_html(r) -> str:
    esc = lambda x: html.escape(str(x)) if pd.notna(x) else "?"
    head = (f"<b>{esc(r.get('name') or 'Unnamed site')}</b> "
            f"<span style='color:#666'>({esc(r.get('site_type'))}{', ' + esc(r['denomination']) if r.get('denomination') else ''})</span><br>")
    rank_line = "Not ranked" if pd.isna(r["rank"]) else f"Rank <b>#{int(r['rank'])}</b> · score {r['score']:.2f}"
    units = (f"~{r['units_parking_only']:.0f} homes on parking · up to ~{r['units_whole_site']:.0f} whole site "
             f"at {r['density_du_ac']:.0f}/acre (pre-design estimate)")
    rows = []
    cl = screens.checklist(r)
    for _, t in cl.iterrows():
        c = STATUS_COLOR.get(t["status"], "#000")
        src = f"<a href='{esc(t['url'])}' target='_blank'>source</a>" if t["url"] else ""
        rows.append(f"<tr><td>{esc(t['test'])}</td><td style='color:{c};font-weight:bold'>{esc(t['status'])}</td>"
                    f"<td>{esc(t['detail'])} {src}</td></tr>")
    table = ("<table style='font-size:11px;border-collapse:collapse' border='1' cellpadding='3'>"
             "<tr><th>Test</th><th>Result</th><th>Why</th></tr>" + "".join(rows) + "</table>")
    return (f"<div style='font-family:sans-serif;font-size:12px'>{head}{rank_line}<br>"
            f"<i>{esc(r['why'])}</i><br>{units}<br>APN {esc(r.get('apn'))} · zone {esc(r.get('zone_city'))}<br><br>"
            f"{table}<br><span style='color:#666'>Checked {screens.CHECKED}. Screening tool, not legal advice. "
            f"Ownership must be confirmed with the county assessor.</span></div>")


def _legend() -> str:
    return ("<div style='position:fixed;bottom:20px;left:20px;z-index:9999;background:#fff;padding:8px 10px;"
            "border:1px solid #999;font:12px sans-serif;max-width:260px'>"
            "<b>SB 4 site finder</b><br>Green = best first build: most homes on parking, no parking "
            "required near transit, higher need and tax-credit opportunity.<br>Dashed grey = fails flood or fire.<br>"
            "Click a site for its checklist and sources.</div>")
