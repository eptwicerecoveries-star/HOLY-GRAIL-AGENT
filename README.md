# SurplusAI

An operating system for a surplus funds recovery business: county surplus PDFs in,
qualified leads out. See `PROJECT.md` for the mission and `ARCHITECTURE.md` for the full
design and phase roadmap.

**Current status: Phase 1 (Foundations) complete.** The database schema, configuration,
logging, migrations, and the `surplusai db` CLI are implemented and tested. Parsing,
classification, compliance, research, scoring, CRM sync, and reporting are later phases and
are not implemented yet.

---

## Requirements

- Python 3.12+
- PostgreSQL 15+ (16 recommended)
- `pg_dump` / `pg_restore` on `PATH` for `surplusai db backup`

## Setup

Start a database (either use your own Postgres or the bundled compose file):

```bash
docker compose up -d postgres
```

Create a virtualenv and install the project:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Configure the environment:

```bash
cp .env.example .env
```

`.env` is git-ignored and is the only place credentials live. Every setting can also be
supplied as a `SURPLUS_AI_`-prefixed environment variable, which takes precedence:

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SURPLUS_AI_DATABASE_URL` | yes | — | Must use the `postgresql+psycopg://` scheme |
| `SURPLUS_AI_ENV` | no | `dev` | `dev`, `test`, or `prod` |
| `SURPLUS_AI_LOG_LEVEL` | no | `INFO` | Standard logging levels |
| `SURPLUS_AI_LOG_DIR` | no | `logs` | Rotating JSON logs are written here |

## Usage

```bash
surplusai --version
surplusai db init       # verify connectivity, then create/upgrade the schema
surplusai db status     # applied revision vs. newest available revision
surplusai db migrate    # apply pending migrations
surplusai db seed       # dev-only reference data; refuses to run when env != dev
surplusai db backup path/to/backup.dump
```

`db init` and `db seed` are both idempotent — running them repeatedly is safe.

## Development

```bash
pytest              # unit + integration tests
ruff check .        # lint
black .             # format
mypy surplus_ai     # strict type check
pre-commit install  # run lint/format/types on every commit
```

Integration tests need a reachable Postgres. They use `surplus_ai_test` by default and
recreate the `public` schema, so never point them at a database holding real data:

```bash
export SURPLUS_AI_TEST_DATABASE_URL="postgresql+psycopg://surplus_ai:...@localhost:5432/surplus_ai_test"
```

If no test database is reachable, those tests skip rather than fail.

### Changing the schema

1. Edit or add a model under `surplus_ai/database/models/` and export it from that
   package's `__init__.py`.
2. `alembic revision --autogenerate -m "describe the change"`
3. Review the generated file. Autogenerate does **not** emit `DROP TYPE` for Postgres
   enums, so if you add an enum, add its type name to the migration's downgrade path the
   way `9f2ecd86a736_initial_schema.py` does — otherwise downgrade leaves the type behind
   and the next upgrade fails.
4. `black surplus_ai/database/migrations/versions/` — generated files are not formatted.
5. `pytest tests/integration/test_db_lifecycle.py` — this runs `alembic check` to prove the
   migration and the models have not drifted, and exercises a full
   upgrade → downgrade → upgrade cycle.

## Layout

```
surplus_ai/
    cli/          Typer CLI (surplusai)
    database/     ORM models, engine/session, Alembic migrations, seed data
    utils/        settings, logging, base exceptions
tests/
    unit/         fast tests, transaction-rolled-back DB access
    integration/  full CLI + migration lifecycle against a real database
config/           configuration for counties, states, scoring (later phases)
docs/             architecture decisions and runbooks
```
