# app/data

## places.csv

The place names `app/area.py` recognises as Greater Pittsburgh (#42). It's built by
`scripts/build_places.py`, which says how. Rebuild it from newer source files rather than editing
it by hand.

- **Municipalities and Census places:** U.S. Census Bureau, 2024 Gazetteer Files (county
  subdivisions and places). Public domain.
- **Post-office names:** place names © [GeoNames](https://www.geonames.org/), from its US postal
  codes file (`US.zip`, fetched 2026-11-19), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Changes: filtered to the seven
  counties, one row per name, with each one's county and coordinates. They add the names job
  postings use that aren't municipalities (Wexford, Warrendale, Gibsonia…).

## employers.csv

The Pittsburgh starter list for the employer registry (Admin → The employer registry → "Add the
starter list"). Each row is an employer whose own Greenhouse, Lever or Ashby job board answered
its public API on 2026-11-19 with at least one job that `app/area.py` placed in the area.

How it was built: about 330 candidate employers (Pittsburgh companies, and national ones with
Pittsburgh offices) were tried on all three systems under the obvious board names; 99 boards
answered, and the 25 with jobs in the area are here. Employers on Workday, Taleo and other systems
aren't (#59, #45). A board that has since closed shows as an error on its employer, not a failure.

