# SurplusAI

An operating system for a surplus funds recovery business: county surplus PDFs in,
qualified leads out. See `PROJECT.md` for the mission and `ARCHITECTURE.md` for the full
design and phase roadmap.

**Current status: Phase 1 and all of Phase 2 (2A extraction, 2B interpretation,
2C OCR and profile learning) complete.**

Implemented: configuration, logging, the database schema and migrations, the `surplusai db`
CLI, county-agnostic PDF extraction including OCR for scanned tables, interpretation of a
county's own columns onto a universal schema including the surplus rule, and append-only
county profile learning.

Not yet implemented: everything from owner classification onward (Phase 3 and later), plus
two Phase 2 follow-ups noted at the end of this file.

---

## Requirements

- Python 3.12+
- PostgreSQL 15+ (16 recommended)
- `pg_dump` / `pg_restore` on `PATH` for `surplusai db backup`
- `tesseract-ocr` and `poppler-utils` for reading scanned tables
  (`apt-get install tesseract-ocr poppler-utils`). Without them, counties whose data is a
  raster image fail with a clear `OCRRequiredError` instead of being partially read;
  everything else works unchanged, and the OCR tests skip.

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

surplusai parser interpret data/test_pdfs/Harford_County_MD.pdf
surplusai parser interpret data/test_pdfs/Marion_County_IN_2023.pdf --state in --county marion

surplusai parser profile data/test_pdfs/Marion_County_IN_2023.pdf --slug marion --state in --county marion
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
  yields nothing. Such a region is read by OCR, or — if the OCR toolchain is not installed
  — parsing fails with `OCRRequiredError` rather than returning the surrounding text as if
  it were data.
- **Header position and wrapping are detected, not assumed.** Headers may sit below a title
  and a date line, and labels wrap across physical lines. Wrapped labels are rejoined by
  word position rather than reading order, because the linearized text can pair the
  continuation word with the wrong column.
- **Nothing is discarded.** Titles, footers and banner lines that are not data rows are
  retained as `unparsed_fragments`. Ragged rows keep their extra cells under overflow keys,
  and duplicate or blank column names are given positional suffixes so no column collapses
  into another.
- **Scanned tables are read by OCR, and never trusted.** When a data region holds no text,
  the page is rasterised and recognised, and the recovered word boxes go through the same
  column clustering a text layer would. Every resulting row is routed to human review
  regardless of score, because a misread digit in a money field is expensive and
  recognition confidence does not predict that failure well.

### How interpretation works

Extraction records what a county published. Interpretation is a separate, re-runnable
opinion about what those columns mean, so a correction never requires reopening the PDF.

Each published column resolves in precedence order: a county config override, then the
surplus verdict, then an exact alias match, then a fuzzy match, then inference from the
column's own values, and finally unresolved — in which case the column is still preserved
under its published name and reported so an alias can be added.

Aliases live in `config/parsing/field_aliases.yaml`. Adding a spelling there helps every
county that follows, which is the mechanism by which the system gets better as counties are
added.

### The surplus rule

This is the highest-risk logic in the system, so it is deliberately the most conservative.
`surplus_amount` is populated **only** when a county names a column as surplus. It is never
computed, and near-misses do not count.

| Rule | Why |
|---|---|
| A denylist beats everything | A sale price, winning bid, assessment or already-refunded amount is never surplus, at any confidence |
| Surplus is matched **exactly**, never fuzzily | `sale amount` and `surplus amount` share enough tokens to fool any similarity measure, and that is the expensive confusion |
| Rival columns produce **no answer** | Marion publishes `Overbid`, `Refunded Overbid` and `Remaining Overbid`; picking one automatically would be a guess |
| Arithmetic is never used | Calvert bids $15,000.00 against a $3,743.93 sale and publishes no surplus. Liens, fees and costs come out first, so the difference is not the amount owed |

An ambiguous county reports what it saw and asks for one line of config:

```yaml
# config/counties/in/marion.yaml
surplus_column: "Remaining Overbid"
```

Two consequences worth knowing:

- **`NULL` is not zero.** A null surplus means no figure was published or none could be
  chosen; `$0.00` is a real figure meaning the money has already been paid out. Marion has
  950 records with a figure but only 130 with anything left to claim.
- **Unresolved surplus never auto-accepts.** Every other field may have resolved perfectly,
  but the figure the business exists to find is unknown, so those rows route to review
  rather than appearing ready to work. OCR-derived rows are capped the same way.

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

### County profiles

Every parsed document yields a profile recording how that county publishes: its column
names, whether the file was searchable or scanned, whether OCR was needed, the table
structure, which strategy read it, whether a surplus was explicitly listed, and what its
owner names tend to look like.

Profiles are **append-only and never overwritten**. A profile's identity is a hash of its
layout, deliberately excluding record counts and confidences — so Marion's 2023 file (950
records) and its 2024 file (917) produce the *same* fingerprint and the second is recorded
as another sighting of one profile rather than as a new one. A genuine layout change writes
a new version and marks the old one superseded; nothing is ever edited or deleted, so a
county quietly renaming a column stays visible and any past parse remains reproducible.

A profile is a prior, never a decision. It reorders the strategy cascade so the extractor
that worked last time is tried first, and it flags drift when the columns just read differ
from the ones on record. It cannot force an outcome — the winning strategy is still
whichever scores best structurally — so a county that changes its layout is re-read
correctly instead of forced into last year's shape.

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
      interpretation/  canonical schema, alias registry, type inference,
                       surplus resolution, confidence and routing
    utils/        settings, logging, base exceptions
tests/
    unit/         fast tests, transaction-rolled-back DB access, corpus parser tests
    integration/  full CLI + migration lifecycle against a real database
config/parsing/   field aliases, surplus vocabulary, confidence thresholds
config/counties/  per-county overrides, added without touching code
data/test_pdfs/   county PDF corpus plus golden expectations
docs/             architecture decisions and runbooks
```

## Known follow-ups

Two items from the Phase 2 design are deliberately not built yet:

- **A persisted review queue.** Rows routed to `review` or `quarantine` are labelled and
  returned, but there is no table backing a reviewer workflow. Nothing is lost in the
  meantime — the routing decision travels with every row.
- **Leave-one-out evaluation.** The corpus proves the pipeline works on the counties it has
  seen. A harness that re-runs a county with its own profile and contributed aliases
  excluded would measure cold-start accuracy directly, which is the sharper guard against
  overfitting as the corpus grows.
