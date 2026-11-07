"""Find, screen and rank SB 4 (YIGBY) church and college sites.

    uv run python examples/rank_sites.py                 # Irvine, live OSM
    uv run python examples/rank_sites.py --area oc       # all of Orange County
    uv run python examples/rank_sites.py --sites data/irvine_sites.geojson   # skip OSM

Steps:
  1. candidates   OSM worship + private-college footprints, adjacent surface parking
  2. screens      flood, fire, transit (parking waiver), freeway, zoning, tract context
  3. rank         capacity + feasibility + need/fundability, failing sites unranked
Writes output/<area>_ranked.csv, output/<area>_ranked.geojson and output/<area>_map.html.
"""

import argparse
from pathlib import Path

import geopandas as gpd

from findit import IRVINE_BBOX, ORANGE_COUNTY_BBOX, ROOT
from findit import rank, screens, yigby
from findit.sitemap import site_map

AREAS = {"irvine": IRVINE_BBOX, "oc": ORANGE_COUNTY_BBOX}


def load_candidates(area: str, sites_path: str | None) -> gpd.GeoDataFrame:
    if sites_path:
        sites = gpd.read_file(sites_path)
        print(f"Loaded {len(sites)} candidate sites from {sites_path}")
        return sites
    bbox = AREAS[area]
    sites = yigby.candidate_sites(bbox)
    sites = yigby.add_parking_area(sites, bbox)
    sites = sites.drop(columns=["tags"], errors="ignore")
    path = ROOT / "data" / f"{area}_sites.geojson"
    path.parent.mkdir(exist_ok=True)
    sites.to_file(path)
    print(f"{len(sites)} candidate sites from OSM, saved to {path.relative_to(ROOT)}")
    return sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", choices=AREAS, default="irvine")
    ap.add_argument("--sites", help="GeoJSON of candidate sites (skips the OSM step)")
    ap.add_argument("--osm-only", action="store_true", help="just fetch and save OSM candidates")
    args = ap.parse_args()

    sites = load_candidates(args.area, args.sites)
    if args.osm_only:
        return

    screened = screens.screen(sites, AREAS[args.area])
    ranked = rank.rank(rank.add_units(screened))

    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    keep = ["rank", "score", "name", "site_type", "denomination", "verdict", "why", "city", "apn",
            "zone_city", "parcel_acres", "area_acres", "parking_acres", "density_du_ac",
            "units_parking_only", "units_whole_site", "parking_waiver", "freeway_ft",
            "opportunity", "housing_burden_pctl", "poverty_pctl", "ces_pctl",
            "capacity_score", "feasibility_score", "need_fund_score", "n_unknown"]
    keep += [c for c in ranked.columns if c.endswith(("_status", "_detail"))]
    keep = [c for c in keep if c in ranked.columns]
    ranked[keep].to_csv(out_dir / f"{args.area}_ranked.csv", index=False)
    ranked[keep + ["geometry"]].to_file(out_dir / f"{args.area}_ranked.geojson")
    site_map(ranked, AREAS[args.area]).save(out_dir / f"{args.area}_map.html")

    n_ok = ranked["rank"].notna().sum()
    print(f"\n{n_ok} of {len(ranked)} sites pass every checked test "
          f"({(ranked['verdict'] == 'fails a site test').sum()} fail flood or fire).")
    print(f"About {ranked.loc[ranked['rank'].notna(), 'units_parking_only'].sum():,.0f} homes "
          f"on parking at eligible sites (pre-design estimate).\n")
    cols = ["rank", "name", "units_parking_only", "parking_waiver", "opportunity", "verdict"]
    print(ranked[cols].head(15).to_string(index=False))
    print(f"\nWrote output/{args.area}_ranked.csv, .geojson and _map.html")


if __name__ == "__main__":
    main()
