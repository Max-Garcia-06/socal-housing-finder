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
DENSITY_SUBURBAN = 20             # HCD default density list (2020 Census update): OC "suburban" jurisdictions
SUBURBAN_CITIES = {"La Palma", "Laguna Beach", "Laguna Woods", "Los Alamitos", "Villa Park"}
HEAVY_IND_FT, IND_ADJ_M = 1200, 6  # SB 4: >1,200 ft from heavy industrial; adjoining-industrial check
DENSITY_NONRES = 40                # SB 4 non-residential zone density (HCD fact sheet)
BUILDABLE_SHARE = 0.5              # ASSUMPTION: half the parcel (parking / open area) is buildable; sanctuary stays
# Total development cost (TDC) model. Every input is sourced or marked ASSUMPTION; see README "How we estimate cost".
NRSF_PER_HOME = 850                # ASSUMPTION: net rentable sq ft per home (Legacy Square units are 602-1,115 sq ft)
TDC_PSF_LO = 481                   # RAND 2025: avg LIHTC TDC per net rentable sq ft, San Diego metro (CA's lowest)
TDC_PSF_HI = 625                   # RAND 2025: LA metro ~1.3x San Diego -> 1.3 x 481 (ASSUMPTION: OC priced like LA metro)
PW_PREMIUM = 94_000                # Terner 2024: prevailing-wage LIHTC new construction ~$94k/home more (high case only)
HARD_SHARE = 0.60                  # Terner 2020: hard costs were >60% of total cost in California
IMPACT_FEES = 29_000               # RAND 2025: avg municipal impact/development fees per unit in California
PARKING_PER_SPACE = 52_000         # UCLA ITS 2025: avg above-ground structured parking cost per space (17 US cities)
MIN_ACRES_RANKED = 0.5             # ASSUMPTION: smaller lots rarely pencil for a nonprofit rental

APR_SRC = "https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year"
# Terner Center (2023) county table for Orange County churches, parcels >= 10,000 sq ft (github.com/Terner-Center/SB4-Analysis)
TERNER_OC = {"acres": 1907, "parcels": 707, "median_acres": 1.6, "transit_acres_pct": 26.52}

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
    # From HCD Annual Progress Report Table A (city-reported applications), data.ca.gov; see data/reference/
    dict(name="Church of the Blessed Sacrament", city="Placentia", lat=33.886064, lon=-117.861808, units=65, year="Approved (2022 APR)",
         partner="Blessed Sacrament Episcopal Church", law="Pre-SB 4",
         note="City reported 64 lower-income homes + 1 other on church land.", src=APR_SRC),
    dict(name="Melia (Victory Baptist Church)", city="Anaheim", lat=33.835294, lon=-117.976049, units=59, year="Entitled (2020 APR)",
         partner="Victory Baptist Church", law="Rezoning, not SB 4",
         note="Mostly market-rate (53 above-moderate, 6 moderate): a church-land project that needed a rezoning. SB 4 requires 100% affordable.", src=APR_SRC),
    dict(name="Our Lady of Peace Church + homes", city="Irvine", lat=33.700484, lon=-117.769150, units=68, year="Pending (2025 APR)",
         partner="Our Lady of Peace parish", law="Not SB 4 (mostly market-rate)",
         note="59 above-moderate, 3 moderate, 6 very-low-income homes reported by the city.", src=APR_SRC),
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
    res_density = np.where(p["CITY"].isin(SUBURBAN_CITIES), DENSITY_SUBURBAN, DENSITY_RES)
    p["density"] = np.where(p["zone_res"] | p["ZN24_SCAG"].isna(), res_density, DENSITY_NONRES)
    p["suburban"] = p["CITY"].isin(SUBURBAN_CITIES)

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

    # Industrial (SCAG LU24 13xx): heavy industrial (132x) within 1,200 ft fails; adjoining any industrial parcel is a caution
    ind = load("industrial").to_crs(M)
    heavy = ind[ind["LU24"].str.startswith("132")][["geometry"]]
    near = gpd.sjoin_nearest(pm[["pid", "geometry"]], heavy, distance_col="d").groupby("pid")["d"].min()
    p["heavy_ft"] = (near.reindex(p["pid"]).values / FT).round()
    adj = gpd.sjoin(pm[["pid", "geometry"]].assign(geometry=pm.buffer(IND_ADJ_M)), ind[["geometry", "LU24"]], predicate="intersects")
    p["ind_adjoin"] = p["pid"].map(adj.groupby("pid")["LU24"].apply(lambda x: ",".join(sorted(set(x))))).fillna("")

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
        if r.heavy_ft < HEAVY_IND_FT:
            t["industrial"] = ("fail", f"Heavy-industrial parcel {int(r.heavy_ft):,} ft away (limit 1,200 ft)")
        elif r.ind_adjoin:
            t["industrial"] = ("caution", f"Adjoins an industrial parcel (SCAG land use {r.ind_adjoin}); check SB 4's adjoining-industrial rule. Title V sites not checked")
        else:
            t["industrial"] = ("pass", f"No adjoining industrial parcel; nearest heavy industrial {int(r.heavy_ft):,} ft. Title V sites not checked")
        for k, label in [("fault", "Earthquake fault zone (CGS layer requires a token)"),
                         ("wetland", "Wetlands, habitat, farmland, conservation easement"),
                         ("historic", "Historic structures / existing tenants on site")]:
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
    # Components (0-1) for the client-side "what matters most" sliders
    p.loc[rank, "c_yield"] = homes.round(3)
    p.loc[rank, "c_need"] = need.round(3)
    p.loc[rank, "c_fin"] = fund.round(3)
    p.loc[rank, "c_cert"] = feas.clip(0, 1).round(3)
    p["need_idx"] = ((p["HousBurd_Pctl"].fillna(50) + p["Poverty_Pctl"].fillna(50)) / 2).round(0)
    p["n_caution"] = p["tests"].map(lambda t: sum(v[0] == "caution" for v in t.values()))
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
    per_lo = TDC_PSF_LO * NRSF_PER_HOME
    per_hi = TDC_PSF_HI * NRSF_PER_HOME + PW_PREMIUM
    p["cost_lo"] = p["units_est"] * per_lo
    p["cost_hi"] = p["units_est"] * per_hi
    p["parking_cost"] = p["parking_req"] * PARKING_PER_SPACE

    cols = ["pid", "APN24", "CITY", "ACRES", "SLOPE", "lat", "lon", "ZN24_CITY", "ZN24_SCAG", "zone_res", "density",
            "units_max", "units_est", "parking_req", "hqta", "oppcat", "CIscoreP", "HousBurd", "HousBurd_Pctl",
            "Poverty_Pctl", "he_low", "he_total", "name", "street", "ein", "precedent", "tests", "fails", "verdict",
            "score", "rank", "funding", "cost_lo", "cost_hi", "well_active_ft", "suburban", "heavy_ft", "ind_adjoin",
            "c_yield", "c_need", "c_fin", "c_cert", "need_idx", "n_caution", "parking_cost"]
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

    big = out[out.ACRES >= 10000 / 43560]
    bench = {"terner": TERNER_OC, "ours": {"acres": int(round(big.ACRES.sum())), "parcels": int(len(big)),
             "median_acres": round(float(big.ACRES.median()), 2),
             "transit_acres_pct": round(100 * float(big[big.hqta].ACRES.sum() / big.ACRES.sum()), 1)}}
    print("Terner benchmark:", bench)
    (OUT / "sites.js").write_text("window.SITES=" + json.dumps(recs, default=str) + ";\nwindow.PARCELS=" + json.dumps(gj)
                                  + ";\nwindow.WELLS=" + json.dumps(wells_gj) + ";\nwindow.PRECEDENTS=" + json.dumps(PRECEDENTS)
                                  + ";\nwindow.BENCH=" + json.dumps(bench) + ";\nwindow.COST=" + json.dumps(dict(
                                      nrsf=NRSF_PER_HOME, psf_lo=TDC_PSF_LO, psf_hi=TDC_PSF_HI, pw=PW_PREMIUM, hard=HARD_SHARE,
                                      fees=IMPACT_FEES, parking=PARKING_PER_SPACE)) + ";\nwindow.BUILT='" + pd.Timestamp.now().strftime("%Y-%m-%d") + "';\n")
    likely = out[out.verdict == "likely"]
    print(f"{len(out)} religious parcels; {len(likely)} likely eligible; {int((out.score > 0).sum())} ranked")
    print("excluded by:", pd.Series([f for fs in out.fails for f in fs]).value_counts().to_dict())
    print(f"homes (est, likely, >=0.5ac): {int(likely[likely.ACRES >= 0.5].units_est.sum()):,}")
    print(out.sort_values("score", ascending=False).head(12)[["CITY", "name", "ACRES", "units_est", "score", "hqta", "oppcat", "HousBurd_Pctl", "precedent"]].to_string())
    print(out[out.precedent != ""][["precedent", "name", "ACRES", "units_est", "verdict", "score"]])


if __name__ == "__main__":
    main()
