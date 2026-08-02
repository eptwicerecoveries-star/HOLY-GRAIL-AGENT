# SurplusAI

## Mission

Build an AI-powered operating system for a surplus funds recovery business.

The system should automate every repetitive task while remaining modular, testable, and easy to maintain.

The software should accept county surplus funds PDFs from any county in the United States and transform them into qualified leads inside Airtable.

The software should NEVER hardcode county-specific logic.

Everything must be configuration-driven.

---

## Objectives

1. Read county surplus PDFs.
2. Extract every row.
3. Normalize data.
4. Classify owner types.
5. Remove companies.
6. Keep individuals.
7. Research official county property records where supported.
8. Export CSV.
9. Upload to Airtable.
10. Score opportunities.
11. Generate daily call sheets.
12. Keep complete CRM history.

---

## Architecture

Python

Backend only.

No frontend initially.

CLI first.

Later add Streamlit dashboard.

---

## Principles

Every feature must be modular.

Every module must have tests.

Every module must have logging.

Every module must have configuration.

Never duplicate code.

Never hardcode counties.

Never hardcode state rules.

Everything configurable.

---

## Code Style

Python 3.12

PEP8

Type hints

Dataclasses

Pydantic

pytest

ruff

black

mypy

Use environment variables for secrets.

No credentials inside code.

---

## Folder Structure

surplus_ai/

    parser/

    classifier/

    research/

    crm/

    compliance/

    scoring/

    reports/

    dashboard/

    database/

    utils/

tests/

config/

docs/

data/

logs/

exports/
