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
