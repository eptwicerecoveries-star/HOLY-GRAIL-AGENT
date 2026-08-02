# SurplusAI

An operating system for a surplus funds recovery business: county surplus PDFs in,
qualified leads out. See `PROJECT.md` for the mission and `ARCHITECTURE.md` for the full
design and phase roadmap.

**Current status: Phase 1 complete, Phase 2A (extraction core) complete.**

Implemented: configuration, logging, the database schema and migrations, the `surplusai db`
CLI, and the county-agnostic PDF extraction pipeline behind `surplusai parser`. Extraction
reads every column exactly as published and keeps the county's own column names.

Not yet implemented: mapping those columns onto a universal schema, surplus determination,
OCR, county profile learning (Phases 2B and 2C), and everything from classification onward.
Because column interpretation is Phase 2B, the parser currently reports a county's headers
verbatim and makes no claim about which column, if any, represents surplus funds.

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

surplusai parser classify data/test_pdfs/St_Marys_County_MD.pdf
surplusai parser inspect data/test_pdfs/Harford_County_MD.pdf --rows 5
surplusai parser inspect data/test_pdfs/Calvert_County_MD.pdf --json
```

`db init` and `db seed` are both idempotent — running them repeatedly is safe.
`parser inspect` writes nothing; it parses and prints, which is how a new county is
assessed before onboarding.

### How parsing works

No stage branches on which county produced a file. Several extraction strategies run and
compete on structural quality — column consistency, cell fill rate, per-column type
coherence, header plausibility — so an unfamiliar layout takes the same path as a known one.

Three behaviours are worth knowing before adding a county:

- **Searchable vs scanned is decided per region, not per document.** A page can carry
  headings, disclaimers and navigation text while the table itself is a raster image.
  Asking only whether a PDF has a text layer classifies such a page as searchable and
  yields nothing. When a data region needs OCR, parsing fails with `OCRRequiredError`
  rather than returning the surrounding text as if it were data.
- **Header position and wrapping are detected, not assumed.** Headers may sit below a title
  and a date line, and labels wrap across physical lines. Wrapped labels are rejoined by
  word position rather than reading order, because the linearized text can pair the
  continuation word with the wrong column.
- **Nothing is discarded.** Titles, footers and banner lines that are not data rows are
  retained as `unparsed_fragments`. Ragged rows keep their extra cells under overflow keys,
  and duplicate or blank column names are given positional suffixes so no column collapses
  into another.

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

### The county corpus

Parser tests are driven by the PDFs in `data/test_pdfs/`. Discovery happens at collection
time, so **dropping a new county PDF into that folder adds it to every corpus-wide test
with no code change**. Add a matching `data/test_pdfs/expected/<name>.json` to assert that
county's structure too — page count, table count, row count, headers, spot-check rows, and
any text that must survive as a fragment.

Where a document declares its own record count, record it as `declared_row_count`; the
suite then asserts extraction reproduces that number exactly. It is the strongest available
check on row recall because it comes from the publisher rather than from us, and it catches
both dropped rows and repeated header rows miscounted as data.

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
    parser/       document classification, extraction strategies, quality scoring,
                  header reconstruction, multi-page stitching, pipeline
    utils/        settings, logging, base exceptions
tests/
    unit/         fast tests, transaction-rolled-back DB access, corpus parser tests
    integration/  full CLI + migration lifecycle against a real database
config/           configuration for counties, states, scoring (later phases)
data/test_pdfs/   county PDF corpus plus golden expectations
docs/             architecture decisions and runbooks
```
