"""Census tract lookup, ACS tract tables and LODES job counts.

Adapted from austin/daytime_population.py, austin/median_income.py and
leo/add_trade_area_population.py. The originals geocoded every point through
the Census API (~3 HTTP calls per point); here tract shapes and ACS tables are
downloaded once per county and joined locally.
"""

import hashlib
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from findit import CA_FIPS, CACHE_DIR
from findit.geo import to_gdf

load_dotenv()

ACS_YEAR = 2023
TIGER_YEAR = 2023
LODES_YEAR = 2022

# ACS 5-year variables useful for affordable-housing work
HOUSING_VARS = {
    "B01003_001E": "population",
    "B19013_001E": "median_household_income",
    "B25064_001E": "median_gross_rent",
    "B25077_001E": "median_home_value",
    "B25003_001E": "occupied_units",
    "B25003_003E": "renter_units",
    "B25070_001E": "renters_total",          # renter households, gross rent as % of income
    "B25070_007E": "rent_burden_30_35",
    "B25070_008E": "rent_burden_35_40",
    "B25070_009E": "rent_burden_40_50",
    "B25070_010E": "rent_burden_50_plus",
    "B25070_011E": "rent_burden_not_computed",
    "B25002_003E": "vacant_units",
    "B25002_001E": "housing_units",
    "B17001_002E": "below_poverty",
    "B17001_001E": "poverty_universe",
    "B08201_002E": "households_no_vehicle",
    "B08201_001E": "households_total",
}


def _census_key() -> str:
    key = os.getenv("CENSUS_API_KEY")
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY not set. Get one free at https://api.census.gov/data/key_signup.html "
            "and put it in .env (see .env.example)."
        )
    return key


def acs_tracts(counties, variables=None, year: int = ACS_YEAR, state: str = CA_FIPS) -> pd.DataFrame:
    """ACS 5-year tract table for whole counties, one request per county.

    counties: iterable of 3-digit county FIPS (see findit.SOCAL_COUNTIES).
    variables: dict of {ACS code: column name}; defaults to HOUSING_VARS.
    Census sentinel values (e.g. -666666666 for "not available") become NaN.
    Result is cached as parquet under data/cache/.
    """
    variables = variables or HOUSING_VARS
    counties = sorted(counties)
    var_hash = hashlib.md5(",".join(sorted(variables)).encode()).hexdigest()[:8]
    cache = CACHE_DIR / f"acs{year}_{state}_{'-'.join(counties)}_{var_hash}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    frames = []
    for county in counties:
        resp = requests.get(
            f"https://api.census.gov/data/{year}/acs/acs5",
            params={
                "get": ",".join(["NAME", *variables]),
                "for": "tract:*",
                "in": f"state:{state} county:{county}",
                "key": _census_key(),
            },
            timeout=60,
        )
        resp.raise_for_status()
        header, *rows = resp.json()
        frames.append(pd.DataFrame(rows, columns=header))

    df = pd.concat(frames, ignore_index=True)
    df["GEOID"] = df["state"] + df["county"] + df["tract"]
    for code in variables:
        df[code] = pd.to_numeric(df[code], errors="coerce")
        df.loc[df[code] < 0, code] = np.nan
    df = df.rename(columns=variables)

    # Derived shares, only when their inputs were requested
    if {"rent_burden_30_35", "renters_total"} <= set(df.columns):
        burdened = df[["rent_burden_30_35", "rent_burden_35_40", "rent_burden_40_50", "rent_burden_50_plus"]].sum(axis=1)
        computed = df["renters_total"] - df["rent_burden_not_computed"]
        df["rent_burdened_share"] = burdened / computed.replace(0, np.nan)
        df["severely_rent_burdened_share"] = df["rent_burden_50_plus"] / computed.replace(0, np.nan)
    if {"renter_units", "occupied_units"} <= set(df.columns):
        df["renter_share"] = df["renter_units"] / df["occupied_units"].replace(0, np.nan)
    if {"below_poverty", "poverty_universe"} <= set(df.columns):
        df["poverty_rate"] = df["below_poverty"] / df["poverty_universe"].replace(0, np.nan)
    if {"households_no_vehicle", "households_total"} <= set(df.columns):
        df["no_vehicle_share"] = df["households_no_vehicle"] / df["households_total"].replace(0, np.nan)

    df.to_parquet(cache)
    return df


def tract_shapes(state: str = CA_FIPS, year: int = TIGER_YEAR) -> gpd.GeoDataFrame:
    """Census cartographic-boundary tract polygons for a state (cached)."""
    cache = CACHE_DIR / f"tracts_{year}_{state}.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    url = f"https://www2.census.gov/geo/tiger/GENZ{year}/shp/cb_{year}_{state}_tract_500k.zip"
    gdf = gpd.read_file(url)[["GEOID", "ALAND", "geometry"]].to_crs(epsg=4326)
    gdf.to_parquet(cache)
    return gdf


def points_to_tracts(points: pd.DataFrame, state: str = CA_FIPS) -> pd.DataFrame:
    """Add an 11-digit tract `GEOID` column to a lat/lon frame via local spatial join.

    Points outside every tract (ocean, out of state) get NaN.
    """
    joined = gpd.sjoin(to_gdf(points.reset_index(drop=True)), tract_shapes(state),
                       how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"]))


def geocode_point(lat: float, lon: float) -> dict | None:
    """Single-point tract lookup through the Census geocoder (no key needed).

    Prefer points_to_tracts for anything more than a handful of points.
    """
    resp = requests.get(
        "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
        params={"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                "vintage": "Current_Current", "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    tracts = resp.json()["result"]["geographies"].get("Census Tracts", [])
    if not tracts:
        return None
    t = tracts[0]
    return {"state": t["STATE"], "county": t["COUNTY"], "tract": t["TRACT"],
            "GEOID": t["STATE"] + t["COUNTY"] + t["TRACT"]}


def lodes_by_tract(kind: str = "wac", year: int = LODES_YEAR, state_abbr: str = "ca") -> pd.Series:
    """LODES job counts aggregated to tract GEOID.

    kind="wac": jobs located in the tract (where people work).
    kind="rac": employed residents of the tract (where workers live).
    """
    cache = CACHE_DIR / f"{state_abbr}_{kind}_{year}.csv.gz"
    if not cache.exists():
        url = f"https://lehd.ces.census.gov/data/lodes/LODES8/{state_abbr}/{kind}/{state_abbr}_{kind}_S000_JT00_{year}.csv.gz"
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(cache, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    geocode_col = "w_geocode" if kind == "wac" else "h_geocode"
    df = pd.read_csv(cache, usecols=[geocode_col, "C000"], dtype={geocode_col: str})
    return df.groupby(df[geocode_col].str.zfill(15).str[:11])["C000"].sum().rename(f"jobs_{kind}")


def jobs_by_tract(year: int = LODES_YEAR) -> pd.DataFrame:
    """Jobs in tract, employed residents, and their ratio (jobs-housing balance), indexed by GEOID."""
    df = pd.concat([lodes_by_tract("wac", year), lodes_by_tract("rac", year)], axis=1).fillna(0)
    df["jobs_per_worker"] = df["jobs_wac"] / df["jobs_rac"].replace(0, np.nan)
    df.index.name = "GEOID"
    return df
