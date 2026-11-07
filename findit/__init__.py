"""Geo + Census helpers for the Find It track.

Adapted from the UCI Datathon 2026 In-N-Out site-selection project
(github.com/justinsiek/in-n-out) by Justin Siek, Austin H, Leo Wu-Hacohen
and Max Garcia, then reworked to bulk-download data instead of making
per-point API calls.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Bounding boxes as (south, west, north, east)
IRVINE_BBOX = (33.63, -117.90, 33.77, -117.72)
ORANGE_COUNTY_BBOX = (33.38, -118.13, 33.95, -117.41)
SOCAL_BBOX = (32.53, -119.45, 35.81, -114.13)

CA_FIPS = "06"
SOCAL_COUNTIES = {
    "Los Angeles": "037",
    "Orange": "059",
    "Riverside": "065",
    "San Bernardino": "071",
    "San Diego": "073",
    "Ventura": "111",
    "Imperial": "025",
}
