# Hiring-system API responses (#44)

Trimmed responses in the exact shape each public job-board API answered on 2026-11-19
(field names and nesting copied from live responses), with made-up jobs and text. The tests
never reach the network.

`TOKEN` in an id is replaced by each test's own board token, so tests running in parallel
never share a posting.

- `greenhouse.json`: `GET boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`.
  `content` is entity-encoded HTML, as Greenhouse sends it.
- `lever.json`: `GET api.lever.co/v0/postings/<token>?mode=json`.
- `ashby.json`: `GET api.ashbyhq.com/posting-api/job-board/<token>`.

## The aggregators (#45)

**Unlike the three above, these weren't copied from a live answer:** Job Hunt had no keys
yet. They follow the shapes in each API's docs (read 2026-11-20), including the docs' own
mismatches (USAJobs' `MatchedObjectId` as a string and a number). Replace them with a trimmed
live answer once the keys exist.

- `adzuna.json`: `GET api.adzuna.com/v1/api/jobs/us/search/<page>?what=…&where=…`.
- `usajobs.json`: `GET data.usajobs.gov/api/search?Keyword=…&LocationName=…&Radius=50`.
  The second job's `MatchedObjectId` is a number; the tests make it unique.
