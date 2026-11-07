"""Rank screened SB 4 sites: which church and college land is best to build on first.

"Best" blends three things, each scaled 0-1 across the eligible sites:

    capacity     homes that fit on the site's surface parking (the realistic first build)
    feasibility  no parking required near transit, no freeway filtration cost, no flood/fire sliver
    need_fund    tract need (CalEnviroScreen housing burden) and tax-credit fundability
                 (CTCAC/HCD 2026 Opportunity Map: higher resource scores better for 9% credits)

Sites that fail a checked test are listed but not ranked. Unknown tests are the
same for every site today, so they do not move the order; they are shown, not
guessed. Weights are ASSUMPTIONS picked for the demo and can be changed.
"""

import numpy as np
import pandas as pd

from findit.yigby import MULLIN_DENSITY, NON_RESIDENTIAL_ZONE_DENSITY

WEIGHTS = {"capacity": 0.4, "feasibility": 0.3, "need_fund": 0.3}

# Opportunity category -> 0-1. "High Segregation & Poverty" and missing tracts get 0
# for fundability; their need still counts through housing burden.
OPPORTUNITY_SCORE = {
    "Highest Resource": 1.0,
    "High Resource": 0.75,
    "Moderate Resource": 0.5,
    "Moderate Resource (Rapidly Changing)": 0.5,
    "Low Resource": 0.25,
}

# Default density for Orange County cities. ASSUMPTION: metropolitan-county
# jurisdiction; confirm the city against HCD's Mullin density list.
DEFAULT_DENSITY = MULLIN_DENSITY["metropolitan"]


def add_units(sites: pd.DataFrame) -> pd.DataFrame:
    """Homes by right: parking-only and whole-site, at the density SB 4 gives that zone.

    Needs `parking_acres` and `area_acres` (from findit.yigby) and `zoned_residential`
    (from findit.screens.zoning). Unknown zoning uses the lower residential density.
    """
    out = sites.copy()
    non_res = out.get("zoned_residential", pd.Series(np.nan, index=out.index)).eq(False)
    out["density_du_ac"] = np.where(non_res, NON_RESIDENTIAL_ZONE_DENSITY, DEFAULT_DENSITY)
    out["units_parking_only"] = (out["parking_acres"].fillna(0) * out["density_du_ac"]).round()
    out["units_whole_site"] = ((out["area_acres"].fillna(0) + out["parking_acres"].fillna(0)) * out["density_du_ac"]).round()
    return out


def rank(sites: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """Score and rank sites. Returns all sites, best first; failing sites at the bottom with rank NaN."""
    w = {**WEIGHTS, **(weights or {})}
    out = sites.copy()
    eligible = out["verdict"].ne("fails a site test")

    # Capacity: percentile among eligible sites, so one mega-campus doesn't flatten everyone else
    units = out["units_parking_only"].where(eligible)
    out["capacity_score"] = units.rank(pct=True, method="average").where(units > 0, 0.0)

    # Feasibility: parking waiver matters most (no structured parking = far cheaper)
    exclusion_flag = (out[["flood_status", "fire_status"]] == "flag").any(axis=1)
    out["feasibility_score"] = (
        0.6 * out["parking_waiver"].astype(float)
        + 0.2 * out["freeway_status"].eq("pass").astype(float)
        + 0.2 * (~exclusion_flag).astype(float)
    )

    # Need and fundability: average what we have
    burden = out["housing_burden_pctl"] / 100
    opp = out["opportunity"].map(OPPORTUNITY_SCORE).fillna(0.0)
    out["need_fund_score"] = pd.concat([burden, opp], axis=1).mean(axis=1, skipna=True)

    out["score"] = (
        w["capacity"] * out["capacity_score"]
        + w["feasibility"] * out["feasibility_score"]
        + w["need_fund"] * out["need_fund_score"]
    ).where(eligible)
    out["rank"] = out["score"].rank(ascending=False, method="min")
    out["why"] = out.apply(_why, axis=1)
    return out.sort_values(["rank", "units_parking_only"], ascending=[True, False], na_position="last")


def _why(r) -> str:
    if r["verdict"] == "fails a site test":
        failed = [c[:-7] for c in r.index if c.endswith("_status") and r[c] == "fail"]
        return "Not ranked: fails " + ", ".join(failed)
    bits = [f"~{r['units_parking_only']:.0f} homes on parking at {r['density_du_ac']:.0f}/acre"]
    bits.append("no parking required (transit)" if r["parking_waiver"] else "needs 1 parking space per home")
    if r["freeway_status"] == "flag":
        bits.append("freeway filtration cost")
    if isinstance(r.get("opportunity"), str):
        bits.append(r["opportunity"].lower() + " tract")
    if pd.notna(r.get("housing_burden_pctl")):
        bits.append(f"housing burden {r['housing_burden_pctl']:.0f}th pctl")
    return "; ".join(bits)
