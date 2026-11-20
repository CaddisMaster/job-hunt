"""#42: is a posting in Greater Pittsburgh? Location strings in the shapes the sources really
send, each with the answer it must get. Offline: app/data/places.csv, no network, no model."""

import csv

import pytest

from app import area
from app.area import Where

IN, REMOTE, OUT, UNKNOWN = Where.IN_AREA, Where.REMOTE, Where.OUT_OF_AREA, Where.UNKNOWN

CASES = [
    # the city, written every way the sources write it
    ("Pittsburgh, PA", IN),
    ("Pittsburgh, Pennsylvania", IN),
    ("Pittsburgh, Pennsylvania, United States", IN),
    ("Pittsburgh, PA 15222", IN),
    ("Pittsburgh, PA, USA", IN),
    ("Pittsburgh Pennsylvania", IN),
    ("Pittsburgh", IN),
    ("PITTSBURGH, PA", IN),
    ("Greater Pittsburgh Area", IN),
    ("Pittsburgh Metropolitan Area", IN),
    ("Pgh", IN),
    # towns, boroughs and townships in the seven counties
    ("Cranberry Township, PA", IN),
    ("Cranberry Twp., Pennsylvania", IN),
    ("Mt. Lebanon, PA", IN),
    ("Mount Lebanon, PA", IN),
    ("Monroeville, PA", IN),
    ("Canonsburg, PA", IN),
    ("Greensburg, PA", IN),
    ("Butler, PA", IN),
    ("Uniontown, PA", IN),
    ("Kittanning, PA", IN),
    ("Beaver Falls, PA", IN),
    ("Washington, PA", IN),
    ("Allegheny County, PA", IN),
    ("Westmoreland County, Pennsylvania", IN),
    # within 50 miles, over a state or county line
    ("Weirton, WV", IN),
    ("Steubenville, OH", IN),
    # hybrid and on-site in the area are in the area
    ("Hybrid (Pittsburgh)", IN),
    ("Hybrid - Pittsburgh, PA", IN),
    ("On-site: Pittsburgh, PA", IN),
    # post-office names, which aren't Census places (GeoNames)
    ("Wexford, PA", IN),
    ("Warrendale, PA 15086", IN),
    ("Gibsonia, Pennsylvania", IN),
    ("Wexford, Pennsylvania, United States", IN),
    # a neighbourhood or a missing comma
    ("Strip District, Pittsburgh, PA", IN),
    ("Southpointe, Canonsburg, PA", IN),
    ("Sewickley PA", IN),
    ("Canonsburg PA 15317", IN),
    ("Columbus OH", UNKNOWN),  # a two-letter code without a comma only ever says yes
    ("Seattle WA", UNKNOWN),
    ("Erie Pennsylvania", OUT),  # spelled out, it's certainly a state
    # a township here that shares its name with a town elsewhere in Pennsylvania
    ("Lancaster, PA", UNKNOWN),
    ("Fairfield, Pennsylvania", UNKNOWN),
    ("Hampton, PA", UNKNOWN),
    ("Hampton Township, PA", IN),
    ("Hampton Twp., PA", IN),
    ("Richland Township, Pennsylvania", IN),
    ("Lancaster, PA; Pittsburgh, PA", IN),
    # another country, or a region of the world
    ("Tokyo, Japan", OUT),
    ("London, England", OUT),
    ("London, United Kingdom", OUT),
    ("Bangalore, India", OUT),
    ("Toronto, ON", OUT),
    ("Toronto, Ontario, Canada", OUT),
    ("Europe", OUT),
    ("Tbilisi, Georgia", OUT),  # read as the state, and still not here
    ("Washington, D.C.", OUT),
    ("Sewickley, Pa.", IN),
    ("Remote (US); Europe", REMOTE),
    ("Pittsburgh, PA; London, England", IN),
    # several locations: in the area if any one is
    ("New York, NY; Pittsburgh, PA", IN),
    ("Boston, MA | Cranberry Township, PA", IN),
    ("Remote or Pittsburgh, PA", IN),
    # remote
    ("Remote", REMOTE),
    ("Remote - US", REMOTE),
    ("Remote, United States", REMOTE),
    ("United States (Remote)", REMOTE),
    ("Work from home", REMOTE),
    ("Remote - New York, NY", REMOTE),
    # somewhere else
    ("Pittsburg, KS", OUT),
    ("Pittsburg, CA", OUT),
    ("New York, NY", OUT),
    ("Philadelphia, PA", OUT),
    ("Harrisburg, Pennsylvania", OUT),
    ("Erie, PA", OUT),
    ("Columbus, OH", OUT),
    ("Seattle, Washington", OUT),
    ("Washington, DC", OUT),
    ("Greene County, PA", OUT),
    ("Cook County, IL", OUT),
    # can't tell: kept out
    ("", UNKNOWN),
    (None, UNKNOWN),
    ("Washington", UNKNOWN),  # a bare name shared with places elsewhere
    ("Cranberry", UNKNOWN),
    ("Multiple Locations", UNKNOWN),
    ("United States", UNKNOWN),
    ("TBD", UNKNOWN),
]


@pytest.mark.parametrize(("location", "expected"), CASES, ids=[str(c[0]) for c in CASES])
def test_where_a_posting_is(location, expected):
    assert area.classify(location) == expected


@pytest.mark.criterion(42, "A town in the seven counties is in the area")
def test_a_town_in_the_seven_counties_is_in_the_area():
    assert area.classify("Cranberry Township, PA") == IN


@pytest.mark.criterion(42, "Another Pittsburgh isn't")
def test_another_pittsburgh_isnt():
    assert area.classify("Pittsburg, KS") == OUT


@pytest.mark.criterion(42, "Remote is told apart")
def test_remote_is_told_apart():
    assert area.classify("Remote - US") == REMOTE


@pytest.mark.criterion(42, "An unknown location is kept out")
def test_an_unknown_location_is_kept_out():
    assert area.classify("Multiple Locations") == UNKNOWN


@pytest.mark.parametrize(
    ("lat", "lon", "text", "expected"),
    [
        (40.4406, -79.9959, None, IN),  # downtown
        (40.6, -80.4, "Somewhere new", IN),  # 25 miles out, a name the table lacks
        (39.95, -75.17, None, UNKNOWN),  # Philadelphia's coordinates, no text
        (39.95, -75.17, "Philadelphia, PA", OUT),
    ],
)
def test_coordinates_within_50_miles_are_in_the_area(lat, lon, text, expected):
    assert area.classify(text, lat, lon) == expected


def test_the_radius_is_50_miles():
    assert area.miles_from_downtown(*area.DOWNTOWN) == 0
    assert 255 < area.miles_from_downtown(39.9526, -75.1652) < 265  # Philadelphia, about 258


def test_the_place_table_covers_all_seven_counties():
    with area.PLACES.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    counties = {r["county"] for r in rows if r["county"]}
    assert counties == {"Allegheny", "Armstrong", "Beaver", "Butler", "Fayette", "Washington", "Westmoreland"}
    assert sum(1 for r in rows if r["county"] == "Allegheny") > 120  # 130 municipalities
    assert all(r["state"] in {"PA", "OH", "WV"} for r in rows)
    for r in rows:  # everything without a county is there because it's within the radius
        if not r["county"]:
            assert area.miles_from_downtown(float(r["lat"]), float(r["lon"])) <= area.RADIUS_MILES
