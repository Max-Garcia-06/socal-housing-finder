"""SB 4 site screens: one pass / fail / flag / unknown per statutory test, with sources.

Input is any GeoDataFrame of candidate sites (polygons or points, EPSG:4326),
e.g. from findit.yigby.candidate_sites. Each test adds `<test>_status` and
`<test>_detail` columns. Rules follow Gov. Code § 65913.16 as summarised in the
HCD SB 4 fact sheet (April 2026); the exclusion list is borrowed from
§ 65913.4(a)(6). This is a screening tool, not a legal determination.

Status meanings:
    pass     checked, the site clears the test
    fail     checked, a meaningful share of the site is in an excluded area
    flag     checked, a sliver touches an excluded area, or a cost/permit issue
             (not a disqualifier on its own; needs a closer look)
    unknown  no verified free data layer, so not checked
"""

from concurrent.futures import ThreadPoolExecutor
import zipfile

import geopandas as gpd
import pandas as pd
import requests

from findit import CACHE_DIR
from findit.arcgis import query

CHECKED = "2026-11-07"
EQUAL_AREA = 3310  # California Albers, metres

# Share of a site's area inside an excluded zone that counts as "fail".
# Below it (but above zero) is a "flag": OSM outlines are not survey-grade, so a
# few metres of overlap at the edge is usually mapping noise. ASSUMPTION.
FAIL_SHARE = 0.10

# Within 500 ft of a freeway: MERV-13 filtration required (cost, not exclusion)
FREEWAY_BUFFER_M = 152.4

SOURCES = {
    "flood": ("FEMA National Flood Hazard Layer (layer 28)",
              "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"),
    "flood_fallback": ("OC Floodplains (county copy of FEMA zones)",
                       "https://www.ocgis.com/arcpub/rest/services/Map_Layers/Floodplains/MapServer/0"),
    "fire_lra": ("OC Fire Hazard Severity Zones, Local Responsibility Area",
                 "https://www.ocgis.com/arcpub/rest/services/Map_Layers/Fire_Hazard_Severity_Zones_LRA/MapServer/0"),
    "fire_sra": ("CAL FIRE Fire Hazard Severity Zones, State Responsibility Area (2007)",
                 "https://services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones/MapServer/0"),
    "transit": ("Caltrans California High Quality Transit Areas",
                "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHrailroad/CA_HQ_Transit_Areas/FeatureServer/0"),
    "freeway": ("Caltrans CRS Functional Classification (F_System 1-2: interstates, freeways, expressways)",
                "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/CRS_Functional_Classification/FeatureServer/0"),
    "zoning": ("SCAG Zoning_poly_OR (2024 parcel zoning)",
               "https://maps.scag.ca.gov/scaggis/rest/services/LDX/Zoning_poly_OR/MapServer/0"),
    "calenviroscreen": ("CalEnviroScreen 5.0 results (OEHHA)",
                        "https://services1.arcgis.com/PCHfdHz4GlDNAhBb/arcgis/rest/services/calenviroscreen50results_F_070126_gdb/FeatureServer/2"),
    "opportunity": ("CTCAC/HCD 2026 Opportunity Map",
                    "https://www.treasurer.ca.gov/sites/default/files/2025-11/shapefile.zip"),
}

# Statutory tests with no verified free layer. Same for every site, so they never
# change the ranking, but they are shown so nobody mistakes "not checked" for "pass".
NOT_CHECKED = {
    "ownership": "Owned by a religious institution or independent college on 2024-01-01. "
                 "Free parcel data has no owner field; OSM worship tag is a lead only. Confirm with assessor or title.",
    "fault_zone": "Alquist-Priolo earthquake fault zone. CGS layer blocks automated access.",
    "wetlands": "Wetlands (USFWS NWI) not yet wired in.",
    "farmland": "Prime or statewide-importance farmland (DOC FMMP) not yet wired in.",
    "habitat": "Conservation easement, NCCP/HCP land or protected-species habitat not checked.",
    "hazardous_site": "Cortese-list hazardous waste site (DTSC EnviroStor / SWRCB GeoTracker) not checked.",
    "industrial_buffer": "Distance to heavy industrial (1,200 ft), Title V (1,600 ft) and oil and gas (3,200 ft) not checked.",
    "tenants": "No demolition of rent-restricted or recently tenant-occupied housing: needs city records.",
    "historic": "Demolition of a historic structure: needs city or state register lookup.",
    "urban_location": "In an urbanized area with 75% of the perimeter adjoining urban uses: not checked.",
}

# SCAG ZN24_SCAG categories (first two digits) where housing is a permitted use:
# 11xx residential, 16xx mixed residential and commercial. ASSUMPTION: codes read
# against SCAG's land-use scheme and spot-checked on Irvine parcels (2.2 low-density
# residential -> 1110, 3.1 multi-use -> 1600, 5.4 industrial -> 1300).
RESIDENTIAL_ZONE_PREFIXES = ("11", "16")


def screen(sites: gpd.GeoDataFrame, bbox) -> gpd.GeoDataFrame:
    """Run every SB 4 screen on sites inside bbox = (south, west, north, east)."""
    out = sites.copy()
    if out.crs is None:
        out = out.set_crs(4326)
    out = out.to_crs(4326)
    out["site_area_m2"] = out.to_crs(EQUAL_AREA).area

    out = flood(out, bbox)
    out = fire(out, bbox)
    out = transit(out, bbox)
    out = freeway(out, bbox)
    out = zoning(out)
    out = tract_context(out, bbox)
    for test, why in NOT_CHECKED.items():
        out[f"{test}_status"] = "unknown"
        out[f"{test}_detail"] = why
    return summarize(out)


# ---------------------------------------------------------------- tests

def flood(sites, bbox):
    """FEMA special flood hazard area or regulatory floodway: excluded.

    SB 4 allows a flood-zone site only with a FEMA LOMR or proof it meets NFIP
    floodplain management rules, so a "fail" here means "fail unless mitigated".
    """
    try:
        zones = query(SOURCES["flood"][1], bbox, where="SFHA_TF='T'",
                      out_fields="FLD_ZONE,ZONE_SUBTY,SFHA_TF")
        src = "flood"
    except RuntimeError:
        zones = query(SOURCES["flood_fallback"][1], bbox, where="SFHA_TF='T'",
                      out_fields="FLD_ZONE,ZONE_SUBTY,SFHA_TF")
        src = "flood_fallback"
    zones = zones.assign(label=zones["FLD_ZONE"] + zones["ZONE_SUBTY"].fillna("").map(
        lambda s: " floodway" if "FLOODWAY" in s.upper() else ""))
    share, labels = _overlap(sites, zones)
    status = _share_status(share, sites)
    detail = [
        f"{s:.0%} of site in Zone {l} (excluded unless FEMA LOMR or NFIP compliance)" if s > 0
        else "Not in a FEMA special flood hazard area"
        for s, l in zip(share, labels)
    ]
    return _set(sites, "flood", status, detail, src)


def fire(sites, bbox):
    """Very high fire hazard severity zone (LRA or SRA): excluded."""
    lra = query(SOURCES["fire_lra"][1], bbox, out_fields="HAZ_CLASS")
    sra = query(SOURCES["fire_sra"][1], bbox, out_fields="HAZ_CLASS")
    vh = pd.concat([lra, sra], ignore_index=True)
    vh = gpd.GeoDataFrame(vh[vh["HAZ_CLASS"].eq("Very High")], crs=4326).assign(label="Very High")
    share, _ = _overlap(sites, vh)
    status = _share_status(share, sites)
    detail = [
        f"{s:.0%} of site in a Very High fire hazard zone (excluded)" if s > 0
        else "Not in a mapped Very High fire hazard zone (county LRA layer; CAL FIRE's 2025 LRA update may differ)"
        for s in share
    ]
    return _set(sites, "fire", status, detail, "fire_lra")


def transit(sites, bbox):
    """Parking waiver: no parking required within 1/2 mile of high-quality transit.

    Caltrans HQTA polygons are already the 1/2-mile area around major stops and
    corridors, measured in a straight line. SB 4 says walking distance, so a
    site at the edge may not qualify.
    """
    hq = query(SOURCES["transit"][1], bbox, out_fields="hqta_type,agency_primary")
    hits = _sjoin(sites, hq.assign(label=hq["hqta_type"].str.replace("_", " ") + " (" + hq["agency_primary"] + ")"))
    labels = hits.groupby(level=0)["label"].agg(lambda s: "; ".join(sorted(set(s))[:2]))
    out = sites.copy()
    out["parking_waiver"] = out.index.isin(labels.index)
    out["transit_status"] = out["parking_waiver"].map({True: "pass", False: "flag"})
    out["transit_detail"] = [
        f"Likely 0 parking required: inside a high-quality transit area, {labels[i]} (straight-line, confirm walking distance)"
        if i in labels.index else "1 parking space per home required: not in a high-quality transit area"
        for i in out.index
    ]
    out["transit_source"] = "transit"
    return out


def freeway(sites, bbox):
    """Within 500 ft of a freeway: MERV-13 air filtration required (cost flag)."""
    s, w, n, e = bbox
    pad = 0.01  # include freeways just outside the bbox
    roads = query(SOURCES["freeway"][1], (s - pad, w - pad, n + pad, e + pad),
                  where="F_System IN (1,2)", out_fields="F_System,RouteID")
    sites_m = sites.to_crs(EQUAL_AREA)
    roads_m = roads.to_crs(EQUAL_AREA)
    dist = sites_m.geometry.apply(lambda g: roads_m.distance(g).min() if len(roads_m) else float("inf"))
    out = sites.copy()
    out["freeway_ft"] = (dist / 0.3048).round()
    near = dist <= FREEWAY_BUFFER_M
    out["freeway_status"] = near.map({True: "flag", False: "pass"})
    out["freeway_detail"] = [
        f"{ft:,.0f} ft from a freeway: MERV-13 filtration required" if nr else f"{ft:,.0f} ft from nearest freeway"
        for ft, nr in zip(out["freeway_ft"], near)
    ]
    out["freeway_source"] = "freeway"
    return out


def zoning(sites, workers: int = 8):
    """SCAG parcel zoning at each site's interior point: APN, parcel acres, density rule.

    Zoned for housing -> default (Mullin) density; otherwise SB 4's 40 du/ac.
    Queried per site because the parcel layer is too big to pull by bbox.
    """
    def ask(geometry, geometry_type):
        r = requests.get(f"{SOURCES['zoning'][1]}/query", params={
            "geometry": geometry, "geometryType": geometry_type, "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false",
            "outFields": "APN24,CITY,ACRES,ZN24_CITY,ZN24_SCAG", "f": "json"}, timeout=60)
        return [f["attributes"] for f in r.json().get("features", [])]

    def one(geom):
        # Interior point first; if it lands on a street, fall back to the largest
        # parcel touching the site outline.
        pt = geom.representative_point()
        try:
            hits = ask(f"{pt.x},{pt.y}", "esriGeometryPoint")
            if not hits:
                x0, y0, x1, y1 = geom.bounds
                hits = sorted(ask(f"{x0},{y0},{x1},{y1}", "esriGeometryEnvelope"),
                              key=lambda a: -(a.get("ACRES") or 0))
            return hits[0] if hits else {}
        except (requests.RequestException, ValueError):
            return None

    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, sites.geometry))

    out = sites.copy()
    out["apn"] = [r.get("APN24") if r else None for r in rows]
    out["city"] = [r.get("CITY") if r else None for r in rows]
    out["parcel_acres"] = [r.get("ACRES") if r else None for r in rows]
    out["zone_city"] = [r.get("ZN24_CITY") if r else None for r in rows]
    out["zone_scag"] = [r.get("ZN24_SCAG") if r else None for r in rows]
    res = out["zone_scag"].fillna("").str[:2].isin(RESIDENTIAL_ZONE_PREFIXES)
    known = out["zone_scag"].fillna("") != ""
    out["zoned_residential"] = res.where(known)
    out["zoning_status"] = known.map({True: "pass", False: "unknown"})  # SB 4 applies in every zone
    out["zoning_detail"] = [
        ("unknown: no SCAG parcel at this point" if not k else
         f"APN {a}, city zone {zc} ({'housing allowed: default density' if r else 'non-residential: 40 homes/acre'})")
        for k, a, zc, r in zip(known, out["apn"], out["zone_city"], res)
    ]
    out["zoning_source"] = "zoning"
    return out


def tract_context(sites, bbox):
    """Tract need and fundability: CalEnviroScreen housing burden and the TCAC opportunity category."""
    ces = query(SOURCES["calenviroscreen"][1], bbox,
                out_fields="tract,CIscoreP,HousBurd_Pctl,Poverty_Pctl,Cleanups_Pctl,Traffic_Pctl")
    opp = opportunity_map()
    pts = gpd.GeoDataFrame(geometry=sites.geometry.representative_point(), crs=4326)
    j1 = gpd.sjoin(pts, ces, how="left", predicate="within")
    j1 = j1[~j1.index.duplicated()]
    j2 = gpd.sjoin(pts, opp[["fips", "oppcat", "oppscore", "geometry"]], how="left", predicate="within")
    j2 = j2[~j2.index.duplicated()]
    out = sites.copy()
    out["tract"] = j2["fips"].fillna(j1["tract"].astype("string"))
    for col, src in [("CIscoreP", "ces_pctl"), ("HousBurd_Pctl", "housing_burden_pctl"),
                     ("Poverty_Pctl", "poverty_pctl"), ("Cleanups_Pctl", "cleanups_pctl")]:
        out[src] = pd.to_numeric(j1[col], errors="coerce")
    out["opportunity"] = j2["oppcat"]
    return out


def opportunity_map() -> gpd.GeoDataFrame:
    """2026 CTCAC/HCD Opportunity Map (tracts and rural block groups), cached locally."""
    cache = CACHE_DIR / "tcac_opp_2026.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    zpath = CACHE_DIR / "tcac_opp_2026.zip"
    if not zpath.exists():
        r = requests.get(SOURCES["opportunity"][1], timeout=180)
        r.raise_for_status()
        zpath.write_bytes(r.content)
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".gpkg"))
        z.extract(name, CACHE_DIR)
    gdf = gpd.read_file(CACHE_DIR / name).to_crs(4326)
    gdf.to_parquet(cache)
    return gdf


# ---------------------------------------------------------------- summary

EXCLUSION_TESTS = ["flood", "fire"]
COST_TESTS = ["transit", "freeway"]


def summarize(out):
    """Overall verdict from the checked tests; unknowns are counted, not guessed."""
    status_cols = [c for c in out.columns if c.endswith("_status")]
    out["n_fail"] = (out[status_cols] == "fail").sum(axis=1)
    out["n_flag"] = (out[[f"{t}_status" for t in EXCLUSION_TESTS + ["freeway"]]] == "flag").sum(axis=1)
    out["n_unknown"] = (out[status_cols] == "unknown").sum(axis=1)
    out["verdict"] = "likely eligible"
    out.loc[out["n_flag"] > 0, "verdict"] = "likely eligible, check flags"
    out.loc[out["n_fail"] > 0, "verdict"] = "fails a site test"
    return out


def checklist(row) -> pd.DataFrame:
    """One site's tests as a table: test, status, detail, source, date checked."""
    rows = []
    for col in [c for c in row.index if c.endswith("_status")]:
        test = col[: -len("_status")]
        src_key = row.get(f"{test}_source")
        src = SOURCES.get(src_key, ("", "")) if isinstance(src_key, str) else ("", "")
        rows.append({"test": test, "status": row[col], "detail": row.get(f"{test}_detail", ""),
                     "source": src[0], "url": src[1], "checked": CHECKED if src[0] else ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- helpers

def _sjoin(sites, layer):
    if layer.empty:
        return pd.DataFrame(columns=["label"])
    j = gpd.sjoin(sites[["geometry"]], layer[["label", "geometry"]], how="inner", predicate="intersects")
    return j


def _overlap(sites, zones):
    """Share of each site's area inside zones, and the zone labels it touches."""
    if zones.empty:
        return [0.0] * len(sites), [""] * len(sites)
    s_m = sites[["geometry"]].to_crs(EQUAL_AREA)
    z_m = zones[["label", "geometry"]].to_crs(EQUAL_AREA)
    shares, labels = [], []
    sidx = z_m.sindex
    for geom in s_m.geometry:
        cand = z_m.iloc[list(sidx.query(geom, predicate="intersects"))]
        if cand.empty:
            shares.append(0.0)
            labels.append("")
            continue
        area = geom.area
        inter = cand.geometry.intersection(geom).union_all().area if area > 0 else 1.0
        shares.append(inter / area if area > 0 else 1.0)
        labels.append(", ".join(sorted(set(cand["label"]))))
    return shares, labels


def _share_status(share, sites):
    return ["fail" if s >= FAIL_SHARE else "flag" if s > 0 else "pass" for s in share]


def _set(sites, test, status, detail, src):
    out = sites.copy()
    out[f"{test}_status"] = status
    out[f"{test}_detail"] = detail
    out[f"{test}_source"] = src
    return out
