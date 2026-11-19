# app/data

## places.csv

The place names `app/area.py` recognises as Greater Pittsburgh (#42). It's built by
`scripts/build_places.py`, which says how. Rebuild it from newer source files rather than editing
it by hand.

- **Municipalities and Census places:** U.S. Census Bureau, 2024 Gazetteer Files (county
  subdivisions and places). Public domain.
- **Post-office names (not in the table yet, #42):** the build script can add the seven
  counties' post-office names (Wexford, Warrendale…) from [GeoNames](https://www.geonames.org/)
  US postal codes, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). When
  they're added, this line becomes the attribution: place names © GeoNames, filtered to the seven
  counties, with each one's county and coordinates.
