"""End-to-end smoke test: score an Irvine grid on transit + grocery access, map it.

    uv run python examples/irvine_demo.py

Adds rent-burden tract data too when CENSUS_API_KEY is set.
"""

import os

from findit import IRVINE_BBOX, SOCAL_COUNTIES
from findit import census, geo, maps, osm

N = 40

grid = geo.grid(IRVINE_BBOX, n=N)
grid = census.points_to_tracts(grid)
print(f"{len(grid)} grid points, {grid['GEOID'].notna().sum()} inside a tract")

bus = osm.pois(osm.AMENITIES["bus_stop"], IRVINE_BBOX)
grocery = osm.pois(osm.AMENITIES["grocery"], IRVINE_BBOX)
print(f"{len(bus)} bus stops, {len(grocery)} grocery stores")

grid = geo.add_proximity_features(grid, bus, "bus_stop", k=5, radius_km=0.8)
grid = geo.add_proximity_features(grid, grocery, "grocery", k=3, radius_km=1.6)

jobs = census.jobs_by_tract()
grid = grid.merge(jobs, left_on="GEOID", right_index=True, how="left")

# Toy access score: more stops nearby, closer groceries, more jobs in tract
grid["score"] = (
    grid["bus_stop_within_0.8km"].rank(pct=True)
    + (-grid["grocery_nearest_km"]).rank(pct=True)
    + grid["jobs_wac"].rank(pct=True)
)
grid.loc[grid["GEOID"].isna(), "score"] = None

m = maps.grid_heatmap(grid, IRVINE_BBOX, N)
maps.add_markers(m, grocery, color="green", icon="shopping-cart")
m.save("irvine_access.html")
print("Wrote irvine_access.html")

if os.getenv("CENSUS_API_KEY"):
    acs = census.acs_tracts([SOCAL_COUNTIES["Orange"]])
    tracts = census.tract_shapes().merge(acs, on="GEOID")
    tracts = tracts[tracts["county"] == SOCAL_COUNTIES["Orange"]]
    print(acs[["population", "median_gross_rent", "rent_burdened_share"]].describe().round(2))
    maps.choropleth(tracts, "rent_burdened_share", IRVINE_BBOX, label="Rent-burdened share").save("oc_rent_burden.html")
    print("Wrote oc_rent_burden.html")
else:
    print("CENSUS_API_KEY not set; skipped ACS rent-burden map")
