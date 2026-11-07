# SoCal Housing Finder

> For a congregation's facilities chair or a small nonprofit's acquisitions lead: find faith and nonprofit-college land in Orange County where SB 4 ("Yes In God's Backyard") allows 100% affordable housing by right, with a yes / no / unknown for every site test and the source behind it.

**Team:** TBD
**Members:** Max Garcia, Tyler
**Lane:** 1 Find it
**Built at:** Claude Impact Lab: UCI × SoCal Affordable Housing, November 7, 2026

> Screening tool only. Not legal, financial or lending advice.

## The real user

_Made-up user, per the event rules (matches lane scenario F01)._ The facilities chair of a small Orange County congregation with a parking lot that sits half-empty except on weekends. They keep hearing churches can build housing now, but have no planner, no GIS and no idea which rules apply to their land.

**Before:** calls the city, waits, gets a vague answer; or a developer happens to knock.
**After:** types the congregation's address, sees whether SB 4 likely applies, which site tests pass, fail or are unknown, roughly how many homes fit on the lot, and what to confirm next.

## What we built

_TBD._

**Demo video:** TBD
**Live demo:** TBD

## How Claude is used

- _TBD: built with Claude Code._
- _TBD: Claude turns computed site facts into a plain-language memo; every fact it cites comes from a listed source._

## YIGBY: Yes In God's Backyard (SB 4)

California's **Affordable Housing on Faith and Higher Education Lands Act** (SB 4, 2023; Gov. Code § 65913.16, amended by AB 130 in 2025) lets 100% affordable housing be approved **by right**, with no discretionary review and no CEQA, on land owned by a **religious institution** or an **independent (private nonprofit) college** as of **January 1, 2024**. It is in effect from 2024 through **January 1, 2036**.

Why it matters: congregations often sit on underused land, especially parking lots, but don't know they can build or what would fit. Terner Center estimates about **171,000 potentially developable acres** statewide (about 47,000 faith-owned and 125,000 college-owned), and nearly half of it is in high-opportunity neighborhoods.

### Rules we encode (from HCD's April 2026 fact sheet and Hayward's SB 4 checklist)

| Rule | Requirement |
|---|---|
| Owner | Religious institution or independent higher-ed institution (or affiliated nonprofit), owned as of 2024-01-01 |
| Affordability | 100% lower-income; up to 20% may be moderate-income; up to 5% may house institution staff. Deed-restricted 55 yr rental / 45 yr ownership |
| Zones | Allowed in **all** zones, including single-family and non-residential |
| Density, residential zone | Mullin default density: 30 du/ac metropolitan county, 20 suburban, 15 non-metro city, 10 non-metro unincorporated. Or the adjoining parcel's density if higher |
| Density, non-residential zone | 40 du/ac, or the parcel's or an adjoining parcel's density if higher |
| Height | One story (or 11 ft) above the local limit |
| Parking | 1 space/unit, **none** within 0.5 mi walk of a high-quality transit corridor or major transit stop, or within 1 block of car share |
| Location | In a Census urbanized area / urban cluster; at least 75% of the site perimeter adjoins urban uses |
| Exclusions | Prime/statewide farmland, wetlands, very high fire hazard severity zone, hazardous waste site (unless cleared), earthquake fault zone, FEMA special flood hazard area or floodway, conservation easements or NCCP/HCP land, protected-species habitat, historic structure demolition |
| Industrial buffers | Not adjoining a site with more than 1/3 light industrial; more than 1,200 ft from heavy industrial; more than 1,600 ft from Title V industrial; more than 3,200 ft from oil and gas extraction or refining |
| Tenants | No demolition of deed-restricted, rent-controlled, or tenant-occupied (past 10 yrs) housing |
| Air quality | Within 500 ft of a freeway, MERV-13 filtration required (a cost flag, not an exclusion) |
| Ancillary uses | In single-family zones: childcare and community facilities. In other zones: by-right commercial uses on the ground floor |
| Labor | Prevailing wage; 50+ units add apprenticeship and health-care requirements |
| Review clock | City must flag conflicts within 60 days (≤150 units) or 90 days (>150), or the project is deemed compliant |

### Implementation ideas

**1. YIGBY site finder (core Find It product).** A map of every faith and private-college site in OC, each with an eligibility verdict and a unit estimate.
- Candidates: OSM `amenity=place_of_worship` and `amenity=university|college` footprints (`findit.yigby.candidate_sites`). Drop public UC/CSU/community colleges, since SB 4 covers only independent institutions.
- Buildable land: surface parking on or next to each site (`add_parking_area`). Parking lots are the realistic first build.
- Capacity: acres × Mullin density (`estimate_units`). Show a parking-only number and a whole-site number.
- Screening layers, each a pass / fail / unknown column with source and date. Verified endpoints from the event's [Lane 1 data list](https://github.com/travcjohnson/claude-impact-lab-uci/blob/main/lanes/1-find-it/data/DATA-SOURCES.md):
  - Parcels: OC Survey Parcels FeatureServer (APN, geometry; no owner field)
  - Zoning + acres + slope: SCAG `Zoning_poly_OR`; existing land use (find parking): SCAG `Existinglanduse_poly_OR`
  - Flood: FEMA NFHL layer 28 (slow, retry) or OC Floodplains
  - Fire: CAL FIRE Fire Hazard Severity Zones, OC FHSZ LRA
  - Environment / equity: CalEnviroScreen 5.0
  - Transit (parking waiver): Caltrans HQ Transit Areas, SCAG Major Transit Stops, OCTA GTFS
  - Opportunity: TCAC/HCD 2026 Opportunity Maps
  - Still unknown without a verified free layer: earthquake fault zones (CGS blocks automated fetch), wetlands, farmland, industrial buffers, tenant history. The tool says "unknown" for these.
- Ownership proxy: an OSM worship site on the parcel, plus an address match to the IRS Exempt Organizations Business Master File (religious NTEE codes). A proxy is a lead, not a fact.
- Parking waiver: 0 required spaces within 0.5 mi of a high-quality transit corridor or major transit stop. That changes what fits on a lot.

**2. "What could my congregation build?" one-pager.** Pick a site and get a pre-filled SB 4 eligibility checklist (modeled on Hayward's), unit range, parking requirement, nearby transit, groceries and schools, and next steps such as a qualified nonprofit developer list. Claude drafts the plain-language memo from the computed facts, so it is useful to a pastor rather than a planner.

**3. Rank sites by impact, not just capacity.** Combine units with need and opportunity: tract rent burden and severe rent burden (ACS B25070), poverty rate, no-vehicle households, jobs nearby (LODES), and TCAC/HCD Opportunity Map resource level. Higher-resource tracts also score better for tax-credit financing.

**4. Resident-facing flip side.** The same proximity features (transit, grocery, clinics, schools, jobs) power a "where can I afford to live and still get to work" search over existing affordable listings.

**Already working (Irvine, 2026-11-07):** 51 worship sites and 6 private-college sites from OSM, about 2,700 homes if only their adjacent surface parking were built at 30 du/ac (ASSUMPTION: metropolitan-county Mullin density; parking polygons from OSM, not parcels).

Caveats to state in the demo: OSM footprints are not legal parcels. Ownership as of 2024-01-01 needs an assessor or title check. Unit counts are pre-design estimates. This is a screening tool, not legal advice.

## Code

`findit/` holds reusable data helpers, adapted from our UCI Datathon 2026 In-N-Out site-selection project ([justinsiek/in-n-out](https://github.com/justinsiek/in-n-out), with Justin Siek, Austin H and Leo Wu-Hacohen) and reworked to download data in bulk instead of calling an API per point.

| Module | What |
|---|---|
| `findit.geo` | haversine, BallTree nearest-k / count-within-radius, proximity features, bbox grids, nearest-feature snapping |
| `findit.census` | tract polygons + local point-to-tract join, bulk ACS tract tables with housing variables (rent burden, rent, income, poverty, vehicles), LODES jobs by tract |
| `findit.osm` | Overpass queries (cached), POIs and polygons by tag, amenity presets |
| `findit.traffic` | Caltrans AADT counts, freeway ramps |
| `findit.maps` | folium grid heatmaps and tract choropleths on a percentile colour scale |
| `findit.yigby` | SB 4 candidate sites, adjacent parking area, unit estimates |

Region constants: `findit.IRVINE_BBOX`, `ORANGE_COUNTY_BBOX`, `SOCAL_BBOX`, `SOCAL_COUNTIES`.

## Try it

```bash
uv sync
cp .env.example .env   # add a free Census API key: https://api.census.gov/data/key_signup.html
uv run python examples/irvine_demo.py
```

Needs [uv](https://docs.astral.sh/uv/) (installs Python 3.13 and pinned deps from `uv.lock`). Downloads are cached in `data/cache/` (gitignored). Never commit `.env`; this repo is public.

## Sources and assumptions

| Item | Source or assumption | Date checked |
|---|---|---|
| SB 4 rules | HCD fact sheet (April 2026), Hayward SB 4 checklist (July 2025) | 2026-11-07 |
| Worship and college sites | OpenStreetMap via Overpass (ODbL) | 2026-11-07 |
| Tract data | Census ACS 2023 5-year, TIGER/cartographic tracts 2023, LODES8 2022 | 2026-11-07 |
| 30 du/ac default density | ASSUMPTION: Irvine treated as metropolitan-county jurisdiction; confirm against HCD's Mullin density list | 2026-11-07 |
| Buildable area = adjacent surface parking | ASSUMPTION | 2026-11-07 |

## What is real and what is mocked

| Part | Status |
|---|---|
| Candidate faith / private-college sites (OSM) | working |
| Adjacent parking area and unit estimate | working, rough |
| Hazard and zoning screens | not built yet |
| Ownership confirmation | not possible from free data; proxy only |

## Where it gets things wrong

- OSM footprints are not legal parcels; some churches are mapped as a point or a building only.
- Public-college filter is a name list; an unlisted public campus could slip through.
- Parking lots shared between neighbours are counted for each site.

## Data and privacy

This project uses only public or provided data. It contains no personal information about real tenants, buyers or residents. It does not file applications or contact any city or person.

## License

MIT, see [LICENSE](LICENSE).

## References

- [HCD Housing Law Fact Sheet: Affordable Housing on Faith & Higher Education Lands Act (April 2026)](https://www.hcd.ca.gov/sites/default/files/docs/planning-and-community/aff-hsg-faith-higher-ed-sb-4.pdf)
- [City of Hayward SB 4 eligibility checklist (July 2025)](https://hayward-ca.gov/sites/default/files/documents/Senate-Bill-4-Checklist.pdf)
- [Terner Center: Faith-Based and College Land Housing (2023)](https://ternercenter.berkeley.edu/blog/faith-based-and-college-land-housing)
