"""Build app/data/places.csv, the place names app/area.py recognises (#42).

Source: the Census Bureau's Gazetteer files (public domain), 2024 edition:
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/
  2024_Gaz_cousubs_national.zip and 2024_Gaz_place_national.zip

Three kinds of rows:
- **Every municipality in the seven counties.** In Pennsylvania each city, borough and township
  is a county subdivision, and its GEOID carries the county code, so this is exact.
- **Every place within RADIUS_MILES of downtown**, in PA, OH or WV (cities, boroughs and the
  Census-designated places, like Monroeville's neighbours, that aren't municipalities). The area
  is the seven counties *and* that radius (plan §4), so a town just over a county line but close
  in counts, as a posting with coordinates would.
- **Post-office names in the seven counties**, which job postings use as often as the township's
  own name (Wexford is in Pine and McCandless; Warrendale is in Marshall). From GeoNames' US
  postal codes, https://download.geonames.org/export/zip/US.zip, licensed CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/): place names © GeoNames, attributed in
  app/data/README.md.

Run:  python3 -I scripts/build_places.py <cousubs.txt> <places.txt> <geonames US.txt> > app/data/places.csv
"""

import csv
import math
import re
import sys

COUNTIES = {  # FIPS county codes in Pennsylvania (42)
    "003": "Allegheny", "005": "Armstrong", "007": "Beaver", "019": "Butler",
    "051": "Fayette", "125": "Washington", "129": "Westmoreland",
}
DOWNTOWN = (40.4406, -79.9959)
RADIUS_MILES = 50
STATES = {"PA", "OH", "WV"}
# What the Census appends to a name; a job posting says "Cranberry Township" or just "Cranberry".
SUFFIX = re.compile(r"\s+(township|borough|city|town|village|municipality|CDP|CCD|UT)$", re.I)


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def rows(path: str):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        for row in reader:
            yield dict(zip(header, (c.strip() for c in row), strict=True))  # a malformed file fails loudly


def main(cousubs: str, places: str, postal: str | None = None) -> None:
    out = {}
    for r in rows(cousubs):
        county = COUNTIES.get(r["GEOID"][2:5]) if r["USPS"] == "PA" else None
        if county and not r["NAME"].startswith("County subdivisions not defined"):
            name = SUFFIX.sub("", r["NAME"])
            out[(name.casefold(), "PA")] = (name, "PA", county, r["INTPTLAT"], r["INTPTLONG"])
    for r in rows(places):
        if r["USPS"] not in STATES:
            continue
        lat, lon = float(r["INTPTLAT"]), float(r["INTPTLONG"])
        if miles(*DOWNTOWN, lat, lon) <= RADIUS_MILES:
            name = SUFFIX.sub("", r["NAME"])
            out.setdefault((name.casefold(), r["USPS"]), (name, r["USPS"], "", r["INTPTLAT"], r["INTPTLONG"]))
    if postal:
        wanted = set(COUNTIES.values())
        with open(postal, encoding="utf-8", newline="") as f:
            for r in csv.reader(f, delimiter="\t"):
                # country, postal code, place, state name, state code, county, ..., lat, lon, accuracy
                if r[4] == "PA" and r[5] in wanted:
                    out.setdefault((r[2].casefold(), "PA"), (r[2], "PA", r[5], r[9], r[10]))
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(["name", "state", "county", "lat", "lon"])
    for key in sorted(out):
        writer.writerow(out[key])


if __name__ == "__main__":
    main(*sys.argv[1:4])
