from __future__ import annotations

import re
from collections import Counter

from surplus_ai.parser.models import ExtractedTable

CURRENCY_RE = re.compile(r"^\(?\$?\s*-?[\d,]+(?:\.\d{1,2})?\)?$")
DATE_RE = re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}")
INTEGER_RE = re.compile(r"^-?\d+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-./]*$")

# Weights sum to 1.0. Every signal is structural, so the scorer generalizes to counties
# that have never been seen rather than recognizing familiar layouts.
WEIGHTS: dict[str, float] = {
    "column_consistency": 0.30,
    "fill_rate": 0.20,
    "type_coherence": 0.25,
    "header_plausibility": 0.15,
    "size": 0.10,
}

MAX_REASONABLE_CELL_LENGTH = 200


def score_tables(tables: list[ExtractedTable]) -> tuple[float, dict[str, float]]:
    """Score a strategy's whole output, weighting each table by its row count."""
    if not tables:
        return 0.0, dict.fromkeys(WEIGHTS, 0.0)

    per_table = [(t, score_table(t)) for t in tables]
    total_rows = sum(t.row_count for t, _ in per_table) or 1

    breakdown: dict[str, float] = {}
    for signal in WEIGHTS:
        breakdown[signal] = (
            sum(parts[signal] * t.row_count for t, (_, parts) in per_table) / total_rows
        )
    overall = sum(total * t.row_count for t, (total, _) in per_table) / total_rows
    return _clamp(overall), breakdown


def score_table(table: ExtractedTable) -> tuple[float, dict[str, float]]:
    """Score one table 0..1 on structural evidence alone."""
    rows = table.rows
    if not rows:
        return 0.0, dict.fromkeys(WEIGHTS, 0.0)

    signals = {
        "column_consistency": column_consistency(rows),
        "fill_rate": fill_rate(rows),
        "type_coherence": type_coherence(rows),
        "header_plausibility": header_plausibility(rows),
        "size": size_signal(rows),
    }
    penalty = overflow_penalty(rows)
    total = sum(signals[name] * weight for name, weight in WEIGHTS.items()) * (1.0 - penalty)
    return _clamp(total), signals


def column_consistency(rows: tuple[tuple[str, ...], ...]) -> float:
    """Share of rows whose column count matches the most common one.

    A table split down the wrong gutter produces ragged rows and scores poorly here.
    """
    counts = Counter(len(row) for row in rows)
    modal_count = counts.most_common(1)[0][1]
    return modal_count / len(rows)


def fill_rate(rows: tuple[tuple[str, ...], ...]) -> float:
    """Share of cells that carry content. Over-splitting leaves empty columns behind."""
    total = sum(len(row) for row in rows)
    if total == 0:
        return 0.0
    filled = sum(1 for row in rows for cell in row if cell.strip())
    return filled / total


def type_coherence(rows: tuple[tuple[str, ...], ...]) -> float:
    """Average share of cells in each column that share one inferred type.

    A correctly split column is mostly one kind of thing: all currency, all dates, all
    identifiers. Mixed columns are evidence the boundaries are wrong.
    """
    if len(rows) < 2:
        return 0.0
    width = max(len(row) for row in rows)
    if width == 0:
        return 0.0

    scores: list[float] = []
    for column in range(width):
        values = [row[column] for row in rows[1:] if column < len(row) and row[column].strip()]
        if not values:
            continue
        kinds = Counter(infer_kind(v) for v in values)
        scores.append(kinds.most_common(1)[0][1] / len(values))
    return sum(scores) / len(scores) if scores else 0.0


def header_plausibility(rows: tuple[tuple[str, ...], ...]) -> float:
    """How much the best of the first few rows looks like a row of column labels."""
    return max((row_header_score(row) for row in rows[:5]), default=0.0)


def row_header_score(row: tuple[str, ...]) -> float:
    """Labels are short, wordy, distinct, and contain no money or dates."""
    values = [cell.strip() for cell in row if cell.strip()]
    if len(values) < 2:
        return 0.0

    alphabetic = sum(1 for v in values if any(c.isalpha() for c in v)) / len(values)
    no_currency = sum(1 for v in values if not CURRENCY_RE.match(v)) / len(values)
    no_dates = sum(1 for v in values if not DATE_RE.match(v)) / len(values)
    concise = sum(1 for v in values if len(v) <= 40) / len(values)
    distinct = len({v.casefold() for v in values}) / len(values)
    density = len(values) / len(row) if row else 0.0

    return (alphabetic + no_currency + no_dates + concise + distinct + density) / 6


def size_signal(rows: tuple[tuple[str, ...], ...]) -> float:
    """Prefer real tables over one- or two-row fragments, saturating quickly."""
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return 0.0
    return min(len(rows) / 10.0, 1.0)


def overflow_penalty(rows: tuple[tuple[str, ...], ...]) -> float:
    """Penalize cells so long they are clearly several columns collapsed into one."""
    total = sum(len(row) for row in rows)
    if total == 0:
        return 0.0
    overflowing = sum(1 for row in rows for cell in row if len(cell) > MAX_REASONABLE_CELL_LENGTH)
    return min(overflowing / total * 5.0, 0.5)


def infer_kind(value: str) -> str:
    """Coarse type label used only for column coherence, never for data conversion."""
    text = value.strip()
    if not text:
        return "empty"
    # Bare digits are checked before currency: the currency pattern also matches "2023",
    # which would label a tax-year column as money.
    if INTEGER_RE.match(text):
        return "integer"
    if CURRENCY_RE.match(text):
        return "currency"
    if DATE_RE.match(text):
        return "date"
    if IDENTIFIER_RE.match(text) and not text.replace(" ", "").isalpha():
        return "identifier"
    return "text"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
