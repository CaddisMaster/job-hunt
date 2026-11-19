# Testing

## Running

```bash
./test.sh                        # ruff, fresh apply_test DB at head, pytest -n auto
./test.sh tests/test_health.py -k head
SKIP_LINT=1 ./test.sh
```

`test.sh` reuses the running dev `web` container when it has the dev dependencies, otherwise it
builds a throwaway one. It drops and recreates `apply_test` and runs `alembic upgrade head` each
time, so tests always see the current schema.

## Conventions

- **Real Postgres, no SQL mocks.** Fixtures create rows with unique names and delete them
  afterwards, which is what makes `-n auto` safe.
- **Claim acceptance criteria** with `@pytest.mark.criterion(<issue>, "<Scenario title>")`.
  Both arguments must be literals; `scripts/check_criteria.py` parses the source.
- **Only the PR that finishes an issue closes it.** A PR doing part of the work says `Part of #N`.
- **`@pytest.mark.xdist_group` doesn't work on pytest 9.2.0 with xdist 3.8.0** (Orbit Ops #389).
  Nothing here groups.
- **Repository rules are tests too** (`tests/test_repo_hardening.py`): exact pins, digest-pinned
  images, prod as the final stage, localhost-only ports, memory limits, required `TAG`, SHA-pinned
  actions, `.env.example` completeness.

## CI

`ci.yml` runs Lint, Tests (with an Alembic `downgrade base` / `upgrade head` / `check`
round-trip) and Image builds on every PR and on `main`.
