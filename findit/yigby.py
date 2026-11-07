"""YIGBY (SB 4, Gov. Code § 65913.16) candidate sites: faith and college land.

SB 4 lets 100% affordable housing be approved by right on land owned by a
religious institution or independent (private nonprofit) college as of
2024-01-01. This module finds candidate parcels from OSM and estimates unit
capacity. It does NOT confirm ownership or clear the statutory exclusions;
see README "YIGBY" for the screening layers still to add.
"""

import re

import geopandas as gpd
import pandas as pd

from findit import osm

# Default ("Mullin") densities, units/acre, for sites in zones allowing residential use.
# Most Orange County cities are in a metropolitan county -> 30. Verify the city's
# category against HCD's list before quoting a number.
MULLIN_DENSITY = {
    "metropolitan": 30,
    "suburban": 20,
    "nonmetro_city": 15,
    "nonmetro_unincorporated": 10,
}
NON_RESIDENTIAL_ZONE_DENSITY = 40

FAITH_FILTER = '["amenity"="place_of_worship"]'
COLLEGE_FILTER = '["amenity"~"university|college"]'
PARKING_FILTER = '["amenity"="parking"]'

# Public campuses SB 4 does not cover: UC, CSU and community colleges (which in OC
# are mostly "<Place> College"). Anything left still needs a nonprofit check.
PUBLIC_COLLEGE = re.compile(
    r"University of California|California State|Cal State|Community College|"
    r"^(Irvine Valley|Santa Ana|Saddleback|Orange Coast|Golden West|Coastline|Cypress|Fullerton|Santiago Canyon) College",
    re.IGNORECASE,
)


def candidate_sites(bbox) -> gpd.GeoDataFrame:
    """Place-of-worship and college footprints in bbox, with `site_type`.

    Public campuses matching PUBLIC_COLLEGE are dropped. Remaining colleges must
    still be confirmed as independent nonprofits (Ed. Code § 66010).
    """
    faith = osm.polygons(FAITH_FILTER, bbox).assign(site_type="faith")
    college = osm.polygons(COLLEGE_FILTER, bbox).assign(site_type="college")
    college = college[~college["name"].str.contains(PUBLIC_COLLEGE)]
    sites = pd.concat([faith, college], ignore_index=True)
    sites["religion"] = sites["tags"].map(lambda t: t.get("religion", ""))
    sites["denomination"] = sites["tags"].map(lambda t: t.get("denomination", ""))
    return gpd.GeoDataFrame(sites, geometry="geometry", crs="EPSG:4326")


def add_parking_area(sites: gpd.GeoDataFrame, bbox, buffer_m: float = 30) -> gpd.GeoDataFrame:
    """Acres of surface parking on or touching each site (within buffer_m).

    Church lots are usually mapped as separate parking polygons next to the
    building, so the site is buffered before intersecting. Surface parking is
    the most realistic land to build on without touching the sanctuary.
    """
    parking = osm.polygons(PARKING_FILTER, bbox)
    sites_m = sites.to_crs(epsg=3310)
    parking_m = parking.to_crs(epsg=3310)
    buffered = sites_m.assign(geometry=sites_m.buffer(buffer_m))
    hits = gpd.sjoin(parking_m[["osm_id", "geometry"]], buffered[["geometry"]], predicate="intersects")
    # A lot shared by two neighbouring sites counts for each
    area = hits.assign(acres=hits.geometry.area / 4046.86).groupby("index_right")["acres"].sum()
    out = sites.copy()
    out["parking_acres"] = area.reindex(out.index).fillna(0)
    return out


def estimate_units(sites: gpd.GeoDataFrame, density: float = MULLIN_DENSITY["metropolitan"]) -> gpd.GeoDataFrame:
    """Rough by-right capacity: parking-only scenario and whole-site scenario.

    Ignores the one-story height bonus, density bonus law and adjoining-parcel
    density, all of which can only raise the number. Whole-site can double-count
    a lot that OSM maps inside the site footprint; treat it as an upper bound.
    """
    out = sites.copy()
    out["units_parking_only"] = (out["parking_acres"] * density).round()
    out["units_whole_site"] = ((out["area_acres"] + out["parking_acres"]) * density).round()
    return out
