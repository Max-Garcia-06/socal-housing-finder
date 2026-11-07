"""Download every public layer the SB 4 church-site screen needs, once, into data/raw/.

    uv run python scripts/fetch_layers.py

All sources are free public ArcGIS REST services or downloads (see README "Sources").
Each request is paged and cached; re-running skips layers already on disk.
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "socal-housing-finder (UCI Claude Impact Lab hackathon)"}
OC_ENV = "-118.13,33.38,-117.41,33.95"  # west,south,east,north

LAYERS = {
    # Parcel-level existing land use, SCAG 2024. LU24 1245 = "Religious Facilities"
    # (SCAG land use legend). Orange County only service.
    "religious_parcels": dict(
        url="https://maps.scag.ca.gov/scaggis/rest/services/LDX/Existinglanduse_poly_OR/MapServer/0",
        where="LU24='1245'", fields="APN24,CITY,ACRES,SLOPE,GEOID20,LU24,LU24_2ND,YEAR", page=1000),
    # Industrial parcels (SCAG LU24 13xx) for SB 4's industrial-adjacency and buffer tests
    "industrial": dict(
        url="https://maps.scag.ca.gov/scaggis/rest/services/LDX/Existinglanduse_poly_OR/MapServer/0",
        where="LU24 LIKE '13%'", fields="APN24,LU24", page=2000),
    # Housing-element sites (adopted 6th cycle), to flag church parcels a city already counts
    "he_sites": dict(
        url="https://maps.scag.ca.gov/scaggis/rest/services/LDX/HousingElementSite_poly_scag/MapServer/0",
        where="COUNTY='Orange'", fields="APN24,Jurisdiction,Low_Income_HE_Capacity,Total_HE_Capacity", page=1000,
        geometry=False),
    # FEMA special flood hazard areas, county copy of the DFIRM
    "flood": dict(
        url="https://www.ocgis.com/arcpub/rest/services/Map_Layers/Floodplains/MapServer/0",
        where="SFHA_TF='T'", fields="FLD_ZONE,ZONE_SUBTY,SFHA_TF", page=500),
    # CAL FIRE FHSZ, local responsibility areas (county copy) and state responsibility areas
    "fire_lra": dict(
        url="https://www.ocgis.com/arcpub/rest/services/Map_Layers/Fire_Hazard_Severity_Zones_LRA/MapServer/0",
        where="1=1", fields="HAZ_CODE,HAZ_CLASS,INCORP", page=500),
    "fire_sra": dict(
        url="https://services.gis.ca.gov/arcgis/rest/services/Environment/Fire_Severity_Zones/MapServer/0",
        where="1=1", fields="*", page=500, envelope=True),
    # Caltrans high-quality transit areas (parking waiver test)
    "hqta": dict(
        url="https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHrailroad/CA_HQ_Transit_Areas/FeatureServer/0",
        where="1=1", fields="agency_primary,hqta_type,hqta_details", page=1000, envelope=True),
    # CalGEM active/idle/new oil & gas wells (3,200 ft extraction-facility buffer)
    "wells": dict(
        url="https://gis.conservation.ca.gov/server/rest/services/WellSTAR/Wells/MapServer/0",
        where="WellStatus IN ('Active','Idle','New')", fields="API,WellStatus,WellTypeLabel,FieldName,OperatorName",
        page=1000, envelope=True),
    # CalEnviroScreen 5.0 tracts
    "ces": dict(
        url="https://services1.arcgis.com/PCHfdHz4GlDNAhBb/arcgis/rest/services/calenviroscreen50results_F_070126_gdb/FeatureServer/2",
        where="county='Orange'", fields="tract,CIscore,CIscoreP,Population,ApproxLoc,Poverty,Poverty_Pctl,HousBurd,HousBurd_Pctl", page=1000),
    # IRS-registered places of worship (EO BMF, geocoded), HIFLD Open copy: names + EIN
    "irs_worship": dict(
        url="https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/All_Places_Of_Worship__HiFLD_Open_/FeatureServer/42",
        where="1=1", fields="EIN,NAME,STREET,CITY,ZIP,NTEE_CD,SCORE,MATCH_TYPE", page=2000, envelope=True),
}

DOWNLOADS = {
    # TCAC/HCD 2026 Opportunity Map (adopted Dec 2025)
    "tcac_2026": "https://www.treasurer.ca.gov/sites/default/files/2025-11/shapefile.zip",
}


def query(name, url, where, fields, page, geometry=True, envelope=False):
    out = RAW / f"{name}.geojson"
    if out.exists():
        print(f"skip {name}")
        return
    feats, offset = [], 0
    while True:
        params = {"where": where, "outFields": fields, "outSR": 4326, "f": "geojson",
                  "returnGeometry": str(geometry).lower(), "resultOffset": offset, "resultRecordCount": page}
        if envelope:
            params |= {"geometry": OC_ENV, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                       "spatialRel": "esriSpatialRelIntersects"}
        for attempt in range(4):
            try:
                r = requests.post(f"{url}/query", data=params, headers=HEADERS, timeout=180)
                r.raise_for_status()
                batch = r.json()
                break
            except Exception as e:  # FEMA/CAL FIRE servers time out on first call
                print(f"  {name} retry {attempt}: {e}")
                time.sleep(3)
        else:
            raise RuntimeError(name)
        got = batch.get("features", [])
        feats += got
        print(f"  {name}: {len(feats)}")
        if len(got) < page and not batch.get("exceededTransferLimit"):
            break
        offset += len(got)
        time.sleep(0.5)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"wrote {name} ({len(feats)})")


def download(name, url):
    out = RAW / name
    if out.exists():
        print(f"skip {name}")
        return
    r = requests.get(url, headers=HEADERS, timeout=300)
    r.raise_for_status()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(out)
    print(f"wrote {name}")


def socal_counties():
    """Census cartographic-boundary (land-clipped) county outlines for OC and neighbours."""
    out = RAW / "socal_counties.geojson"
    if out.exists():
        print("skip socal_counties")
        return
    import geopandas as gpd
    r = requests.get("https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip", headers=HEADERS, timeout=300)
    r.raise_for_status()
    d = RAW / "cb_county"
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(d)
    g = gpd.read_file(d / "cb_2023_us_county_500k.shp")
    g = g[(g.STATEFP == "06") & g.COUNTYFP.isin(["037", "059", "065", "071", "073", "111", "029", "025"])]
    g = g[["NAME", "COUNTYFP", "geometry"]].rename(columns={"COUNTYFP": "COUNTY"}).to_crs(4326)
    g["geometry"] = g.geometry.simplify(0.0015)
    out.write_text(g.to_json(drop_id=True))
    print("wrote socal_counties")


def city_contacts():
    """County GIS jurisdiction contacts: each city's Community Development phone and website (public offices)."""
    out = RAW / "city_contacts.json"
    if out.exists():
        print("skip city_contacts")
        return
    r = requests.post("https://www.ocgis.com/arcpub/rest/services/Map_Layers/Jurisdiction_Contacts/MapServer/0/query",
                      data={"where": "1=1", "outFields": "Jurisdiction,City_Website,City_Hall__,Community_Dev__,Community_Dev",
                            "returnGeometry": "false", "f": "json"}, headers=HEADERS, timeout=120)
    r.raise_for_status()
    out.write_text(json.dumps([f["attributes"] for f in r.json()["features"]]))
    print("wrote city_contacts")


def city_bounds():
    """OC city outlines (county GIS) for the tile-free standalone map."""
    out = RAW / "city_bounds.geojson"
    if out.exists():
        print("skip city_bounds")
        return
    r = requests.post("https://www.ocgis.com/arcpub/rest/services/Map_Layers/City_Boundaries/MapServer/0/query",
                      data={"where": "1=1", "outFields": "CITY", "outSR": 4326, "maxAllowableOffset": 0.0005,
                            "geometryPrecision": 5, "f": "geojson"}, headers=HEADERS, timeout=120)
    r.raise_for_status()
    out.write_text(r.text)
    print("wrote city_bounds")


def zoning_for_religious():
    """Zoning (SCAG Zoning_poly_OR) for every religious parcel APN, no geometry."""
    out = RAW / "religious_zoning.json"
    if out.exists():
        print("skip religious_zoning")
        return
    apns = [f["properties"]["APN24"] for f in json.loads((RAW / "religious_parcels.geojson").read_text())["features"]]
    url = "https://maps.scag.ca.gov/scaggis/rest/services/LDX/Zoning_poly_OR/MapServer/0/query"
    rows = []
    for i in range(0, len(apns), 150):
        chunk = ",".join(f"'{a}'" for a in apns[i:i + 150])
        r = requests.post(url, data={"where": f"APN24 IN ({chunk})", "outFields": "APN24,ZN24_CITY,ZN24_SCAG",
                                     "returnGeometry": "false", "f": "json"}, headers=HEADERS, timeout=120)
        r.raise_for_status()
        rows += [f["attributes"] for f in r.json().get("features", [])]
        time.sleep(0.5)
    out.write_text(json.dumps(rows))
    print(f"wrote religious_zoning ({len(rows)})")


if __name__ == "__main__":
    only = set(sys.argv[1:])
    for name, spec in LAYERS.items():
        if not only or name in only:
            try:
                query(name, **spec)
            except Exception as e:
                print(f"FAILED {name}: {e}")
    if not only or "zoning" in only:
        zoning_for_religious()
    if not only or "context" in only:
        socal_counties()
        city_bounds()
        city_contacts()
    for name, url in DOWNLOADS.items():
        if not only or name in only:
            try:
                download(name, url)
            except Exception as e:
                print(f"FAILED {name}: {e}")
