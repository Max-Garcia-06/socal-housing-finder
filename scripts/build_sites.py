"""Screen every religious-facility parcel in Orange County against SB 4 and rank them.

    uv run python scripts/fetch_layers.py   # once, downloads public layers to data/raw/
    uv run python scripts/build_sites.py    # writes web/data/*.js for the locator

Every test is pass / fail / caution / unknown with its source. Numbers that are not
from a source are marked ASSUMPTION here, in the UI and in the README.
"""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "web" / "data"
OUT.mkdir(parents=True, exist_ok=True)
M = 3310  # California Albers, metres

FT = 0.3048
WELL_BUFFER_M = 3200 * FT          # SB 4: >3,200 ft from oil/gas extraction or refining facility
DENSITY_RES = 30                   # Mullin default density, metropolitan county (HCD fact sheet)
DENSITY_NONRES = 40                # SB 4 non-residential zone density (HCD fact sheet)
BUILDABLE_SHARE = 0.5              # ASSUMPTION: half the parcel (parking / open area) is buildable; sanctuary stays
COST_PER_UNIT = (450_000, 700_000) # ASSUMPTION: coastal-California affordable cost range cited at a 2023 state oversight hearing
MIN_ACRES_RANKED = 0.5             # ASSUMPTION: smaller lots rarely pencil for a nonprofit rental

PRECEDENTS = [
    dict(name="Legacy Square", city="Santa Ana", lat=33.749858, lon=-117.865506, units=93, year="2023 (open)",
         partner="National CORE + Santa Ana United Methodist Church", law="Pre-SB 4 (city approvals)",
         note="Ground lease from the church; 33 permanent supportive homes; 2024 OC Project of the Year; $25.4M state AHSC award.",
         src="https://nationalcore.org/what-we-do/planning-and-development/faith-based-partnerships/"),
    dict(name="Wesley Village", city="Garden Grove", lat=33.776026, lon=-117.942733, units=47, year="2017 (open)",
         partner="Jamboree Housing + Garden Grove United Methodist Church", law="Pre-SB 4",
         note="75-year ground lease; 4% LIHTC, HOME and FHLB AHP financing; ~$18.9M total cost.",
         src="https://jamboreehousing.com/blogs/affordable-housing-resident/wesley-village-garden-grove"),
    dict(name="340 St. Ann's Drive (NCC)", city="Laguna Beach", lat=33.537568, lon=-117.777622, units=29, year="Proposed",
         partner="Related California + Neighborhood Congregational Church", law="SB 4",
         note="Scaled from 72 to ~30 homes after federal cuts; city committed $6.71M; state LHTF $2.5M to the city.",
         src="https://www.lagunabeachindy.com/news/city-commits-6-71m-in-funding-for-affordable-housing-project/article_f60c6735-3a80-42e6-a47d-164c19c713c1.html"),
]


def load(name):
    return gpd.read_file(RAW / f"{name}.geojson")


def share_covered(parcels_m, hazard_m):
    """Fraction of each parcel's area inside the hazard polygons (0..1)."""
    hz = hazard_m[["geometry"]].copy()
    hz["geometry"] = hz.buffer(0)
    inter = gpd.overlay(parcels_m[["pid", "geometry"]], hz, how="intersection", keep_geom_type=True)
    area = inter.assign(a=inter.area).groupby("pid")["a"].sum()
    return (area / parcels_m.set_index("pid").area).reindex(parcels_m["pid"]).fillna(0).clip(0, 1).values


def main():
    p = load("religious_parcels").reset_index(drop=True)
    p["pid"] = np.arange(len(p))
    pm = p.to_crs(M)
    p["lat"] = pm.centroid.to_crs(4326).y.round(6)
    p["lon"] = pm.centroid.to_crs(4326).x.round(6)

    # Zoning (SCAG Zoning_poly_OR). ZN24_SCAG uses SCAG land-use style codes: 11xx = residential.
    zn = pd.DataFrame(json.loads((RAW / "religious_zoning.json").read_text())).drop_duplicates("APN24")
    p = p.merge(zn, on="APN24", how="left")
    p["zone_res"] = p["ZN24_SCAG"].fillna("").str.startswith("11")
    p["density"] = np.where(p["zone_res"], DENSITY_RES, DENSITY_NONRES)
    p.loc[p["ZN24_SCAG"].isna(), "density"] = DENSITY_RES

    # Flood (FEMA SFHA, county DFIRM copy)
    p["flood_share"] = share_covered(pm, load("flood").to_crs(M))

    # Fire: Very High FHSZ in LRA (county copy) and SRA (CAL FIRE)
    fire = pd.concat([load("fire_lra"), load("fire_sra")])
    fire = fire[fire["HAZ_CLASS"].str.contains("Very High", na=False)]
    p["fire_share"] = share_covered(pm, gpd.GeoDataFrame(fire, crs=4326).to_crs(M))

    # Oil & gas wells (CalGEM WellSTAR): distance to nearest active/new and idle well
    wells = load("wells").to_crs(M)
    cent = gpd.GeoDataFrame({"pid": p["pid"]}, geometry=pm.centroid, crs=M)
    for status, col in [(["Active", "New"], "well_active_ft"), (["Idle"], "well_idle_ft")]:
        w = wells[wells["WellStatus"].isin(status)][["geometry"]]
        near = gpd.sjoin_nearest(pm[["pid", "geometry"]], w, distance_col="d").groupby("pid")["d"].min()
        p[col] = (near.reindex(p["pid"]).values / FT).round()

    # High-quality transit areas (Caltrans): parking waiver proxy
    hq = load("hqta").to_crs(M)
    hit = gpd.sjoin(cent, hq[["geometry", "agency_primary", "hqta_type"]], predicate="within")
    p["hqta"] = p["pid"].isin(hit["pid"])
    p["hqta_agency"] = p["pid"].map(hit.drop_duplicates("pid").set_index("pid")["agency_primary"]).fillna("")

    # Tract need: CalEnviroScreen 5.0 (housing burden, poverty, CES percentile)
    p["tract"] = p["GEOID20"].str[:11]
    ces = load("ces").drop(columns="geometry")
    ces["tract"] = ces["tract"].astype("int64").astype(str).str.zfill(11)
    p = p.merge(ces[["tract", "CIscoreP", "HousBurd", "HousBurd_Pctl", "Poverty_Pctl", "ApproxLoc"]], on="tract", how="left")

    # TCAC/HCD 2026 Opportunity Map category by tract
    tc = gpd.read_file(RAW / "tcac_2026" / "final_opp_2026_public.gpkg", ignore_geometry=True)
    tc = tc[tc["county_name"] == "Orange"]
    tc["tract"] = tc["fips"].astype(str).str.zfill(11)
    p = p.merge(tc[["tract", "oppcat"]].drop_duplicates("tract"), on="tract", how="left")

    # Housing-element sites (SCAG): does the city already count this parcel?
    he = load("he_sites").drop(columns="geometry")
    he = he.groupby("APN24").agg(he_low=("Low_Income_HE_Capacity", "sum"), he_total=("Total_HE_Capacity", "sum"))
    p = p.merge(he, left_on="APN24", right_index=True, how="left")

    # Names: IRS-registered places of worship (HIFLD) inside or within 75 m of the parcel
    irs = load("irs_worship").to_crs(M)
    pm2 = p[["pid"]].join(gpd.GeoSeries(pm.geometry.values, crs=M).buffer(75).rename("geometry")).pipe(gpd.GeoDataFrame, crs=M)
    j = gpd.sjoin(irs, pm2, predicate="within")
    cpts = cent.set_index("pid").geometry
    j["d"] = [g.distance(cpts.loc[i]) for g, i in zip(j.geometry, j["pid"])]
    j = j.sort_values(["pid", "d"])
    names = j.groupby("pid").agg(name=("NAME", "first"), street=("STREET", "first"), ein=("EIN", "first"),
                                 n_orgs=("NAME", "nunique"))
    p = p.merge(names, left_on="pid", right_index=True, how="left")

    # Precedents: tag the parcel nearest each known church-housing project
    prec_pts = gpd.GeoDataFrame(PRECEDENTS, geometry=gpd.points_from_xy([x["lon"] for x in PRECEDENTS],
                                                                         [x["lat"] for x in PRECEDENTS]), crs=4326).to_crs(M)
    pn = gpd.sjoin_nearest(prec_pts, pm[["pid", "geometry"]], distance_col="d", max_distance=150)
    p["precedent"] = p["pid"].map(pn.set_index("pid")["name"]).fillna("")

    # Capacity
    p["units_max"] = np.floor(p["ACRES"] * p["density"]).astype(int)
    p["units_est"] = np.floor(p["ACRES"] * BUILDABLE_SHARE * p["density"]).astype(int)
    p["parking_req"] = np.where(p["hqta"], 0, p["units_est"])

    # Verdicts
    def tests(r):
        t = {}
        t["owner"] = ("unknown", "Religious-facility land use (SCAG 2024)" + (f"; IRS-registered org on site: {r['name']}" if isinstance(r["name"], str) else "")
                      + ". Ownership as of 2024-01-01 needs a title check.")
        t["flood"] = ("fail" if r.flood_share > 0.5 else "caution" if r.flood_share > 0.02 else "pass",
                      f"{r.flood_share:.0%} of parcel in FEMA special flood hazard area")
        t["fire"] = ("fail" if r.fire_share > 0.5 else "caution" if r.fire_share > 0.02 else "pass",
                     f"{r.fire_share:.0%} of parcel in Very High fire hazard severity zone")
        wa, wi = r.well_active_ft, r.well_idle_ft
        if wa < 3200:
            t["oil"] = ("fail", f"Active oil/gas well {int(wa):,} ft away (limit 3,200 ft)")
        elif wi < 3200:
            t["oil"] = ("caution", f"Idle oil/gas well {int(wi):,} ft away; active wells {int(wa):,} ft")
        else:
            t["oil"] = ("pass", f"Nearest active oil/gas well {int(wa):,} ft away")
        t["parking"] = ("pass", f"In a high-quality transit area ({r.hqta_agency}): no parking can be required") if r.hqta \
            else ("info", f"Outside high-quality transit areas: up to 1 space per home (~{int(r.units_est)} spaces)")
        t["slope"] = ("caution" if r.SLOPE and r.SLOPE > 15 else "pass", f"SCAG slope class {int(r.SLOPE or 0)}")
        for k, label in [("fault", "Earthquake fault zone (CGS layer requires a token)"),
                         ("wetland", "Wetlands, habitat, farmland, conservation easement"),
                         ("historic", "Historic structures / existing tenants on site"),
                         ("industrial", "Industrial-use buffers (1,200 ft heavy, 1,600 ft Title V)")]:
            t[k] = ("unknown", label + ": not checked")
        return t

    p["tests"] = p.apply(tests, axis=1)
    p["fails"] = p["tests"].map(lambda t: [k for k, v in t.items() if v[0] == "fail"])
    p["verdict"] = np.where(p["fails"].map(len) > 0, "excluded", "likely")

    # Impact-first score (0-100) among likely-eligible parcels big enough to pencil
    rank = (p["verdict"] == "likely") & (p["ACRES"] >= MIN_ACRES_RANKED)
    r = p[rank]
    pct = lambda s: s.rank(pct=True)
    homes = pct(r["units_est"].clip(upper=150))
    need = (r["HousBurd_Pctl"].fillna(50) / 100 + r["Poverty_Pctl"].fillna(50) / 100) / 2
    fund = (r["oppcat"].isin(["Highest Resource", "High Resource"]).astype(float) * 0.5 + r["hqta"].astype(float) * 0.5)
    feas = 1 - r["flood_share"].clip(0, 1) * 0 - (r["SLOPE"].fillna(0) > 15) * 0.5 \
        - r["tests"].map(lambda t: sum(v[0] == "caution" for v in t.values())) * 0.15
    p.loc[rank, "score"] = (100 * (0.35 * homes + 0.30 * need + 0.20 * fund + 0.15 * feas.clip(0, 1))).round(0)
    p["score"] = p["score"].fillna(0).astype(int)
    p["rank"] = p["score"].rank(ascending=False, method="first").where(p["score"] > 0)

    # Funding paths (rules of thumb, labelled in UI). Every program has a source link in web/index.html.
    def funding(r):
        f = ["lihtc4", "ochft", "ahp"]
        if r.oppcat in ("Highest Resource", "High Resource"):
            f.insert(0, "lihtc9")
        if r.hqta:
            f.insert(1, "ahsc")
        f += ["city", "lisc"]
        return f

    p["funding"] = p.apply(funding, axis=1)
    p["cost_lo"] = p["units_est"] * COST_PER_UNIT[0]
    p["cost_hi"] = p["units_est"] * COST_PER_UNIT[1]

    cols = ["pid", "APN24", "CITY", "ACRES", "SLOPE", "lat", "lon", "ZN24_CITY", "ZN24_SCAG", "zone_res", "density",
            "units_max", "units_est", "parking_req", "hqta", "oppcat", "CIscoreP", "HousBurd", "HousBurd_Pctl",
            "Poverty_Pctl", "he_low", "he_total", "name", "street", "ein", "precedent", "tests", "fails", "verdict",
            "score", "rank", "funding", "cost_lo", "cost_hi", "well_active_ft"]
    out = p[cols].copy()
    out["ACRES"] = out["ACRES"].round(2)
    for c in ["CIscoreP", "HousBurd", "HousBurd_Pctl", "Poverty_Pctl"]:
        out[c] = out[c].round(0)
    out = out.replace({np.nan: None})
    recs = out.to_dict("records")

    geo = gpd.GeoDataFrame(p[["pid"]], geometry=p.geometry.simplify(0.00003), crs=4326)
    gj = json.loads(geo.to_json(drop_id=True))
    for f in gj["features"]:
        f["geometry"]["coordinates"] = json.loads(json.dumps(f["geometry"]["coordinates"]), parse_float=lambda x: round(float(x), 5))

    w = load("wells")
    w = w[w["WellStatus"].isin(["Active", "New"])]
    buf = gpd.GeoSeries([w.to_crs(M).buffer(WELL_BUFFER_M).union_all().simplify(30)], crs=M).to_crs(4326)
    wells_gj = json.loads(buf.to_json(drop_id=True))
    wells_gj["features"][0]["properties"] = {}

    (OUT / "sites.js").write_text("window.SITES=" + json.dumps(recs, default=str) + ";\nwindow.PARCELS=" + json.dumps(gj)
                                  + ";\nwindow.WELLS=" + json.dumps(wells_gj) + ";\nwindow.PRECEDENTS=" + json.dumps(PRECEDENTS)
                                  + ";\nwindow.BUILT='" + pd.Timestamp.now().strftime("%Y-%m-%d") + "';\n")
    likely = out[out.verdict == "likely"]
    print(f"{len(out)} religious parcels; {len(likely)} likely eligible; {int((out.score > 0).sum())} ranked")
    print("excluded by:", pd.Series([f for fs in out.fails for f in fs]).value_counts().to_dict())
    print(f"homes (est, likely, >=0.5ac): {int(likely[likely.ACRES >= 0.5].units_est.sum()):,}")
    print(out.sort_values("score", ascending=False).head(12)[["CITY", "name", "ACRES", "units_est", "score", "hqta", "oppcat", "HousBurd_Pctl", "precedent"]].to_string())
    print(out[out.precedent != ""][["precedent", "name", "ACRES", "units_est", "verdict", "score"]])


if __name__ == "__main__":
    main()
